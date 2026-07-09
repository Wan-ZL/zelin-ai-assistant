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
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from act.lib import config, secrets

OK = "ok"
WARN = "warn"
FAIL = "fail"

ACTD_LABEL = "com.zelin.aiassistant.actd"

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
                              timeout=timeout, env=env)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timed out after %ss" % timeout
    except OSError as exc:
        return 127, str(exc)


def _launchctl_list() -> str:
    return _run(["launchctl", "list"], timeout=10)[1]


def _crontab() -> str:
    rc, out = _run(["crontab", "-l"], timeout=10)
    return out if rc == 0 else ""


@dataclass
class Probes:
    which: Callable[[str], Optional[str]] = shutil.which
    run: Callable[..., Tuple[int, str]] = _run
    launchctl_list: Callable[[], str] = _launchctl_list
    crontab: Callable[[], str] = _crontab
    now: Callable[[], float] = time.time
    # None -> derive from act/launchd/*.plist basenames under AIASSISTANT_HOME
    launchd_labels: Optional[List[str]] = None
    screenpipe_db: Path = field(
        default_factory=lambda: Path.home() / ".screenpipe" / "db.sqlite")
    legacy_key_path: Path = field(
        default_factory=lambda: Path("~/.config/anthropic-key.txt").expanduser())


@dataclass
class CheckResult:
    name: str
    status: str  # OK | WARN | FAIL
    detail: str  # the symptom, one line
    fix: str = ""  # one-line fix (empty for OK)


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
            "install Claude Code (https://claude.com/claude-code), then re-run this check")
    rc, out = probes.run([path, "--version"], timeout=15)
    if rc != 0:
        return CheckResult(
            "claude CLI", WARN,
            "%s exists but `claude --version` failed (%s)" % (path, out.strip()[:80]),
            "reinstall Claude Code")
    version = out.strip().splitlines()[0][:60] if out.strip() else "unknown version"
    return CheckResult("claude CLI", OK, "%s (%s)" % (path, version))


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
            "fix the syntax; verify: python3 -c \"import yaml; yaml.safe_load(open('config.yaml'))\"")
    return CheckResult("config.yaml", OK, str(config.CONFIG_PATH))


def _check_anthropic_key(probes: Probes):
    sec = secrets.SECRETS_DIR / secrets.ANTHROPIC_API_KEY_FILE
    key, source = _resolve_key(probes)
    if key and source.startswith("config/secrets"):
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
                "bash install.sh (renders + loads the agents)"))
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
                "for the daemon python, missing API key" % short))
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
            "bash install.sh (reinstalls the §18 cron lines)"))
    if "act.digest" in text:
        results.append(CheckResult("cron digest", OK, "installed (Mon 09:07)"))
    else:
        results.append(CheckResult(
            "cron digest", WARN,
            "Monday digest line missing from crontab",
            "bash install.sh"))
    return results


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
        "launchctl list | grep aiassistant; tail -20 state/actd.launchd.log")


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
            "menu-bar app -> recording toggle (needs node/npx)")
    return CheckResult("screenpipe db", OK,
                       "recording data fresh (last write %d min ago)" % int(age // 60))


def _check_npx(probes: Probes):
    path = probes.which("npx")
    if not path:
        return CheckResult(
            "node/npx", WARN,
            "missing - the recording engine (`npx screenpipe`) cannot start",
            "brew install node")
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
    return CheckResult("claude auth", FAIL,
                       "live call failed via %s (exit %s: %s)" % (via, rc, tail), fix)


_CHECKS = [
    _check_home,
    _check_claude,
    _check_runtime_python,
    _check_config,
    _check_anthropic_key,
    _check_state_dirs,
    _check_launchd,
    _check_cron,
    _check_dashboard,
    _check_obsidian,
    _check_screenpipe,
    _check_npx,
    _check_gh,
]


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
    checks = list(_CHECKS)
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


def main(argv: Optional[List[str]] = None, probes: Optional[Probes] = None) -> int:
    """Run all checks, print the report, return the number of FAILs (max 99)."""
    try:
        parser = argparse.ArgumentParser(
            prog="python3 -m act.doctor",
            description="Post-install diagnostics for Zelin's AI Assistant.")
        parser.add_argument("--fast", action="store_true",
                            help="skip the live claude auth probe (spends no tokens)")
        args = parser.parse_args(argv)
        results = run_checks(probes=probes, fast=args.fast)
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
