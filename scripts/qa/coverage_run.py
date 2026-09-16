#!/usr/bin/env python3
"""全覆盖跑者：清单 × proof DSL → 一份三态报告 R（docs/CONTRACT.md §58 QA 闸门）。

goal 2026-09-15「全覆盖」的执行端。`qa/coverage_inventory.json` 的每一行带一条
`proof` 字符串（DSL：`unittest:` / `swift:` / `parity:` / `playwright:` / `http:` /
`settings:` / `flow:` / `axprobe:` / `fixture:`，多条用 ` && ` 串联、全中才算在）。
本模块把同类 proof 归组，每种**重工具只跑一次**（一次 full unittest、一次
`shell/tests/run.sh`、一次 vitest parity、一次 playwright、一个 demo server），
再按 id 顺序写出 `qa/coverage-report/report.md`：

    <id> PRESENT evidence=<one line>
    <id> MISSING reason=<one line> evidence=<one line>
    <id> WAIVED reason=<tombstone|covered-by-…|design-not-carried D..> evidence=-
    PRESENT=<n>
    MISSING=<n>
    WAIVED=<n>

纪律：
- **绝不碰 live 数据**：demo server / install.sh / uninstall.sh / doctor 全部跑在
  `/tmp` 的临时 HOME 里，`launchctl` `open` `osascript` `crontab` 一律 PATH 前缀
  假货（argv 只记账），收尾 trap 删干净（§58 的门只读、不改仓）。
- **注入缝走参数**：Shell / Http / demo-server 工厂三个 seam 都是构造参数，单元
  测试注入假执行器——不起子进程、不联网（防腐 #3：禁 module-global 注入缝）。
- **退出码**：MISSING=0 → 0；有 MISSING → 1；清单缺席 → 2。

用法：
    bash scripts/qa/full_coverage.sh [--logdir DIR] [--only <id prefix>] [--skip-ax]
    python3 scripts/qa/coverage_run.py --inventory qa/coverage_inventory.json
"""

from __future__ import annotations

import argparse
import atexit
import glob
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import namedtuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 守护进程解释器的候选序（install.sh pick_python 同一口味）：能 `import yaml` 的第一个。
# Xcode 的 python3 在 PATH 上却看不到 PyYAML——act.* / server 在它上面一律 ModuleNotFoundError，
# 所以这里探一次、全跑复用（注入缝走参数：Tools/Judge 的 python_bin）。
PYTHON_CANDIDATES = ("/usr/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3")


def resolve_python(candidates=None, probe=None):
    """第一个能 import yaml 的解释器（探不到 → sys.executable，诚实往下跑、报真错）。"""
    def _can_yaml(path):
        try:
            return subprocess.run([path, "-c", "import yaml"], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=30).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    check = probe or _can_yaml
    order = list(candidates or ((sys.executable,) + PYTHON_CANDIDATES))
    for path in order:
        if path and check(path):
            return path
    return sys.executable or "python3"


def yaml_site(python_bin, runner=None):
    """PyYAML 所在的 site-packages 目录（可能在 **真** HOME 的 user site 里）。

    本模块的每个子进程都跑在临时 HOME 上（绝不碰 live 数据），而这台机器的 PyYAML 装在
    `~/Library/Python/<ver>/lib/python/site-packages`——HOME 一换，`import yaml` 就没了。
    所以把它的目录显式拼进 PYTHONPATH：HOME 仍是临时的，依赖仍找得到。None = 探不到。"""
    def _run(cmd):
        try:
            done = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, timeout=30)
            return done.returncode, (done.stdout or "").strip()
        except (OSError, subprocess.SubprocessError):
            return 1, ""

    rc, out = (runner or _run)([python_bin, "-c",
                                "import os, yaml; print(os.path.dirname(os.path.dirname(yaml.__file__)))"])
    return out if rc == 0 and out else None


def python_path(site=None, extra=None):
    """子进程的 PYTHONPATH：repo root + PyYAML 的 site 目录（+ 调用方补充）。"""
    parts = [REPO_ROOT] + [p for p in (site, extra) if p]
    return os.pathsep.join(parts)

DEFAULT_INVENTORY = os.path.join("qa", "coverage_inventory.json")
DEFAULT_REPORT = os.path.join("qa", "coverage-report", "report.md")
DEFAULT_LOGDIR = os.path.join("qa", "coverage-report", "logs")

PRESENT = "PRESENT"
MISSING = "MISSING"
WAIVED = "WAIVED"

EVIDENCE_MAX = 200
# 一条 proof 里的分句分隔符（brief §3：全中才算在）
CLAUSE_SEP = " && "
# 整跑预算 45 min（brief §4）——每种重工具的单体预算之和留出余量
T_UNITTEST = 1500
T_SWIFT = 900
T_VITEST = 600
T_PLAYWRIGHT = 900
T_FLOW = 900
T_SHORT = 120
# flow:pages_controls 的 spec（本 PR 新增；playwright: proof 也可以直接点名它）
PAGES_SPEC = "coverage.spec.ts"

Proc = namedtuple("Proc", "rc out")
Resp = namedtuple("Resp", "status text")
Verdict = namedtuple("Verdict", "state reason evidence")
# 一个 map 项：ok = 在场，evidence = 一行证据
Hit = namedtuple("Hit", "ok evidence")


def one_line(text, limit=EVIDENCE_MAX):
    """任意输出 → 一行 ≤ limit 字符的证据（R 的每行只许一行）。"""
    flat = " ".join(str(text or "").split())
    return flat[:limit] if flat else "-"


def tail_line(text):
    """最后一行非空输出（fixture: 的 evidence 约定）。"""
    lines = [ln for ln in str(text or "").splitlines() if ln.strip()]
    return one_line(lines[-1]) if lines else "-"


