"""doctor 探针家族：macOS launchd（CONTRACT §25 行目录；§55 模板路径纪律与
TCC 三幕；§56.3 第 1 步卷访问）。

行：``<agent short>``（每个模板 label 一行：未注册 / running / loaded /
crash-loop 并按日志归因 ``No module named 'act'`` vs ``'yaml'``）、
``launchd paths`` + ``launchd python``（§55 四种症状）、``launchd orphans``
（退役 agent 残留）、``launchd fd limit``（只抬 soft）、``launchd claude``
（在一次性 launchd job 里问 launchd 本人 claude 读不读得到任务目录）、
``launchd volume access``（读 §56.4 HOME 镜像的无人值守判决）。默认探针实现
（agent 日志路径 / 尾部 / mtime、已装 plist label、launchd claude 探针）也住
这里——tests 一律注入，绝不真起 launchd（tests/__init__.py 的 env 保险）。
"""
from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from act.lib import claude_bin as claude_bin_lib
from act.lib import config, deploy_state, platform
from act.lib.checks.core import (ACTD_LABEL, FAIL, LABEL_PREFIX, OK,
                                 RESIDENT_LABELS, WARN, CheckResult, installer,
                                 launchctl_table, pick, pinned_interpreter,
                                 row_from, templated_labels)

# 两条 ModuleNotFoundError 在 `launchctl list` 里长得一模一样，修复动作却相反
# （§55）。断言 PyYAML 而不读日志，正是 2026-08-31 那次把排查带偏几个小时的原因：
# /opt/homebrew/bin/python3 全程都装着 PyYAML，缺的是对 repo 的读权限。
MISSING_ACT = "act"      # 解释器看不见 repo（TCC per-binary / PYTHONPATH 错）
MISSING_YAML = "yaml"    # 守护进程唯一的非 stdlib 依赖没装

AUTODEPLOY_LABEL = "com.zelin.aiassistant.autodeploy"
_VOLUME_ROW = "launchd volume access"
_FD_LIMIT_MIN = 4096   # anything below this is the launchd default territory

# launchd 在 spawn 前触碰的 plist 键——任何一个指向 repo 都是 §55 之前的渲染
_PLIST_SPAWN_PATH_KEYS = ("StandardOutPath", "StandardErrorPath",
                          "WorkingDirectory")
# repo 路径唯一允许出现的地方（§55）——这两个值必须是 PHYSICAL 路径
_PLIST_REPO_PATH_KEYS = ("AIASSISTANT_HOME", "PYTHONPATH")

INSTALL_SH_FIX = ("bash install.sh  # re-renders ALL agents; the app's"
                  " one-click repair only re-renders actd")

# 解释器「看得见 yaml、看不见 repo」时的唯一正确动作（§55）。install.sh 自
# 起用 launchd 真实探针挑解释器（§55 第二道闸门），所以重跑就会换掉瞎的
# 那个；换不掉
# （比如只有一个 python）时才轮到手动授 FDA。
INTERPRETER_BLIND_FIX = (
    "bash install.sh  # now probes launchd viability and picks an interpreter"
    " that can actually read the repo; if it still fails, grant Full Disk"
    " Access to that interpreter binary in System Settings > Privacy & Security")

_CLAUDE_BLIND_RE = re.compile(
    r"possibly due to low max file descriptors|operation not permitted|\bEPERM\b",
    re.IGNORECASE)

# The payload is /bin/sh (an Apple platform binary — it can cd anywhere the
# way python's pre-exec chdir did on 2026-08-31); only the exec'd claude is
# what TCC judges, exactly as in a real dispatch. Stages let the reader tell
# "cd itself failed" from "claude started and hung" from "claude exited".
_CLAUDE_PROBE_SH = (
    'cd "$1" || { echo "cd_failed:$?" > "$3"; exit 0; }; '
    'echo started > "$3.stage"; '
    '"$2" --version > "$3.out" 2>&1; echo "rc:$?" > "$3"')


# --------------------------------------------------------------------------- #
# Default probe implementations (Probes.launchd_log_tail / ...)
# --------------------------------------------------------------------------- #
def launchd_log_paths(short: str):
    """agent 自管日志的候选路径：v0.48 起住 ~/Library/Logs/，旧址兜底。"""
    return (Path.home() / "Library" / "Logs" / "zelin-ai-assistant"
            / ("%s.launchd.log" % short),
            config.HOME / "state" / ("%s.launchd.log" % short))


