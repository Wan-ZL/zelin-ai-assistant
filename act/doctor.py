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
           fix: launchctl list | grep aiassistant; tail -20 state/actd.launchd.log

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
    """Anthropic key content per CONTRACT §19 order, plus its source label."""
    try:
        val = (secrets.SECRETS_DIR / secrets.ANTHROPIC_API_KEY_FILE).read_text(
            encoding="utf-8").strip()
        if val:
            return val, "config/secrets/anthropic-api-key.txt"
    except OSError:
        pass
    try:
        val = probes.legacy_key_path.read_text(encoding="utf-8").strip()
        if val:
            return val, str(probes.legacy_key_path)
    except OSError:
        pass
    return None, ""


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def _check_home(probes: Probes):
    if not (config.HOME / "install.sh").exists():
        return CheckResult(
            "AIASSISTANT_HOME", FAIL,
            "%s does not look like the repo (no install.sh) - every path below derives from it" % config.HOME,
            "export AIASSISTANT_HOME=<your clone>, or run bash <your clone>/install.sh (writes the home pointer)")
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
            "%s exists but `claude --version` failed (%s)" % (path, out.strip()[:80]),
            "reinstall Claude Code")
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
            "actd %s not installed (or carries no PATH) - cannot verify "
            "which claude the daemon runs" % where,
            "bash %s (renders the agent with your shell's claude dir first on PATH)"
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
            "config/runtime.json missing - launchd agents and the app guess at an interpreter",
            "bash install.sh (re-detects and pins the interpreter)")
    try:
        py = str(json.loads(rj.read_text(encoding="utf-8")).get("python") or "")
    except Exception:  # noqa: BLE001 - malformed file is just another symptom
        py = ""
    if not py or not os.access(py, os.X_OK):
        return CheckResult(
            "daemon python", FAIL,
            "config/runtime.json points at a non-executable python (%s)" % (py or "empty"),
            "bash install.sh (re-detects the interpreter)")
    rc, out = probes.run(
        [py, "-c", "import sys, yaml; print('%d.%d' % sys.version_info[:2])"],
        timeout=20)
    if rc != 0:
        return CheckResult(
            "daemon python", FAIL,
            "%s cannot `import yaml` - actd/radar exit immediately under launchd" % py,
            "%s -m pip install --user pyyaml   (PEP 668 python: add --break-system-packages)" % py)
    ver = out.strip().splitlines()[-1] if out.strip() else ""
    try:
        if tuple(int(x) for x in ver.split(".")) < MIN_PYTHON:
            return CheckResult(
                "daemon python", FAIL,
                "%s is Python %s (need >= %s)" % (py, ver, ".".join(map(str, MIN_PYTHON))),
                "AIASSISTANT_PYTHON=<newer python3> bash install.sh")
    except ValueError:
        pass
    return CheckResult("daemon python", OK,
                       "%s (Python %s, PyYAML importable)" % (py, ver))


def _check_config(probes: Probes):
    if not config.CONFIG_PATH.exists():
        return CheckResult(
            "config.yaml", WARN,
            "missing - running on config.example.yaml defaults (no vault, no watched people)",
            "cp config.example.yaml config.yaml && edit sources.*")
    if config.yaml is None:
        return CheckResult(
            "config.yaml", FAIL,
            "PyYAML missing for this python - config cannot be parsed",
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
                "config/secrets/anthropic-api-key.txt is readable by other users (mode %o)" % mode,
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
        "no key file - headless claude (cron/launchd) falls back to CLI credentials "
        "(subscription-auth mode), which daemon sessions usually cannot read",
        "paste your API key in the app's Settings window (writes config/secrets/anthropic-api-key.txt)")


def _check_state_dirs(probes: Probes):
    dirs = (config.STATE_DIR, config.INBOX_DIR, config.LOG_DIR)
    missing = [d for d in dirs if not d.is_dir()]
    if missing:
        return CheckResult(
            "state dirs", FAIL,
            "missing: %s - actd/capture cannot persist anything" % ", ".join(map(str, missing)),
            "bash install.sh (creates state/ + state/inbox/)")
    blocked = [d for d in dirs if not os.access(d, os.W_OK)]
    if blocked:
        return CheckResult(
            "state dirs", FAIL,
            "not writable: %s" % ", ".join(map(str, blocked)),
            "chown -R $(whoami) '%s'" % config.STATE_DIR)
    return CheckResult("state dirs", OK, "%s writable" % config.STATE_DIR)