def log(msg):
    sys.stderr.write("[coverage_run %s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stderr.flush()


# --------------------------------------------------------------------------- #
# 注入缝 1/3：子进程
# --------------------------------------------------------------------------- #

class Shell:
    """真跑子进程（stdout+stderr 合并）。单元测试注入假货，绝不走这里。"""

    def __init__(self, logdir=None):
        self.logdir = logdir

    def run(self, cmd, cwd=None, env=None, timeout=T_SHORT, log_name=None):
        full_env = dict(os.environ)
        if env:
            full_env.update({k: str(v) for k, v in env.items()})
        try:
            done = subprocess.run(cmd, cwd=cwd or REPO_ROOT, env=full_env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, errors="replace", timeout=timeout)
            proc = Proc(done.returncode, done.stdout or "")
        except subprocess.TimeoutExpired as exc:
            proc = Proc(124, (exc.output or "") + "\nTIMEOUT after %ss" % timeout)
        except OSError as exc:
            proc = Proc(127, "cannot exec %s: %s" % (cmd[0], exc))
        self._write_log(log_name, cmd, proc)
        return proc

    def _write_log(self, log_name, cmd, proc):
        if not (self.logdir and log_name):
            return
        try:
            os.makedirs(self.logdir, exist_ok=True)
            path = os.path.join(self.logdir, log_name + ".log")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write("$ %s\n[rc=%s]\n%s\n" % (" ".join(cmd), proc.rc, proc.out))
        except OSError as exc:      # 日志写不下去不许毁掉整跑
            log("logdir write failed: %s" % exc)


# --------------------------------------------------------------------------- #
# 注入缝 2/3：HTTP
# --------------------------------------------------------------------------- #

class Http:
    """loopback HTTP（urllib）。4xx/5xx 不抛，按状态码返回——proof 要判的就是状态码。"""

    def request(self, method, url, body=None, headers=None, timeout=30):
        data = body.encode("utf-8") if isinstance(body, str) else body
        req = urllib.request.Request(url, data=data, method=method)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return Resp(resp.status, resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            return Resp(exc.code, exc.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return Resp(0, "request failed: %s" % exc)


# --------------------------------------------------------------------------- #
# 临时 HOME / PATH 前缀假货（install / uninstall / doctor / actd 用）
# --------------------------------------------------------------------------- #

_SHIM_BODY = """#!/bin/sh
# QA shim (scripts/qa/coverage_run.py)：只记 argv，绝不碰真 gui domain
printf '%s %s\\n' "$(basename "$0")" "$*" >> "$ZAA_SHIM_LOG"
exit 0
"""

# stub `claude`：executor 的注入缝之外的兜底（brief §3 flow:card_lifecycle）——
# 打印一行 canned 结果，永不联网、永不真跑 agent。
_CLAUDE_STUB = """#!/bin/sh
printf '%s %s\\n' claude "$*" >> "$ZAA_SHIM_LOG"
echo '{"result":"qa coverage stub","is_error":false}'
exit 0
"""

SHIMMED = ("launchctl", "open", "osascript", "crontab", "claude")


class TempHomes:
    """/tmp 下的临时 HOME 台账（收尾一并删除；只删 /tmp 下的路径）。"""

    def __init__(self):
        self.paths = []

    def make(self, slug):
        path = tempfile.mkdtemp(prefix="zaa-cov-%s-" % slug, dir="/tmp")
        self.paths.append(path)
        return path

    def cleanup(self):
        for path in list(self.paths):
            if path.startswith("/tmp/"):
                shutil.rmtree(path, ignore_errors=True)
            self.paths.remove(path)


def write_shims(home, names=SHIMMED):
    """<home>/.shims 里放假 launchctl/open/osascript/crontab/claude；返回 (dir, log)。"""
    shim_dir = os.path.join(home, ".shims")
    os.makedirs(shim_dir, exist_ok=True)
    shim_log = os.path.join(home, "shims.log")
    for name in names:
        path = os.path.join(shim_dir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_CLAUDE_STUB if name == "claude" else _SHIM_BODY)
        os.chmod(path, 0o755)
    return shim_dir, shim_log


def shim_env(home, shim_dir, shim_log, extra=None):
    """HOME=临时目录 + PATH 前缀假货 + 去掉 node/npm（install.sh 的 UI 步会自己跳过）。"""
    env = {
        "HOME": home,
        "PATH": "%s:/usr/bin:/bin:/usr/sbin:/sbin" % shim_dir,
        # `python3 -m …` 的 PYTHONPATH 由各调用点给；这里只保证 HOME/PATH 两条红线

        "ZAA_SHIM_LOG": shim_log,
        "ZAI_NO_OPEN": "1",
    }
    if extra:
        env.update(extra)
    return env


# --------------------------------------------------------------------------- #
# 注入缝 3/3：demo server（临时 HOME + 随机端口 + 等 /api/board）
# --------------------------------------------------------------------------- #

class DemoServer:
    def __init__(self, base_url, home, token, proc=None):
        self.base_url = base_url
        self.home = home
        self.token = token
        self.proc = proc

    def stop(self):
        if self.proc is None:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_demo_server(homes, http, shell, scene="initial", logdir=None, python_bin=None,
                      pythonpath=None):
    """种 demo 数据 → 起 `python3 -m server` → 等 /api/board（web/e2e/demoServer.ts 同一条链）。"""
    python_bin = python_bin or resolve_python()
    pythonpath = pythonpath or python_path(yaml_site(python_bin))
    home = homes.make("srv")
    seed = shell.run([python_bin, os.path.join(REPO_ROOT, "scripts", "demo_seed.py"), home,
                      "--scene", scene], timeout=T_SHORT, log_name="demo_seed")
    if seed.rc != 0:
        raise RuntimeError("demo_seed.py failed: %s" % one_line(seed.out))
    os.makedirs(os.path.join(home, "state"), exist_ok=True)
    # §68.5 首次运行判定：临时 home 没有 config.yaml → 整页换向导；写「向导已完成」标记
    with open(os.path.join(home, "state", "setup_done.json"), "w", encoding="utf-8") as handle:
        json.dump({"completed_at": "2026-09-02T12:00:00Z"}, handle)
    example = os.path.join(REPO_ROOT, "config.example.yaml")
    target = os.path.join(home, "config.yaml")
    if os.path.exists(example) and not os.path.exists(target):
        shutil.copyfile(example, target)
    port = free_port()
    env = dict(os.environ)
    env.update({"HOME": home, "AIASSISTANT_HOME": home, "ZAI_PORT": str(port),
                "PYTHONPATH": pythonpath, "PYTHONUNBUFFERED": "1"})
    out_path = os.path.join(logdir or tempfile.gettempdir(), "demo_server.log")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # 句柄要活到 server 进程结束（Popen 的 stdout），所以不能用 with
    handle = open(out_path, "a", encoding="utf-8")
    proc = subprocess.Popen([python_bin, "-m", "server"], cwd=REPO_ROOT, env=env,
                            stdout=handle, stderr=subprocess.STDOUT)
    base_url = "http://127.0.0.1:%d" % port
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("server exited early rc=%s (see %s)" % (proc.returncode, out_path))
        if http.request("GET", base_url + "/api/board", timeout=5).status == 200:
            token_file = os.path.join(home, "state", "server.token")
            token = ""
            if os.path.exists(token_file):
                with open(token_file, encoding="utf-8") as tok:
                    token = tok.read().strip()
            log("demo server up on %s (home=%s)" % (base_url, home))
            return DemoServer(base_url, home, token, proc)
        time.sleep(0.2)
    proc.terminate()
    raise RuntimeError("server did not answer /api/board in 90 s (see %s)" % out_path)


# --------------------------------------------------------------------------- #
# 清单
# --------------------------------------------------------------------------- #

def load_inventory(path):
    """清单 → 行列表（按 id 排序）。缺席 = 调用方 exit 2。"""
    with open(path, encoding="utf-8") as handle:
        doc = json.load(handle)
    rows = doc.get("scenarios") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        raise ValueError("inventory %s has no scenario list" % path)
    return sorted((r for r in rows if isinstance(r, dict)), key=lambda r: str(r.get("id") or ""))


def parse_proof(proof):
    """proof 字符串 → [(kind, arg), …]（` && ` 分句；无冒号的分句 kind = ""）。"""
    out = []
    for clause in str(proof or "").split(CLAUSE_SEP):
        clause = clause.strip()
        if not clause:
            continue
        kind, _, arg = clause.partition(":")
        out.append((kind.strip(), arg.strip()))
    return out


def proof_kinds(rows):
    """清单里出现过的 proof 种类（决定哪些重工具要跑）。"""
    kinds = set()
    for row in rows:
        if str(row.get("status")) == "waived":
            continue
        for kind, _arg in parse_proof(row.get("proof")):
            kinds.add(kind)
    return kinds


# --------------------------------------------------------------------------- #
# 重工具：每种只跑一次（惰性 memoize），返回 {key: Hit} + error
# --------------------------------------------------------------------------- #

_VERBOSE_CASE = re.compile(r"\(([A-Za-z0-9_.]+)\)\s*\.\.\.\s*(.+)$")


def module_of(dotted):
    """`tests.test_a.Case.test_x` / `tests.test_a.Case` → `tests.test_a`（首段全小写的前缀）。"""
    parts = dotted.split(".")
    keep = []
    for part in parts:
        if part[:1].isupper():
            break
        keep.append(part)
    if len(keep) == len(parts) and len(parts) > 1:
        keep = parts[:-1]                  # 类名也是小写时：丢最后一段（方法名）
    return ".".join(keep)


def parse_unittest_verbose(text):
    """`python3 -m unittest -v` 输出 → {module: Hit}（一个 FAIL/ERROR 就判整模块不在）。"""
    stats = {}
    for line in str(text or "").splitlines():
        match = _VERBOSE_CASE.search(line.strip())
        if not match:
            continue
        module = module_of(match.group(1))
        verdict = match.group(2).strip()
        if not module:
            continue
        ran, bad = stats.get(module, (0, 0))
        failed = verdict.startswith("FAIL") or verdict.startswith("ERROR")
        stats[module] = (ran + 1, bad + (1 if failed else 0))
    return {mod: Hit(bad == 0, "%s: %d tests, %d failed" % (mod, ran, bad))
            for mod, (ran, bad) in stats.items()}


def parse_playwright_json(text):
    """playwright `--reporter=json` → {(spec file, title): Hit}。"""
    out = {}
    try:
        doc = json.loads(text or "{}")
    except ValueError:
        return out

    def walk(suite, file_hint):
        spec_file = suite.get("file") or file_hint
        for spec in suite.get("specs", []) or []:
            for test in spec.get("tests", []) or []:
                results = test.get("results", []) or []
                status = (results[-1].get("status") if results else "") or ""
                title = spec.get("title", "")
                ok = status == "passed" or spec.get("ok") is True and status != "failed"
                out[(os.path.basename(spec_file or ""), title)] = Hit(
                    ok, "playwright %s::%s %s" % (os.path.basename(spec_file or ""), title,
                                                  status or "no result"))
        for child in suite.get("suites", []) or []:
            walk(child, spec_file)

    for suite in doc.get("suites", []) or []:
        walk(suite, suite.get("file"))
    return out


def parse_vitest_parity(text):
    """vitest parity 报告 → {id: Hit}；优先复用 scripts/ui/parity_check.py 的映射。"""
    mapping = _parity_check_mapping(text)
    if mapping is None:
        try:
            doc = json.loads(text or "{}")
        except ValueError:
            return {}
        mapping = {}
        for suite in doc.get("testResults", []) or []:
            for case in suite.get("assertionResults", []) or []:
                mapping[case.get("title", "")] = case.get("status", "") == "passed"
    return {pid: Hit(bool(ok), "vitest parity %s %s" % (pid, "passed" if ok else "failed"))
            for pid, ok in mapping.items()}


def _parity_check_mapping(text):
    """scripts/ui/parity_check.py 的 parse_vitest_report + control_presence（唯一真源）。"""
    import importlib.util
    path = os.path.join(REPO_ROOT, "scripts", "ui", "parity_check.py")
    try:
        spec = importlib.util.spec_from_file_location("zaa_parity_check", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        results = module.parse_vitest_report(text)
        return module.control_presence(results, frozenset())
    except Exception as exc:
        log("parity_check.py mapping unavailable (%s) — falling back to local parse" % exc)
        return None


def parse_swift_run(text, rc, harnesses):
    """`shell/tests/run.sh` 输出 → {harness: Hit}（rc=0 全过；否则按最后一个步骤定罪）。"""
    steps = [ln for ln in str(text or "").splitlines() if ln.startswith("==> [")]
    last = steps[-1] if steps else ""
    out = {}
    for name in harnesses:
        slug = name.replace("Harness", "").lower()
        if rc == 0:
            seen = any(slug in step.lower() for step in steps)
            out[name] = Hit(seen, "shell/tests/run.sh rc=0, %s exercised" % name
                            if seen else "run.sh rc=0 but no step mentions %s" % name)
        else:
            blamed = slug in last.lower()
            out[name] = Hit(False, "shell/tests/run.sh rc=%s%s" % (
                rc, " at %s" % one_line(last, 80) if blamed else ", %s not reached" % name))
    return out


class Tools:
    """重工具的单次执行台账。每个 map 惰性跑一次、结果 memoize。"""

    def __init__(self, shell, http, opts, homes, server_factory=None, python_bin=None,
                 pythonpath=None):
        self.python = python_bin or resolve_python()
        self.pythonpath = pythonpath or python_path(yaml_site(self.python))
        self.shell = shell
        self.http = http
        self.opts = opts
        self.homes = homes
        self._server_factory = server_factory or (
            lambda: start_demo_server(homes, http, shell, logdir=opts.logdir,
                                      python_bin=self.python, pythonpath=self.pythonpath))
        self._cache = {}
        self._server = None
        self._server_error = None

    # -- unittest ---------------------------------------------------------- #
    def unittest_map(self, modules):
        if "unittest" in self._cache:
            return self._cache["unittest"]
        wanted = sorted(modules)
        scope = self.opts.unittest_scope
        if scope == "auto":
            scope = "listed" if 0 < len(wanted) <= self.opts.unittest_listed_max else "discover"
        if scope == "listed" and wanted:
            cmd = [self.python, "-m", "unittest", "-v"] + wanted
        else:
            cmd = [self.python, "-m", "unittest", "discover", "-v", "-s", "tests", "-t", ".",
                   "-p", "test_*.py"]
        log("unittest (%s, %d modules requested) …" % (scope, len(wanted)))
        proc = self.shell.run(cmd, cwd=REPO_ROOT, env={"PYTHONPATH": self.pythonpath},
                              timeout=T_UNITTEST, log_name="unittest")
        result = (parse_unittest_verbose(proc.out), None if proc.out.strip() else
                  "unittest produced no output (rc=%s)" % proc.rc)
        self._cache["unittest"] = result
        return result

    # -- swift ------------------------------------------------------------- #
    def swift_map(self):
        if "swift" in self._cache:
            return self._cache["swift"]
        harnesses = sorted(os.path.basename(p)[:-6] for p in
                           glob.glob(os.path.join(REPO_ROOT, "shell", "tests", "*Harness.swift")))
        if not shutil.which("swiftc"):
            self._cache["swift"] = ({}, "swiftc absent — shell harnesses cannot run here")
            return self._cache["swift"]
        log("shell/tests/run.sh (%d harnesses) …" % len(harnesses))
        proc = self.shell.run(["bash", os.path.join(REPO_ROOT, "shell", "tests", "run.sh")],
                              cwd=REPO_ROOT, timeout=T_SWIFT, log_name="swift")
        self._cache["swift"] = (parse_swift_run(proc.out, proc.rc, harnesses), None)
        return self._cache["swift"]

    # -- vitest parity ----------------------------------------------------- #
    def parity_map(self):
        if "parity" in self._cache:
            return self._cache["parity"]
        web = os.path.join(REPO_ROOT, "web")
        if not os.path.isdir(os.path.join(web, "node_modules")):
            self._cache["parity"] = ({}, "web/node_modules absent — run npm ci in web/")
            return self._cache["parity"]
        out_path = os.path.join(self.opts.logdir or tempfile.gettempdir(), "parity-vitest.json")
        log("vitest parity …")
        proc = self.shell.run(["npx", "vitest", "run", "src/parity.test.tsx", "--reporter=json",
                               "--outputFile=" + out_path], cwd=web, timeout=T_VITEST,
                              log_name="parity")
        text = _read(out_path)
        if not text:
            self._cache["parity"] = ({}, "vitest produced no report (rc=%s)" % proc.rc)
            return self._cache["parity"]
        self._cache["parity"] = (parse_vitest_parity(text), None)
        return self._cache["parity"]

    # -- playwright -------------------------------------------------------- #
    def playwright_map(self, specs=()):
        """一次 `npx playwright test`。specs 非空 = 只跑清单引用到的 spec 文件（同一趟、更省时）。"""
        if "playwright" in self._cache:
            return self._cache["playwright"]
        web = os.path.join(REPO_ROOT, "web")
        if not os.path.isdir(os.path.join(web, "node_modules")):
            self._cache["playwright"] = ({}, "web/node_modules absent — run npm ci in web/")
            return self._cache["playwright"]
        out_path = os.path.join(self.opts.logdir or tempfile.gettempdir(), "playwright.json")
        log("playwright (%s) …" % (", ".join(sorted(specs)) or "all specs"))
        argv = ["npx", "playwright", "test"] + ["e2e/" + s for s in sorted(specs)] + \
               ["--reporter=json"]
        proc = self.shell.run(argv, cwd=web,
                              env={"PLAYWRIGHT_JSON_OUTPUT_NAME": out_path},
                              timeout=T_PLAYWRIGHT, log_name="playwright")
        text = _read(out_path) or proc.out
        mapping = parse_playwright_json(text)
        error = None if mapping else "playwright produced no parsable report (rc=%s)" % proc.rc
        self._cache["playwright"] = (mapping, error)
        return self._cache["playwright"]

    # -- demo server ------------------------------------------------------- #
    def server(self):
        """唯一的 demo server（http: / settings: / 大半 flow: 共用）。"""
        if self._server is None and self._server_error is None:
            try:
                self._server = self._server_factory()
            except Exception as exc:
                self._server_error = one_line(exc)
        return self._server, self._server_error

    def stop(self):
        if self._server is not None:
            self._server.stop()
            self._server = None


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# http: / settings: 执行器
# --------------------------------------------------------------------------- #

_HTTP_RE = re.compile(r"^(?P<method>[A-Z]+)\s+(?P<path>\S+)(?P<rest>.*)$")


def parse_http_proof(arg):
    """`GET /api/board expect=200 noauth contains=lanes body=@f.json` → dict。"""
    match = _HTTP_RE.match(arg.strip())
    if not match:
        raise ValueError("cannot parse http proof %r" % arg)
    spec = {"method": match.group("method"), "path": match.group("path"),
            "expect": 200, "noauth": False, "contains": None, "body": None}
    for token in match.group("rest").split():
        if token == "noauth":
            spec["noauth"] = True
        elif token.startswith("expect="):
            spec["expect"] = int(token.split("=", 1)[1])
        elif token.startswith("contains="):
            spec["contains"] = token.split("=", 1)[1]
        elif token.startswith("body=@"):
            spec["body"] = {"file": token.split("=@", 1)[1]}
        elif token.startswith("body="):
            spec["body"] = {"inline": token.split("=", 1)[1]}
    return spec


def board_ids(http, server):
    """seeded board 上的卡片 id（{id} 占位符的解析源；按出现顺序去重）。"""
    resp = http.request("GET", server.base_url + "/api/board")
    if resp.status != 200:
        return []
    try:
        doc = json.loads(resp.text)
    except ValueError:
        return []
    found, seen = [], set()

    def walk(node):
        if isinstance(node, dict):
            ident = node.get("id")
            if isinstance(ident, str) and ident and ident not in seen:
                seen.add(ident)
                found.append(ident)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(doc)
    return found


def http_body(spec):
    """proof 的 body 描述 → 请求体字符串（`@file` 相对 repo root）。"""
    if not spec.get("body"):
        return None
    if "inline" in spec["body"]:
        return spec["body"]["inline"]
    path = spec["body"]["file"]
    if not os.path.isabs(path):
        path = os.path.join(REPO_ROOT, path)
    text = _read(path)
    if not text:
        raise ValueError("body file absent or empty: %s" % path)
    return text


def run_http_proof(arg, http, server, ids):
    """一条 http: proof → Verdict。"""
    try:
        spec = parse_http_proof(arg)
        body = http_body(spec)
    except ValueError as exc:
        return Verdict(MISSING, one_line(exc), "-")
    path = spec["path"]
    if "{id}" in path:
        if not ids:
            return Verdict(MISSING, "no card id on the seeded board for {id}", "-")
        path = path.replace("{id}", ids[0])
    headers = {}
    if body is not None or spec["method"] in ("POST", "PUT", "PATCH", "DELETE"):
        headers["Content-Type"] = "application/json"
        body = body if body is not None else "{}"
    if not spec["noauth"] and server.token:
        headers["X-Zai-Token"] = server.token
    resp = http.request(spec["method"], server.base_url + path, body=body, headers=headers)
    evidence = "%s %s -> %s" % (spec["method"], path, resp.status)
    if resp.status != spec["expect"]:
        return Verdict(MISSING, "expected %s, got %s: %s" % (
            spec["expect"], resp.status, one_line(resp.text, 90)), evidence)
    if spec["contains"] and spec["contains"] not in resp.text:
        return Verdict(MISSING, "response lacks %r" % spec["contains"], evidence)
    return Verdict(PRESENT, "-", one_line(evidence))


# --- settings: ------------------------------------------------------------- #

_CHECK_SAMPLES = {"email": "qa.probe@example.com", "clock_time": "23:45",
                  "session_id": "qa-coverage-probe-session"}


def load_sections():
    """server.settings_catalog.SECTIONS（唯一真源；scripts/ui/parity_fixture.py 同款 import）。"""
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    from server import settings_catalog
    return settings_catalog.SECTIONS


def non_default_value(field):
    """一把旋钮的「非默认」探测值；None = 没有安全的非默认值。"""
    kind, default = field.get("kind"), field.get("default")
    if kind == "bool":
        return not bool(default)
    if kind == "enum":
        for choice in field.get("choices") or []:
            if choice != default:
                return choice
        return None
    if kind in ("int", "number"):
        low, high = (field.get("bounds") or (0, None))
        base = default if isinstance(default, (int, float)) else 0
        candidate = base + 1
        if high is not None and candidate > high:
            candidate = base - 1 if base - 1 >= (low or 0) else None
        if candidate is not None and kind == "int":
            candidate = int(candidate)
        return candidate
    if kind == "list":
        return ["qa-coverage-probe"]
    if kind == "string":
        check = field.get("check")
        if check:
            return _CHECK_SAMPLES.get(check)
        return (str(default or "") + "-qa-coverage-probe") or "qa-coverage-probe"
    return None


def _put_section(http, server, section_id, payload):
    return http.request("PUT", "%s/api/settings/%s" % (server.base_url, section_id),
                        body=json.dumps(payload),
                        headers={"Content-Type": "application/json",
                                 "X-Zai-Token": server.token})


def _field_effective(http, server, section_id, key):
    resp = http.request("GET", "%s/api/settings/%s" % (server.base_url, section_id))
    if resp.status != 200:
        return None, "GET section %s -> %s" % (section_id, resp.status)
    try:
        doc = json.loads(resp.text)
    except ValueError:
        return None, "GET section %s: unparsable body" % section_id
    for field in doc.get("fields", []) or []:
        if field.get("key") == key:
            return field, None
    return None, "section %s has no field %s" % (section_id, key)


def roundtrip_setting(http, server, section_id, key, field):
    """PUT 非默认 → GET 读回 → effective 反映 → PUT 默认回位。返回 Verdict。"""
    probe = non_default_value(field)
    if probe is None:
        return Verdict(MISSING, "no safe non-default value for kind=%s check=%s" % (
            field.get("kind"), field.get("check")), "-")
    put = _put_section(http, server, section_id, {key: probe})
    if put.status != 200:
        return Verdict(MISSING, "PUT %s=%r -> %s %s" % (key, probe, put.status,
                                                        one_line(put.text, 80)), "-")
    got, error = _field_effective(http, server, section_id, key)
    if error:
        return Verdict(MISSING, error, "-")
    if got.get("effective") != probe:
        _put_section(http, server, section_id, {key: field.get("default")})
        return Verdict(MISSING, "effective %r != written %r" % (got.get("effective"), probe),
                       "PUT/GET %s.%s" % (section_id, key))
    back = _put_section(http, server, section_id, {key: field.get("default")})
    if back.status != 200:
        return Verdict(MISSING, "restore default -> %s %s" % (back.status,
                                                             one_line(back.text, 80)), "-")
    return Verdict(PRESENT, "-", "settings %s.%s round trip %r→default ok" % (
        section_id, key, probe))


def run_settings_proof(arg, http, server):
    section_id, _, key = arg.partition(".")
    if not (section_id and key):
        return Verdict(MISSING, "malformed settings proof %r" % arg, "-")
    try:
        sections = {s["id"]: s for s in load_sections()}
    except Exception as exc:
        return Verdict(MISSING, "settings catalog unavailable: %s" % one_line(exc), "-")
    section = sections.get(section_id)
    if section is None:
        return Verdict(MISSING, "unknown settings section %r" % section_id, "-")
    field = next((f for f in section["fields"] if f["key"] == key), None)
    if field is None:
        return Verdict(MISSING, "section %s has no key %r" % (section_id, key), "-")
    return roundtrip_setting(http, server, section_id, key, field)


# --------------------------------------------------------------------------- #
# flow: 执行器（brief §3 的八条）
# --------------------------------------------------------------------------- #

class FlowCtx:
    def __init__(self, tools, shell, http, homes, opts, pw_specs=None):
        self.pw_specs = pw_specs if pw_specs is not None else set()
        self.python = tools.python
        self.pythonpath = tools.pythonpath
        self.tools = tools
        self.shell = shell
        self.http = http
        self.homes = homes
        self.opts = opts

    def server(self):
        return self.tools.server()


def _need_server(ctx):
    server, error = ctx.server()
    if server is None:
        return None, Verdict(MISSING, "demo server unavailable: %s" % error, "-")
    return server, None


def flow_install_fresh(ctx):
    """install.sh 在临时 HOME 里从零装一遍：exit 0 + plist 落在临时 LaunchAgents。"""
    home = ctx.homes.make("install")
    shim_dir, shim_log = write_shims(home)
    # install.sh 的 AIASSISTANT_HOME 恒等于它自己所在的 checkout（这里 = 本 worktree），
    # 它会在 checkout 里现建 config.yaml / state/（两者 .gitignore 在列）——跑前记下
    # 有无，跑后把本次新建的删掉，工作树不留痕。
    born = [p for p in (os.path.join(REPO_ROOT, "config.yaml"), os.path.join(REPO_ROOT, "state"))
            if not os.path.exists(p)]
    proc = ctx.shell.run(["bash", os.path.join(REPO_ROOT, "install.sh"), "--non-interactive"],
                         cwd=REPO_ROOT, env=shim_env(home, shim_dir, shim_log),
                         timeout=T_FLOW, log_name="flow_install_fresh")
    for path in born:
        shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else _unlink(path)
    plists = sorted(glob.glob(os.path.join(home, "Library", "LaunchAgents", "*.plist")))
    calls = one_line(_read(shim_log), 80)
    if proc.rc != 0:
        return Verdict(MISSING, "install.sh --non-interactive rc=%s: %s" % (
            proc.rc, one_line(proc.out, 90)), "shims: %s" % calls)
    if not plists:
        return Verdict(MISSING, "no plist under %s/Library/LaunchAgents" % home, "shims: %s" % calls)
    labels = ", ".join(os.path.basename(p) for p in plists)
    return Verdict(PRESENT, "-", "install.sh rc=0, %d plist(s): %s" % (len(plists), labels))


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def flow_doctor_clean(ctx):
    """`python3 -m act.doctor --fast --json` 在临时 HOME 里 fail=0（退出码 = FAIL 条数）。"""
    home = ctx.homes.make("doctor")
    example = os.path.join(REPO_ROOT, "config.example.yaml")
    if os.path.exists(example):
        shutil.copyfile(example, os.path.join(home, "config.yaml"))
    # state/ 与 dashboard.json 用 demo 种子铺出来（doctor 的「state dirs」「dashboard」两检查
    # 问的就是它们；没铺 = 判一个与被测行为无关的 FAIL）
    ctx.shell.run([ctx.python, os.path.join(REPO_ROOT, "scripts", "demo_seed.py"), home],
                  timeout=T_SHORT, log_name="flow_doctor_clean")
    os.makedirs(os.path.join(home, "state"), exist_ok=True)
    shim_dir, shim_log = write_shims(home)
    proc = ctx.shell.run([ctx.python, "-m", "act.doctor", "--fast", "--json"], cwd=REPO_ROOT,
                         env=shim_env(home, shim_dir, shim_log,
                                      {"AIASSISTANT_HOME": home,
                                       "PYTHONPATH": ctx.pythonpath}),
                         timeout=T_FLOW, log_name="flow_doctor_clean")
    fails = _doctor_fails(proc.out)
    evidence = "doctor --fast rc=%s%s" % (proc.rc, (", FAIL: " + ", ".join(fails[:4])) if fails else "")
    if proc.rc != 0 or fails:
        return Verdict(MISSING, "doctor reports %s FAIL (rc=%s)" % (len(fails) or proc.rc, proc.rc),
                       one_line(evidence))
    return Verdict(PRESENT, "-", one_line(evidence))


def _doctor_fails(text):
    """doctor --json 输出里 status=fail 的检查名（形状防御：任何带 status 的 dict 列表）。"""
    start = text.find("{")
    if start < 0:
        return []
    try:
        doc = json.loads(text[start:])
    except ValueError:
        return []
    names = []

    def walk(node):
        if isinstance(node, dict):
            if str(node.get("status", "")).lower() in ("fail", "failed"):
                names.append(str(node.get("name") or node.get("id") or "?"))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(doc)
    return names


_LANE_VERBS = (("approve", None), ("stop_to_review", None), ("accept", None))


def flow_card_lifecycle(ctx):
    """capture → triage → approve → dispatch（stub claude）→ review → done / reject /
    archive / trash / restore：每一步经 HTTP 落账，再由一趟 `act.actd --once` 推进闸门，
    最后从 /api/board 复核车道。"""
    server, missing = _need_server(ctx)
    if missing:
        return missing
    steps = []
    cap = ctx.http.request("POST", server.base_url + "/api/actions",
                           body=json.dumps({"action": "capture",
                                            "text": "QA full-coverage lifecycle probe"}),
                           headers={"Content-Type": "application/json",
                                    "X-Zai-Token": server.token})
    if cap.status != 200:
        return Verdict(MISSING, "capture -> %s %s" % (cap.status, one_line(cap.text, 80)), "-")
    steps.append("capture 200")
    ids = board_ids(ctx.http, server)
    card = next((i for i in ids if i.startswith(("P-", "R-", "MS-"))), ids[0] if ids else None)
    if card is None:
        return Verdict(MISSING, "seeded board carries no card id", "; ".join(steps))
    for verb in ("approve", "comment", "stop_to_review", "accept", "archive", "unarchive",
                 "trash", "restore", "reject"):
        payload = {"action": verb, "id": card}
        if verb == "comment":
            payload["comment"] = "QA full-coverage probe"
        resp = ctx.http.request("POST", server.base_url + "/api/actions",
                                body=json.dumps(payload),
                                headers={"Content-Type": "application/json",
                                         "X-Zai-Token": server.token})
        if resp.status != 200:
            return Verdict(MISSING, "%s %s -> %s %s" % (verb, card, resp.status,
                                                        one_line(resp.text, 70)),
                           "; ".join(steps))
        steps.append("%s 200" % verb)
    inbox = glob.glob(os.path.join(server.home, "state", "inbox", "*.json"))
    if not inbox:
        inbox = glob.glob(os.path.join(server.home, "**", "inbox", "*.json"), recursive=True)
    if not inbox:
        return Verdict(MISSING, "no inbox action file landed under %s" % server.home,
                       "; ".join(steps))
    steps.append("%d inbox files" % len(inbox))
    moved = _actd_once(ctx, server, card)
    steps.append(moved.evidence)
    if moved.state != PRESENT:
        return Verdict(MISSING, moved.reason, one_line("; ".join(steps)))
    return Verdict(PRESENT, "-", one_line("; ".join(steps)))


def _actd_once(ctx, server, card):
    """一趟 actd --once（PATH 里只有 stub claude）→ 卡是否离开原车道。"""
    before = _lane_of(ctx.http, server, card)
    shim_dir, shim_log = write_shims(server.home)
    proc = ctx.shell.run([ctx.python, "-m", "act.actd", "--once"], cwd=REPO_ROOT,
                         env=shim_env(server.home, shim_dir, shim_log,
                                      {"AIASSISTANT_HOME": server.home,
                                       "PYTHONPATH": ctx.pythonpath}),
                         timeout=T_FLOW, log_name="flow_card_lifecycle_actd")
    after = _lane_of(ctx.http, server, card)
    if proc.rc != 0:
        return Verdict(MISSING, "act.actd --once rc=%s: %s" % (proc.rc, one_line(proc.out, 90)),
                       "actd rc=%s, lane %s→%s" % (proc.rc, before, after))
    if before == after:
        return Verdict(MISSING, "lane unchanged (%s) after actd --once — inbox not applied" % before,
                       "actd rc=0, lane %s" % before)
    return Verdict(PRESENT, "-", "actd rc=0, lane %s→%s" % (before, after))


def _lane_of(http, server, card):
    """/api/board 上某卡所在车道（找不到 = "absent"）。"""
    resp = http.request("GET", server.base_url + "/api/board")
    if resp.status != 200:
        return "board-%s" % resp.status
    try:
        doc = json.loads(resp.text)
    except ValueError:
        return "unparsable"
    for lane, payload in (doc.items() if isinstance(doc, dict) else []):
        if json.dumps(payload, ensure_ascii=False).find('"%s"' % card) >= 0:
            return str(lane)
    return "absent"


def flow_settings_roundtrip_all(ctx):
    """settings_catalog.SECTIONS 的**每一个** field 都 PUT 非默认 → GET → 复位。"""
    server, missing = _need_server(ctx)
    if missing:
        return missing
    try:
        sections = load_sections()
    except Exception as exc:
        return Verdict(MISSING, "settings catalog unavailable: %s" % one_line(exc), "-")
    total, bad = 0, []
    for section in sections:
        for field in section["fields"]:
            total += 1
            verdict = roundtrip_setting(ctx.http, server, section["id"], field["key"], field)
            if verdict.state != PRESENT:
                bad.append("%s.%s (%s)" % (section["id"], field["key"], verdict.reason))
    evidence = "%d/%d settings fields round-tripped" % (total - len(bad), total)
    if bad:
        return Verdict(MISSING, one_line("; ".join(bad[:3])), evidence)
    return Verdict(PRESENT, "-", evidence)


def flow_recaps(ctx):
    """会议 recap 的全套面：设置往返 / 生成 / 重生成 / 回退 / 归档标记 / 意图问答 / history 形状。"""
    server, missing = _need_server(ctx)
    if missing:
        return missing
    steps = []
    snap = ctx.http.request("GET", server.base_url + "/api/settings/recap")
    if snap.status != 200:
        return Verdict(MISSING, "GET /api/settings/recap -> %s" % snap.status, "-")
    steps.append("settings/recap 200")
    put = ctx.http.request("PUT", server.base_url + "/api/settings/recap",
                           body=json.dumps({"enabled": True}),
                           headers={"Content-Type": "application/json",
                                    "X-Zai-Token": server.token})
    steps.append("PUT settings/recap %s" % put.status)
    # §63 recap 键的字面形状（server/inbox_writer._RECAP_KEY_RE）：meeting:<ISO 日期>T<HHMM>-<slug>
    key = "meeting:2026-09-15T0930-qa-coverage-probe"
    actions = [
        ("generate", {"action": "recap_generate", "meeting_key": key}),
        ("regenerate", {"action": "recap_generate", "meeting_key": key, "note": "again"}),
        ("shape", {"action": "recap_generate", "meeting_key": key, "shape": "sections"}),
        ("intent-qa", {"action": "recap_generate", "meeting_key": key,
                       "answers": ["split1=drop"]}),
        ("revert", {"action": "recap_revert", "meeting_key": key, "version": 1}),
        ("archive", {"action": "recap_slack_draft", "meeting_key": key, "channel_id": "D0QAPROBE"}),
    ]
    for label, payload in actions:
        resp = ctx.http.request("POST", server.base_url + "/api/actions",
                                body=json.dumps(payload),
                                headers={"Content-Type": "application/json",
                                         "X-Zai-Token": server.token})
        if resp.status != 200:
            return Verdict(MISSING, "recap %s -> %s %s" % (label, resp.status,
                                                           one_line(resp.text, 70)),
                           one_line("; ".join(steps)))
        steps.append("%s 200" % label)
    mark = ctx.http.request("POST", server.base_url + "/api/recaps/mark",
                            body=json.dumps({"key": key, "mark": "dismissed"}),
                            headers={"Content-Type": "application/json",
                                     "X-Zai-Token": server.token})
    if mark.status != 200:
        return Verdict(MISSING, "POST /api/recaps/mark -> %s %s" % (mark.status,
                                                                    one_line(mark.text, 70)),
                       one_line("; ".join(steps)))
    steps.append("mark 200")
    # §63.9 history 的 wire 形状：{key, current, entries[], history_cap, truncated}
    hist = ctx.http.request("GET", server.base_url + "/api/recaps/history?key=" + key)
    if hist.status != 200 or '"entries"' not in hist.text or '"history_cap"' not in hist.text:
        return Verdict(MISSING, "GET /api/recaps/history -> %s (sections shape missing)" % hist.status,
                       one_line("; ".join(steps)))
    steps.append("history 200 entries[]")
    if put.status != 200:
        return Verdict(MISSING, "PUT /api/settings/recap -> %s" % put.status,
                       one_line("; ".join(steps)))
    return Verdict(PRESENT, "-", one_line("; ".join(steps)))


_PWA_ASSETS = (("/manifest.webmanifest", "manifest"), ("/icon-192.png", "png"),
               ("/icon-512.png", "png"))


def flow_pwa(ctx):
    """PWA 的三件套（manifest + 两个 icon）都 200，且 content-type 对。"""
    server, missing = _need_server(ctx)
    if missing:
        return missing
    if not os.path.exists(os.path.join(REPO_ROOT, "web", "dist", "index.html")):
        return Verdict(MISSING, "web/dist absent — run `npm run build` in web/ first", "-")
    steps = []
    for path, want in _PWA_ASSETS:
        resp = ctx.http.request("GET", server.base_url + path)
        if resp.status != 200:
            return Verdict(MISSING, "GET %s -> %s" % (path, resp.status), one_line("; ".join(steps)))
        if want == "manifest" and '"icons"' not in resp.text:
            return Verdict(MISSING, "manifest carries no icons[]", one_line("; ".join(steps)))
        if want == "png" and not resp.text.startswith("\ufffdPNG") and "PNG" not in resp.text[:16]:
            return Verdict(MISSING, "%s is not PNG payload" % path, one_line("; ".join(steps)))
        steps.append("%s 200" % path)
    return Verdict(PRESENT, "-", one_line("; ".join(steps)))


def flow_uninstall_reinstall(ctx):
    """uninstall.sh --yes（临时 HOME + 假货）→ 再 install.sh 一遍：两趟都 exit 0。"""
    home = ctx.homes.make("uninst")
    shim_dir, shim_log = write_shims(home)
    env = shim_env(home, shim_dir, shim_log)
    born = [p for p in (os.path.join(REPO_ROOT, "config.yaml"), os.path.join(REPO_ROOT, "state"))
            if not os.path.exists(p)]
    first = ctx.shell.run(["bash", os.path.join(REPO_ROOT, "install.sh"), "--non-interactive"],
                          cwd=REPO_ROOT, env=env, timeout=T_FLOW,
                          log_name="flow_uninstall_reinstall")
    rm = ctx.shell.run(["bash", os.path.join(REPO_ROOT, "uninstall.sh"), "--yes"],
                       cwd=REPO_ROOT, env=env, timeout=T_FLOW,
                       log_name="flow_uninstall_reinstall")
    left = sorted(glob.glob(os.path.join(home, "Library", "LaunchAgents", "*.plist")))
    again = ctx.shell.run(["bash", os.path.join(REPO_ROOT, "install.sh"), "--non-interactive"],
                          cwd=REPO_ROOT, env=env, timeout=T_FLOW,
                          log_name="flow_uninstall_reinstall")
    for path in born:
        shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else _unlink(path)
    plists = sorted(glob.glob(os.path.join(home, "Library", "LaunchAgents", "*.plist")))
    evidence = "install rc=%s, uninstall rc=%s (left %d plist), reinstall rc=%s (%d plist)" % (
        first.rc, rm.rc, len(left), again.rc, len(plists))
    if first.rc != 0:
        return Verdict(MISSING, "first install rc=%s: %s" % (first.rc, one_line(first.out, 90)),
                       evidence)
    if rm.rc != 0:
        return Verdict(MISSING, "uninstall.sh --yes rc=%s: %s" % (rm.rc, one_line(rm.out, 90)),
                       evidence)
    if left:
        return Verdict(MISSING, "uninstall left %d plist behind" % len(left), evidence)
    if again.rc != 0 or not plists:
        return Verdict(MISSING, "reinstall rc=%s, %d plist" % (again.rc, len(plists)), evidence)
    return Verdict(PRESENT, "-", one_line(evidence))


def flow_pages_controls(ctx):
    """Playwright 走一遍左栏每个页面、点安全控件、断言零 console error（web/e2e/coverage.spec.ts）。"""
    mapping, error = ctx.tools.playwright_map(ctx.pw_specs)
    if error and not mapping:
        return Verdict(MISSING, error, "-")
    hits = [(key, hit) for key, hit in mapping.items() if key[0] == PAGES_SPEC]
    if not hits:
        return Verdict(MISSING, "playwright report has no %s tests" % PAGES_SPEC, "-")
    bad = [key[1] for key, hit in hits if not hit.ok]
    evidence = PAGES_SPEC + ": %d/%d tests passed" % (len(hits) - len(bad), len(hits))
    if bad:
        return Verdict(MISSING, one_line("failed: " + ", ".join(bad[:3])), evidence)
    return Verdict(PRESENT, "-", evidence)


FLOWS = {
    "install_fresh": flow_install_fresh,
    "doctor_clean": flow_doctor_clean,
    "card_lifecycle": flow_card_lifecycle,
    "settings_roundtrip_all": flow_settings_roundtrip_all,
    "recaps": flow_recaps,
    "pwa": flow_pwa,
    "uninstall_reinstall": flow_uninstall_reinstall,
    "pages_controls": flow_pages_controls,
}


# --------------------------------------------------------------------------- #
# 判决：一行清单 → Verdict
# --------------------------------------------------------------------------- #

def _hit_verdict(mapping, error, key, kind, absent_reason):
    if error and not mapping:
        return Verdict(MISSING, error, "-")
    hit = mapping.get(key)
    if hit is None:
        return Verdict(MISSING, absent_reason, "-")
    if not hit.ok:
        return Verdict(MISSING, one_line(hit.evidence), one_line(hit.evidence))
    return Verdict(PRESENT, "-", one_line(hit.evidence))


class Judge:
    """proof 分句 → Verdict（每个 kind 一个执行器；重工具从 Tools 拿 memoized map）。"""

    def __init__(self, tools, shell, http, homes, opts):
        self.python = tools.python
        self.pythonpath = tools.pythonpath
        self.tools = tools
        self.shell = shell
        self.http = http
        self.homes = homes
        self.opts = opts
        self._flow_cache = {}
        self._ids = None
        self._unittest_modules = set()
        self._pw_specs = set()
        self.flow_ctx = FlowCtx(tools, shell, http, homes, opts, self._pw_specs)

    # -- 预扫：unittest 的模块集合（一次跑全都要的模块）------------------- #
    def collect(self, rows):
        for row in rows:
            if str(row.get("status")) == "waived":
                continue
            for kind, arg in parse_proof(row.get("proof")):
                if kind == "unittest":
                    self._unittest_modules.update(m.strip() for m in arg.split(",") if m.strip())
                elif kind == "playwright":
                    spec = os.path.basename(arg.partition("::")[0].strip())
                    if spec:
                        self._pw_specs.add(spec)
                elif kind == "flow" and arg == "pages_controls":
                    self._pw_specs.add(PAGES_SPEC)

    def ids(self, server):
        if self._ids is None:
            self._ids = board_ids(self.http, server)
        return self._ids

    def clause(self, kind, arg):
        if kind == "unittest":
            mapping, error = self.tools.unittest_map(self._unittest_modules)
            parts = [m.strip() for m in arg.split(",") if m.strip()]
            if not parts:
                return Verdict(MISSING, "unittest proof lists no module", "-")
            verdicts = [_hit_verdict(mapping, error, mod, kind,
                                     "module %s did not run" % mod) for mod in parts]
            bad = [v for v in verdicts if v.state != PRESENT]
            if bad:
                return bad[0]
            return Verdict(PRESENT, "-", one_line("; ".join(v.evidence for v in verdicts)))
        if kind == "swift":
            mapping, error = self.tools.swift_map()
            return _hit_verdict(mapping, error, arg, kind, "no harness named %s" % arg)
        if kind == "parity":
            mapping, error = self.tools.parity_map()
            return _hit_verdict(mapping, error, arg, kind,
                                "vitest report has no it() for %s" % arg)
        if kind == "playwright":
            return self._playwright(arg)
        if kind == "http":
            server, missing = _need_server(self.flow_ctx)
            return missing or run_http_proof(arg, self.http, server, self.ids(server))
        if kind == "settings":
            server, missing = _need_server(self.flow_ctx)
            return missing or run_settings_proof(arg, self.http, server)
        if kind == "flow":
            return self.flow(arg)
        if kind == "axprobe":
            return self.axprobe(arg)
        if kind == "fixture":
            return self.fixture(arg)
        return Verdict(MISSING, "unknown proof kind %r" % kind, "-")

    def _playwright(self, arg):
        spec, _, title = arg.partition("::")
        mapping, error = self.tools.playwright_map(self._pw_specs)
        if error and not mapping:
            return Verdict(MISSING, error, "-")
        try:
            pattern = re.compile(title or ".")
        except re.error as exc:
            return Verdict(MISSING, "bad title regex %r: %s" % (title, exc), "-")
        want = os.path.basename(spec.strip())
        hits = [hit for (file_name, name), hit in mapping.items()
                if file_name == want and pattern.search(name)]
        if not hits:
            return Verdict(MISSING, "playwright report has no %s::%s" % (want, title), "-")
        bad = [h for h in hits if not h.ok]
        if bad:
            return Verdict(MISSING, one_line(bad[0].evidence), one_line(bad[0].evidence))
        return Verdict(PRESENT, "-", one_line(hits[0].evidence))

    def flow(self, name):
        if name in self._flow_cache:
            return self._flow_cache[name]
        func = FLOWS.get(name)
        if func is None:
            verdict = Verdict(MISSING, "unknown flow %r" % name, "-")
        else:
            log("flow:%s …" % name)
            try:
                verdict = func(self.flow_ctx)
            except Exception as exc:
                verdict = Verdict(MISSING, "flow raised %s: %s" % (
                    type(exc).__name__, one_line(exc, 120)), "-")
        self._flow_cache[name] = verdict
        return verdict

    def axprobe(self, probe):
        script = os.path.join(REPO_ROOT, "scripts", "qa", "shell_ui_probe.py")
        if self.opts.skip_ax:
            return Verdict(MISSING, "skipped", "--skip-ax")
        if not os.path.exists(script):
            return Verdict(MISSING, "probe script absent", "-")
        proc = self.shell.run([self.python, script, "--probe", probe, "--json"], cwd=REPO_ROOT,
                              timeout=T_SHORT, log_name="axprobe_" + re.sub(r"\W+", "_", probe))
        present, detail = _probe_json(proc.out)
        if proc.rc != 0 and not present:
            return Verdict(MISSING, "probe rc=%s: %s" % (proc.rc, one_line(proc.out, 90)),
                           tail_line(proc.out))
        if not present:
            return Verdict(MISSING, one_line(detail or "probe reports present=false"),
                           tail_line(proc.out))
        return Verdict(PRESENT, "-", one_line(detail or tail_line(proc.out)))

    def fixture(self, slug):
        script = os.path.join(REPO_ROOT, "scripts", "qa", "fixtures_b", "%s.py" % slug)
        if not os.path.exists(script):
            return Verdict(MISSING, "fixture script absent: scripts/qa/fixtures_b/%s.py" % slug, "-")
        proc = self.shell.run([self.python, script], cwd=REPO_ROOT,
                              env={"PYTHONPATH": self.pythonpath},
                              timeout=T_FLOW, log_name="fixture_" + re.sub(r"\W+", "_", slug))
        if proc.rc != 0:
            return Verdict(MISSING, "fixture rc=%s" % proc.rc, tail_line(proc.out))
        return Verdict(PRESENT, "-", tail_line(proc.out))

    def row(self, row):
        if str(row.get("status")) == "waived":
            return Verdict(WAIVED, one_line(row.get("waive_reason") or "waived"), "-")
        clauses = parse_proof(row.get("proof"))
        if not clauses:
            return Verdict(MISSING, "no proof declared in inventory", "-")
        evidences, first_bad = [], None
        for kind, arg in clauses:
            verdict = self.clause(kind, arg)
            evidences.append(verdict.evidence)
            if verdict.state != PRESENT and first_bad is None:
                first_bad = verdict
        if first_bad is not None:
            return Verdict(MISSING, first_bad.reason, one_line(" | ".join(evidences)))
        return Verdict(PRESENT, "-", one_line(" | ".join(evidences)))


def _probe_json(text):
    """shell_ui_probe.py 的 JSON → (present, detail)。"""
    start = text.find("{")
    if start < 0:
        return False, ""
    try:
        doc = json.loads(text[start:])
    except ValueError:
        return False, ""
    detail = doc.get("evidence") or doc.get("detail") or doc.get("reason") or ""
    return bool(doc.get("present")), str(detail)


# --------------------------------------------------------------------------- #
# 报告 R
# --------------------------------------------------------------------------- #

def render_report(pairs):
    """[(row, Verdict)] → R 的全文（末三行 PRESENT=/MISSING=/WAIVED=）。"""
    lines, counts = [], {PRESENT: 0, MISSING: 0, WAIVED: 0}
    for row, verdict in pairs:
        ident = str(row.get("id") or "?")
        counts[verdict.state] = counts.get(verdict.state, 0) + 1
        if verdict.state == PRESENT:
            lines.append("%s PRESENT evidence=%s" % (ident, verdict.evidence or "-"))
        elif verdict.state == WAIVED:
            lines.append("%s WAIVED reason=%s evidence=-" % (ident, verdict.reason or "waived"))
        else:
            lines.append("%s MISSING reason=%s evidence=%s" % (
                ident, verdict.reason or "-", verdict.evidence or "-"))
    for state in (PRESENT, MISSING, WAIVED):
        lines.append("%s=%d" % (state, counts.get(state, 0)))
    return "\n".join(lines) + "\n", counts


def write_report(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--inventory", default=DEFAULT_INVENTORY,
                        help="清单路径（默认 %s）" % DEFAULT_INVENTORY)
    parser.add_argument("--report", default=DEFAULT_REPORT, help="R 的落点")
    parser.add_argument("--logdir", default=DEFAULT_LOGDIR, help="每个场景的日志目录")
    parser.add_argument("--only", default=None, help="只跑 id 以此前缀开头的行")
    parser.add_argument("--skip-ax", action="store_true",
                        help="不跑 axprobe:（记 MISSING reason=skipped）")
    parser.add_argument("--unittest-scope", choices=("auto", "listed", "discover"), default="auto",
                        help="auto = 引用模块 ≤ --unittest-listed-max 时只跑那些模块，否则 full discover")
    parser.add_argument("--unittest-listed-max", type=int, default=40)
    return parser


def resolve_path(path):
    return path if os.path.isabs(path) else os.path.join(REPO_ROOT, path)


def main(argv=None, shell=None, http=None, homes=None, server_factory=None, python_bin=None):
    opts = build_parser().parse_args(argv)
    opts.logdir = resolve_path(opts.logdir) if opts.logdir else None
    inventory = resolve_path(opts.inventory)
    if not os.path.exists(inventory):
        sys.stderr.write(
            "coverage_run: inventory %s not found — run "
            "`python3 scripts/qa/coverage_inventory.py --write` first (or pass --inventory PATH)\n"
            % inventory)
        return 2
    try:
        rows = load_inventory(inventory)
    except (OSError, ValueError) as exc:
        sys.stderr.write("coverage_run: unreadable inventory %s: %s\n" % (inventory, exc))
        return 2
    if opts.only:
        rows = [r for r in rows if str(r.get("id") or "").startswith(opts.only)]
    if opts.logdir:
        os.makedirs(opts.logdir, exist_ok=True)

    homes = homes or TempHomes()
    shell = shell or Shell(opts.logdir)
    http = http or Http()
    tools = Tools(shell, http, opts, homes, server_factory=server_factory, python_bin=python_bin)
    judge = Judge(tools, shell, http, homes, opts)
    judge.collect(rows)

    def cleanup():
        tools.stop()
        homes.cleanup()

    atexit.register(cleanup)
    _install_signal_traps(cleanup)
    started = time.time()
    try:
        pairs = [(row, judge.row(row)) for row in rows]
    finally:
        cleanup()
        atexit.unregister(cleanup)
    text, counts = render_report(pairs)
    write_report(resolve_path(opts.report), text)
    sys.stdout.write(text)
    log("%d rows in %.0f s → %s" % (len(rows), time.time() - started, resolve_path(opts.report)))
    return 0 if counts.get(MISSING, 0) == 0 else 1


def _install_signal_traps(cleanup):
    def handler(signum, _frame):
        cleanup()
        sys.exit(128 + signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):       # 非主线程 / 平台不支持
            pass


if __name__ == "__main__":
    sys.exit(main())