def launchd_log_tail(short: str) -> str:
    """agent 自管日志的末尾；"" = 读不到。"""
    for p in launchd_log_paths(short):
        try:
            return p.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            continue
    return ""


def launchd_log_mtime(short: str) -> Optional[float]:
    """agent 自管日志的 mtime；None = 读不到（launchd 的 stderr 没有时间戳，§56.3 第 1 步）。"""
    for p in launchd_log_paths(short):
        try:
            return p.stat().st_mtime
        except OSError:
            continue
    return None


def installed_agent_labels() -> List[str]:
    """~/Library/LaunchAgents 里带我们前缀的 plist 文件名（label）——孤儿探测
    用（§55）：有文件没模板 = 退役 agent 的残留，下次登录还会被 launchd 装载。"""
    d = Path.home() / "Library" / "LaunchAgents"
    try:
        return sorted(p.stem for p in d.glob(LABEL_PREFIX + "*.plist"))
    except OSError:
        return []


def log_missing_module(tail: str) -> Optional[str]:
    """日志里 `No module named 'X'` 的 X，只认我们分得清的两个；None = 没有。

    取 **最后** 一次匹配：KeepAlive 会把历次失败都留在同一个文件里，最新那条
    才是当前状态。
    """
    hits = re.findall(r"No module named '([A-Za-z_][A-Za-z0-9_]*)'", tail)
    for name in reversed(hits):
        if name in (MISSING_ACT, MISSING_YAML):
            return name
    return None


# --------------------------------------------------------------------------- #
# §55 第三幕：launchd 起的 claude 读不读得到任务目录 —— 只能问 launchd 本人
# --------------------------------------------------------------------------- #
def _probe_plist(label: str, cwd: str, claude_bin: str, verdict: Path) -> dict:
    return {
        "Label": label,
        "ProgramArguments": ["/bin/sh", "-c", _CLAUDE_PROBE_SH, "_",
                             cwd, claude_bin, str(verdict)],
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        "WorkingDirectory": str(Path.home()),
        "RunAtLoad": True,
        # kill the whole group on bootout so a hung claude does not linger
        "AbandonProcessGroup": False,
    }


def _unavailable(text: str) -> dict:
    return {"state": "unavailable", "rc": None, "text": text}


