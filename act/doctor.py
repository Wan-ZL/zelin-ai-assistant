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
import re
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from act.lib import config, failures, platform, secrets, taskscheduler

OK = "ok"
WARN = "warn"
FAIL = "fail"

ACTD_LABEL = "com.zelin.aiassistant.actd"      # launchd label (macOS)
ACTD_UNIT = "zelin-actd.service"               # systemd --user unit (Linux)
ACTD_TASK = taskscheduler.TASK_PATH_PREFIX + "actd"  # schtasks TaskName (Windows)
# Resident systemd services doctor expects up (the rest are timer-driven
# oneshots that are correctly inactive between fires — the timer is the signal).
SYSTEMD_RESIDENT = ("zelin-actd.service", "zelin-webui.service")


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

# actd rewrites dashboard.json every ~10s pass; anything older than this means
# the daemon is not writing (same threshold as the app's staleness banner).
DASHBOARD_FRESH_SECONDS = 90
# the export cron fires every 30 min while recording; 2h with no db write
# means the capture engine is stopped.
SCREENPIPE_STALE_SECONDS = 2 * 3600
MIN_PYTHON = (3, 9)
_PROBE_TIMEOUT = 90  # ceiling for the live claude call


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


def _launchd_log_tail(short: str) -> str:
    """agent 自管日志的末尾；"" = 读不到。v0.48 起住 ~/Library/Logs/，旧址兜底。"""
    for p in (Path.home() / "Library" / "Logs" / "zelin-ai-assistant"
              / ("%s.launchd.log" % short),
              config.HOME / "state" / ("%s.launchd.log" % short)):
        try:
            return p.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            continue
    return ""


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
        severity = FAIL if label == ACTD_LABEL else WARN
        if label not in table:
            results.append(CheckResult(
                short, severity,
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
            # 名出真因，别猜（§55）：读它自己的日志，把两条 ModuleNotFoundError
            # 分开——'act' = 解释器看不见 repo，'yaml' = 缺 PyYAML。
            missing = _log_missing_module(probes.launchd_log_tail(short))
            if missing == MISSING_ACT:
                detail = ("loaded but exits with status %s - its log says "
                          "\"No module named 'act'\": the interpreter cannot see "
                          "the repo (PyYAML is NOT the problem)" % status)
                fix = _INTERPRETER_BLIND_FIX
            elif missing == MISSING_YAML:
                detail = ("loaded but exits with status %s - its log says "
                          "\"No module named 'yaml'\": PyYAML is missing for the "
                          "daemon python" % status)
                fix = "%s -m pip install --user --break-system-packages pyyaml" % (
                    _pinned_interpreter(probes) or "python3")
            else:
                detail = "loaded but its process exits with status %s" % status
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
    if "act.digest" in text:
        results.append(CheckResult("cron digest", OK, "installed (Mon 09:07)"))
    else:
        results.append(CheckResult(
            "cron digest", WARN,
            "Monday digest line missing from crontab",
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


# Shared checks that run on every OS (pure Python / portable subprocess).
_CHECKS_COMMON_HEAD = [
    _check_home,
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
        middle = [_check_launchd, _check_launchd_paths, _check_cron]
        tail_extra = [_check_screenpipe, _check_npx]
    elif platform.is_windows():
        middle = [_check_scheduled_tasks]
        tail_extra = []
    else:
        middle = [_check_systemd]
        tail_extra = []
    return (_CHECKS_COMMON_HEAD + middle
            + [_check_dashboard, _check_obsidian] + tail_extra + [_check_gh])


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