def _check_launchd(probes: Probes):
    labels = probes.launchd_labels
    if labels is None:
        labels = sorted(p.stem for p in (config.HOME / "act" / "launchd").glob("*.plist"))
    if not labels:
        return CheckResult(
            "launchd agents", WARN,
            "no plist templates under act/launchd - incomplete checkout?",
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
            results.append(CheckResult(
                short, severity,
                "loaded but its process exits with status %s" % status,
                "tail -20 state/%s.launchd.log  # usual causes: PyYAML missing "
                "for the daemon python, missing API key" % short,
            ).with_failure("agent_unloaded"))
    return results


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
            "no unit templates under act/systemd - incomplete checkout?",
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
            "no task templates under act/tasksched - incomplete checkout?",
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
            return CheckResult(name, WARN,
                               "no probe data (cron chain not installed yet)",
                               "bash install.sh, then wait ~30 min for the first cron run")
        return CheckResult(
            name, WARN,
            "no probe yet - the cron chain has not run since this version was installed",
            "rerun bash install.sh (updates the cron line), then wait ~30 min")
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
        return CheckResult(name, WARN,
                           "state/cron_probe.json unreadable - wait for the next cron run",
                           "if it stays unreadable: rerun bash install.sh")
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
            "state/dashboard.json missing - the app shows 'missing' forever",
            "start actd (bash install.sh), or seed once: python3 -m act.lib.dashboard")
    try:
        gen = json.loads(path.read_text(encoding="utf-8")).get("generated_at", "")
        ts = _dt.datetime.strptime(gen, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc).timestamp()
    except Exception:  # noqa: BLE001 - torn/malformed file is the symptom
        return CheckResult(
            "dashboard", FAIL,
            "state/dashboard.json is unreadable or has no valid generated_at",
            "delete it and restart actd (it rewrites atomically)")
    age = probes.now() - ts
    if age <= DASHBOARD_FRESH_SECONDS:
        return CheckResult("dashboard", OK, "fresh (generated %ds ago)" % max(int(age), 0))
    return CheckResult(
        "dashboard", FAIL,
        "stale (generated %d min ago) - actd is not writing; the app renders old data" % int(age // 60),
        "launchctl list | grep aiassistant; tail -20 state/actd.launchd.log",
    ).with_failure("dashboard_stale")


def _check_obsidian(probes: Probes):
    cfg = config.load_config()
    raw = cfg.obsidian_raw
    if not (raw and str(raw).strip()):
        return CheckResult(
            "obsidian vault", WARN,
            "sources.obsidian_raw not set - the obsidian radar idles (quick capture still works)",
            "set sources.obsidian_raw in config.yaml to your vault's raw-notes folder")
    raw_path = Path(str(raw)).expanduser()
    if not raw_path.is_dir():
        return CheckResult(
            "obsidian vault", WARN,
            "sources.obsidian_raw does not exist (%s) - radar scans nothing, silently" % raw_path,
            "create the folder or fix the path in config.yaml")
    unprocessed = Path(str(cfg.obsidian_unprocessed)).expanduser()
    if not unprocessed.is_dir():
        return CheckResult(
            "obsidian vault", WARN,
            "ingest inbox missing (%s) - exports have nowhere to land" % unprocessed,
            "mkdir -p '%s'" % unprocessed)
    return CheckResult("obsidian vault", OK, "%s (+ ingest inbox)" % raw_path)


def _check_screenpipe(probes: Probes):
    db = probes.screenpipe_db
    if not db.exists():
        return CheckResult(
            "screenpipe db", WARN,
            "%s missing - recording has never run (fine if you keep recording off)" % db,
            "menu-bar app -> enable recording (the engine runs via npx)")
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
            "missing - repo-mode cards deliver as local branches only (optional)",
            "brew install gh && gh auth login")
    rc, _ = probes.run([path, "auth", "status"], timeout=15)
    if rc != 0:
        return CheckResult(
            "gh CLI", WARN,
            "%s present but not authenticated - draft-PR delivery will fail" % path,
            "gh auth login")
    return CheckResult("gh CLI", OK, "%s (authenticated)" % path)


def _check_claude_auth(probes: Probes):
    """One cheap live call, with the SAME credential resolution headless runs use."""
    path = probes.which("claude")
    if not path:
        return CheckResult("claude auth", WARN, "skipped (claude CLI not found)")
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
        middle = [_check_launchd, _check_cron]
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