def _bootout_quietly(domain: str, label: str) -> None:
    try:
        subprocess.run(["launchctl", "bootout", "%s/%s" % (domain, label)],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def _wait_for(path: Path, budget_s: float) -> bool:
    deadline = time.time() + budget_s
    while time.time() < deadline and not path.exists():
        time.sleep(0.25)
    return path.exists()


def _no_verdict(tmp: Path, budget_s: float) -> dict:
    started = (tmp / "verdict.stage").exists()
    return {"state": "hang" if started else "unavailable", "rc": None,
            "text": ("claude started under launchd but produced no exit within "
                     "%.0f s" % budget_s) if started
            else "launchd ran nothing observable within %.0f s" % budget_s}


def _read_optional(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_rc(raw: str) -> Optional[int]:
    """``rc:<n>`` → n；别的形状 None。"""
    try:
        return int(raw.split(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _read_verdict(tmp: Path, cwd: str) -> dict:
    raw = (tmp / "verdict").read_text(encoding="utf-8", errors="replace").strip()
    out = _read_optional(tmp / "verdict.out")
    if raw.startswith("cd_failed"):
        return {"state": "cd_failed", "rc": None, "text": "sh could not cd into %s" % cwd}
    rc = _parse_rc(raw)
    if rc is None:
        return _unavailable("unreadable verdict %r" % raw[:60])
    return {"state": "ok" if rc == 0 else "failed", "rc": rc, "text": out.strip()[-600:]}


def _run_probe_job(tmp: Path, label: str, domain: str, claude_bin: str,
                   cwd: str, budget_s: float) -> dict:
    verdict = tmp / "verdict"
    plist = tmp / "probe.plist"
    plist.write_bytes(plistlib.dumps(_probe_plist(label, cwd, claude_bin, verdict)))
    subprocess.run(["launchctl", "bootout", "%s/%s" % (domain, label)],
                   capture_output=True, timeout=10)
    boot = subprocess.run(["launchctl", "bootstrap", domain, str(plist)],
                          capture_output=True, text=True, timeout=15)
    if boot.returncode != 0:
        return _unavailable("launchd refused the probe job: %s"
                            % (boot.stderr or boot.stdout).strip()[-200:])
    if not _wait_for(verdict, budget_s):
        return _no_verdict(tmp, budget_s)
    return _read_verdict(tmp, cwd)


def claude_probe(claude_bin: str, cwd: str, budget_s: float = 20.0) -> dict:
    """Run ``<claude_bin> --version`` with cwd=``cwd`` inside a throwaway
    gui-domain launchd job (same recipe as install.sh's viability probe) and
    report what happened::

        {"state": "ok" | "failed" | "hang" | "cd_failed" | "unavailable",
         "rc": int | None, "text": str}

    Doctor runs in the owner's terminal, whose TCC grants make claude read
    everything — so the terminal cannot see this failure at all. Verified
    2026-09-01: from the same launchd job, ``claude --version`` succeeds with
    cwd=$HOME and dies with Bun's "possibly due to low max file descriptors
    (Unexpected)" the moment cwd is on the external volume, while /bin/ls and
    /usr/bin/python3 read it fine and homebrew node shows the raw EPERM.
    "unavailable" = no launchd here, probe switched off
    (``AIASSISTANT_LAUNCHD_PROBE=0``), or launchd refused the job — never a
    verdict about claude. Sub-30 s, self-cleaning (bootout + rm)."""
    if not platform.is_darwin() or os.environ.get("AIASSISTANT_LAUNCHD_PROBE", "1") == "0":
        return _unavailable("probe disabled or no launchd")
    if not shutil.which("launchctl"):
        return _unavailable("launchctl not found")
    tmp = Path(tempfile.mkdtemp(prefix="zai-claude-probe-"))
    label = "com.zelin.aiassistant.claudeprobe.%d" % os.getpid()
    domain = "gui/%d" % os.getuid()
    try:
        return _run_probe_job(tmp, label, domain, claude_bin, cwd, budget_s)
    except (OSError, subprocess.SubprocessError) as exc:
        return _unavailable("probe error: %r" % (exc,))
    finally:
        _bootout_quietly(domain, label)
        shutil.rmtree(str(tmp), ignore_errors=True)


# --------------------------------------------------------------------------- #
# plist readers
# --------------------------------------------------------------------------- #
def plist_string(text: str, key: str) -> Optional[str]:
    m = re.search(r"<key>%s</key>\s*<string>([^<]+)</string>" % key, text)
    return m.group(1) if m else None


def plist_interpreter(text: str) -> Optional[str]:
    """ProgramArguments[0] —— launchd 真正 exec 的那个二进制。"""
    m = re.search(r"<key>ProgramArguments</key>\s*<array>\s*<string>([^<]+)</string>",
                  text)
    return m.group(1) if m else None


def plist_number_of_files(text: str, key: str) -> Optional[int]:
    """`<key>KEY</key><dict>…<key>NumberOfFiles</key><integer>N</integer>…</dict>`
    — NumberOfFiles may sit anywhere inside the dict (a hand-edited plist
    with another limit first must not read as unset)."""
    m = re.search(r"<key>%s</key>\s*<dict>(.*?)</dict>" % key, text, re.S)
    if not m:
        return None
    n = re.search(r"<key>NumberOfFiles</key>\s*<integer>(\d+)</integer>", m.group(1))
    return int(n.group(1)) if n else None


def symlink_shaped(value: Optional[str]) -> bool:
    """该路径是否经过 symlink（≠ 自己的 realpath）。不存在的路径原样返回，
    所以未安装/占位路径不会误报。"""
    if not value or not value.startswith("/"):
        return False
    trimmed = value.rstrip("/") or "/"
    try:
        return os.path.realpath(trimmed) != trimmed
    except OSError:  # noqa: BLE001 - 探针不许崩
        return False


def interpreter_ok(probes, py: str) -> bool:
    """plist 里的解释器能不能真的 `import yaml`（§55）。"""
    if not py.startswith("/") or not os.access(py, os.X_OK):
        return False
    return probes.run([py, "-c", "import yaml"], timeout=20)[0] == 0


def crashing_agents(probes) -> set:
    """当前真的在崩的 agent（short name）：已注册、没有 PID、上次退出码非 0。

    日志是历史，`launchctl list` 才是现状——KeepAlive 治好之后旧日志还躺在那
    里，只看日志会给一个跑得好好的 agent 报故障。
    """
    crashing = set()
    try:
        lines = probes.launchctl_list().splitlines()
    except Exception:  # noqa: BLE001 - 探针不许崩（宪法第 11 条）
        # launchctl 坏了是 check_agents 的发现，不该连累路径检查；此时只是
        # 确认不了「此刻在崩」，于是症状 4 沉默（少报 > 误报）。
        return crashing
    for line in lines:
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "-" and parts[1] not in ("0", "Status"):
            crashing.add(parts[2].rsplit(".", 1)[-1])
    return crashing


# --------------------------------------------------------------------------- #
# launchd agents (one row per template label)
# --------------------------------------------------------------------------- #
def _parse_launchctl(text: str) -> dict:
    """label → (pid, last exit status)."""
    table = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            table[parts[2]] = (parts[0], parts[1])
    return table


def _crash_detail(short: str, status: str, loop: str, missing: Optional[str]) -> "tuple[str, str]":
    """名出真因，别猜（§55）：读它自己的日志，把两条 ModuleNotFoundError 分开——
    'act' = 解释器看不见 repo，'yaml' = 缺 PyYAML。"""
    if missing == MISSING_ACT:
        return ("loaded but exits with status %s%s - its log says "
                "\"No module named 'act'\": the interpreter cannot see "
                "the repo (PyYAML is NOT the problem)" % (status, loop),
                INTERPRETER_BLIND_FIX)
    if missing == MISSING_YAML:
        return ("loaded but exits with status %s%s - its log says "
                "\"No module named 'yaml'\": PyYAML is missing for the "
                "daemon python" % (status, loop),
                "%s -m pip install --user --break-system-packages pyyaml" % (
                    pinned_interpreter() or "python3"))
    return ("loaded but its process exits with status %s%s" % (status, loop),
            "tail -20 ~/Library/Logs/zelin-ai-assistant/%s.launchd.log"
            " (pre-v0.48 installs: state/%s.launchd.log)"
            "  # usual causes: the interpreter cannot see the repo"
            " (\"No module named 'act'\"), PyYAML missing"
            " (\"No module named 'yaml'\"), missing API key"
            % (short, short))


def _crash_loop_row(probes, short: str, label: str, status: str) -> CheckResult:
    # A KeepAlive agent with no pid and a non-zero exit is crash-looping
    # (launchd respawns it every ThrottleInterval, it dies again) — FAIL
    # for every resident label, not just actd: a broken syncd is the
    # phone/web board gone, and only FAIL rows drive §56's rollback.
    # Periodic agents (RunAtLoad radars, weeklydigest, autodeploy)
    # exiting non-zero once is a WARN — one network blip would
    # otherwise roll a deploy back.
    resident = label in RESIDENT_LABELS
    missing = log_missing_module(probes.launchd_log_tail(short))
    detail, fix = _crash_detail(short, status, " (KeepAlive: crash loop)" if resident else "", missing)
    return (CheckResult(short, FAIL if resident else WARN, detail, fix)
            .with_failure("interpreter_blind" if missing == MISSING_ACT else "agent_unloaded"))


def _agent_row(probes, label: str, table: dict) -> CheckResult:
    short = label.rsplit(".", 1)[-1]
    # actd is the resident daemon the whole product hangs off; the radar
    # agents are periodic and recommended via cron anyway (TCC), so their
    # absence only warns.
    if label not in table:
        return CheckResult(
            short, FAIL if label == ACTD_LABEL else WARN,
            "%s not registered with launchd%s" % (
                label, " - cards never move" if label == ACTD_LABEL else ""),
            "bash install.sh (renders + loads the agents)",
        ).with_failure("agent_unloaded")
    pid, status = table[label]
    if pid != "-":
        return CheckResult(short, OK, "running (pid %s)" % pid)
    if status == "0":
        return CheckResult(short, OK, "loaded (last run exited 0)")
    return _crash_loop_row(probes, short, label, status)


def check_agents(probes):
    labels = templated_labels(probes)
    if not labels:
        return CheckResult(
            "launchd agents", WARN,
            pick("act/launchd 下没有 plist 模板——checkout 不完整？",
                 "no plist templates under act/launchd - incomplete checkout?"),
            "git -C '%s' checkout act/launchd" % config.HOME)
    table = _parse_launchctl(probes.launchctl_list())
    return [_agent_row(probes, label, table) for label in labels]


# --------------------------------------------------------------------------- #
# §55 launchd paths / launchd python
# --------------------------------------------------------------------------- #
def _points_at_repo(value: Optional[str], repo: str) -> bool:
    return value == repo or (value or "").startswith(repo + "/")


@dataclass
class _PlistScan:
    """What the installed plists say, accumulated across agents."""
    repo: str
    stale: list = field(default_factory=list)       # spawn-time key points at repo
    symlinked: list = field(default_factory=list)   # repo env var is symlink-shaped
    bad_py: dict = field(default_factory=dict)      # interpreter → agents (no yaml)
    blind_py: dict = field(default_factory=dict)    # interpreter → agents (TCC-blind)
    verdicts: dict = field(default_factory=dict)    # interpreter → import yaml ok?
    seen_any: bool = False

    def add(self, probes, short: str, text: str, crashing: set) -> None:
        self.seen_any = True
        spawn = [plist_string(text, key) for key in _PLIST_SPAWN_PATH_KEYS]
        if any(_points_at_repo(v, self.repo) for v in spawn):
            self.stale.append(short)
        elif any(symlink_shaped(plist_string(text, key)) for key in _PLIST_REPO_PATH_KEYS):
            self.symlinked.append(short)
        py = plist_interpreter(text)
        if py:
            self._judge_interpreter(probes, py, short, crashing)

    def _judge_interpreter(self, probes, py: str, short: str, crashing: set) -> None:
        # 同一个解释器只探一次——agent 有五个，别起五次进程
        if py not in self.verdicts:
            self.verdicts[py] = interpreter_ok(probes, py)
        if not self.verdicts[py]:
            self.bad_py.setdefault(py, []).append(short)
        elif (short in crashing
              and log_missing_module(probes.launchd_log_tail(short)) == MISSING_ACT):
            # yaml 过了、路径也对，agent 此刻在崩、日志说没有 act
            # = 解释器读不到 repo。三个条件缺一不可：只看日志会把治好之后
            # 的陈旧日志当成现故障。
            self.blind_py.setdefault(py, []).append(short)


def _paths_severity(repo: str) -> str:
    """repo 在 $HOME 下时症状 1/2 只让 agent 变慢/变脏而不致命，降级为 WARN。"""
    home = str(Path.home()).rstrip("/")
    return WARN if repo == home or repo.startswith(home + "/") else FAIL


def _paths_row(scan: _PlistScan) -> CheckResult:
    repo = scan.repo
    severity = _paths_severity(repo)
    if scan.stale:
        return CheckResult(
            "launchd paths", severity,
            "installed plist still points at the repo (pre-v0.48 render): %s%s"
            % (", ".join(scan.stale),
               "" if severity == WARN
               else " - repo is on an external volume; launchd refuses to spawn (78)"),
            INSTALL_SH_FIX)
    if scan.symlinked:
        return CheckResult(
            "launchd paths", severity,
            "installed plist carries a symlinked repo path (%s): %s%s"
            % (repo, ", ".join(scan.symlinked),
               "" if severity == WARN
               else " - launchd is TCC-denied through that shape; the agents"
                    " exit with \"No module named 'act'\""),
            INSTALL_SH_FIX)
    return CheckResult("launchd paths", OK,
                       "installed plists keep spawn-time paths out of the "
                       "repo and the repo path physical")


def _agents_named(by_interp: dict) -> str:
    return ", ".join(sorted({a for agents in by_interp.values() for a in agents}))


def _python_row(scan: _PlistScan, paths: CheckResult) -> Optional[CheckResult]:
    """症状 3（缺 yaml，永远 FAIL）或症状 4（TCC-blind，只在路径干净时才报：
    路径本身坏的时候，重渲染就把两件事一起修了，多报一行只会让人先去授一个
    其实不需要的 FDA）。"""
    if scan.bad_py:
        return CheckResult(
            "launchd python", FAIL,
            "the interpreter rendered into %s cannot `import yaml` (%s) - the "
            "agents exit before they log anything"
            % (_agents_named(scan.bad_py), ", ".join(sorted(scan.bad_py))),
            INSTALL_SH_FIX)
    if scan.blind_py and paths.status == OK:
        return CheckResult(
            "launchd python", FAIL,
            "%s imports yaml and the rendered paths are correct, yet %s still "
            "exit with \"No module named 'act'\" - that interpreter cannot READ "
            "the repo when launchd spawns it (macOS grants file access per "
            "binary, and launchd jobs do not inherit your terminal's grant)"
            % (", ".join(sorted(scan.blind_py)), _agents_named(scan.blind_py)),
            INTERPRETER_BLIND_FIX).with_failure("interpreter_blind")
    return None


def check_paths(probes):
    """§55 渲染纪律探测，四种症状：

    1. spawn 前路径键还指着 repo = pre-v0.48 渲染残留。repo 在外置卷
       （TCC-gated volume）上时 launchd 以 EX_CONFIG(78) 拒绝 spawn。
    2. 携带 repo 的环境变量（AIASSISTANT_HOME / PYTHONPATH）是 **symlink 形
       状**（≠ 自己的 realpath）。2026-08-31 live 事故：~/Projects ->
       /Volumes/… 这条便利 symlink 被渲进 plist，launchd 会话经该路径形状被
       TCC 拒绝，agent 每次 spawn 都以 `No module named 'act'` 退出 1。
    3. plist 里的解释器 import 不了 yaml —— PyYAML 是守护进程唯一的非 stdlib
       依赖，缺了它 agent 在写下任何日志之前就死（同一次事故的第二个症状：
       /opt/homebrew/bin/python3 是 3.14，没装 PyYAML）。
    4. **路径全对、解释器也能 import yaml，agent 还是死在
       `No module named 'act'`**——同一次事故的最后一幕，v0.48.2 修好 1/2 之后
       才露出来：TCC 按 **binary** 授权，`/usr/bin/python3` 有权读外置卷而
       `/opt/homebrew/bin/python3` 没有，两个都能 import yaml，所以老的 yaml
       单闸门恰好挑中瞎的那个。这条的修复动作与 1-3 不同（换解释器 / 授 FDA），
       所以自成一行，且只在 1/2 都干净时才报——否则先修路径。

    repo 在 $HOME 下时 1/2 只让 agent 变慢/变脏而不致命，降级为 WARN；3/4 永远
    是 FAIL。App 的「一键修复」只重渲染 actd，所以这里点名每一个坏 agent。
    """
    scan = _PlistScan(str(config.HOME).rstrip("/"))
    crashing = crashing_agents(probes)
    for label in templated_labels(probes):
        text = probes.installed_plist_text(label)
        if text:   # 没装——check_agents 已经报 unregistered
            scan.add(probes, label.rsplit(".", 1)[-1], text, crashing)
    if not scan.seen_any:
        return []
    paths = _paths_row(scan)
    python = _python_row(scan, paths)
    return [paths] if python is None else [paths, python]


# --------------------------------------------------------------------------- #
# §55 orphans / fd limit
# --------------------------------------------------------------------------- #
def _is_orphan(label: str, known: set) -> bool:
    return label.startswith(LABEL_PREFIX) and label not in known


def _on_disk_orphans(probes, known: set, loaded: list) -> list:
    try:
        return sorted(lbl for lbl in probes.installed_agent_labels()
                      if _is_orphan(lbl, known) and lbl not in loaded)
    except Exception:  # noqa: BLE001 - 探针不许崩
        return []


def check_orphans(probes):
    """§55 孤儿 agent：带我们前缀、但 act/launchd 里已没有模板的 label。

    2026-08-31 审计：v0.21 删掉的 imessageradar agent 又跑了 51 天、23,613 条
    traceback（14.5 MB 日志）——install.sh 的 RETIRED 卸载把 bootout 失败吞进
    `/dev/null`，而旧 doctor 只查有模板的 label，孤儿结构性不可见。两个面都
    扫：`launchctl list` 里已装载的（此刻在耗资源/刷日志 → FAIL）与
    ~/Library/LaunchAgents 里只剩文件的（下次登录复活 → WARN）。
    """
    known = set(templated_labels(probes))
    loaded = sorted(lbl for lbl in launchctl_table(probes) if _is_orphan(lbl, known))
    on_disk = _on_disk_orphans(probes, known, loaded)
    if not loaded and not on_disk:
        return CheckResult("launchd orphans", OK,
                           "no retired agents loaded or left in ~/Library/LaunchAgents")
    uid_hint = "launchctl bootout gui/$(id -u)/%s && rm ~/Library/LaunchAgents/%s.plist"
    if loaded:
        return CheckResult(
            "launchd orphans", FAIL,
            "retired agent(s) still loaded in launchd (no template in act/launchd): "
            "%s - each one keeps running/crash-looping and logging forever"
            % ", ".join(loaded),
            "bash install.sh  # unloads retired labels; or by hand: "
            + "; ".join(uid_hint % (lbl, lbl) for lbl in loaded),
        ).with_failure("launchd_orphan")
    return CheckResult(
        "launchd orphans", WARN,
        "retired agent plist(s) left in ~/Library/LaunchAgents (not loaded now, but "
        "launchd reloads them at next login): %s" % ", ".join(on_disk),
        "bash install.sh  # or: rm " + " ".join(
            "~/Library/LaunchAgents/%s.plist" % lbl for lbl in on_disk),
    ).with_failure("launchd_orphan")


def _shown_limit(soft: Optional[int]) -> str:
    return str(soft) if soft is not None else "unset"


def _fd_limit_row(soft: Optional[int], hard: Optional[int]) -> CheckResult:
    if hard is not None:
        return CheckResult(
            "launchd fd limit", WARN,
            "installed actd plist sets HardResourceLimits.NumberOfFiles=%d - launchd's "
            "default hard limit is unlimited, so this only LOWERS the ceiling (the "
            "2026-08-31 hotfix shape; soft=%s)" % (hard, _shown_limit(soft)),
            "bash install.sh  # re-renders the agents: soft limit only, hard left unlimited",
        ).with_failure("fd_limit")
    if soft is not None and soft >= _FD_LIMIT_MIN:
        return CheckResult("launchd fd limit", OK,
                           "installed actd plist raises the soft NumberOfFiles limit to %d "
                           "(hard stays launchd's unlimited)" % soft)
    return CheckResult(
        "launchd fd limit", WARN,
        "installed actd plist carries no soft NumberOfFiles limit (soft=%s) - launchd's "
        "default is 256, thin headroom for a daemon and its children (EMFILE)"
        % _shown_limit(soft),
        "bash install.sh  # re-renders the agents with SoftResourceLimits.NumberOfFiles",
    ).with_failure("fd_limit")


def check_fd_limit(probes):
    """§55 资源上限：已安装 actd plist 的 SoftResourceLimits.NumberOfFiles。

    launchd gui domain 给 job 的默认是 soft 256 / hard unlimited。模板只抬
    soft（余量）；**HardResourceLimits 不该出现**——它只会把 unlimited 压低
    （2026-09-01 实测：Soft+Hard 8192 → [8192, 8192]，只 Soft → [8192,
    unlimited]）。当晚 hotfix 的形状正是两把都设，而 8-31 的派发失败根本不是
    fd 问题（TCC，见 check_claude）——所以这一行只说资源上限的事实，
    不再把 dispatch 失败归到它头上。
    """
    text = probes.installed_plist_text(ACTD_LABEL)
    if not text:
        return []   # 没装——check_agents 已经报 unregistered
    return _fd_limit_row(plist_number_of_files(text, "SoftResourceLimits"),
                         plist_number_of_files(text, "HardResourceLimits"))


# --------------------------------------------------------------------------- #
# §55 第三幕 launchd claude / §56.3 volume access
# --------------------------------------------------------------------------- #
def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _real_bin(claude_bin: str) -> str:
    try:
        return str(Path(claude_bin).resolve())
    except OSError:
        return claude_bin


def _first_line(res: dict) -> "tuple[str, str]":
    """(full text, first line ≤200 chars) of the probe's captured output."""
    text = str(res.get("text") or "").strip()
    first = (text.splitlines() or [""])[0][:200] if text else ""
    return text, first


def _claude_failed_row(res: dict, claude_bin: str, cwd: Path, text: str,
                       first: str, fda_fix: str) -> CheckResult:
    if _CLAUDE_BLIND_RE.search(text):
        return CheckResult(
            "launchd claude", FAIL,
            "launchd-spawned %s cannot read %s (rc=%s: %s) - macOS grants Full Disk "
            "Access per executable path and this claude has none; every dispatch into "
            "that folder dies the same way" % (claude_bin, cwd, res.get("rc"), first),
            fda_fix,
        ).with_failure("claude_blind")
    return CheckResult(
        "launchd claude", WARN,
        "`%s --version` under launchd with cwd=%s exited %s: %s"
        % (claude_bin, cwd, res.get("rc"), first),
        "run the same command in a terminal; if it works there, the difference is the "
        "launchd session (permissions, environment) - see docs/TROUBLESHOOTING.md")


def _claude_probe_row(res: dict, claude_bin: str, cwd: Path) -> CheckResult:
    state = res.get("state")
    text, first = _first_line(res)
    fda_fix = ("System Settings > Privacy & Security > Full Disk Access: %s, or move the "
               "repo under $HOME on the boot volume; then Stop > Discard & re-propose > "
               "approve the halted cards"
               % claude_bin_lib.grant_text(claude_bin, _real_bin(claude_bin), installer()))
    if state == "ok":
        return CheckResult("launchd claude", OK,
                           "launchd-spawned %s reads %s" % (claude_bin, cwd))
    if state == "unavailable":
        return CheckResult(
            "launchd claude", WARN,
            "could not ask launchd whether claude can read %s (%s)" % (cwd, first),
            "re-run doctor; or by hand: bootstrap a throwaway agent that runs "
            "`%s --version` with WorkingDirectory=%s" % (claude_bin, cwd))
    if state == "cd_failed":
        return CheckResult(
            "launchd claude", FAIL,
            "even /bin/sh inside a launchd job cannot cd into %s - the folder is "
            "missing or unreadable to background jobs" % cwd,
            "check the path in config.yaml (execution.default_target_repo) and that the "
            "volume is mounted")
    if state == "hang":
        return CheckResult(
            "launchd claude", WARN,
            "%s started under launchd with cwd=%s but never exited - what a pending "
            "macOS file-access prompt looks like for a job that has no UI to show it"
            % (claude_bin, cwd),
            fda_fix,
        ).with_failure("claude_blind")
    return _claude_failed_row(res, claude_bin, cwd, text, first, fda_fix)


def check_claude(probes):
    """§55 第三幕（v0.48.4）：launchd 起的 claude 可执行文件能否读任务目录。

    macOS 按可执行文件路径授「完全磁盘访问」；终端里的 claude 继承终端的授权，
    launchd 里的 claude 只有它自己的——而 ~/.local/share/claude/versions/<v>
    每次更新都是新路径。任务目录在外置卷 / Documents / Desktop / Downloads 时，
    每次派发都死在 Bun 的「possibly due to low max file descriptors」上。
    2026-08-31 事故就是这个，抬 fd 上限一字未改。doctor 自己在终端里看不见
    它，所以照 §55 的规矩问 launchd 本人（probes.launchd_claude_probe，测试注入）。
    没有 actd plist（没装 launchd 服务）→ 无此行：本行说的是 launchd 会话。
    """
    if not probes.installed_plist_text(ACTD_LABEL):
        return []
    cfg = config.load_config()
    claude_bin = config.resolve_claude_bin(cfg)
    cwd = _resolved(cfg.target_repo_path)
    if not cwd.is_dir():
        return []   # 默认工作 repo 不存在 — _check_target_repo 之类另有报法
    res = probes.launchd_claude_probe(claude_bin, str(cwd))
    return _claude_probe_row(res, claude_bin, cwd)


def check_volume_access(probes):
    """§56.3 第 1 步：部署 agent 在 launchd 会话里能否读写 repo 所在的卷。doctor 跑在
    终端里、借着终端的 TCC 授权什么都读得到，所以本行**不探**，只读无人值守那一轮的
    证据（判决与文案：`deploy_state.unattended_verdict` / `volume_access_row`）。没装
    autodeploy plist、或它部署的是另一个 checkout → 无此行。"""
    text = probes.installed_plist_text(AUTODEPLOY_LABEL)
    if not text:
        return []
    repo = str(config.HOME)
    if not deploy_state.same_repo(plist_string(text, "AIASSISTANT_HOME"), repo):
        return []
    interp = plist_interpreter(text) or "<ProgramArguments[0] of the autodeploy plist>"
    verdict = deploy_state.unattended_verdict(
        probes.deploy_mirror_read(), repo, probes.launchd_log_tail("autodeploy"),
        probes.launchd_log_mtime("autodeploy"), probes.now())
    return row_from(deploy_state.volume_access_row(verdict, interp, repo), _VOLUME_ROW)
