"""Post-install diagnostics — ``python3 -m act.doctor``.

Every failure mode a fresh install has hit is SILENT: a launchd agent that
loads but never spawns, TCC blocking cron off the vault, a missing API key
killing headless claude minutes later in a log nobody reads, the app polling
the wrong AIASSISTANT_HOME. HANDOFF §2.15 requires "0 new cards" and
"silently dead" to be distinguishable — this module is the user-facing tool
for that.

    python3 -m act.doctor          # full run (ends with one cheap live claude call)
    python3 -m act.doctor --fast   # skip the live auth probe (spends no tokens)
    bash install.sh --check        # same as the full run

One line per check — symptom first, then the one-line fix:

    [ ok ] actd: running (pid 4242)
    [FAIL] dashboard: stale (generated 23 min ago) - actd is not writing; ...
           fix: launchctl list | grep aiassistant; tail -20 ~/Library/Logs/zelin-ai-assistant/actd.launchd.log

Never raises; exit code = number of FAILs (0 = healthy). Warnings cover
optional or degraded-but-working states (no Obsidian vault, recording off,
subscription-auth mode without a key file, ...).

Every touch of the machine goes through the :class:`Probes` dataclass so
tests can inject fakes (tests/test_doctor.py); the real implementations are
the defaults.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from act import llm
from act.lib import (
    board_server,
    config,
    deploy_state,
    failures,
    heartbeat,
    install_report,
    platform,
    secrets,
    taskscheduler,
    version as version_lib,
)

OK = "ok"
WARN = "warn"
FAIL = "fail"

ACTD_LABEL = "com.zelin.aiassistant.actd"      # launchd label (macOS)
SYNCD_LABEL = "com.zelin.aiassistant.syncd"
# §54 看板 server（`python3 -m server`）——v0.48.18 起由 launchd 托管而非壳 app
# 的子进程（GUI app 是子进程的 TCC responsible process，壳没有磁盘授权）。
# 探针与判决的纯逻辑住 act/lib/board_server.py（doctor.py 的文件上限，防腐 #1）。
SERVER_LABEL = board_server.LABEL
# 常驻 agent（模板 KeepAlive=true）：进程一退出 launchd 就在 ThrottleInterval 后
# 再拉起。这类 label「已加载、无 pid、上次退出码非 0」= 正在 crash-loop（每个周期
# 死一次），不是周期性 agent 的「上次跑失败一次」——FAIL（§55；§56.3 的回滚判据
# 由此看见 syncd / server 被新版本弄坏）。集合与 act/launchd/*.plist 的 KeepAlive
# 键逐字一致，tests/test_doctor.py 钉住漂移。
RESIDENT_LABELS = frozenset({ACTD_LABEL, SYNCD_LABEL, SERVER_LABEL})
ACTD_UNIT = "zelin-actd.service"               # systemd --user unit (Linux)
SERVER_UNIT = board_server.UNIT                # §54 board server (Linux mirror)
ACTD_TASK = taskscheduler.TASK_PATH_PREFIX + "actd"  # schtasks TaskName (Windows)
# Resident systemd services doctor expects up (the rest are timer-driven
# oneshots that are correctly inactive between fires — the timer is the signal).
SYSTEMD_RESIDENT = ("zelin-actd.service", "zelin-webui.service", SERVER_UNIT)


def _installer() -> str:
    """The installer to point fixes at on this OS."""
    if platform.is_darwin():
        return "install.sh"
    if platform.is_windows():
        return "install.ps1"
    return "install-linux.sh"


def _pick(zh: str, en: str) -> str:
    """§15 single language switch (act/lib/failures.pick) for the doctor's
    user-facing detail/fix prose (v0.42, audit #16). Classified rows already
    speak the UI language via the Swift FailureCatalog; this covers the
    unclassified ones. Commands, paths and technical tokens stay English
    inside BOTH variants — they are commands, not prose."""
    return failures.pick(zh, en)

# cron ingest chain fires every 30 min; a probe older than this means either
# the chain stopped firing or it comes from an install predating the probe.
CRON_PROBE_FRESH_SECONDS = 2 * 3600
CRON_PROBE_PATH = config.STATE_DIR / "cron_probe.json"
# §17 D19: the installer's digest line never passes --now; a crontab line
# that does is the pre-D19 Monday form (or a hand edit) and forces a card
# every fire, past digest.frequency.
_LEGACY_DIGEST_NOW_RE = re.compile(r"act\.digest\s+--now\b")

# actd rewrites dashboard.json every ~10s pass; anything older than this means
# the daemon is not writing (same threshold as the app's staleness banner).
DASHBOARD_FRESH_SECONDS = 90
# the export cron fires every 30 min while recording; 2h with no db write
# means the capture engine is stopped.
SCREENPIPE_STALE_SECONDS = 2 * 3600
MIN_PYTHON = (3, 9)
_PROBE_TIMEOUT = 90  # ceiling for the live claude call
# §59 model liveness: one "ok" per explicit knob; a model that exists answers
# in seconds, one that does not is rejected before any generation.
_MODEL_PROBE_TIMEOUT = 60


# --------------------------------------------------------------------------- #
# Probes — every external effect, injectable for tests
# --------------------------------------------------------------------------- #
def _run(cmd: List[str], env: Optional[dict] = None,
         timeout: Optional[float] = _PROBE_TIMEOUT) -> Tuple[int, str]:
    """(exit code, combined stdout+stderr). Never raises: 124 timeout, 127 spawn error."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, env=env,
                              stdin=subprocess.DEVNULL)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timed out after %ss" % timeout
    except OSError as exc:
        return 127, str(exc)


def _launchctl_list() -> str:
    # via the OS seam: "" off-macOS (the agents then honestly read unregistered)
    return platform.service_list_text()


def _crontab() -> str:
    rc, out = _run(["crontab", "-l"], timeout=10)
    return out if rc == 0 else ""


def _installed_actd_path_env() -> Optional[str]:
    """The PATH the resident daemon actually runs with — read from the
    INSTALLED unit, not the repo template: what the installer rendered is what
    the service manager exports.

    darwin: ~/Library/LaunchAgents/<label>.plist (<key>PATH</key>).
    linux:  ~/.config/systemd/user/zelin-actd.service (Environment=PATH=).
    windows: None — the task's PATH is embedded in a `powershell -Command`
    action, not a readable env stanza; the daemon-claude check degrades to a
    plain PATH probe there (the login-shell comparison is macOS/Linux-only)."""
    if platform.is_windows():
        return None
    if platform.is_darwin():
        plist = Path.home() / "Library" / "LaunchAgents" / (ACTD_LABEL + ".plist")
        try:
            text = plist.read_text(encoding="utf-8")
        except OSError:
            return None
        m = re.search(r"<key>PATH</key>\s*<string>([^<]+)</string>", text)
        return m.group(1) if m else None
    unit = Path.home() / ".config" / "systemd" / "user" / ACTD_UNIT
    try:
        text = unit.read_text(encoding="utf-8")
    except OSError:
        return None
    # last Environment=PATH= wins, mirroring systemd's own override order
    found = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("Environment=PATH="):
            found = s[len("Environment=PATH="):].strip()
    return found


def _login_shell_claude() -> Optional[str]:
    """The claude the USER'S login shell resolves (same probe install.sh uses).
    None when the shell probe fails or finds nothing."""
    shell = os.environ.get("SHELL") or "/bin/zsh"
    rc, out = _run([shell, "-lc", "command -v claude"], timeout=15)
    if rc != 0 or not out.strip():
        return None
    last = out.strip().splitlines()[-1].strip()
    return last if last.startswith("/") else None


def _installed_plist_text(label: str) -> Optional[str]:
    """已安装（非模板）plist 的原文；None = 该 agent 没装。§55 迁移探测用。"""
    p = Path.home() / "Library" / "LaunchAgents" / (label + ".plist")
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def _launchd_log_paths(short: str):
    """agent 自管日志的候选路径：v0.48 起住 ~/Library/Logs/，旧址兜底。"""
    return (Path.home() / "Library" / "Logs" / "zelin-ai-assistant"
            / ("%s.launchd.log" % short),
            config.HOME / "state" / ("%s.launchd.log" % short))


def _launchd_log_tail(short: str) -> str:
    """agent 自管日志的末尾；"" = 读不到。"""
    for p in _launchd_log_paths(short):
        try:
            return p.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            continue
    return ""


def _launchd_log_mtime(short: str) -> Optional[float]:
    """agent 自管日志的 mtime；None = 读不到（launchd 的 stderr 没有时间戳，§56.3 第 1 步）。"""
    for p in _launchd_log_paths(short):
        try:
            return p.stat().st_mtime
        except OSError:
            continue
    return None


LABEL_PREFIX = "com.zelin.aiassistant."


def _installed_agent_labels() -> List[str]:
    """~/Library/LaunchAgents 里带我们前缀的 plist 文件名（label）——孤儿探测
    用（§55）：有文件没模板 = 退役 agent 的残留，下次登录还会被 launchd 装载。"""
    d = Path.home() / "Library" / "LaunchAgents"
    try:
        return sorted(p.stem for p in d.glob(LABEL_PREFIX + "*.plist"))
    except OSError:
        return []


def _pid_alive(pid: int) -> Optional[bool]:
    """进程是否存在；None = 本平台判不了（Windows 的 os.kill(pid, 0) 会
    TerminateProcess，绝不能拿来探活）。"""
    if platform.is_windows():
        return None
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # 存在但不是我们的——对「活着」的判断足够
    except (OSError, TypeError, ValueError):
        return None


# 两条 ModuleNotFoundError 在 `launchctl list` 里长得一模一样，修复动作却相反
# （§55）。断言 PyYAML 而不读日志，正是 2026-08-31 那次把排查带偏几个小时的原因：
# /opt/homebrew/bin/python3 全程都装着 PyYAML，缺的是对 repo 的读权限。
MISSING_ACT = "act"      # 解释器看不见 repo（TCC per-binary / PYTHONPATH 错）
MISSING_YAML = "yaml"    # 守护进程唯一的非 stdlib 依赖没装


def _log_missing_module(tail: str) -> Optional[str]:
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


