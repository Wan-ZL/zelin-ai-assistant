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

`http:` 的 proof token：`expect=` / `contains=` / `noauth` / `body=` / `body=@file` /
`ctype=<mime>`（显式 Content-Type；写动词没带 body 时发空 bytes）。路径里的占位符
`{id}`（board 第一张卡）、`{section}` `{log}` `{job}` `{recap}`（各自列表端点的第一项，
truth = PLACEHOLDER_SOURCES）由跑者现场解析；列表是空的就换 `__absent__`，并在证据行
里明说——绝不假装解析到了。

纪律：
- **绝不碰 live 数据**：demo server / install.sh / uninstall.sh / doctor 全部跑在
  `/tmp` 的临时 HOME 里，`launchctl` `open` `osascript` `crontab` 一律 PATH 前缀
  假货（`crontab` / `launchctl` 两只**有状态**，台账在沙箱 HOME 内 —— 见
  `scripts/qa/coverage_sandbox.py`），收尾 trap 删干净（§58 的门只读、不改仓）。
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


def _can_import_yaml(path):
    try:
        return subprocess.run([path, "-c", "import yaml"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _python_order(candidates):
    return [path for path in (candidates or ((sys.executable,) + PYTHON_CANDIDATES)) if path]


def resolve_python(candidates=None, probe=None):
    """第一个能 import yaml 的解释器（探不到 → sys.executable，诚实往下跑、报真错）。"""
    check = probe or _can_import_yaml
    for path in _python_order(candidates):
        if check(path):
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


def json_tail(text):
    """输出里第一个 `{` 起的 JSON（doctor / probe 的横幅在前面）；不是 JSON → None。"""
    body = str(text or "")
    start = body.find("{")
    if start < 0:
        return None
    try:
        return json.loads(body[start:])
    except ValueError:
        return None


def walk_json(node, visit):
    """递归遍历 JSON 树，每个 dict 交给 visit（board / doctor 两处共用）。"""
    if isinstance(node, dict):
        visit(node)
        for value in node.values():
            walk_json(value, visit)
    elif isinstance(node, list):
        for item in node:
            walk_json(item, visit)


def listed(node, key):
    """`node[key]` 当列表读（缺席 / null → 空列表）。"""
    return (node or {}).get(key) or []


def csv_parts(arg):
    """`a, b ,c` → ["a", "b", "c"]。"""
    return [part.strip() for part in str(arg or "").split(",") if part.strip()]


def combine(verdicts):
    """多分句 → 一个 Verdict（全中才 PRESENT，证据合并成一行）。"""
    evidence = one_line(" | ".join(v.evidence for v in verdicts))
    bad = [v for v in verdicts if v.state != PRESENT]
    if bad:
        return Verdict(MISSING, bad[0].reason, evidence)
    return Verdict(PRESENT, "-", evidence)


def log(msg):
    sys.stderr.write("[coverage_run %s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stderr.flush()


# --------------------------------------------------------------------------- #
# 注入缝 1/3：子进程
# --------------------------------------------------------------------------- #

def _merged_env(env):
    """os.environ + 调用方补充（值一律 str）。"""
    full = dict(os.environ)
    full.update({k: str(v) for k, v in (env or {}).items()})
    return full


class Shell:
    """真跑子进程（stdout+stderr 合并）。单元测试注入假货，绝不走这里。"""

    def __init__(self, logdir=None):
        self.logdir = logdir

    def run(self, cmd, cwd=None, env=None, timeout=T_SHORT, log_name=None):
        proc = self._exec(cmd, cwd, _merged_env(env), timeout)
        self._write_log(log_name, cmd, proc)
        return proc

    @staticmethod
    def _exec(cmd, cwd, env, timeout):
        try:
            done = subprocess.run(cmd, cwd=cwd or REPO_ROOT, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, errors="replace", timeout=timeout)
            return Proc(done.returncode, done.stdout or "")
        except subprocess.TimeoutExpired as exc:
            return Proc(124, (exc.output or "") + "\nTIMEOUT after %ss" % timeout)
        except OSError as exc:
            return Proc(127, "cannot exec %s: %s" % (cmd[0], exc))

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

    # 状态码在读 body 之前就已到手；body 只为 contains 判据服务。流式端点（SSE
    # `/api/events`）的 body 永不结束，`resp.read()` 会把整轮挂死（2026-09-16 事故）——
    # 所以 body 读取封顶且带硬墙钟：到点就放弃、返回已到手的部分，绝不阻塞。
    BODY_CAP = 1 << 20   # 1 MiB：本地大 JSON（/api/board 全景）绰绰有余
    READ_DEADLINE = 5.0  # s：整个 body 的墙钟；loopback 读不完即判流式，放弃余下 body

    @staticmethod
    def _socket_of(resp):
        """HTTPResponse / HTTPError → 底层 socket（沿 .fp 往下找 .raw._sock；拿不到回 None）。"""
        node = resp
        for _ in range(3):
            raw = getattr(getattr(node, "fp", None), "raw", None)
            sock = getattr(raw, "_sock", None)
            if sock is not None:
                return sock
            node = getattr(node, "fp", None)
            if node is None:
                break
        return None

    def _read_capped(self, resp):
        """封顶 + 硬墙钟的 body 读取，**单线程**。

        状态码在读 body 之前已到手，body 只为 contains 判据服务。流式端点（SSE
        `/api/events`）的 body 永不结束：`read()` 会把整轮挂死；上一版另起线程读、到点
        `resp.close()`，结果主线程死在 BufferedReader 的锁上（2026-09-16 卡了 12 h）。
        现在：底层 socket 设超时（防静默流）、`read1` 每次只做一次 recv、墙钟封顶
        （防 keepalive 刷屏），永不跨线程 close。"""
        self._arm_timeout(resp)
        deadline = time.monotonic() + self.READ_DEADLINE
        return self._drain(self._reader_of(resp), deadline).decode("utf-8", "replace")

    def _arm_timeout(self, resp):
        """底层 socket 设超时（防静默流把整轮挂死）；拿不到 socket 就算了。"""
        sock = self._socket_of(resp)
        if sock is None:
            return
        try:
            sock.settimeout(self.READ_DEADLINE)
        except OSError:
            pass

    @staticmethod
    def _reader_of(resp):
        """一次只做一次 recv 的读法（read1 优先；没有就退回 read）。"""
        return (getattr(resp, "read1", None)
                or getattr(getattr(resp, "fp", None), "read1", None) or resp.read)

    def _drain(self, reader, deadline):
        """封顶 + 墙钟的单线程读循环（到点就放弃余下 body，永不跨线程 close）。"""
        chunks, total = [], 0
        try:
            while total < self.BODY_CAP and time.monotonic() < deadline:
                chunk = reader(min(65536, self.BODY_CAP - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
        except (socket.timeout, TimeoutError, OSError, ValueError):
            pass   # 超时 / 断开：状态码已知，body 取已到手的部分
        return b"".join(chunks)

    def request(self, method, url, body=None, headers=None, timeout=30):
        data = body.encode("utf-8") if isinstance(body, str) else body
        req = urllib.request.Request(url, data=data, method=method)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return Resp(resp.status, self._read_capped(resp))
        except urllib.error.HTTPError as exc:
            return Resp(exc.code, self._read_capped(exc))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return Resp(0, "request failed: %s" % exc)


# --------------------------------------------------------------------------- #
# 临时 HOME / PATH 前缀假货 —— 同层 coverage_sandbox.py（本文件 ≤2000 行的拆分线）
# --------------------------------------------------------------------------- #

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coverage_sandbox import TempHomes, shim_env, write_shims  # noqa: E402



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


def _seed_home(homes, shell, python_bin, scene):
    """demo 数据 + 向导完成标记 + config.yaml（种不出来 = 抛，不带半个 home 往下跑）。"""
    home = homes.make("srv")
    seed = shell.run([python_bin, os.path.join(REPO_ROOT, "scripts", "demo_seed.py"), home,
                      "--scene", scene], timeout=T_SHORT, log_name="demo_seed")
    if seed.rc != 0:
        raise RuntimeError("demo_seed.py failed: %s" % one_line(seed.out))
    os.makedirs(os.path.join(home, "state"), exist_ok=True)
    # §68.5 首次运行判定：临时 home 没有 config.yaml → 整页换向导；写「向导已完成」标记
    with open(os.path.join(home, "state", "setup_done.json"), "w", encoding="utf-8") as handle:
        json.dump({"completed_at": "2026-09-02T12:00:00Z"}, handle)
    _copy_example_config(home)
    _link_skills(home)
    return home


def _link_skills(home):
    """demo home 里软链 checkout 的 `skills/`（§67 的 manifest 真源 = AIASSISTANT_HOME/skills）。

    临时 home 里没有它，`GET /api/skills` 恒 409「index.yaml is unusable」——技能页
    上就是一条假报错。写面（enable/disable）只写 `$HOME/.claude/skills` 与
    `state/skills.json`，都在沙箱里，所以这条链只被读（scripts/media/record.mjs 同一手）。"""
    src = os.path.join(REPO_ROOT, "skills")
    dst = os.path.join(home, "skills")
    if not os.path.isdir(src) or os.path.exists(dst):
        return
    try:
        os.symlink(src, dst)
    except OSError as exc:      # 软链建不起来不许毁掉整跑（技能页的 proof 会诚实记 MISSING）
        log("skills symlink failed: %s" % exc)


def _copy_example_config(home):
    example = os.path.join(REPO_ROOT, "config.example.yaml")
    target = os.path.join(home, "config.yaml")
    if os.path.exists(example) and not os.path.exists(target):
        shutil.copyfile(example, target)


def _server_token(home):
    path = os.path.join(home, "state", "server.token")
    if not os.path.exists(path):
        return ""
    return _read(path).strip()


def _wait_for_board(http, base_url, proc, out_path):
    """等 /api/board 通（≤90 s；server 中途退出 = 抛，带上日志路径）。"""
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("server exited early rc=%s (see %s)" % (proc.returncode, out_path))
        if http.request("GET", base_url + "/api/board", timeout=5).status == 200:
            return
        time.sleep(0.2)
    proc.terminate()
    raise RuntimeError("server did not answer /api/board in 90 s (see %s)" % out_path)


def start_demo_server(homes, http, shell, scene="initial", logdir=None, python_bin=None,
                      pythonpath=None):
    """种 demo 数据 → 起 `python3 -m server` → 等 /api/board（web/e2e/demoServer.ts 同一条链）。"""
    python_bin = python_bin or resolve_python()
    pythonpath = pythonpath or python_path(yaml_site(python_bin))
    home = _seed_home(homes, shell, python_bin, scene)
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
    _wait_for_board(http, base_url, proc, out_path)
    log("demo server up on %s (home=%s)" % (base_url, home))
    return DemoServer(base_url, home, _server_token(home), proc)


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


def _tally_case(stats, line):
    """一行 `-v` 输出 → 记一笔（不是用例行则什么都不记）。"""
    match = _VERBOSE_CASE.search(line.strip())
    if not match:
        return
    module = module_of(match.group(1))
    if not module:
        return
    ran, bad = stats.get(module, (0, 0))
    stats[module] = (ran + 1, bad + (1 if _is_failure(match.group(2).strip()) else 0))


def _is_failure(verdict):
    return verdict.startswith("FAIL") or verdict.startswith("ERROR")


def parse_unittest_verbose(text):
    """`python3 -m unittest -v` 输出 → {module: Hit}（一个 FAIL/ERROR 就判整模块不在）。"""
    stats = {}
    for line in str(text or "").splitlines():
        _tally_case(stats, line)
    return {mod: Hit(bad == 0, "%s: %d tests, %d failed" % (mod, ran, bad))
            for mod, (ran, bad) in stats.items()}


def _spec_name(primary, fallback):
    return os.path.basename(str(primary or fallback or ""))


def _pw_status(test):
    """一条 test 的最后一次结果状态（没有结果 = 空串）。"""
    results = listed(test, "results")
    if not results:
        return ""
    return str(results[-1].get("status") or "")


def _pw_spec(spec_file, spec, out):
    title = spec.get("title", "")
    for test in listed(spec, "tests"):
        status = _pw_status(test)
        out[(spec_file, title)] = Hit(status == "passed", "playwright %s::%s %s" % (
            spec_file, title, status or "no result"))


def _pw_suite(suite, file_hint, out):
    spec_file = _spec_name(suite.get("file"), file_hint)
    for spec in listed(suite, "specs"):
        _pw_spec(spec_file, spec, out)
    for child in listed(suite, "suites"):
        _pw_suite(child, spec_file, out)


def parse_playwright_json(text):
    """playwright `--reporter=json` → {(spec file, title): Hit}。"""
    doc = json_tail(text) or {}
    out = {}
    for suite in listed(doc, "suites"):
        _pw_suite(suite, suite.get("file"), out)
    return out


def _vitest_local_map(text):
    """parity_check.py 用不了时的兜底解析（title → passed）。"""
    doc = json_tail(text) or {}
    mapping = {}
    for suite in listed(doc, "testResults"):
        for case in listed(suite, "assertionResults"):
            mapping[case.get("title", "")] = case.get("status", "") == "passed"
    return mapping


def parse_vitest_parity(text):
    """vitest parity 报告 → {id: Hit}；优先复用 scripts/ui/parity_check.py 的映射。"""
    mapping = _parity_check_mapping(text)
    if mapping is None:
        mapping = _vitest_local_map(text)
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


def _swift_hit(name, steps, last, rc):
    """一个 harness 的判决：rc=0 且有它的步骤 = 在；rc≠0 按最后一个步骤定罪。"""
    slug = name.replace("Harness", "").lower()
    if rc != 0:
        blamed = slug in last.lower()
        return Hit(False, "shell/tests/run.sh rc=%s%s" % (
            rc, " at %s" % one_line(last, 80) if blamed else ", %s not reached" % name))
    seen = any(slug in step.lower() for step in steps)
    if seen:
        return Hit(True, "shell/tests/run.sh rc=0, %s exercised" % name)
    return Hit(False, "run.sh rc=0 but no step mentions %s" % name)


def parse_swift_run(text, rc, harnesses):
    """`shell/tests/run.sh` 输出 → {harness: Hit}。"""
    steps = [ln for ln in str(text or "").splitlines() if ln.startswith("==> [")]
    last = steps[-1] if steps else ""
    return {name: _swift_hit(name, steps, last, rc) for name in harnesses}


FIXTURES_DIR = os.path.join("scripts", "qa", "fixtures_b")
# 清单 id 形如 `B-03-slack-poison-reject`，脚本却叫 `slack_poison_reject.py`——
# 编号前缀由清单负责、文件名由 fixtures builder 负责，两边不必逐字相等。
_B_PREFIX_RE = re.compile(r"^B-\d+-")


def fixture_candidates(slug, note=None):
    """一条 `fixture:<slug>` 的脚本候选（依次：清单行的 note、<slug>.py、去编号前缀 + '-'→'_'）。"""
    candidates = [note] if note else []
    candidates.append(os.path.join(FIXTURES_DIR, "%s.py" % slug))
    bare = _B_PREFIX_RE.sub("", slug).replace("-", "_")
    candidates.append(os.path.join(FIXTURES_DIR, "%s.py" % bare))
    return [c for c in candidates if c]


def resolve_fixture_script(slug, note=None, exists=None):
    """候选里第一个真存在的脚本绝对路径；一个都不在 → None（调用方记 MISSING）。"""
    check = exists or os.path.exists
    for candidate in fixture_candidates(slug, note):
        path = candidate if os.path.isabs(candidate) else os.path.join(REPO_ROOT, candidate)
        if check(path):
            return path
    return None


def _playwright_argv(specs):
    """只跑清单引用到的 spec（空 = 全量）；仍是一次 `npx playwright test`。"""
    return ["npx", "playwright", "test"] + ["e2e/" + spec for spec in sorted(specs)] + \
           ["--reporter=json"]


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
        scope = self._unittest_scope(wanted)
        log("unittest (%s, %d modules requested) …" % (scope, len(wanted)))
        proc = self.shell.run(self._unittest_cmd(scope, wanted), cwd=REPO_ROOT,
                              env={"PYTHONPATH": self.pythonpath},
                              timeout=T_UNITTEST, log_name="unittest")
        error = None if proc.out.strip() else "unittest produced no output (rc=%s)" % proc.rc
        self._cache["unittest"] = (parse_unittest_verbose(proc.out), error)
        return self._cache["unittest"]

    def _unittest_scope(self, wanted):
        """auto = 清单点名的模块 ≤ --unittest-listed-max 时只跑那些，否则 full discover。"""
        scope = self.opts.unittest_scope
        if scope != "auto":
            return scope
        if 0 < len(wanted) <= self.opts.unittest_listed_max:
            return "listed"
        return "discover"

    def _unittest_cmd(self, scope, wanted):
        if scope == "listed" and wanted:
            return [self.python, "-m", "unittest", "-v"] + wanted
        return [self.python, "-m", "unittest", "discover", "-v", "-s", "tests", "-t", ".",
                "-p", "test_*.py"]

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
        out_path = os.path.join(self._logdir(), "parity-vitest.json")
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
        out_path = os.path.join(self._logdir(), "playwright.json")
        log("playwright (%s) …" % (", ".join(sorted(specs)) or "all specs"))
        proc = self.shell.run(_playwright_argv(specs), cwd=web,
                              env={"PLAYWRIGHT_JSON_OUTPUT_NAME": out_path},
                              timeout=T_PLAYWRIGHT, log_name="playwright")
        mapping = parse_playwright_json(_read(out_path) or proc.out)
        error = None if mapping else "playwright produced no parsable report (rc=%s)" % proc.rc
        self._cache["playwright"] = (mapping, error)
        return self._cache["playwright"]

    def _logdir(self):
        return self.opts.logdir or tempfile.gettempdir()

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
# 需要 JSON 体 + token 的动词（server 的写闸：Origin → Content-Type → token，§49）
_WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


_HTTP_TOKENS = {
    "expect=": lambda spec, value: spec.__setitem__("expect", int(value)),
    "contains=": lambda spec, value: spec.__setitem__("contains", value),
    "body=@": lambda spec, value: spec.__setitem__("body", {"file": value}),
    "body=": lambda spec, value: spec.__setitem__("body", {"inline": value}),
    # `ctype=<mime>`：显式 Content-Type（§49 写闸的第二道；`POST /api/attachments`
    # 要 image/*，没有它 server 在查 token 之前就 415，401 分支永远测不到）
    "ctype=": lambda spec, value: spec.__setitem__("ctype", value),
}


def _apply_http_token(spec, token):
    """一个 proof token（`expect=200` / `noauth` / `contains=…` / `body=@f`）→ 写进 spec。"""
    if token == "noauth":
        spec["noauth"] = True
        return
    for prefix, setter in _HTTP_TOKENS.items():
        if token.startswith(prefix):
            setter(spec, token[len(prefix):])
            return


def parse_http_proof(arg):
    """`GET /api/board expect=200 noauth contains=lanes body=@f.json ctype=image/png` → dict。

    未出现的 token 一律留默认值（`ctype=None` / `body=None`）——旧清单里的 proof
    逐字不变地照旧跑（字段 add-only）。"""
    match = _HTTP_RE.match(arg.strip())
    if not match:
        raise ValueError("cannot parse http proof %r" % arg)
    spec = {"method": match.group("method"), "path": match.group("path"),
            "expect": 200, "noauth": False, "contains": None, "body": None, "ctype": None}
    for token in match.group("rest").split():
        _apply_http_token(spec, token)
    return spec


def board_ids(http, server):
    """seeded board 上的卡片 id（{id} 占位符的解析源；按出现顺序去重）。"""
    resp = http.request("GET", server.base_url + "/api/board")
    doc = json_tail(resp.text) if resp.status == 200 else None
    if doc is None:
        return []
    found, seen = [], set()

    def visit(node):
        ident = node.get("id")
        if isinstance(ident, str) and ident and ident not in seen:
            seen.add(ident)
            found.append(ident)

    walk_json(doc, visit)
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


def _resolve_id(path, ids):
    """`{id}` → seeded board 上的第一张卡；解析不了 → (None, 原因)。"""
    if "{id}" not in path:
        return path, None
    if not ids:
        return None, "no card id on the seeded board for {id}"
    return path.replace("{id}", ids[0]), None


ABSENT_VALUE = "__absent__"

# 占位符 → (列表端点候选, 列表所在的键候选, 取名字的字段)。第一个给出非空列表的
# 端点胜；一个都给不出 → ABSENT_VALUE（证据行里明说是替代值，绝不假装解析到了）。
PLACEHOLDER_SOURCES = {
    "{section}": (("/api/settings",), ("sections",), "id"),
    # /api/logs 没有列表路由（server/app.py 只有前缀表 /api/logs/<name>），
    # 白名单清单在 /api/diagnostics 的 logs[]（§68.4 诊断页同一份）
    "{log}": (("/api/logs", "/api/diagnostics"), ("logs",), "name"),
    "{job}": (("/api/ingest/jobs",), ("jobs", "entries"), "id"),
    "{recap}": (("/api/recaps",), ("recaps", "entries", "keys"), "key"),
}


def _first_str(items, field):
    """列表里第一个非空字符串（元素是 str 就取它，是 dict 就取 field）。"""
    for item in items:
        value = item if isinstance(item, str) else (item or {}).get(field)
        if isinstance(value, str) and value:
            return value
    return None


def _listing_first(doc, keys, field):
    """列表端点的 JSON → 第一项的名字（键候选按序试；形状不对 → None）。"""
    for key in keys:
        node = doc.get(key) if isinstance(doc, dict) else None
        value = _first_str(node, field) if isinstance(node, list) else None
        if value:
            return value
    return None


class Listings:
    """列表端点 → 第一项（`{section}` / `{log}` / `{job}` / `{recap}` 的解析源）。

    与 `{id}`（board 的第一张卡）同一口味：真问一次 server，取第一项，每个占位符
    只问一次并 memoize。空列表 = 解析不到，调用方换 ABSENT_VALUE 并在证据里说明。"""

    def __init__(self, http, server):
        self.http = http
        self.server = server
        self._cache = {}

    def first(self, name):
        if name not in self._cache:
            self._cache[name] = self._resolve(name)
        return self._cache[name]

    def _resolve(self, name):
        paths, keys, field = PLACEHOLDER_SOURCES[name]
        for path in paths:
            doc = self._get(path)
            value = _listing_first(doc, keys, field) if doc is not None else None
            if value:
                return value
        return None

    def _get(self, path):
        headers = {"X-Zai-Token": self.server.token} if self.server.token else {}
        resp = self.http.request("GET", self.server.base_url + path, headers=headers)
        return json_tail(resp.text) if resp.status == 200 else None


def resolve_placeholders(path, listings):
    """路径里的列表型占位符 → 真名字；解析不到就换 `__absent__`。

    返回 (path, notes)：notes 是给证据行用的说明（空列表 = 全都解析到了）。"""
    notes = []
    for name in PLACEHOLDER_SOURCES:
        if name not in path:
            continue
        value = listings.first(name) if listings is not None else None
        if not value:
            value = ABSENT_VALUE
            notes.append("%s unresolved (empty listing) -> %s" % (name, ABSENT_VALUE))
        path = path.replace(name, value)
    return path, notes


def _request_headers(spec, server, body):
    headers = {}
    if spec.get("ctype"):
        headers["Content-Type"] = spec["ctype"]
    elif body is not None:
        headers["Content-Type"] = "application/json"
    if not spec["noauth"] and server.token:
        headers["X-Zai-Token"] = server.token
    return headers


def _judge_response(spec, resp, evidence):
    if resp.status != spec["expect"]:
        return Verdict(MISSING, "expected %s, got %s: %s" % (
            spec["expect"], resp.status, one_line(resp.text, 90)), evidence)
    if spec["contains"] and spec["contains"] not in resp.text:
        return Verdict(MISSING, "response lacks %r" % spec["contains"], evidence)
    return Verdict(PRESENT, "-", one_line(evidence))


def _http_request_body(spec):
    """写动词没带 body 时的默认体：ctype= 显式声明了类型 → 空 bytes，否则 `{}`。"""
    if spec["method"] not in _WRITE_METHODS:
        return None
    return "" if spec.get("ctype") else "{}"


def run_http_proof(arg, http, server, ids, listings=None):
    """一条 http: proof → Verdict（占位符先解析，再发一次请求）。"""
    try:
        spec = parse_http_proof(arg)
        body = http_body(spec)
    except ValueError as exc:
        return Verdict(MISSING, one_line(exc), "-")
    path, error = _resolve_id(spec["path"], ids)
    if error:
        return Verdict(MISSING, error, "-")
    path, absent = resolve_placeholders(path, listings)
    if body is None:
        body = _http_request_body(spec)
    resp = http.request(spec["method"], server.base_url + path, body=body,
                        headers=_request_headers(spec, server, body))
    evidence = "%s %s -> %s%s" % (spec["method"], path, resp.status,
                                  (" [%s]" % "; ".join(absent)) if absent else "")
    return _judge_response(spec, resp, evidence)


# --- settings: ------------------------------------------------------------- #

_CHECK_SAMPLES = {"email": "qa.probe@example.com", "clock_time": "23:45",
                  "session_id": "qa-coverage-probe-session"}


def load_sections():
    """server.settings_catalog.SECTIONS（唯一真源；scripts/ui/parity_fixture.py 同款 import）。"""
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    from server import settings_catalog
    return settings_catalog.SECTIONS


def _nd_bool(field):
    return not bool(field.get("default"))


def _nd_enum(field):
    return next((c for c in (field.get("choices") or []) if c != field.get("default")), None)


def _bounds(field):
    low, high = field.get("bounds") or (0, None)
    return low or 0, high


def _num_default(field):
    got = field.get("default")
    return got if isinstance(got, (int, float)) else 0


def _as_kind(field, value):
    return int(value) if field.get("kind") == "int" else value


def _nd_number(field):
    """默认值 ±1（受 bounds 约束；上下都出界 → None）。"""
    low, high = _bounds(field)
    base = _num_default(field)
    if high is None or base + 1 <= high:
        return _as_kind(field, base + 1)
    if base - 1 >= low:
        return _as_kind(field, base - 1)
    return None


def _nd_string(field):
    """带 `check` 的字符串用样例值（校验过不了就不是往返，是 400）；否则默认值加后缀。"""
    check = field.get("check")
    if check:
        return _CHECK_SAMPLES.get(check)
    return str(field.get("default") or "") + "-qa-coverage-probe"


_NON_DEFAULT = {"bool": _nd_bool, "enum": _nd_enum, "int": _nd_number, "number": _nd_number,
                "list": lambda field: ["qa-coverage-probe"], "string": _nd_string}


def non_default_value(field):
    """一把旋钮的「非默认」探测值；None = 没有安全的非默认值（诚实记 MISSING）。"""
    maker = _NON_DEFAULT.get(field.get("kind"))
    return maker(field) if maker else None


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


def _lookup_field(section_id, key):
    """(field, 原因)：目录里找 `<section>.<key>`，找不到就说清楚哪一层没有。"""
    try:
        sections = {s["id"]: s for s in load_sections()}
    except Exception as exc:
        return None, "settings catalog unavailable: %s" % one_line(exc)
    section = sections.get(section_id)
    if section is None:
        return None, "unknown settings section %r" % section_id
    field = next((f for f in section["fields"] if f["key"] == key), None)
    if field is None:
        return None, "section %s has no key %r" % (section_id, key)
    return field, None


def run_settings_proof(arg, http, server):
    section_id, _, key = arg.partition(".")
    if not key:
        return Verdict(MISSING, "malformed settings proof %r" % arg, "-")
    field, error = _lookup_field(section_id, key)
    if error:
        return Verdict(MISSING, error, "-")
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


Install = namedtuple("Install", "home shim_dir shim_log proc plists born")


def run_install(ctx, slug, log_name):
    """install.sh 在一个新的沙箱 HOME 里从零装一遍；返回台账（born 由调用方 _drop_born）。

    install.sh 的 AIASSISTANT_HOME 恒等于它自己所在的 checkout（这里 = 本 worktree），
    它会在 checkout 里现建 config.yaml / state/（两者 .gitignore 在列）——跑前记下
    有无，调用方跑完把本次新建的删掉，工作树不留痕。"""
    home = ctx.homes.make(slug)
    shim_dir, shim_log = write_shims(home)
    born = _checkout_born()
    proc = ctx.shell.run(["bash", os.path.join(REPO_ROOT, "install.sh"), "--non-interactive"],
                         cwd=REPO_ROOT, env=shim_env(home, shim_dir, shim_log),
                         timeout=T_FLOW, log_name=log_name)
    return Install(home, shim_dir, shim_log, proc, _plists(home), born)


def flow_install_fresh(ctx):
    """install.sh 在临时 HOME 里从零装一遍：exit 0 + plist 落在临时 LaunchAgents。"""
    box = run_install(ctx, "install", "flow_install_fresh")
    _drop_born(box.born)
    calls = one_line(_read(box.shim_log), 80)
    if box.proc.rc != 0:
        return Verdict(MISSING, "install.sh --non-interactive rc=%s: %s" % (
            box.proc.rc, one_line(box.proc.out, 90)), "shims: %s" % calls)
    if not box.plists:
        return Verdict(MISSING, "no plist under %s/Library/LaunchAgents" % box.home,
                       "shims: %s" % calls)
    labels = ", ".join(os.path.basename(p) for p in box.plists)
    return Verdict(PRESENT, "-", "install.sh rc=0, %d plist(s): %s" % (len(box.plists), labels))


CHECKOUT_BORN_CANDIDATES = ("config.yaml", "state", os.path.join("state", "inbox"),
                            os.path.join("state", "logs"))


def _checkout_born():
    """install.sh / actd 开机会在 checkout 里现建 config.yaml 与 state/ 三件套（都在
    .gitignore 上）——跑前记下哪些还不存在，跑后只删本次新建的那几个：工作树不留痕。"""
    return [os.path.join(REPO_ROOT, rel) for rel in CHECKOUT_BORN_CANDIDATES
            if not os.path.exists(os.path.join(REPO_ROOT, rel))]


def _drop_born(born):
    for path in born:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            _unlink(path)


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def flow_doctor_clean(ctx):
    """`act.doctor --fast --json` 在 **install.sh 刚装好的** 环境里 fail=0（spec §2）。

    doctor 的三条检查问的就是 install.sh 的产物，所以必须问同一套环境，否则判的是
    「临时 home 不是 clone」这类与被测行为无关的红（run4 的三条 FAIL）：
      - `AIASSISTANT_HOME`：``config.HOME/install.sh`` 在不在 → HOME 指针必须指 checkout，
        install.sh 自己就把 AIASSISTANT_HOME 钉在它所在的 checkout 上；
      - `state dirs`：``<checkout>/state{,/inbox,/logs}`` 可写——install.sh 现建的那几个；
      - `cron ingest chain`：``crontab -l`` 里的 §18 行——沙箱里的有状态 crontab 假货
        存着 install.sh 刚写进去的内容（真 crontab 一个字节都不碰）。
    所以：先在自己的沙箱 HOME 里跑一遍 install.sh，再用 HOME=沙箱 +
    AIASSISTANT_HOME=REPO_ROOT 问 doctor，最后才删掉本次在 checkout 里新建的东西。"""
    box = run_install(ctx, "doctor", "flow_doctor_clean_install")
    try:
        if box.proc.rc != 0:
            return Verdict(MISSING, "install.sh (doctor sandbox) rc=%s: %s" % (
                box.proc.rc, one_line(box.proc.out, 90)), "-")
        return _doctor_verdict(ctx, box)
    finally:
        _drop_born(box.born)


def _boot_state_dirs(ctx, box):
    """launchd 的替身：install.sh 之后本该由 actd 开机跑的 `config.ensure_state_dirs()`。

    install.sh 第 3 步只建 `state/` + `state/inbox/`；`state/logs/` 的真源是
    `act/lib/config.ensure_state_dirs()`（STATE/INBOX/LOG 三件套），真机上
    `launchctl bootstrap` 之后几秒 actd 就把它建好了，沙箱里 launchctl 是假货、
    actd 永不开机，于是 doctor 的 `state dirs` 会为一件与被测行为无关的事红。
    这里只补这一个开机步骤——**不跑 actd 主循环**（registry 单写者，§44）。"""
    return ctx.shell.run(
        [ctx.python, "-c", "from act.lib import config; config.ensure_state_dirs()"],
        cwd=REPO_ROOT,
        env=shim_env(box.home, box.shim_dir, box.shim_log,
                     {"AIASSISTANT_HOME": REPO_ROOT, "PYTHONPATH": ctx.pythonpath}),
        timeout=T_SHORT, log_name="flow_doctor_clean_state_dirs")


def _doctor_verdict(ctx, box):
    """刚装好的沙箱里问一次 doctor（HOME=沙箱，AIASSISTANT_HOME=checkout）→ Verdict。"""
    _boot_state_dirs(ctx, box)
    proc = ctx.shell.run([ctx.python, "-m", "act.doctor", "--fast", "--json"], cwd=REPO_ROOT,
                         env=shim_env(box.home, box.shim_dir, box.shim_log,
                                      {"AIASSISTANT_HOME": REPO_ROOT,
                                       "PYTHONPATH": ctx.pythonpath}),
                         timeout=T_FLOW, log_name="flow_doctor_clean")
    fails = _doctor_fails(proc.out)
    evidence = "doctor --fast rc=%s%s" % (proc.rc, (", FAIL: " + ", ".join(fails[:4])) if fails else "")
    if proc.rc != 0 or fails:
        return Verdict(MISSING, "doctor reports %s FAIL (rc=%s)" % (len(fails) or proc.rc, proc.rc),
                       one_line(evidence))
    return Verdict(PRESENT, "-", one_line(evidence))


def _doctor_fails(text):
    """doctor --json 输出里 status=fail 的检查名（形状防御：任何带 status 的 dict）。"""
    doc = json_tail(text)
    if doc is None:
        return []
    names = []

    def visit(node):
        if str(node.get("status", "")).lower() in ("fail", "failed"):
            names.append(str(node.get("name") or node.get("id") or "?"))

    walk_json(doc, visit)
    return names


_LANE_VERBS = (("approve", None), ("stop_to_review", None), ("accept", None))


_LIFECYCLE_VERBS = ("approve", "comment", "stop_to_review", "accept", "archive", "unarchive",
                    "trash", "restore", "reject")


def _write_headers(server):
    """写请求的三闸头（§49：Content-Type + instance token；Origin 不发 = 不查）。"""
    return {"Content-Type": "application/json", "X-Zai-Token": server.token}


def post_action(http, server, payload):
    """POST /api/actions（inbox 的唯一写口，§44 单写者：server 只落文件，actd 才改卡）。"""
    return http.request("POST", server.base_url + "/api/actions", body=json.dumps(payload),
                        headers=_write_headers(server))


def _verb_payload(verb, card):
    payload = {"action": verb, "id": card}
    if verb == "comment":
        payload["comment"] = "QA full-coverage probe"
    return payload


def _walk_verbs(ctx, server, card, steps):
    """十个卡片动词逐个 POST；第一个非 200 → 原因串（None = 全过）。"""
    for verb in _LIFECYCLE_VERBS:
        resp = post_action(ctx.http, server, _verb_payload(verb, card))
        if resp.status != 200:
            return "%s %s -> %s %s" % (verb, card, resp.status, one_line(resp.text, 70))
        steps.append("%s 200" % verb)
    return None


def _probe_card(http, server):
    """走生命周期的那张卡（seeded board 上第一张 P-/R-/MS- 卡）。"""
    ids = board_ids(http, server)
    known = [ident for ident in ids if ident.startswith(("P-", "R-", "MS-"))]
    if known:
        return known[0]
    return ids[0] if ids else None


def _inbox_files(home):
    """server 落下的 inbox 动作文件（§44 的回执面）。"""
    direct = glob.glob(os.path.join(home, "state", "inbox", "*.json"))
    if direct:
        return direct
    return glob.glob(os.path.join(home, "**", "inbox", "*.json"), recursive=True)


def _capture_step(ctx, server, steps):
    cap = post_action(ctx.http, server, {"action": "capture",
                                         "text": "QA full-coverage lifecycle probe"})
    if cap.status != 200:
        return "capture -> %s %s" % (cap.status, one_line(cap.text, 80))
    steps.append("capture 200")
    return None


def _actd_step(ctx, server, card, steps):
    """一趟 actd --once 推闸门；车道没动 / actd 非 0 = 原因串。"""
    moved = _actd_once(ctx, server, card)
    steps.append(moved.evidence)
    if moved.state != PRESENT:
        return moved.reason
    return None


def _inbox_step(server, steps):
    inbox = _inbox_files(server.home)
    if not inbox:
        return "no inbox action file landed under %s" % server.home
    steps.append("%d inbox files" % len(inbox))
    return None


def _lifecycle_problem(ctx, server, steps):
    """生命周期的第一个毛病（None = 每一步都过）。"""
    problem = _capture_step(ctx, server, steps)
    if problem:
        return problem
    card = _probe_card(ctx.http, server)
    if card is None:
        return "seeded board carries no card id"
    return (_walk_verbs(ctx, server, card, steps) or _inbox_step(server, steps)
            or _actd_step(ctx, server, card, steps))


def flow_card_lifecycle(ctx):
    """capture → triage → approve → dispatch（stub claude）→ review → done / reject /
    archive / trash / restore：每一步经 HTTP 落账，再由一趟 `act.actd --once` 推进闸门，
    最后从 /api/board 复核车道。"""
    server, missing = _need_server(ctx)
    if missing:
        return missing
    steps = []
    problem = _lifecycle_problem(ctx, server, steps)
    if problem:
        return Verdict(MISSING, problem, one_line("; ".join(steps)))
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


def _all_fields(sections):
    """目录的每一格 → (section id, field)。"""
    return [(section["id"], field) for section in sections for field in section["fields"]]


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
    for section_id, field in _all_fields(sections):
        total += 1
        verdict = roundtrip_setting(ctx.http, server, section_id, field["key"], field)
        if verdict.state != PRESENT:
            bad.append("%s.%s (%s)" % (section_id, field["key"], verdict.reason))
    evidence = "%d/%d settings fields round-tripped" % (total - len(bad), total)
    if bad:
        return Verdict(MISSING, one_line("; ".join(bad[:3])), evidence)
    return Verdict(PRESENT, "-", evidence)


# §63 recap 键的字面形状（server/inbox_writer._RECAP_KEY_RE）：meeting:<ISO 日期>T<HHMM>-<slug>
_RECAP_KEY = "meeting:2026-09-15T0930-qa-coverage-probe"


def _recap_actions(key):
    """§63 recap 的动作面：生成 / 重生成 / 出稿形状 / 意图问答的答案 / 回退 / Slack 草稿。"""
    return [
        ("generate", {"action": "recap_generate", "meeting_key": key}),
        ("regenerate", {"action": "recap_generate", "meeting_key": key, "note": "again"}),
        ("shape", {"action": "recap_generate", "meeting_key": key, "shape": "sections"}),
        ("intent-qa", {"action": "recap_generate", "meeting_key": key,
                       "answers": ["split1=drop"]}),
        ("revert", {"action": "recap_revert", "meeting_key": key, "version": 1}),
        ("archive", {"action": "recap_slack_draft", "meeting_key": key,
                     "channel_id": "D0QAPROBE"}),
    ]


def _walk_recap_actions(ctx, server, key, steps):
    for label, payload in _recap_actions(key):
        resp = post_action(ctx.http, server, payload)
        if resp.status != 200:
            return "recap %s -> %s %s" % (label, resp.status, one_line(resp.text, 70))
        steps.append("%s 200" % label)
    return None


def _recap_settings_roundtrip(ctx, server, steps):
    """GET + PUT /api/settings/recap（三把旋钮的写口，§63）。"""
    snap = ctx.http.request("GET", server.base_url + "/api/settings/recap")
    if snap.status != 200:
        return "GET /api/settings/recap -> %s" % snap.status
    steps.append("settings/recap 200")
    put = ctx.http.request("PUT", server.base_url + "/api/settings/recap",
                           body=json.dumps({"enabled": True}), headers=_write_headers(server))
    if put.status != 200:
        return "PUT /api/settings/recap -> %s %s" % (put.status, one_line(put.text, 70))
    steps.append("PUT settings/recap 200")
    return None


def _recap_history_shape(ctx, server, key, steps):
    """§63.9 history 的 wire 形状：{key, current, entries[], history_cap, truncated}。"""
    hist = ctx.http.request("GET", server.base_url + "/api/recaps/history?key=" + key)
    if hist.status != 200:
        return "GET /api/recaps/history -> %s" % hist.status
    missing_keys = [k for k in ('"entries"', '"history_cap"') if k not in hist.text]
    if missing_keys:
        return "history lacks %s" % ", ".join(missing_keys)
    steps.append("history 200 entries[]")
    return None


def flow_recaps(ctx):
    """会议 recap 的全套面：设置往返 / 生成 / 重生成 / 回退 / 归档标记 / 意图问答 / history 形状。"""
    server, missing = _need_server(ctx)
    if missing:
        return missing
    steps = []
    checks = (lambda: _recap_settings_roundtrip(ctx, server, steps),
              lambda: _walk_recap_actions(ctx, server, _RECAP_KEY, steps),
              lambda: _recap_mark(ctx, server, _RECAP_KEY, steps),
              lambda: _recap_history_shape(ctx, server, _RECAP_KEY, steps))
    for check in checks:
        problem = check()
        if problem:
            return Verdict(MISSING, problem, one_line("; ".join(steps)))
    return Verdict(PRESENT, "-", one_line("; ".join(steps)))


def _recap_mark(ctx, server, key, steps):
    """§63.5「忽略」= POST /api/recaps/mark {mark: dismissed}。"""
    mark = ctx.http.request("POST", server.base_url + "/api/recaps/mark",
                            body=json.dumps({"key": key, "mark": "dismissed"}),
                            headers=_write_headers(server))
    if mark.status != 200:
        return "POST /api/recaps/mark -> %s %s" % (mark.status, one_line(mark.text, 70))
    steps.append("mark 200")
    return None


_PWA_ASSETS = (("/manifest.webmanifest", "manifest"), ("/icon-192.png", "png"),
               ("/icon-512.png", "png"))


def _pwa_asset_problem(path, want, resp):
    """一个 PWA 资源的毛病（None = 没毛病）。"""
    if resp.status != 200:
        return "GET %s -> %s" % (path, resp.status)
    if want == "manifest":
        return None if '"icons"' in resp.text else "manifest carries no icons[]"
    return None if "PNG" in resp.text[:16] else "%s is not PNG payload" % path


def flow_pwa(ctx):
    """PWA 的三件套（manifest + 两个 icon）都 200，且负载类型对。"""
    server, missing = _need_server(ctx)
    if missing:
        return missing
    if not os.path.exists(os.path.join(REPO_ROOT, "web", "dist", "index.html")):
        return Verdict(MISSING, "web/dist absent — run `npm run build` in web/ first", "-")
    steps = []
    for path, want in _PWA_ASSETS:
        problem = _pwa_asset_problem(path, want, ctx.http.request("GET", server.base_url + path))
        if problem:
            return Verdict(MISSING, problem, one_line("; ".join(steps)))
        steps.append("%s 200" % path)
    return Verdict(PRESENT, "-", one_line("; ".join(steps)))


def _plists(home):
    return sorted(glob.glob(os.path.join(home, "Library", "LaunchAgents", "*.plist")))


def _run_installer(ctx, script, args, env):
    return ctx.shell.run(["bash", os.path.join(REPO_ROOT, script)] + list(args),
                         cwd=REPO_ROOT, env=env, timeout=T_FLOW,
                         log_name="flow_uninstall_reinstall")


def _uninstall_problem(first, rm, left, again, plists):
    """三趟脚本 + 两次 plist 点数 → 第一个毛病（None = 往返成立）。"""
    if first.rc != 0:
        return "first install rc=%s: %s" % (first.rc, one_line(first.out, 90))
    if rm.rc != 0:
        return "uninstall.sh --yes rc=%s: %s" % (rm.rc, one_line(rm.out, 90))
    if left:
        return "uninstall left %d plist behind" % len(left)
    if again.rc != 0 or not plists:
        return "reinstall rc=%s, %d plist" % (again.rc, len(plists))
    return None


def flow_uninstall_reinstall(ctx):
    """uninstall.sh --yes（临时 HOME + 假货）→ 再 install.sh 一遍：两趟都 exit 0。"""
    home = ctx.homes.make("uninst")
    shim_dir, shim_log = write_shims(home)
    env = shim_env(home, shim_dir, shim_log)
    born = _checkout_born()
    first = _run_installer(ctx, "install.sh", ["--non-interactive"], env)
    rm = _run_installer(ctx, "uninstall.sh", ["--yes"], env)
    left = _plists(home)
    again = _run_installer(ctx, "install.sh", ["--non-interactive"], env)
    _drop_born(born)
    plists = _plists(home)
    evidence = "install rc=%s, uninstall rc=%s (left %d plist), reinstall rc=%s (%d plist)" % (
        first.rc, rm.rc, len(left), again.rc, len(plists))
    problem = _uninstall_problem(first, rm, left, again, plists)
    if problem:
        return Verdict(MISSING, problem, evidence)
    return Verdict(PRESENT, "-", one_line(evidence))


def _spec_hits(mapping, spec_file):
    return {name: hit for (spec, name), hit in mapping.items() if spec == spec_file}


def flow_pages_controls(ctx):
    """Playwright 走一遍左栏每个页面、点安全控件、断言零 console error（web/e2e/coverage.spec.ts）。"""
    mapping, error = ctx.tools.playwright_map(ctx.pw_specs)
    if error and not mapping:
        return Verdict(MISSING, error, "-")
    hits = _spec_hits(mapping, PAGES_SPEC)
    if not hits:
        return Verdict(MISSING, "playwright report has no %s tests" % PAGES_SPEC, "-")
    bad = sorted(name for name, hit in hits.items() if not hit.ok)
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


def _compile_title(title):
    try:
        return re.compile(title or ".")
    except re.error:
        return None


def _pw_matches(mapping, want, pattern):
    return [hit for (file_name, name), hit in mapping.items()
            if file_name == want and pattern.search(name)]


def playwright_verdict(mapping, spec, title):
    """playwright map × `<spec>::<title regex>` → Verdict。"""
    pattern = _compile_title(title)
    if pattern is None:
        return Verdict(MISSING, "bad title regex %r" % title, "-")
    want = os.path.basename(spec.strip())
    hits = _pw_matches(mapping, want, pattern)
    if not hits:
        return Verdict(MISSING, "playwright report has no %s::%s" % (want, title), "-")
    bad = [hit for hit in hits if not hit.ok]
    if bad:
        return Verdict(MISSING, one_line(bad[0].evidence), one_line(bad[0].evidence))
    return Verdict(PRESENT, "-", one_line(hits[0].evidence))


def _log_slug(text):
    return re.sub(r"\W+", "_", str(text))


# 需要整行上下文（不只是 proof 的参数）的 kind：fixture: 要 row["note"] 找脚本
ROW_AWARE_KINDS = ("fixture",)


def _row_note(row):
    """清单行的 note（非 dict / 空 → None）。fixture 脚本路径的第一候选。"""
    note = row.get("note") if isinstance(row, dict) else None
    return note.strip() if isinstance(note, str) and note.strip() else None


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
        self._listings = None
        self._unittest_modules = set()
        self._pw_specs = set()
        self.flow_ctx = FlowCtx(tools, shell, http, homes, opts, self._pw_specs)
        # kind → 执行器（唯一的分派表；未登记的 kind = MISSING unknown proof kind）
        self._handlers = {"unittest": self._unittest, "swift": self._swift,
                          "parity": self._parity, "playwright": self._playwright,
                          "http": self._http, "settings": self._settings,
                          "flow": self.flow, "axprobe": self.axprobe,
                          "fixture": self.fixture}

    # -- 预扫：一次跑要覆盖的 unittest 模块 / playwright spec ------------- #
    def collect(self, rows):
        for row in rows:
            for kind, arg in parse_proof(row.get("proof")):
                self._note(row, kind, arg)

    def _note(self, row, kind, arg):
        if str(row.get("status")) == "waived":
            return
        if kind == "unittest":
            self._unittest_modules.update(csv_parts(arg))
        elif kind == "playwright":
            self._pw_specs.add(os.path.basename(arg.partition("::")[0].strip()))
        elif kind == "flow" and arg == "pages_controls":
            self._pw_specs.add(PAGES_SPEC)

    def ids(self, server):
        if self._ids is None:
            self._ids = board_ids(self.http, server)
        return self._ids

    def listings(self, server):
        """列表型占位符的解析器（一个 server 一个，占位符各问一次）。"""
        if self._listings is None:
            self._listings = Listings(self.http, server)
        return self._listings

    def clause(self, kind, arg, row=None):
        handler = self._handlers.get(kind)
        if handler is None:
            return Verdict(MISSING, "unknown proof kind %r" % kind, "-")
        if kind in ROW_AWARE_KINDS:
            return handler(arg, row)
        return handler(arg)

    # -- 每个 kind 一个执行器 --------------------------------------------- #
    def _unittest(self, arg):
        mapping, error = self.tools.unittest_map(self._unittest_modules)
        modules = csv_parts(arg)
        if not modules:
            return Verdict(MISSING, "unittest proof lists no module", "-")
        return combine([_hit_verdict(mapping, error, mod, "unittest",
                                     "module %s did not run" % mod) for mod in modules])

    def _swift(self, arg):
        mapping, error = self.tools.swift_map()
        return _hit_verdict(mapping, error, arg, "swift", "no harness named %s" % arg)

    def _parity(self, arg):
        mapping, error = self.tools.parity_map()
        return _hit_verdict(mapping, error, arg, "parity",
                            "vitest report has no it() for %s" % arg)

    def _playwright(self, arg):
        spec, _, title = arg.partition("::")
        mapping, error = self.tools.playwright_map(self._pw_specs)
        if error and not mapping:
            return Verdict(MISSING, error, "-")
        return playwright_verdict(mapping, spec, title)

    def _http(self, arg):
        server, missing = _need_server(self.flow_ctx)
        if missing:
            return missing
        return run_http_proof(arg, self.http, server, self.ids(server), self.listings(server))

    def _settings(self, arg):
        server, missing = _need_server(self.flow_ctx)
        if missing:
            return missing
        return run_settings_proof(arg, self.http, server)

    def flow(self, name):
        """一条 flow: 只跑一次（同名清单行共用判决）。"""
        if name not in self._flow_cache:
            self._flow_cache[name] = self._run_flow(name)
        return self._flow_cache[name]

    def _run_flow(self, name):
        func = FLOWS.get(name)
        if func is None:
            return Verdict(MISSING, "unknown flow %r" % name, "-")
        log("flow:%s …" % name)
        try:
            return func(self.flow_ctx)
        except Exception as exc:
            return Verdict(MISSING, "flow raised %s: %s" % (
                type(exc).__name__, one_line(exc, 120)), "-")

    def axprobe(self, probe):
        if self.opts.skip_ax:
            return Verdict(MISSING, "skipped", "--skip-ax")
        script = os.path.join(REPO_ROOT, "scripts", "qa", "shell_ui_probe.py")
        if not os.path.exists(script):
            return Verdict(MISSING, "probe script absent", "-")
        proc = self.shell.run([self.python, script, "--probe", probe, "--json"], cwd=REPO_ROOT,
                              timeout=T_SHORT, log_name="axprobe_" + _log_slug(probe))
        return _probe_verdict(proc)

    def fixture(self, slug, row=None):
        script = resolve_fixture_script(slug, _row_note(row))
        if script is None:
            return Verdict(MISSING, "fixture script absent: %s" % ", ".join(
                fixture_candidates(slug, _row_note(row))), "-")
        proc = self.shell.run([self.python, script], cwd=REPO_ROOT,
                              env={"PYTHONPATH": self.pythonpath},
                              timeout=T_FLOW, log_name="fixture_" + _log_slug(slug))
        if proc.rc != 0:
            return Verdict(MISSING, "fixture rc=%s" % proc.rc, tail_line(proc.out))
        return Verdict(PRESENT, "-", tail_line(proc.out))

    def row(self, row):
        """一行清单 → Verdict（waived 行不跑任何东西，照抄 waive_reason）。"""
        if str(row.get("status")) == "waived":
            return Verdict(WAIVED, one_line(row.get("waive_reason") or "waived"), "-")
        clauses = parse_proof(row.get("proof"))
        if not clauses:
            return Verdict(MISSING, "no proof declared in inventory", "-")
        return combine([self.clause(kind, arg, row) for kind, arg in clauses])


def _probe_verdict(proc):
    """AX 探针的 (rc, 输出) → Verdict（JSON 的 present 是真源，rc 只作补充证据）。"""
    present, detail = _probe_json(proc.out)
    if present:
        return Verdict(PRESENT, "-", one_line(detail or tail_line(proc.out)))
    if proc.rc != 0:
        return Verdict(MISSING, "probe rc=%s: %s" % (proc.rc, one_line(proc.out, 90)),
                       tail_line(proc.out))
    return Verdict(MISSING, one_line(detail or "probe reports present=false"),
                   tail_line(proc.out))


def _probe_json(text):
    """shell_ui_probe.py 的 JSON → (present, detail)。"""
    doc = json_tail(text)
    if not isinstance(doc, dict):
        return False, ""
    detail = doc.get("evidence") or doc.get("detail") or doc.get("reason") or ""
    return bool(doc.get("present")), str(detail)


# --------------------------------------------------------------------------- #
# 报告 R
# --------------------------------------------------------------------------- #

_REPORT_FORMS = {
    PRESENT: lambda ident, v: "%s PRESENT evidence=%s" % (ident, v.evidence or "-"),
    WAIVED: lambda ident, v: "%s WAIVED reason=%s evidence=-" % (ident, v.reason or "waived"),
    MISSING: lambda ident, v: "%s MISSING reason=%s evidence=%s" % (
        ident, v.reason or "-", v.evidence or "-"),
}


def _report_line(ident, verdict):
    return _REPORT_FORMS.get(verdict.state, _REPORT_FORMS[MISSING])(ident, verdict)


def render_report(pairs):
    """[(row, Verdict)] → R 的全文（末三行 PRESENT=/MISSING=/WAIVED=）。"""
    lines, counts = [], {PRESENT: 0, MISSING: 0, WAIVED: 0}
    for row, verdict in pairs:
        counts[verdict.state] = counts.get(verdict.state, 0) + 1
        lines.append(_report_line(str(row.get("id") or "?"), verdict))
    lines.extend("%s=%d" % (state, counts.get(state, 0))
                 for state in (PRESENT, MISSING, WAIVED))
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


def _load_rows(opts):
    """(rows, exit code)：清单缺席 / 读不动 → 2（诚实退出，不空跑）。"""
    inventory = resolve_path(opts.inventory)
    if not os.path.exists(inventory):
        sys.stderr.write(
            "coverage_run: inventory %s not found — run "
            "`python3 scripts/qa/coverage_inventory.py --write` first (or pass --inventory PATH)\n"
            % inventory)
        return None, 2
    try:
        rows = load_inventory(inventory)
    except (OSError, ValueError) as exc:
        sys.stderr.write("coverage_run: unreadable inventory %s: %s\n" % (inventory, exc))
        return None, 2
    if opts.only:
        rows = [row for row in rows if str(row.get("id") or "").startswith(opts.only)]
    return rows, 0


def _judge_all(judge, rows, cleanup):
    """整跑的 trap 边界：无论怎么收场，临时 HOME 与子进程都清掉。"""
    atexit.register(cleanup)
    _install_signal_traps(cleanup)
    try:
        return [(row, judge.row(row)) for row in rows]
    finally:
        cleanup()
        atexit.unregister(cleanup)


def _assemble(opts, shell, http, homes, server_factory, python_bin):
    """三个注入缝的组装（单元测试传假货进来，真跑用真 Shell / Http / demo server）。"""
    homes = homes or TempHomes()
    shell = shell or Shell(opts.logdir)
    http = http or Http()
    tools = Tools(shell, http, opts, homes, server_factory=server_factory, python_bin=python_bin)
    return tools, Judge(tools, shell, http, homes, opts), homes


def main(argv=None, shell=None, http=None, homes=None, server_factory=None, python_bin=None):
    opts = build_parser().parse_args(argv)
    opts.logdir = resolve_path(opts.logdir) if opts.logdir else None
    rows, code = _load_rows(opts)
    if rows is None:
        return code
    if opts.logdir:
        os.makedirs(opts.logdir, exist_ok=True)
    tools, judge, homes = _assemble(opts, shell, http, homes, server_factory, python_bin)
    judge.collect(rows)

    def cleanup():
        tools.stop()
        homes.cleanup()

    started = time.time()
    pairs = _judge_all(judge, rows, cleanup)
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