def _launchd_claude_probe(claude_bin: str, cwd: str, budget_s: float = 20.0) -> dict:
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
        return {"state": "unavailable", "rc": None, "text": "probe disabled or no launchd"}
    if not shutil.which("launchctl"):
        return {"state": "unavailable", "rc": None, "text": "launchctl not found"}
    tmp = Path(tempfile.mkdtemp(prefix="zai-claude-probe-"))
    label = "com.zelin.aiassistant.claudeprobe.%d" % os.getpid()
    verdict = tmp / "verdict"
    plist = tmp / "probe.plist"
    domain = "gui/%d" % os.getuid()
    try:
        plist.write_bytes(plistlib.dumps({
            "Label": label,
            "ProgramArguments": ["/bin/sh", "-c", _CLAUDE_PROBE_SH, "_",
                                 cwd, claude_bin, str(verdict)],
            "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            "WorkingDirectory": str(Path.home()),
            "RunAtLoad": True,
            # kill the whole group on bootout so a hung claude does not linger
            "AbandonProcessGroup": False,
        }))
        subprocess.run(["launchctl", "bootout", "%s/%s" % (domain, label)],
                       capture_output=True, timeout=10)
        boot = subprocess.run(["launchctl", "bootstrap", domain, str(plist)],
                              capture_output=True, text=True, timeout=15)
        if boot.returncode != 0:
            return {"state": "unavailable", "rc": None,
                    "text": "launchd refused the probe job: %s"
                            % (boot.stderr or boot.stdout).strip()[-200:]}
        deadline = time.time() + budget_s
        while time.time() < deadline and not verdict.exists():
            time.sleep(0.25)
        if not verdict.exists():
            started = (tmp / "verdict.stage").exists()
            return {"state": "hang" if started else "unavailable", "rc": None,
                    "text": ("claude started under launchd but produced no exit within "
                             "%.0f s" % budget_s) if started
                    else "launchd ran nothing observable within %.0f s" % budget_s}
        raw = verdict.read_text(encoding="utf-8", errors="replace").strip()
        out = ""
        try:
            out = (tmp / "verdict.out").read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        if raw.startswith("cd_failed"):
            return {"state": "cd_failed", "rc": None, "text": "sh could not cd into %s" % cwd}
        try:
            rc = int(raw.split(":", 1)[1])
        except (IndexError, ValueError):
            return {"state": "unavailable", "rc": None, "text": "unreadable verdict %r" % raw[:60]}
        return {"state": "ok" if rc == 0 else "failed", "rc": rc, "text": out.strip()[-600:]}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"state": "unavailable", "rc": None, "text": "probe error: %r" % (exc,)}
    finally:
        try:
            subprocess.run(["launchctl", "bootout", "%s/%s" % (domain, label)],
                           capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
        shutil.rmtree(str(tmp), ignore_errors=True)


@dataclass
class Probes:
    which: Callable[[str], Optional[str]] = shutil.which
    run: Callable[..., Tuple[int, str]] = _run
    launchctl_list: Callable[[], str] = _launchctl_list
    crontab: Callable[[], str] = _crontab
    now: Callable[[], float] = time.time
    # None -> derive from act/launchd/*.plist basenames under AIASSISTANT_HOME
    launchd_labels: Optional[List[str]] = None
    # None -> derive from act/systemd (resident services + *.timer); Linux only
    systemd_units: Optional[List[str]] = None
    # None -> derive from act/tasksched (full \ZelinAIAssistant\ names); Windows only
    scheduled_tasks: Optional[List[str]] = None
    screenpipe_db: Path = field(
        default_factory=lambda: Path.home() / ".screenpipe" / "db.sqlite")
    legacy_key_path: Path = field(
        default_factory=lambda: Path("~/.config/anthropic-key.txt").expanduser())
    # daemon-vs-shell claude comparison (the 2026-07-08 two-installs incident)
    daemon_path_env: Callable[[], Optional[str]] = _installed_actd_path_env
    login_shell_claude: Callable[[], Optional[str]] = _login_shell_claude
    # §55 迁移探测：label → 已安装 plist 原文（None = 没装）；tests 注入保持 hermetic
    installed_plist_text: Callable[[str], Optional[str]] = _installed_plist_text
    # §55 日志归因：short name → 该 agent 自管日志的末尾（"" = 读不到）
    launchd_log_tail: Callable[[str], str] = _launchd_log_tail
    # §55 孤儿探测：~/Library/LaunchAgents 里带前缀的 plist label（文件面）
    installed_agent_labels: Callable[[], List[str]] = _installed_agent_labels
    # §47.4 心跳：state/actd.heartbeat 的读取 + 进程探活（tests 注入保持 hermetic）
    heartbeat_read: Callable[[], Optional[dict]] = heartbeat.read
    pid_alive: Callable[[int], Optional[bool]] = _pid_alive
    # §55 第三幕：在一次性 launchd job 里跑 `claude --version`（cwd = 默认工作
    # repo）——终端看不见的 TCC 失败只能这样问出来；tests 注入，绝不真起 launchd
    launchd_claude_probe: Callable[[str, str], dict] = _launchd_claude_probe
    # §59 (D22)：Claude Code 全局默认模型（~/.claude/settings.json `model`）——
    # follow 模式继承的就是它；tests 注入，绝不读开发者的真文件
    claude_code_settings: Callable[[], dict] = llm.read_claude_code_default_model
    # §54 看板 server：回环 /api/health 探针（port → verdict dict）；tests 注入，
    # 默认实现在 AIASSISTANT_HTTP_PROBE=0 下自报 unavailable（行不出）
    board_health: Callable[[int], dict] = board_server.health_probe
    # §56.4 HOME 镜像（auto-deploy 自己的真源，TCC 永不拦 $HOME）：`launchd volume
    # access` 行读它的 unattended_* 三元组；tests 注入保持 hermetic
    deploy_mirror_read: Callable[[], Optional[dict]] = deploy_state.read_mirror
    # §56.3 第 1 步日志证据：launchd stderr 文件的 mtime（它没有时间戳）
    launchd_log_mtime: Callable[[str], Optional[float]] = _launchd_log_mtime
    version_status: Callable[[], dict] = version_lib.status_probe  # §56.1 stamp vs describe；tests 注入（沙箱非 git）


@dataclass
class CheckResult:
    name: str
    status: str  # OK | WARN | FAIL
    detail: str  # the symptom, one line
    fix: str = ""  # one-line fix (empty for OK)
    # §25 classification (act/lib/failures.py) — empty when unclassified; the
    # app maps action_id to a one-click repair, falling back to the raw fix.
    failure_id: str = ""
    action_id: str = ""

    def with_failure(self, failure_id: str) -> "CheckResult":
        """Attach a catalog id (and its action) to a non-ok result."""
        self.failure_id = failure_id
        self.action_id = failures.action_id(failure_id) or ""
        return self


def _resolve_key(probes: Probes) -> Tuple[Optional[str], str]:
    """Anthropic key content per CONTRACT §19 order, plus its source label.

    Goes through act/lib/secrets so the first-token-line semantics match every
    runtime consumer exactly — a whole-file read of a multiline key file used
    to make the live probe FAIL on a key that works everywhere else.
    """
    val = secrets.read_secret(secrets.ANTHROPIC_API_KEY_FILE)
    if val:
        return val, "config/secrets/anthropic-api-key.txt"
    val = secrets._read_path(probes.legacy_key_path)
    if val:
        return val, str(probes.legacy_key_path)
    return None, ""


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def _check_home(probes: Probes):
    if not (config.HOME / "install.sh").exists():
        return CheckResult(
            "AIASSISTANT_HOME", FAIL,
            _pick("%s 不像是仓库目录（没有 install.sh）——下面所有路径都由它推导",
                  "%s does not look like the repo (no install.sh) - every path below derives from it") % config.HOME,
            _pick("export AIASSISTANT_HOME=<你的 clone>，或运行 bash <你的 clone>/install.sh（会写入 home 指针）",
                  "export AIASSISTANT_HOME=<your clone>, or run bash <your clone>/install.sh (writes the home pointer)"))
    return CheckResult("AIASSISTANT_HOME", OK, str(config.HOME))


def _check_version(probes: Probes):
    return CheckResult("version", *version_lib.doctor_row(probes.version_status()))


def _check_claude(probes: Probes):
    path = probes.which("claude")
    if not path:
        return CheckResult(
            "claude CLI", FAIL,
            "not on PATH - nothing can extract, expand or execute cards",
            "install Claude Code (https://claude.com/claude-code), then re-run this check",
        ).with_failure("claude_cli_missing")
    rc, out = probes.run([path, "--version"], timeout=15)
    if rc != 0:
        return CheckResult(
            "claude CLI", WARN,
            _pick("%s 存在但 `claude --version` 失败（%s）",
                  "%s exists but `claude --version` failed (%s)") % (path, out.strip()[:80]),
            _pick("重装 Claude Code", "reinstall Claude Code"))
    version = out.strip().splitlines()[0][:60] if out.strip() else "unknown version"
    return CheckResult("claude CLI", OK, "%s (%s)" % (path, version))


def _version_of(probes: Probes, claude_path: str) -> str:
    rc, out = probes.run([claude_path, "--version"], timeout=15)
    return out.strip().splitlines()[0][:60] if rc == 0 and out.strip() else ""


def _check_daemon_claude(probes: Probes):
    """launchd/cron can resolve a DIFFERENT claude than the login shell — a
    second, outdated install ranked first on the daemon PATH once broke every
    dispatch with "unknown option '--bg'", retrying forever behind a generic
    notification (2026-07-08). Compare the binary the installed actd plist's
    PATH resolves against the login shell's, and probe --bg support."""
    path_env = probes.daemon_path_env()
    if not path_env:
        if platform.is_darwin():
            where = "launchd plist"
        elif platform.is_windows():
            where = "scheduled task"
        else:
            where = "systemd unit"
        return CheckResult(
            "daemon claude", WARN,
            _pick("actd 的 %s 未安装（或没带 PATH）——无法确认后台服务用的是哪个 claude",
                  "actd %s not installed (or carries no PATH) - cannot verify "
                  "which claude the daemon runs") % where,
            _pick("bash %s（重渲染服务配置，把你 shell 的 claude 目录排在 PATH 最前）",
                  "bash %s (renders the agent with your shell's claude dir first on PATH)")
            % _installer())
    daemon_claude = shutil.which("claude", path=path_env)
    if not daemon_claude:
        return CheckResult(
            "daemon claude", FAIL,
            "no claude anywhere on the daemon PATH - dispatch and radar extraction cannot run",
            "install Claude Code, then: bash %s (re-renders the daemon PATH)" % _installer(),
        ).with_failure("claude_cli_missing")
    daemon_ver = _version_of(probes, daemon_claude)
    shell_claude = probes.login_shell_claude()
    if (shell_claude and os.path.realpath(shell_claude) != os.path.realpath(daemon_claude)):
        shell_ver = _version_of(probes, shell_claude)
        if daemon_ver != shell_ver:
            return CheckResult(
                "daemon claude", FAIL,
                "the daemon runs %s (%s) but your shell runs %s (%s) - two installs; "
                "background dispatch uses the old one" % (
                    daemon_claude, daemon_ver or "version unknown",
                    shell_claude, shell_ver or "version unknown"),
                "update or remove the outdated copy, then: bash %s "
                "(re-renders the daemon PATH with your shell's claude first)" % _installer(),
            ).with_failure("claude_cli_outdated")
    # --bg is what dispatch hangs off. Two-step probe: `--help` (side-effect
    # free; 2.1.206 lists "--bg, --background") and, ONLY when help lacks it,
    # a bare `claude --bg` whose error must carry the exact §25 outdated
    # signature — so a reformatted future help page alone can never false-FAIL.
    rc, help_out = probes.run([daemon_claude, "--help"], timeout=15)
    if rc == 0 and help_out.strip() and "--bg" not in help_out:
        rc2, bg_out = probes.run([daemon_claude, "--bg"], timeout=15)
        if rc2 != 0 and failures.classify(bg_out) == "claude_cli_outdated":
            return CheckResult(
                "daemon claude", FAIL,
                "%s (%s) does not support --bg - every dispatch fails with "
                "\"unknown option '--bg'\"" % (daemon_claude, daemon_ver or "version unknown"),
                "update Claude Code (or remove this outdated copy), then: bash %s" % _installer(),
            ).with_failure("claude_cli_outdated")
    same = shell_claude and os.path.realpath(shell_claude) == os.path.realpath(daemon_claude)
    return CheckResult(
        "daemon claude", OK,
        "%s (%s)%s" % (daemon_claude, daemon_ver or "version unknown",
                       " - same as your login shell" if same else ""))


def _check_runtime_python(probes: Probes):
    rj = config.HOME / "config" / "runtime.json"
    if not rj.exists():
        return CheckResult(
            "daemon python", WARN,
            _pick("config/runtime.json 缺失——launchd 服务和 App 只能靠猜解释器",
                  "config/runtime.json missing - launchd agents and the app guess at an interpreter"),
            _pick("bash install.sh（重新探测并固定解释器）",
                  "bash install.sh (re-detects and pins the interpreter)"))
    try:
        py = str(json.loads(rj.read_text(encoding="utf-8")).get("python") or "")
    except Exception:  # noqa: BLE001 - malformed file is just another symptom
        py = ""
    if not py or not os.access(py, os.X_OK):
        return CheckResult(
            "daemon python", FAIL,
            _pick("config/runtime.json 指向一个不可执行的 python（%s）",
                  "config/runtime.json points at a non-executable python (%s)") % (py or "empty"),
            _pick("bash install.sh（重新探测解释器）",
                  "bash install.sh (re-detects the interpreter)"))
    rc, out = probes.run(
        [py, "-c", "import sys, yaml; print('%d.%d' % sys.version_info[:2])"],
        timeout=20)
    if rc != 0:
        return CheckResult(
            "daemon python", FAIL,
            _pick("%s 无法 `import yaml`——actd/radar 在 launchd 下会立即退出",
                  "%s cannot `import yaml` - actd/radar exit immediately under launchd") % py,
            "%s -m pip install --user pyyaml   (PEP 668 python: add --break-system-packages)" % py)
    ver = out.strip().splitlines()[-1] if out.strip() else ""
    try:
        if tuple(int(x) for x in ver.split(".")) < MIN_PYTHON:
            return CheckResult(
                "daemon python", FAIL,
                _pick("%s 是 Python %s（需要 >= %s）",
                      "%s is Python %s (need >= %s)") % (py, ver, ".".join(map(str, MIN_PYTHON))),
                "AIASSISTANT_PYTHON=<newer python3> bash install.sh")
    except ValueError:
        pass
    return CheckResult("daemon python", OK,
                       "%s (Python %s, PyYAML importable)" % (py, ver))


def _check_config(probes: Probes):
    if not config.CONFIG_PATH.exists():
        return CheckResult(
            "config.yaml", WARN,
            _pick("缺失——正在用 config.example.yaml 的默认值运行（没有 vault、没有 watch 名单）",
                  "missing - running on config.example.yaml defaults (no vault, no watched people)"),
            "cp config.example.yaml config.yaml && edit sources.*")
    if config.yaml is None:
        return CheckResult(
            "config.yaml", FAIL,
            _pick("这个 python 缺 PyYAML——config 无法解析",
                  "PyYAML missing for this python - config cannot be parsed"),
            "%s -m pip install --user pyyaml" % sys.executable)
    try:
        config.yaml.safe_load(config.CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - report, don't crash
        first = str(exc).splitlines()[0][:80]
        return CheckResult(
            "config.yaml", FAIL,
            "invalid YAML (%s) - every component silently falls back to defaults" % first,
            "fix the syntax; verify: python3 -c \"import yaml; yaml.safe_load(open('config.yaml'))\"",
        ).with_failure("config_invalid")
    return CheckResult("config.yaml", OK, str(config.CONFIG_PATH))


def _check_anthropic_key(probes: Probes):
    sec = secrets.SECRETS_DIR / secrets.ANTHROPIC_API_KEY_FILE
    key, source = _resolve_key(probes)
    if key and source.startswith("config/secrets"):
        # NTFS has no POSIX mode bits — chmod 600 is a no-op there, so the
        # world-readable check would false-WARN on Windows. Skip it and note
        # that access control is via NTFS ACLs instead (docs/WINDOWS.md).
        if platform.is_windows():
            return CheckResult("anthropic key", OK,
                               "config/secrets/anthropic-api-key.txt (NTFS ACL; no POSIX 0600)")
        mode = stat.S_IMODE(sec.stat().st_mode)
        if mode & 0o077:
            return CheckResult(
                "anthropic key", WARN,
                _pick("config/secrets/anthropic-api-key.txt 其他用户也能读（mode %o）",
                      "config/secrets/anthropic-api-key.txt is readable by other users (mode %o)") % mode,
                "chmod 600 '%s'" % sec)
        return CheckResult("anthropic key", OK,
                           "config/secrets/anthropic-api-key.txt (0600)")
    if key:
        return CheckResult(
            "anthropic key", OK,
            "legacy %s (§19 fallback still honored)" % source,
            "consider migrating: paste the key in the app's Settings window")
    return CheckResult(
        "anthropic key", WARN,
        _pick("没有 key 文件——headless claude（cron/launchd）会退回 CLI 凭据"
              "（subscription-auth 模式），daemon 会话通常读不到它",
              "no key file - headless claude (cron/launchd) falls back to CLI credentials "
              "(subscription-auth mode), which daemon sessions usually cannot read"),
        _pick("在 App 的设置（Settings）页粘贴你的 API key（写入 config/secrets/anthropic-api-key.txt）",
              "paste your API key in the app's Settings window (writes config/secrets/anthropic-api-key.txt)"))


def _check_state_dirs(probes: Probes):
    dirs = (config.STATE_DIR, config.INBOX_DIR, config.LOG_DIR)
    missing = [d for d in dirs if not d.is_dir()]
    if missing:
        return CheckResult(
            "state dirs", FAIL,
            _pick("缺失：%s——actd/capture 无法持久化任何东西",
                  "missing: %s - actd/capture cannot persist anything") % ", ".join(map(str, missing)),
            _pick("bash install.sh（创建 state/ + state/inbox/）",
                  "bash install.sh (creates state/ + state/inbox/)"))
    blocked = [d for d in dirs if not os.access(d, os.W_OK)]
    if blocked:
        return CheckResult(
            "state dirs", FAIL,
            _pick("不可写：%s", "not writable: %s") % ", ".join(map(str, blocked)),
            "chown -R $(whoami) '%s'" % config.STATE_DIR)
    return CheckResult("state dirs", OK, "%s writable" % config.STATE_DIR)


def _check_launchd(probes: Probes):
    labels = probes.launchd_labels
    if labels is None:
        labels = sorted(p.stem for p in (config.HOME / "act" / "launchd").glob("*.plist"))
    if not labels:
        return CheckResult(
            "launchd agents", WARN,
            _pick("act/launchd 下没有 plist 模板——checkout 不完整？",
                  "no plist templates under act/launchd - incomplete checkout?"),
            "git -C '%s' checkout act/launchd" % config.HOME)
    table = {}
    for line in probes.launchctl_list().splitlines():
        parts = line.split()
        if len(parts) >= 3:
            table[parts[2]] = (parts[0], parts[1])  # (pid, last exit status)
    results = []
    for label in labels:
        short = label.rsplit(".", 1)[-1]
        # actd is the resident daemon the whole product hangs off; the radar
        # agents are periodic and recommended via cron anyway (TCC), so their
        # absence only warns.
        if label not in table:
            results.append(CheckResult(
                short, FAIL if label == ACTD_LABEL else WARN,
                "%s not registered with launchd%s" % (
                    label, " - cards never move" if label == ACTD_LABEL else ""),
                "bash install.sh (renders + loads the agents)",
            ).with_failure("agent_unloaded"))
            continue
        pid, status = table[label]
        if pid != "-":
            results.append(CheckResult(short, OK, "running (pid %s)" % pid))
        elif status == "0":
            results.append(CheckResult(short, OK, "loaded (last run exited 0)"))
        else:
            # A KeepAlive agent with no pid and a non-zero exit is crash-looping
            # (launchd respawns it every ThrottleInterval, it dies again) — FAIL
            # for every resident label, not just actd: a broken syncd is the
            # phone/web board gone, and only FAIL rows drive §56's rollback.
            # Periodic agents (RunAtLoad radars, weeklydigest, autodeploy)
            # exiting non-zero once is a WARN — one network blip would
            # otherwise roll a deploy back.
            severity = FAIL if label in RESIDENT_LABELS else WARN
            loop = " (KeepAlive: crash loop)" if label in RESIDENT_LABELS else ""
            # 名出真因，别猜（§55）：读它自己的日志，把两条 ModuleNotFoundError
            # 分开——'act' = 解释器看不见 repo，'yaml' = 缺 PyYAML。
            missing = _log_missing_module(probes.launchd_log_tail(short))
            if missing == MISSING_ACT:
                detail = ("loaded but exits with status %s%s - its log says "
                          "\"No module named 'act'\": the interpreter cannot see "
                          "the repo (PyYAML is NOT the problem)" % (status, loop))
                fix = _INTERPRETER_BLIND_FIX
            elif missing == MISSING_YAML:
                detail = ("loaded but exits with status %s%s - its log says "
                          "\"No module named 'yaml'\": PyYAML is missing for the "
                          "daemon python" % (status, loop))
                fix = "%s -m pip install --user --break-system-packages pyyaml" % (
                    _pinned_interpreter(probes) or "python3")
            else:
                detail = "loaded but its process exits with status %s%s" % (status, loop)
                fix = ("tail -20 ~/Library/Logs/zelin-ai-assistant/%s.launchd.log"
                       " (pre-v0.48 installs: state/%s.launchd.log)"
                       "  # usual causes: the interpreter cannot see the repo"
                       " (\"No module named 'act'\"), PyYAML missing"
                       " (\"No module named 'yaml'\"), missing API key"
                       % (short, short))
            results.append(
                CheckResult(short, severity, detail, fix)
                .with_failure("interpreter_blind" if missing == MISSING_ACT
                              else "agent_unloaded"))
    return results


# launchd 在 spawn 前触碰的 plist 键——任何一个指向 repo 都是 §55 之前的渲染
_PLIST_SPAWN_PATH_KEYS = ("StandardOutPath", "StandardErrorPath",
                          "WorkingDirectory")
# repo 路径唯一允许出现的地方（§55）——这两个值必须是 PHYSICAL 路径
_PLIST_REPO_PATH_KEYS = ("AIASSISTANT_HOME", "PYTHONPATH")

_INSTALL_SH_FIX = ("bash install.sh  # re-renders ALL agents; the app's"
                   " one-click repair only re-renders actd")

# 解释器「看得见 yaml、看不见 repo」时的唯一正确动作（§55）。install.sh 自
# 起用 launchd 真实探针挑解释器（§55 第二道闸门），所以重跑就会换掉瞎的
# 那个；换不掉
# （比如只有一个 python）时才轮到手动授 FDA。
_INTERPRETER_BLIND_FIX = (
    "bash install.sh  # now probes launchd viability and picks an interpreter"
    " that can actually read the repo; if it still fails, grant Full Disk"
    " Access to that interpreter binary in System Settings > Privacy & Security")


def _pinned_interpreter(probes: Probes) -> str:
    """config/runtime.json 里 pin 的解释器；"" = 没 pin / 读不了。"""
    try:
        rj = config.HOME / "config" / "runtime.json"
        return str(json.loads(rj.read_text(encoding="utf-8")).get("python") or "")
    except Exception:  # noqa: BLE001 - 探针不许崩（宪法第 11 条）
        return ""


def _crashing_agents(probes: Probes) -> set:
    """当前真的在崩的 agent（short name）：已注册、没有 PID、上次退出码非 0。

    日志是历史，`launchctl list` 才是现状——KeepAlive 治好之后旧日志还躺在那
    里，只看日志会给一个跑得好好的 agent 报故障。
    """
    crashing = set()
    try:
        lines = probes.launchctl_list().splitlines()
    except Exception:  # noqa: BLE001 - 探针不许崩（宪法第 11 条）
        # launchctl 坏了是 _check_launchd 的发现，不该连累路径检查；此时只是
        # 确认不了「此刻在崩」，于是症状 4 沉默（少报 > 误报）。
        return crashing
    for line in lines:
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "-" and parts[1] not in ("0", "Status"):
            crashing.add(parts[2].rsplit(".", 1)[-1])
    return crashing


def _plist_string(text: str, key: str) -> Optional[str]:
    m = re.search(r"<key>%s</key>\s*<string>([^<]+)</string>" % key, text)
    return m.group(1) if m else None


def _plist_interpreter(text: str) -> Optional[str]:
    """ProgramArguments[0] —— launchd 真正 exec 的那个二进制。"""
    m = re.search(r"<key>ProgramArguments</key>\s*<array>\s*<string>([^<]+)</string>",
                  text)
    return m.group(1) if m else None


def _check_launchd_paths(probes: Probes):
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
    labels = probes.launchd_labels
    if labels is None:
        labels = sorted(p.stem for p in (config.HOME / "act" / "launchd").glob("*.plist"))
    repo = str(config.HOME).rstrip("/")
    stale, symlinked, seen_any = [], [], False
    bad_py, verdicts = {}, {}
    blind_py = {}
    crashing = _crashing_agents(probes)
    for label in labels:
        text = probes.installed_plist_text(label)
        if not text:
            continue  # 没装——_check_launchd 已经报 unregistered
        seen_any = True
        short = label.rsplit(".", 1)[-1]
        spawn = [_plist_string(text, key) for key in _PLIST_SPAWN_PATH_KEYS]
        if any(v == repo or (v or "").startswith(repo + "/") for v in spawn):
            stale.append(short)
        elif any(_symlink_shaped(_plist_string(text, key))
                 for key in _PLIST_REPO_PATH_KEYS):
            symlinked.append(short)
        py = _plist_interpreter(text)
        if py:
            # 同一个解释器只探一次——agent 有五个，别起五次进程
            if py not in verdicts:
                verdicts[py] = _interpreter_ok(probes, py)
            if not verdicts[py]:
                bad_py.setdefault(py, []).append(short)
            elif (short in crashing
                  and _log_missing_module(probes.launchd_log_tail(short))
                  == MISSING_ACT):
                # yaml 过了、路径也对，agent 此刻在崩、日志说没有 act
                # = 解释器读不到 repo。三个条件缺一不可：只看日志会把治好之后
                # 的陈旧日志当成现故障。
                blind_py.setdefault(py, []).append(short)
    if not seen_any:
        return []
    home = str(Path.home()).rstrip("/")
    severity = WARN if repo == home or repo.startswith(home + "/") else FAIL
    if stale:
        paths = CheckResult(
            "launchd paths", severity,
            "installed plist still points at the repo (pre-v0.48 render): %s%s"
            % (", ".join(stale),
               "" if severity == WARN
               else " - repo is on an external volume; launchd refuses to spawn (78)"),
            _INSTALL_SH_FIX)
    elif symlinked:
        paths = CheckResult(
            "launchd paths", severity,
            "installed plist carries a symlinked repo path (%s): %s%s"
            % (repo, ", ".join(symlinked),
               "" if severity == WARN
               else " - launchd is TCC-denied through that shape; the agents"
                    " exit with \"No module named 'act'\""),
            _INSTALL_SH_FIX)
    else:
        paths = CheckResult("launchd paths", OK,
                            "installed plists keep spawn-time paths out of the "
                            "repo and the repo path physical")
    if bad_py:
        named = ", ".join(sorted({a for agents in bad_py.values() for a in agents}))
        return [paths, CheckResult(
            "launchd python", FAIL,
            "the interpreter rendered into %s cannot `import yaml` (%s) - the "
            "agents exit before they log anything"
            % (named, ", ".join(sorted(bad_py))),
            _INSTALL_SH_FIX)]
    # 症状 4 只在路径干净时才报：路径本身坏的时候，重渲染就把两件事一起修了，
    # 多报一行只会让人先去授一个其实不需要的 FDA。
    if blind_py and paths.status == OK:
        named = ", ".join(sorted({a for agents in blind_py.values() for a in agents}))
        return [paths, CheckResult(
            "launchd python", FAIL,
            "%s imports yaml and the rendered paths are correct, yet %s still "
            "exit with \"No module named 'act'\" - that interpreter cannot READ "
            "the repo when launchd spawns it (macOS grants file access per "
            "binary, and launchd jobs do not inherit your terminal's grant)"
            % (", ".join(sorted(blind_py)), named),
            _INTERPRETER_BLIND_FIX).with_failure("interpreter_blind")]
    return [paths]


def _launchctl_table(probes: Probes) -> dict:
    """label → (pid, last exit status) from `launchctl list`; {} when it fails."""
    table = {}
    try:
        for line in probes.launchctl_list().splitlines():
            parts = line.split()
            if len(parts) >= 3:
                table[parts[2]] = (parts[0], parts[1])
    except Exception:  # noqa: BLE001 - 探针不许崩（宪法第 11 条）
        pass
    return table


def _check_launchd_orphans(probes: Probes):
    """§55 孤儿 agent：带我们前缀、但 act/launchd 里已没有模板的 label。

    2026-08-31 审计：v0.21 删掉的 imessageradar agent 又跑了 51 天、23,613 条
    traceback（14.5 MB 日志）——install.sh 的 RETIRED 卸载把 bootout 失败吞进
    `/dev/null`，而旧 doctor 只查有模板的 label，孤儿结构性不可见。两个面都
    扫：`launchctl list` 里已装载的（此刻在耗资源/刷日志 → FAIL）与
    ~/Library/LaunchAgents 里只剩文件的（下次登录复活 → WARN）。
    """
    labels = probes.launchd_labels
    if labels is None:
        labels = sorted(p.stem for p in (config.HOME / "act" / "launchd").glob("*.plist"))
    known = set(labels)
    loaded = sorted(lbl for lbl in _launchctl_table(probes)
                    if lbl.startswith(LABEL_PREFIX) and lbl not in known)
    try:
        on_disk = sorted(lbl for lbl in probes.installed_agent_labels()
                         if lbl.startswith(LABEL_PREFIX) and lbl not in known
                         and lbl not in loaded)
    except Exception:  # noqa: BLE001 - 探针不许崩
        on_disk = []
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


_FD_LIMIT_MIN = 4096   # anything below this is the launchd default territory


def _plist_number_of_files(text: str, key: str) -> Optional[int]:
    """`<key>KEY</key><dict>…<key>NumberOfFiles</key><integer>N</integer>…</dict>`
    — NumberOfFiles may sit anywhere inside the dict (a hand-edited plist
    with another limit first must not read as unset)."""
    m = re.search(r"<key>%s</key>\s*<dict>(.*?)</dict>" % key, text, re.S)
    if not m:
        return None
    n = re.search(r"<key>NumberOfFiles</key>\s*<integer>(\d+)</integer>", m.group(1))
    return int(n.group(1)) if n else None


def _check_launchd_fd_limit(probes: Probes):
    """§55 资源上限：已安装 actd plist 的 SoftResourceLimits.NumberOfFiles。

    launchd gui domain 给 job 的默认是 soft 256 / hard unlimited。模板只抬
    soft（余量）；**HardResourceLimits 不该出现**——它只会把 unlimited 压低
    （2026-09-01 实测：Soft+Hard 8192 → [8192, 8192]，只 Soft → [8192,
    unlimited]）。当晚 hotfix 的形状正是两把都设，而 8-31 的派发失败根本不是
    fd 问题（TCC，见 _check_launchd_claude）——所以这一行只说资源上限的事实，
    不再把 dispatch 失败归到它头上。
    """
    text = probes.installed_plist_text(ACTD_LABEL)
    if not text:
        return []   # 没装——_check_launchd 已经报 unregistered
    soft = _plist_number_of_files(text, "SoftResourceLimits")
    hard = _plist_number_of_files(text, "HardResourceLimits")
    if hard is not None:
        return CheckResult(
            "launchd fd limit", WARN,
            "installed actd plist sets HardResourceLimits.NumberOfFiles=%d - launchd's "
            "default hard limit is unlimited, so this only LOWERS the ceiling (the "
            "2026-08-31 hotfix shape; soft=%s)" % (hard, soft if soft is not None else "unset"),
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
        % (soft if soft is not None else "unset"),
        "bash install.sh  # re-renders the agents with SoftResourceLimits.NumberOfFiles",
    ).with_failure("fd_limit")


def _check_launchd_claude(probes: Probes):
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
    cwd = cfg.target_repo_path
    try:
        cwd = cwd.resolve()
    except OSError:
        pass
    if not cwd.is_dir():
        return []   # 默认工作 repo 不存在 — _check_target_repo 之类另有报法
    real_bin = claude_bin
    try:
        real_bin = str(Path(claude_bin).resolve())
    except OSError:
        pass
    res = probes.launchd_claude_probe(claude_bin, str(cwd))
    state = res.get("state")
    text = str(res.get("text") or "").strip()
    first = (text.splitlines() or [""])[0][:200] if text else ""
    fda_fix = ("System Settings > Privacy & Security > Full Disk Access: enable %s "
               "(again after every claude update), or move the repo under $HOME on the "
               "boot volume; then Stop > Discard & re-propose > approve the halted cards"
               % real_bin)
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
    # failed
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


# --------------------------------------------------------------------------- #
# §56.3 第 1 步：launchd 起的部署任务读不读得到外置卷上的 repo（TCC）
# --------------------------------------------------------------------------- #
AUTODEPLOY_LABEL = "com.zelin.aiassistant.autodeploy"
_VOLUME_ROW = "launchd volume access"


def _check_launchd_volume_access(probes: Probes):
    """§56.3 第 1 步：部署 agent 在 launchd 会话里能否读写 repo 所在的卷。doctor 跑在
    终端里、借着终端的 TCC 授权什么都读得到，所以本行**不探**，只读无人值守那一轮的
    证据（判决与文案：`deploy_state.unattended_verdict` / `volume_access_row`）。没装
    autodeploy plist、或它部署的是另一个 checkout → 无此行。"""
    text = probes.installed_plist_text(AUTODEPLOY_LABEL)
    if not text:
        return []
    repo = str(config.HOME)
    if not deploy_state.same_repo(_plist_string(text, "AIASSISTANT_HOME"), repo):
        return []
    interp = _plist_interpreter(text) or "<ProgramArguments[0] of the autodeploy plist>"
    verdict = deploy_state.unattended_verdict(
        probes.deploy_mirror_read(), repo, probes.launchd_log_tail("autodeploy"),
        probes.launchd_log_mtime("autodeploy"), probes.now())
    return _row_from(deploy_state.volume_access_row(verdict, interp, repo), _VOLUME_ROW)


def _symlink_shaped(value: Optional[str]) -> bool:
    """该路径是否经过 symlink（≠ 自己的 realpath）。不存在的路径原样返回，
    所以未安装/占位路径不会误报。"""
    if not value or not value.startswith("/"):
        return False
    trimmed = value.rstrip("/") or "/"
    try:
        return os.path.realpath(trimmed) != trimmed
    except OSError:  # noqa: BLE001 - 探针不许崩
        return False


def _interpreter_ok(probes: Probes, py: str) -> bool:
    """plist 里的解释器能不能真的 `import yaml`（§55）。"""
    if not py.startswith("/") or not os.access(py, os.X_OK):
        return False
    return probes.run([py, "-c", "import yaml"], timeout=20)[0] == 0


def _systemd_units() -> List[str]:
    """Expected checkable units: resident services + every timer template."""
    d = config.HOME / "act" / "systemd"
    residents = [u for u in SYSTEMD_RESIDENT if (d / u).exists()]
    timers = sorted(p.name for p in d.glob("*.timer"))
    return residents + timers


def _check_systemd(probes: Probes):
    """Linux service check — the systemd --user mirror of _check_launchd.

    Parses ``systemctl --user list-units`` (UNIT / LOAD / ACTIVE / SUB) that
    the OS seam returns off-macOS. actd is the resident daemon (FAIL if not
    active); the radar/digest work is timer-driven, so the *.timer being
    active is what we check (the oneshot .service is correctly inactive between
    fires). A failed-unit bullet (●) is stripped before splitting.
    """
    units = probes.systemd_units
    if units is None:
        units = _systemd_units()
    if not units:
        return CheckResult(
            "systemd units", WARN,
            _pick("act/systemd 下没有 unit 模板——checkout 不完整？",
                  "no unit templates under act/systemd - incomplete checkout?"),
            "git -C '%s' checkout act/systemd" % config.HOME)
    table = {}
    for line in probes.launchctl_list().splitlines():
        parts = line.replace("●", " ").split()  # drop the failed-unit bullet
        if len(parts) >= 4 and (parts[0].endswith(".service")
                                or parts[0].endswith(".timer")):
            table[parts[0]] = (parts[2], parts[3])  # (ACTIVE, SUB)
    results = []
    for unit in units:
        short = unit.rsplit(".", 1)[0].replace("zelin-", "")
        is_actd = unit == ACTD_UNIT
        severity = FAIL if is_actd else WARN
        if unit not in table:
            results.append(CheckResult(
                short, severity,
                "%s not registered with systemd --user%s" % (
                    unit, " - cards never move" if is_actd else ""),
                "bash install-linux.sh (renders + enables the user units)",
            ).with_failure("agent_unloaded"))
            continue
        active, sub = table[unit]
        if active == "active":
            results.append(CheckResult(short, OK, "active (%s)" % sub))
        elif active == "failed":
            results.append(CheckResult(
                short, severity,
                "%s failed to start" % unit,
                "journalctl --user -u %s -n 20  # usual causes: PyYAML missing "
                "for the daemon python, missing API key" % unit,
            ).with_failure("agent_unloaded"))
        else:  # inactive / dead — enabled unit that is not up
            results.append(CheckResult(
                short, severity,
                "%s is %s (not running)" % (unit, active),
                "systemctl --user enable --now %s" % unit,
            ).with_failure("agent_unloaded"))
    return results


def _scheduled_tasks() -> List[str]:
    """Expected checkable Windows tasks — full ``\\ZelinAIAssistant\\<leaf>``
    names derived from the act/tasksched/*.xml templates."""
    d = config.HOME / "act" / "tasksched"
    return [taskscheduler.full_task_name(p.name) for p in sorted(d.glob("*.xml"))]


def _parse_schtasks(text: str) -> dict:
    """Parse ``schtasks /query /fo LIST /v`` into {TaskName: {field: value}}.

    LIST output is one "Field: Value" block per task (verbose can emit a block
    per trigger; same Status each, so last-wins is correct). Only the first ":"
    splits key from value so clock values ("9:00:00 AM") survive intact.
    """
    table: dict = {}
    cur: dict = {}

    def flush() -> None:
        name = cur.get("TaskName")
        if name:
            table[name] = dict(cur)

    for raw in text.splitlines():
        if not raw.strip():
            flush()
            cur = {}
            continue
        key, sep, val = raw.partition(":")
        if sep:
            cur[key.strip()] = val.strip()
    flush()
    return table


def _check_scheduled_tasks(probes: Probes):
    """Windows service check — the Task Scheduler mirror of _check_launchd /
    _check_systemd.

    Parses ``schtasks /query /fo LIST /v`` (what the OS seam returns on Windows)
    filtered to our ``\\ZelinAIAssistant\\`` tasks. actd is the resident daemon
    (FAIL if missing/disabled); the radar/digest tasks are repetition-driven and
    only WARN. NOTE (docs/WINDOWS.md): schtasks reports Ready vs Running vs
    Disabled — it does NOT expose "registered but crash-looping" the way systemd
    does, so a healthy-looking "Ready"/"Running" still needs a real box to prove
    the daemon actually dispatches.
    """
    tasks = probes.scheduled_tasks
    if tasks is None:
        tasks = _scheduled_tasks()
    if not tasks:
        return CheckResult(
            "scheduled tasks", WARN,
            _pick("act/tasksched 下没有任务模板——checkout 不完整？",
                  "no task templates under act/tasksched - incomplete checkout?"),
            "git -C '%s' checkout act/tasksched" % config.HOME)
    table = _parse_schtasks(probes.launchctl_list())
    results = []
    for full in tasks:
        short = full.rsplit("\\", 1)[-1]
        is_actd = full == ACTD_TASK
        severity = FAIL if is_actd else WARN
        info = table.get(full)
        if info is None:
            results.append(CheckResult(
                short, severity,
                "%s not registered with Task Scheduler%s" % (
                    full, " - cards never move" if is_actd else ""),
                "powershell -ExecutionPolicy Bypass -File install.ps1 "
                "(renders + registers the tasks)",
            ).with_failure("agent_unloaded"))
            continue
        status = info.get("Status", "")
        state = info.get("Scheduled Task State", "")
        if state == "Disabled" or status == "Disabled":
            results.append(CheckResult(
                short, severity,
                "%s is disabled (not running)" % full,
                "schtasks /Change /TN \"%s\" /ENABLE" % full,
            ).with_failure("agent_unloaded"))
        elif status == "Running":
            results.append(CheckResult(short, OK, "running"))
        elif status == "Ready":
            results.append(CheckResult(short, OK, "registered (ready)"))
        else:
            results.append(CheckResult(
                short, severity,
                "%s status is %r (not ready/running)" % (full, status or "unknown"),
                "schtasks /Query /TN \"%s\" /V /FO LIST  # inspect; then re-run install.ps1" % full,
            ).with_failure("agent_unloaded"))
    return results


def _install_report_cron_status() -> str:
    """state/install_report.json 里 cron step 的 status；读不了/没有 = ""。"""
    try:
        data = json.loads((config.STATE_DIR / "install_report.json")
                          .read_text(encoding="utf-8"))
        for step in data.get("steps", []):
            if isinstance(step, dict) and step.get("name") == "cron":
                return str(step.get("status") or "")
    except Exception:  # noqa: BLE001 - 探针不许崩（宪法第 11 条）
        pass
    return ""


def _cron_write_access_rows(probes: Probes) -> list:
    """§23 `cron=skipped_tcc`：最近一次 install.sh 想改写 crontab 但被 TCC 拒了
    （launchd 会话，Operation not permitted）——crontab 里的行可能是旧的，而
    `_check_cron` 的两行按内容 pattern 判断，旧行照样匹配、照样绿。这行是唯一
    的窗口；下一次改写成功（cron=ok）它自动消失。2026-09-02 v0.48.12 实战。"""
    if _install_report_cron_status() != "skipped_tcc":
        return []
    _daemon_py = _pinned_interpreter(probes) or "the daemon python (config/runtime.json)"
    return [CheckResult(
        "cron write access", WARN,
        _pick("上次 install.sh 改写 crontab 被拒（Operation not permitted——launchd "
              "会话缺 Full Disk Access）；§18 的 cron 行可能停在旧版本",
              "last install.sh could not rewrite the crontab (Operation not permitted - "
              "the launchd session lacks Full Disk Access); the §18 cron lines may be stale"),
        _pick("系统设置 > 隐私与安全性 > 完全磁盘访问权限：给 %s 打开，然后 bash "
              "install.sh。在终端里跑通不算数——Terminal 自带 FDA，launchd 会话没有",
              "System Settings > Privacy & Security > Full Disk Access: enable %s, then "
              "bash install.sh. A terminal-run install proving it works proves nothing - "
              "Terminal has its own FDA, the launchd session does not") % _daemon_py,
    ).with_failure("cron_tcc_blocked")]


def _check_cron(probes: Probes):
    text = probes.crontab()
    results = []
    if "screenpipe-export.sh" in text:
        results.append(CheckResult("cron ingest chain", OK, "installed (CONTRACT §18)"))
    else:
        results.append(CheckResult(
            "cron ingest chain", FAIL,
            "missing from crontab - screen captures never become vault notes or radar cards",
            "bash install.sh (reinstalls the §18 cron lines)",
        ).with_failure("cron_missing"))
    results.extend(_cron_write_access_rows(probes))
    digest_lines = [ln for ln in text.splitlines()
                    if "act.digest" in ln and not ln.lstrip().startswith("#")]
    if any(_LEGACY_DIGEST_NOW_RE.search(ln) for ln in digest_lines):
        # §17 D19: a crontab line that still passes --now is the pre-D19
        # Monday form — --now bypasses the cadence gate, so this line forces
        # a card every fire no matter what digest.frequency says (default
        # off). Calling it "installed" here would be the lie the knob exists
        # to end; only `bash install.sh` replaces the line.
        results.append(CheckResult(
            "cron digest", WARN,
            "legacy `act.digest --now` line - forces a card every fire, "
            "ignoring digest.frequency (default off)",
            "bash install.sh (replaces it with the daily self-gated line)",
        ).with_failure("cron_missing"))
    elif digest_lines:
        # the line fires daily; the cadence (default off) lives in config,
        # so "installed" says nothing about whether cards appear.
        results.append(CheckResult(
            "cron digest", OK,
            "installed (daily 09:07; cadence = digest.frequency)"))
    else:
        results.append(CheckResult(
            "cron digest", WARN,
            "digest line missing from crontab",
            "bash install.sh",
        ).with_failure("cron_missing"))
    results.append(_check_cron_probe(probes, cron_installed="screenpipe-export.sh" in text))
    return results


def _check_cron_probe(probes: Probes, cron_installed: bool):
    """The cron FDA probe (§25): every cron chain run writes state/cron_probe.json
    with a real read attempt against the protected export target. This is the
    ONLY honest signal for the #1 silent failure — cron blocked by missing
    Full Disk Access writes nothing into ~/Documents and reports nothing.
    """
    name = "cron disk access"
    if not CRON_PROBE_PATH.exists():
        if not cron_installed:
            return CheckResult(
                name, WARN,
                _pick("还没有探针数据（cron 链尚未安装）",
                      "no probe data (cron chain not installed yet)"),
                _pick("bash install.sh，然后等 ~30 分钟让 cron 跑第一轮",
                      "bash install.sh, then wait ~30 min for the first cron run"))
        return CheckResult(
            name, WARN,
            _pick("还没有探针数据——装上这个版本后 cron 链还没跑过",
                  "no probe yet - the cron chain has not run since this version was installed"),
            _pick("重跑 bash install.sh（更新 cron 行），然后等 ~30 分钟",
                  "rerun bash install.sh (updates the cron line), then wait ~30 min"))
    try:
        data = json.loads(CRON_PROBE_PATH.read_text(encoding="utf-8"))
        ts = _dt.datetime.strptime(str(data.get("ts", "")), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc).timestamp()
        read_ok = data.get("read_ok")
        if not isinstance(read_ok, bool):
            # schema 降级（read_ok 缺键/非 bool——半截/手改/旧版文件）与整体
            # 损坏同级处理：WARN unreadable，绝不据此给出「FDA 被禁」的红色
            # 确定性诊断 + 授权指引（shell writer 只写字面量 true/false）
            raise ValueError("read_ok missing or not a bool")
        probed = str(data.get("protected_path") or "")
    except Exception:  # noqa: BLE001 - torn/hand-edited file is the symptom
        return CheckResult(
            name, WARN,
            _pick("state/cron_probe.json 读不出来——等下一轮 cron 再看",
                  "state/cron_probe.json unreadable - wait for the next cron run"),
            _pick("如果一直读不出来：重跑 bash install.sh",
                  "if it stays unreadable: rerun bash install.sh"))
    age = probes.now() - ts
    if age > CRON_PROBE_FRESH_SECONDS:
        return CheckResult(
            name, WARN,
            "last cron probe %dh ago - the cron chain looks stopped" % int(age // 3600),
            "bash install.sh (reinstalls the cron lines); check crontab -l",
        ).with_failure("cron_missing")
    if not read_ok:
        return CheckResult(
            name, FAIL,
            "cron CANNOT read %s - macOS Full Disk Access is blocking it; "
            "captures are silently lost" % (probed or "the vault"),
            "System Settings > Privacy & Security > Full Disk Access > '+' > "
            "Cmd+Shift+G > /usr/sbin/cron (the app's dependency page has a guided button)",
        ).with_failure("cron_fda_blocked")
    return CheckResult(name, OK,
                       "cron read %s ok (probe %d min ago)" % (probed, int(age // 60)))


def _check_store2(probes: Probes):
    """§53.6 数据层真源体检：激活状态 / 拒绝原因 / 每日导出 / 迟到 YAML 写。

    数据源 = act/lib/store2/activate.status()（doctor 与 --report 同一真相）。
    FAIL 两形：refused（迁移比对有差异——YAML 仍是真源，diff 摘要在
    state/store2_activation.json）与 db_missing（标记在、库没了——按
    TROUBLESHOOTING「store2 回滚」处置）。"""
    from act.lib import registry
    from act.lib.store2 import activate
    st = activate.status()
    state = st.get("state")
    if state == "yaml_forced":
        note = "（store2 标记在，回滚开关生效）" if st.get("marker_present") else ""
        return CheckResult(
            "store2", OK,
            _pick("YAML 后端（registry.backend/env 强制）%s" % note,
                  "YAML backend (forced by registry.backend/env)%s" % note))
    if state == "active":
        marker = st.get("marker") or {}
        late = st.get("late_yaml_writes") or []
        if late:
            shown = ", ".join(late[:5]) + ("…" if len(late) > 5 else "")
            return CheckResult(
                "store2", WARN,
                _pick("SQLite 是真源，但激活后仍有进程往 YAML 目录写：%s"
                      "——那些卡不在真源里" % shown,
                      "SQLite is the truth, but YAML files were written after"
                      " activation: %s — those cards are NOT in the truth" % shown),
                _pick("确认写者已升级/重启（旧雷达进程），再手动核对这些文件是否"
                      "需要重新录入（重新触发一次对应捕获）",
                      "restart the stale writer processes, then re-enter those"
                      " cards through a normal capture"))
        return CheckResult(
            "store2", OK,
            "SQLite is the registry truth (%s cards at activation; backup %s;"
            " daily export last_run=%s)" % (
                marker.get("cards", "?"), marker.get("backup_dir", "?"),
                st.get("export_last_run") or "never"))
    if state == "db_missing":
        return CheckResult(
            "store2", FAIL,
            _pick("激活标记在，但 %s 不见了——数据层处于故障半态，管线读写会"
                  "响亮失败" % registry.store2_db_path(),
                  "truth marker present but %s is missing — the data layer is"
                  " in a broken half-state" % registry.store2_db_path()),
            _pick("按 docs/TROUBLESHOOTING.md「store2 回滚」：停守护 → 恢复 "
                  "state/backups/registry-<ts>/ → config registry.backend: yaml"
                  " → 重启",
                  "follow docs/TROUBLESHOOTING.md (store2 rollback): stop the"
                  " daemons, restore state/backups/registry-<ts>/, set"
                  " registry.backend: yaml, restart"),
        ).with_failure("store2_db_missing")
    if state in ("refused", "cooldown"):
        act_info = st.get("activation") or {}
        reason = str(act_info.get("reason") or "?")
        n = act_info.get("diff_total") or 0
        extra = (_pick("；差异 %s 条，明细在 state/store2_activation.json" % n,
                       "; %s field diff(s), details in"
                       " state/store2_activation.json" % n) if n else "")
        return CheckResult(
            "store2", FAIL,
            _pick("store2 激活被拒，YAML 仍是真源：%s%s" % (reason, extra),
                  "store2 activation refused — YAML stays the truth: %s%s"
                  % (reason, extra)),
            _pick("修复点名的卡文件后等重试（或删 state/store2_activation.json"
                  " 立即重试）；备份完好在 %s" % act_info.get("backup_dir"),
                  "fix the named card files and wait for the retry (or delete"
                  " state/store2_activation.json to retry now); the backup is"
                  " intact at %s" % act_info.get("backup_dir")),
        ).with_failure("store2_refused")
    # pending：还没激活过（全新安装 / 升级后第一个 actd pass 会做）
    return CheckResult(
        "store2", OK,
        _pick("尚未激活（YAML 是真源）——actd 下一个 pass 将自动「备份→迁移→"
              "逐字段比对」，零差异才切换",
              "not yet activated (YAML is the truth) — actd's next pass runs"
              " backup → migrate → field-by-field parity, and only a zero diff"
              " flips the truth"))


def _check_dashboard(probes: Probes):
    path = config.DASHBOARD_PATH
    if not path.exists():
        return CheckResult(
            "dashboard", FAIL,
            _pick("state/dashboard.json 缺失——App 会一直显示「missing」",
                  "state/dashboard.json missing - the app shows 'missing' forever"),
            _pick("启动 actd（bash install.sh），或手动生成一次：python3 -m act.lib.dashboard",
                  "start actd (bash install.sh), or seed once: python3 -m act.lib.dashboard"))
    try:
        gen = json.loads(path.read_text(encoding="utf-8")).get("generated_at", "")
        ts = _dt.datetime.strptime(gen, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc).timestamp()
    except Exception:  # noqa: BLE001 - torn/malformed file is the symptom
        return CheckResult(
            "dashboard", FAIL,
            _pick("state/dashboard.json 读不出来或没有合法的 generated_at",
                  "state/dashboard.json is unreadable or has no valid generated_at"),
            _pick("删掉它并重启 actd（它会原子重写）",
                  "delete it and restart actd (it rewrites atomically)"))
    age = probes.now() - ts
    if age <= DASHBOARD_FRESH_SECONDS:
        return CheckResult("dashboard", OK, "fresh (generated %ds ago)" % max(int(age), 0))
    return CheckResult(
        "dashboard", FAIL,
        "stale (generated %d min ago) - actd is not writing; the app renders old data" % int(age // 60),
        "launchctl list | grep aiassistant; "
        "tail -20 ~/Library/Logs/zelin-ai-assistant/actd.launchd.log"
        " (pre-v0.48 installs: state/actd.launchd.log)",
    ).with_failure("dashboard_stale")


def _actd_restart_cmd() -> str:
    """The hard-restart command for the resident daemon on this OS — a stalled
    process needs a kill+respawn, not a reload."""
    if platform.is_darwin():
        return "launchctl kickstart -k gui/$(id -u)/%s" % ACTD_LABEL
    if platform.is_windows():
        return 'schtasks /End /TN "%s" & schtasks /Run /TN "%s"' % (ACTD_TASK, ACTD_TASK)
    return "systemctl --user restart %s" % ACTD_UNIT


def _actd_alive(probes: Probes, hb: Optional[dict]) -> Optional[bool]:
    """Is the resident daemon process alive? darwin asks launchd (the pid
    column); elsewhere the heartbeat's own pid is probed. None = cannot tell."""
    if platform.is_darwin():
        row = _launchctl_table(probes).get(ACTD_LABEL)
        if row is not None:
            return row[0] != "-"
    pid = (hb or {}).get("pid")
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        return probes.pid_alive(pid)
    return None


def _check_heartbeat(probes: Probes):
    """§47.4 stall watchdog: process alive + heartbeat stale = the loop is stuck.

    2026-08-31 22:31: actd kept its pid for 2.5 h with no children, parked in
    time.sleep, dashboard frozen — `launchctl list` said running, loop_health
    counted zero crashes, doctor said healthy. The heartbeat's mtime (touched
    at every phase boundary of every pass) is the only signal that separates
    "alive" from "looping"; ``stale_after_s`` comes from the writer
    (3 × interval, floor 90 s) so the threshold has exactly one owner.
    """
    hb = probes.heartbeat_read()
    alive = _actd_alive(probes, hb)
    restart = _actd_restart_cmd()
    if hb is None:
        if alive:
            return CheckResult(
                "actd heartbeat", WARN,
                "actd is running but has never written state/actd.heartbeat - the "
                "daemon predates v0.48.4 or just started; without it a silent stall "
                "is invisible",
                restart + "  # restart so the upgraded daemon starts beating")
        return []   # not running: the actd row already carries the fix
    age = float(hb.get("age_s") or 0)
    phase = str(hb.get("phase") or "?")
    if not heartbeat.is_stale(hb):
        return CheckResult(
            "actd heartbeat", OK,
            "beating (phase=%s, %ds ago%s)" % (
                phase, int(age), ", pid %s" % hb["pid"] if hb.get("pid") else ""))
    mins = int(age // 60)
    if alive is False:
        return CheckResult(
            "actd heartbeat", WARN,
            "no heartbeat for %d min and actd is not running - see the actd row"
            % mins, restart)
    who = "alive (pid %s)" % hb.get("pid") if alive else "process state unknown"
    return CheckResult(
        "actd heartbeat", FAIL,
        "%s but no heartbeat for %d min (last seen in phase '%s') - the loop is "
        "stuck, not looping; cards will not move and the board goes stale"
        % (who, mins, phase),
        restart,
    ).with_failure("actd_stalled")


# --------------------------------------------------------------------------- #
# §54 看板 server + §56.5 ui 步：判决逻辑在 act/lib/board_server.py，这里只包
# CheckResult（判例 tests/test_doctor_board_server.py）
# --------------------------------------------------------------------------- #
def _row_from(row: dict, name: str) -> CheckResult:
    res = CheckResult(name, row["status"], row["detail"], row.get("fix", ""))
    if row.get("failure_id"):
        res.with_failure(row["failure_id"])
    return res


def _check_board_server(probes: Probes):
    """§54 看板 server 行：`GET /api/health` 答话才算活——`launchctl list` 里的
    pid 只说明进程起了，bind 成功没有它不知道。可达 + 托管 OK；可达但非托管
    （壳 spawn 的旧形状）WARN；不可达 + 托管 FAIL `board_server_down`（crash-loop /
    端口被占）；不可达 + 未托管 WARN。探针不可用（沙箱 / windows）→ 不出行。"""
    if platform.is_windows():
        return []
    port = int(config.load_config().server_port)
    verdict = probes.board_health(port)
    if verdict.get("state") == "unavailable":
        return []
    try:
        listing = probes.launchctl_list()
    except Exception:  # noqa: BLE001 - 探针不许崩（宪法第 11 条）
        listing = ""
    darwin = platform.is_darwin()
    row = board_server.assess(verdict, board_server.hosted(listing, darwin), port,
                              darwin, _installer())
    return _row_from(row, "board server")


def _install_report_step(name: str) -> Optional[dict]:
    """§23 install_report.json 里名为 ``name`` 的 step（最后一个同名者）；
    文件缺失 / 撕裂 / 形状不对 → None（宪法第 11 条：探针不许崩）。"""
    try:
        doc = json.loads(install_report.REPORT_PATH.read_text(encoding="utf-8"))
        steps = [st for st in doc.get("steps", []) if isinstance(st, dict) and st.get("name") == name]
    except (OSError, ValueError, AttributeError):
        return None
    return steps[-1] if steps else None


def _check_ui_build(probes: Probes):
    """§56.5 `ui` 步的可见性：最近一次 install.sh 的 `ui` step 是 `skipped_tcc`
    （node 在 launchd 会话里缺 Full Disk Access——部署照常完成、web 看板却没
    重建）→ WARN `ui_build_tcc_blocked`；`fail`（只可能来自手动 install.sh）→
    WARN 指向 ui-build.log；其余不出行。"""
    row = board_server.ui_build_row(_install_report_step("ui"), _installer())
    if row is None:
        return []
    return _row_from(row, "board ui build")


def _check_auto_deploy(probes: Probes):
    """§56 合并即上岗：最近一次自动部署的结果（`deploy_state.read()`：HOME 镜像描述的
    是本 checkout 时读镜像，否则读 state/ 投影；两个文件都不存在 = 这台机器不跑该
    agent → 不出行）。healthy → OK、其余 → WARN、healthy 但 `last_incident` 在案 →
    WARN（#135 review）；文案与修法住 `deploy_state.auto_deploy_row`（§56.4）。"""
    state = deploy_state.read()
    if not state:
        return []
    return _row_from(deploy_state.auto_deploy_row(state), "auto-deploy")


def _check_obsidian(probes: Probes):
    cfg = config.load_config()
    raw = cfg.obsidian_raw
    if not (raw and str(raw).strip()):
        return CheckResult(
            "obsidian vault", WARN,
            _pick("sources.obsidian_raw 未配置——obsidian 雷达空转（快速捕获不受影响）",
                  "sources.obsidian_raw not set - the obsidian radar idles (quick capture still works)"),
            _pick("在 config.yaml 的 sources.obsidian_raw 填上 vault 的 raw 笔记目录",
                  "set sources.obsidian_raw in config.yaml to your vault's raw-notes folder"))
    raw_path = Path(str(raw)).expanduser()
    if not raw_path.is_dir():
        return CheckResult(
            "obsidian vault", WARN,
            _pick("sources.obsidian_raw 不存在（%s）——雷达什么都扫不到，而且是静默的",
                  "sources.obsidian_raw does not exist (%s) - radar scans nothing, silently") % raw_path,
            _pick("创建该目录，或改 config.yaml 里的路径",
                  "create the folder or fix the path in config.yaml"))
    unprocessed = Path(str(cfg.obsidian_unprocessed)).expanduser()
    if not unprocessed.is_dir():
        return CheckResult(
            "obsidian vault", WARN,
            _pick("ingest 收件目录缺失（%s）——导出的笔记没有落脚点",
                  "ingest inbox missing (%s) - exports have nowhere to land") % unprocessed,
            "mkdir -p '%s'" % unprocessed)
    return CheckResult("obsidian vault", OK, "%s (+ ingest inbox)" % raw_path)


def _check_screenpipe(probes: Probes):
    db = probes.screenpipe_db
    if not db.exists():
        return CheckResult(
            "screenpipe db", WARN,
            _pick("%s 缺失——录制从未运行过（如果你本来就不开录制，这是正常的）",
                  "%s missing - recording has never run (fine if you keep recording off)") % db,
            _pick("菜单栏 App -> 打开录制（引擎经 npx 运行）",
                  "menu-bar app -> enable recording (the engine runs via npx)"))
    age = probes.now() - db.stat().st_mtime
    if age > SCREENPIPE_STALE_SECONDS:
        return CheckResult(
            "screenpipe db", WARN,
            "last write %dh ago - the capture engine looks stopped" % int(age // 3600),
            "menu-bar app -> recording toggle (needs node/npx)",
        ).with_failure("engine_dead")
    return CheckResult("screenpipe db", OK,
                       "recording data fresh (last write %d min ago)" % int(age // 60))


def _check_npx(probes: Probes):
    path = probes.which("npx")
    if not path:
        return CheckResult(
            "node/npx", WARN,
            "missing - the recording engine (`npx screenpipe`) cannot start",
            "brew install node",
        ).with_failure("node_missing")
    return CheckResult("node/npx", OK, path)


def _check_gh(probes: Probes):
    path = probes.which("gh")
    if not path:
        return CheckResult(
            "gh CLI", WARN,
            _pick("缺失——repo 模式的卡片只能交付成本地分支（可选依赖）",
                  "missing - repo-mode cards deliver as local branches only (optional)"),
            "brew install gh && gh auth login")
    rc, _ = probes.run([path, "auth", "status"], timeout=15)
    if rc != 0:
        return CheckResult(
            "gh CLI", WARN,
            _pick("%s 存在但未登录——draft-PR 交付会失败",
                  "%s present but not authenticated - draft-PR delivery will fail") % path,
            "gh auth login")
    return CheckResult("gh CLI", OK, "%s (authenticated)" % path)


def _check_claude_auth(probes: Probes):
    """One cheap live call, with the SAME credential resolution headless runs use."""
    path = probes.which("claude")
    if not path:
        return CheckResult("claude auth", WARN,
                           _pick("跳过（未找到 claude CLI）", "skipped (claude CLI not found)"))
    key, source = _resolve_key(probes)
    env = dict(os.environ)
    if key:
        env["ANTHROPIC_API_KEY"] = key
        via = "API key from %s" % source
    else:
        env.pop("ANTHROPIC_API_KEY", None)
        via = "claude CLI stored credentials (subscription auth)"
    rc, out = probes.run([path, "-p", "Reply with exactly: ok", "--max-turns", "1"],
                         env=env, timeout=_PROBE_TIMEOUT)
    if rc == 0:
        detail = "live call ok (%s)" % via
        if not key:
            # worked here (GUI session) but cron/launchd may still fail: the
            # daemon session cannot read the Keychain this probe just used.
            detail += " - note: headless cron/launchd may still need a key file"
        return CheckResult("claude auth", OK, detail)
    tail = " ".join(out.strip().split())[-120:] if out.strip() else "no output"
    fix = ("check the key (active? billing?) or re-paste it in the app's Settings window"
           if key else
           "paste an API key in the app's Settings window (headless-safe), or log in: claude")
    return CheckResult(
        "claude auth", FAIL,
        "live call failed via %s (exit %s: %s)" % (via, rc, tail), fix,
    ).with_failure(failures.classify(out) or "claude_auth_failed")


# --------------------------------------------------------------------------- #
# §59 (D22) model knobs — what "follow" inherits + does an explicit id answer
# --------------------------------------------------------------------------- #
def _model_knobs(cfg) -> dict:
    """{"dispatch": id|None, "pipeline": id|None} — None = follow."""
    return {mode: llm.model_for(mode, cfg) for mode in llm.MODES}


def _check_claude_code_model(probes: Probes):
    """One row, file reads only (rides under --fast too): the Claude Code global
    default every follow-mode call inherits, plus where the two knobs point.
    Never FAIL — this row informs; §56's rollback verdict must not turn on it.
    WARN when a knob follows a NON-canonical global default: that is exactly
    the 2026 EAP-alias retirement that broke every dispatch silently."""
    info = probes.claude_code_settings() or {}
    cfg = config.load_config()
    knobs = _model_knobs(cfg)
    knob_text = " · ".join(
        "%s: %s" % (mode, knobs[mode] or "follow") for mode in llm.MODES)
    global_model = info.get("model")
    if info.get("exists") and not info.get("parseable"):
        return CheckResult(
            "claude code model", WARN,
            _pick("~/.claude/settings.json 不是合法 JSON——follow 模式继承的全局默认读不出来（%s）",
                  "~/.claude/settings.json is not valid JSON - the global default that follow mode inherits is unreadable (%s)") % knob_text,
            _pick("手动修好那个文件（Claude Code 自己也读它）",
                  "fix that file by hand (Claude Code reads it too)"))
    shown = global_model or _pick("未设置（Claude Code 内置默认）", "unset (Claude Code built-in default)")
    following = [m for m in llm.MODES if knobs[m] is None]
    if global_model and following and not llm.is_canonical(global_model):
        return CheckResult(
            "claude code model", WARN,
            _pick("全局默认 `%s` 不是 canonical id，%s 跟随它——别名/后缀下线那天这些调用会静默全败（%s）",
                  "global default `%s` is not a canonical id and %s follow it - the day the alias/suffix retires those calls fail silently (%s)")
            % (global_model, "/".join(following), knob_text),
            _pick("设置页「模型」→「设为 <canonical id>」改全局默认，或给旋钮选一个显式 canonical id",
                  "Settings > Models > \"Set to <canonical id>\" for the global default, or pick an explicit canonical id per knob"))
    return CheckResult("claude code model", OK,
                       _pick("全局默认 %s（%s）", "global default %s (%s)") % (shown, knob_text))


def _check_model_liveness(probes: Probes):
    """Per explicit knob: one minimal live call with that --model. follow =
    skipped (nothing to probe; the auth row already covers the default).
    FAIL speaks plainly: the model in Settings is unavailable, dispatch /
    pipeline will fail wholesale."""
    cfg = config.load_config()
    knobs = _model_knobs(cfg)
    results = []
    probed: dict = {}
    for mode in llm.MODES:
        model = knobs[mode]
        name = "model %s" % mode
        if model is None:
            results.append(CheckResult(
                name, OK, _pick("follow（继承 Claude Code 全局默认，不探）",
                                "follow (inherits the Claude Code default, not probed)")))
            continue
        if not probes.which("claude"):
            # the `claude CLI` row already FAILs; do not double-blame the model
            results.append(CheckResult(
                name, WARN, _pick("%s — 跳过（未找到 claude CLI）",
                                  "%s - skipped (claude CLI not found)") % model))
            continue
        if model not in probed:   # dispatch == pipeline → one call, not two
            probed[model] = probes.run(llm.probe_argv(model, cfg),
                                       env=llm.runner_env(),
                                       timeout=_MODEL_PROBE_TIMEOUT)
        rc, out = probed[model]
        if rc == 0:
            results.append(CheckResult(
                name, OK, _pick("%s — 活探针 ok", "%s - live probe ok") % model))
            continue
        tail = " ".join(str(out).strip().split())[-120:] if str(out).strip() else "no output"
        consequence = (_pick("派工会全部失败", "every dispatch will fail")
                       if mode == llm.MODE_DISPATCH else
                       _pick("雷达/分诊/判官/问答会全部失败",
                             "radar / triage / judge / ask will all fail"))
        results.append(CheckResult(
            name, FAIL,
            _pick("模型 %s 不可用，%s（exit %s: %s）",
                  "model %s is unavailable, %s (exit %s: %s)")
            % (model, consequence, rc, tail),
            _pick("设置页「模型」改回「跟随 Claude Code 全局」或换一个 canonical id",
                  "Settings > Models: switch back to \"follow Claude Code\" or pick a canonical id"),
        ).with_failure("model_unavailable"))
    return results


# Shared checks that run on every OS (pure Python / portable subprocess).
_CHECKS_COMMON_HEAD = [
    _check_home,
    _check_version,
    _check_claude,
    _check_daemon_claude,
    _check_runtime_python,
    _check_config,
    _check_anthropic_key,
    _check_state_dirs,
]


def _checks_for_platform() -> List:
    """Compose the check list for the current OS.

    Shared checks always run. The service check swaps launchd (macOS) <->
    systemd (Linux) <-> Task Scheduler (Windows). The macOS-only screen-ingest /
    crontab checks (cron chain + FDA probe, screenpipe db, node/npx) are
    conditioned out off-macOS: Linux/Windows v1 defer screen ingest
    (docs/LINUX.md, docs/WINDOWS.md) and drive radars via systemd timers /
    scheduled tasks, so there is no crontab ingest chain to probe.
    """
    if platform.is_darwin():
        middle = [_check_launchd, _check_launchd_paths, _check_launchd_fd_limit,
                  _check_launchd_claude, _check_launchd_volume_access,
                  _check_launchd_orphans, _check_cron]
        tail_extra = [_check_screenpipe, _check_npx]
    elif platform.is_windows():
        middle = [_check_scheduled_tasks]
        tail_extra = []
    else:
        middle = [_check_systemd]
        tail_extra = []
    # §47.4 heartbeat rides right behind the dashboard freshness row on every
    # OS: the two together tell "dead" (dashboard stale, no pid) from "stuck"
    # (pid alive, heartbeat stale).
    return (_CHECKS_COMMON_HEAD + middle
            + [_check_store2, _check_dashboard, _check_heartbeat,
               _check_board_server, _check_ui_build, _check_auto_deploy,
               _check_obsidian]
            + tail_extra + [_check_gh, _check_claude_code_model])


def _safe(fn, probes: Probes) -> List[CheckResult]:
    try:
        res = fn(probes)
        return res if isinstance(res, list) else [res]
    except Exception as exc:  # noqa: BLE001 - a doctor bug must not mask real checks
        name = fn.__name__.replace("_check_", "").replace("_", " ")
        return [CheckResult(
            name, FAIL, "diagnostic crashed: %r" % exc,
            "report this: https://github.com/Wan-ZL/zelin-ai-assistant/issues")]


def run_checks(probes: Optional[Probes] = None, fast: bool = False) -> List[CheckResult]:
    probes = probes or Probes()
    checks = _checks_for_platform()
    if not fast:
        checks.append(_check_claude_auth)
        checks.append(_check_model_liveness)   # §59: spends tokens only for explicit knobs
    results: List[CheckResult] = []
    for fn in checks:
        results.extend(_safe(fn, probes))
    return results


_BADGE = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[FAIL]"}


def render(results: List[CheckResult]) -> str:
    lines = []
    for r in results:
        lines.append("%s %s: %s" % (_BADGE[r.status], r.name, r.detail))
        if r.fix and r.status != OK:
            lines.append("       fix: %s" % r.fix)
    fails = sum(r.status == FAIL for r in results)
    warns = sum(r.status == WARN for r in results)
    oks = sum(r.status == OK for r in results)
    lines.append("")
    lines.append("%d ok / %d warn / %d fail%s" % (
        oks, warns, fails, "" if fails else " - pipeline looks healthy"))
    return "\n".join(lines)


def render_json(results: List[CheckResult]) -> str:
    """§25 machine output: one row per check for the app's diagnostics page."""
    rows = [{"name": r.name, "status": r.status, "detail": r.detail, "fix": r.fix,
             "failure_id": r.failure_id, "action_id": r.action_id}
            for r in results]
    return json.dumps({"home": str(config.HOME), "checks": rows},
                      ensure_ascii=False, indent=1)


def main(argv: Optional[List[str]] = None, probes: Optional[Probes] = None) -> int:
    """Run all checks, print the report, return the number of FAILs (max 99)."""
    try:
        parser = argparse.ArgumentParser(
            prog="python3 -m act.doctor",
            description="Post-install diagnostics for Zelin's AI Assistant.")
        parser.add_argument("--fast", action="store_true",
                            help="skip the live claude auth probe (spends no tokens)")
        parser.add_argument("--json", action="store_true", dest="as_json",
                            help="machine-readable output (one row per check, §25)")
        args = parser.parse_args(argv)
        results = run_checks(probes=probes, fast=args.fast)
        if args.as_json:
            print(render_json(results))
        else:
            print("act.doctor - home: %s" % config.HOME)
            print(render(results))
        return min(sum(r.status == FAIL for r in results), 99)
    except SystemExit:
        raise  # argparse --help / bad flag
    except Exception as exc:  # noqa: BLE001 - the doctor itself must never crash
        print("[FAIL] doctor: internal error: %r" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
