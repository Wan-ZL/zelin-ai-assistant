"""Fresh-install summary — what a just-bootstrapped machine still needs from a human.

CONTRACT §69 (one-command bootstrap + the macOS CI acceptance run); reads the
§23 install report and the §25 doctor rows. ``python3 -m act.doctor
--fresh-install`` runs the normal checks (fast mode — no token is spent before
a key exists) and, instead of one flat ok/warn/FAIL list, buckets every row:

  wired    the installer proved it (OK rows)
  human    only a person can finish it — a TCC grant, a credential, a tool
           install (failure id / row name in the HUMAN catalogs below)
  unwired  scheduler rows that are red BECAUSE install.sh ran ``--no-launchd``
           (install report ``launchd=skipped`` carrying that marker): expected
           for a CI runner or a dry run, never "broken"
  broken   every other FAIL — the machine-readable "something is actually wrong"
  notes    the remaining WARNs (optional sources, cosmetic drift)

and lists the standing manual steps with THIS machine's paths (the daemon
interpreter from config/runtime.json, the claude binary the daemons run, the
Board app bundle). Exit code = ``len(broken)``: a fresh machine with nothing
but TCC grants and credentials left exits 0 — the owner's acceptance criterion
(「在另一台电脑上起一个空白环境……就能够直接使用」), machine-checked by
``.github/workflows/fresh-install.yml``.

Pure functions over plain dicts (doctor rows exactly as ``render_json`` emits
them + the §23 steps); the only file reads are the two helpers at the bottom,
so the buckets are testable without a machine. stdlib + act.lib only.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from act.lib import config, install_report, version as version_lib

OK, WARN, FAIL = "ok", "warn", "fail"

WIRED, HUMAN, UNWIRED, BROKEN, NOTES = "wired", "human", "unwired", "broken", "notes"
BUCKETS = (WIRED, HUMAN, UNWIRED, BROKEN, NOTES)

# `--no-launchd` marker install.sh writes into the §23 `launchd` step detail.
NO_LAUNCHD_MARKER = "--no-launchd"

# §25 failure ids whose fix is a person's act: a TCC grant (macOS asks the
# human per binary, no script can click it), a credential, a tool install.
HUMAN_FAILURE_IDS = frozenset({
    "claude_cli_missing", "claude_cli_outdated", "claude_auth_failed",
    "claude_blind", "interpreter_blind", "deploy_blind_tcc",
    "cron_tcc_blocked", "cron_fda_blocked", "ui_build_tcc_blocked",
    "node_missing", "engine_dead",
})
# Rows without a failure id (or WARN-only rows) that still belong to the human.
HUMAN_ROW_NAMES = frozenset({
    "anthropic key", "claude CLI", "daemon claude", "claude auth",
    "obsidian vault", "screenpipe db", "node/npx", "gh CLI",
    "launchd claude", "launchd volume access", "cron disk access", "cron write access",
})

# Rows that describe the resident daemons / schedulers install.sh wires in
# step 5 (launchd) and step 6 (cron). Under --no-launchd they are red by
# construction; agent rows carry the short label as their name, so the id set
# does the matching there.
UNWIRED_FAILURE_IDS = frozenset({
    "agent_unloaded", "cron_missing", "dashboard_stale", "actd_stalled",
    "board_server_down",
})
UNWIRED_ROW_NAMES = frozenset({
    "daemon claude", "launchd python", "launchd paths", "launchd fd limit",
    "launchd claude", "launchd volume access", "launchd orphans",
    "cron ingest chain", "cron digest", "cron disk access", "cron write access",
    "dashboard", "actd heartbeat", "board server", "systemd units", "scheduled tasks",
})

BOARD_BUNDLE = version_lib.BOARD_APP_NAME   # §54 bundle folder (id com.zelin.ai-board); single source
DEFAULT_CLAUDE = "~/.local/bin/claude"
CRON_BINARY = "/usr/sbin/cron"

# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #


def report_says_no_launchd(report: Optional[dict]) -> bool:
    """True when the last install.sh run wired no scheduler (`launchd=skipped`
    with the --no-launchd marker in its detail)."""
    for step in _steps(report):
        if step.get("name") == "launchd":
            return (step.get("status") == "skipped"
                    and NO_LAUNCHD_MARKER in str(step.get("detail") or ""))
    return False


def _text(value) -> str:
    """str() of a wire value, "" for None/empty (keeps the callers' branch count flat)."""
    return str(value) if value else ""


def _in_catalog(row: dict, failure_ids: frozenset, row_names: frozenset) -> bool:
    return _text(row.get("failure_id")) in failure_ids or _text(row.get("name")) in row_names


def bucket_for(row: dict, no_launchd: bool) -> str:
    """Which bucket one doctor row lands in (see the module docstring order)."""
    status = _text(row.get("status")).lower()
    if status == OK:
        return WIRED
    if no_launchd and _in_catalog(row, UNWIRED_FAILURE_IDS, UNWIRED_ROW_NAMES):
        return UNWIRED
    if _in_catalog(row, HUMAN_FAILURE_IDS, HUMAN_ROW_NAMES):
        return HUMAN
    return BROKEN if status == FAIL else NOTES


def bucket_rows(rows: List[dict], no_launchd: bool) -> Dict[str, List[dict]]:
    buckets: Dict[str, List[dict]] = {b: [] for b in BUCKETS}
    for row in rows:
        buckets[bucket_for(row, no_launchd)].append(row)
    return buckets


# --------------------------------------------------------------------------- #
# the standing manual steps, with this machine's paths
# --------------------------------------------------------------------------- #


def _steps(report: Optional[dict]) -> List[dict]:
    steps = (report or {}).get("steps") if isinstance(report, dict) else None
    return [s for s in (steps or []) if isinstance(s, dict)]


def report_step(report: Optional[dict], name: str) -> Optional[dict]:
    """Last §23 step called ``name``; None when absent."""
    hits = [s for s in _steps(report) if s.get("name") == name]
    return hits[-1] if hits else None


def daemon_claude_path(report: Optional[dict], which=shutil.which) -> str:
    """The claude binary the daemons run — the Full Disk Access subject.

    §55 第五幕: install.sh keeps a STABLE copy at ``config.stable_claude_bin()``
    (a fixed $HOME path, so the grant survives Claude Code updates); when the
    §23 ``stable_claude`` step says it is there, that path is the answer. Else
    install.sh's login-shell resolution (§23 ``claude_bin``), else PATH, else
    the default location once installed — those all move on update."""
    if _step_ok(report, "stable_claude") or config.stable_claude_present():
        return str(config.stable_claude_bin())
    if _step_ok(report, "claude_bin"):
        return _text(report_step(report, "claude_bin").get("detail")) or DEFAULT_CLAUDE
    return which("claude") or DEFAULT_CLAUDE


def _step_ok(report: Optional[dict], name: str) -> bool:
    return (report_step(report, name) or {}).get("status") == OK


def board_app_path(home_dir: Optional[Path] = None,
                   system_apps: Optional[Path] = None) -> Optional[str]:
    """Installed shell bundle (install.sh ui step: /Applications, else ~/Applications).
    ``system_apps`` is the test seam for the machine-wide folder."""
    home_dir = home_dir or Path.home()
    for base in (system_apps or Path("/Applications"), home_dir / "Applications"):
        if (base / BOARD_BUNDLE).is_dir():
            return str(base / BOARD_BUNDLE)
    return None


def repo_outside_home(home: Path, home_dir: Optional[Path] = None) -> bool:
    """§55 TCC shape: physical repo path not under the physical $HOME."""
    home_dir = home_dir or Path.home()
    try:
        repo = os.path.realpath(str(home))
        base = os.path.realpath(str(home_dir))
    except OSError:
        return False
    return not (repo == base or repo.startswith(base.rstrip(os.sep) + os.sep))


def _step(step_id: str, title: str, command: str, why: str) -> dict:
    return {"id": step_id, "title": title, "command": command, "why": why}


def _board_step(home: Path, port: int, home_dir: Optional[Path],
                system_apps: Optional[Path] = None) -> dict:
    app = board_app_path(home_dir, system_apps)
    if app:
        return _step("open_board", "Open the board app", 'open "%s"' % app,
                     "its first-run wizard (?page=setup, CONTRACT §68.5) walks config → disk access → credentials until they are in place")
    if (home / "web" / "dist" / "index.html").is_file():
        return _step("open_board", "Open the board in a browser (no shell app built — swiftc absent)",
                     "open http://127.0.0.1:%d/" % port,
                     "xcode-select --install, then bash install.sh, builds the Dock app")
    return _step("build_board", "Build the board UI",
                 "brew install node && bash %s" % (home / "install.sh"),
                 "node/npm was missing when install.sh ran, so web/dist was not built; "
                 "the server answers with a placeholder page until then")


def _fda_steps(home: Path, report: Optional[dict], home_dir: Optional[Path], which) -> List[dict]:
    required = repo_outside_home(home, home_dir)
    scope = ("REQUIRED: this repo lives outside $HOME (external volume / protected folder)"
             if required else
             "needed only if this repo or your task repos live under ~/Documents, ~/Desktop, "
             "~/Downloads or an external volume; harmless otherwise")
    python = runtime_python(home) or "<config/runtime.json python — run install.sh first>"
    claude = daemon_claude_path(report, which)
    where = ("System Settings > Privacy & Security > Full Disk Access > + (⌘⇧G, paste the path). "
             "macOS grants it PER BINARY; launchd jobs never inherit your terminal's grant")
    return [
        _step("fda_python", "Full Disk Access for the daemon interpreter", python,
              "%s — actd / radars / board server run as this binary. %s" % (where, scope)),
        _step("fda_claude", "Full Disk Access for the daemons' claude",
              claude, "dispatched agents read your repos as this binary; the stable copy under "
              "~/Library/Application Support/ZelinAIAssistant/bin/ keeps this grant valid across "
              "Claude Code updates (§55 第五幕). %s" % scope),
        _step("fda_cron", "Full Disk Access for cron", CRON_BINARY,
              "the ingest chain reads the Obsidian vault under ~/Documents"),
    ]


def _row_ok(rows_by_name: Dict[str, dict], name: str, default_ok: bool) -> bool:
    row = rows_by_name.get(name)
    return default_ok if row is None else row.get("status") == OK


def _claude_step(rows_by_name: Dict[str, dict]) -> List[dict]:
    # no `claude CLI` row at all (platform branch without it) = nothing to say
    if _row_ok(rows_by_name, "claude CLI", default_ok=True):
        return []
    return [_step("install_claude", "Install Claude Code CLI",
                  "curl -fsSL https://claude.ai/install.sh | bash   # then: claude login",
                  "radars, triage and every dispatched agent run on headless claude")]


def _key_step(rows_by_name: Dict[str, dict], home: Path) -> List[dict]:
    if _row_ok(rows_by_name, "anthropic key", default_ok=False):
        return []
    secrets_dir = home / "config/secrets"
    key_file = home / "config/secrets/anthropic-api-key.txt"
    return [_step(
        "api_key", "Anthropic API key file (headless claude under launchd/cron cannot read Keychain OAuth)",
        "mkdir -p %s && chmod 700 %s && printf '%%s\\n' 'sk-ant-…' > %s && chmod 600 %s"
        % (secrets_dir, secrets_dir, key_file, key_file),
        "~/.config/anthropic-key.txt is still honored as the legacy fallback")]


def _wire_step(report: Optional[dict], home: Path) -> List[dict]:
    if not report_says_no_launchd(report):
        return []
    return [_step("wire_scheduler", "Wire the daemons (this run passed --no-launchd)",
                  "bash %s" % (home / "install.sh"),
                  "no launchd agent or crontab line was installed; nothing runs in the background yet")]


def manual_steps(rows: List[dict], report: Optional[dict], home: Path,
                 home_dir: Optional[Path] = None, which=shutil.which,
                 port: Optional[int] = None, system_apps: Optional[Path] = None) -> List[dict]:
    """The ordered to-do list for the human, each with a copy-pasteable command."""
    port = port if port is not None else _server_port()
    by_name = {str(r.get("name")): r for r in rows}
    return ([_board_step(home, port, home_dir, system_apps)]
            + _claude_step(by_name) + _key_step(by_name, home)
            + _fda_steps(home, report, home_dir, which)
            + _wire_step(report, home))


# --------------------------------------------------------------------------- #
# summary + rendering
# --------------------------------------------------------------------------- #


def summarize(rows: List[dict], report: Optional[dict], home: Path,
              home_dir: Optional[Path] = None, which=shutil.which,
              port: Optional[int] = None, system_apps: Optional[Path] = None) -> dict:
    """The whole verdict as one JSON-able dict (fields add-only)."""
    no_launchd = report_says_no_launchd(report)
    buckets = bucket_rows(rows, no_launchd)
    return {
        "home": str(home),
        "no_launchd": no_launchd,
        "buckets": buckets,
        "manual_steps": manual_steps(rows, report, home, home_dir, which, port, system_apps),
        "exit_code": min(len(buckets[BROKEN]), 99),
    }


_BUCKET_TITLES = {
    WIRED: "wired by the installer",
    HUMAN: "waiting on you (the machine cannot do these)",
    UNWIRED: "not wired in this run (--no-launchd) — expected, run bash install.sh for a real install",
    BROKEN: "BROKEN — needs a fix before this machine is usable",
    NOTES: "notes",
}
_BADGE = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[FAIL]"}


def _row_lines(rows: List[dict], with_fix: bool) -> List[str]:
    lines = []
    for r in rows:
        badge = _BADGE.get(str(r.get("status") or "").lower(), "[ ?  ]")
        lines.append("  %s %s: %s" % (badge, r.get("name"), r.get("detail")))
        if with_fix and r.get("fix"):
            lines.append("         fix: %s" % r["fix"])
    return lines


def render(summary: dict) -> str:
    """Human-readable report; the bucket order is the reading order."""
    buckets = summary["buckets"]
    lines = ["fresh install — %s" % summary["home"], ""]
    lines.append("%s (%d):" % (_BUCKET_TITLES[WIRED], len(buckets[WIRED])))
    wired_names = ", ".join(str(r.get("name")) for r in buckets[WIRED])
    lines.append("  " + (wired_names or "(none)"))
    for key in (BROKEN, HUMAN, UNWIRED, NOTES):
        if not buckets[key]:
            continue
        lines += ["", "%s (%d):" % (_BUCKET_TITLES[key], len(buckets[key]))]
        lines += _row_lines(buckets[key], with_fix=(key != UNWIRED))
    lines += ["", "what is left for you, in order:"]
    for i, step in enumerate(summary["manual_steps"], 1):
        lines.append("  %d. %s" % (i, step["title"]))
        lines.append("       %s" % step["command"])
        lines.append("       why: %s" % step["why"])
    n = summary["exit_code"]
    lines += ["", "exit %d — %s" % (n, "nothing broken: the rest is yours" if n == 0
                                     else "%d broken row(s) above" % n)]
    return "\n".join(lines)


def render_json(summary: dict) -> str:
    return json.dumps(summary, ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------- #
# the two file reads
# --------------------------------------------------------------------------- #


def read_report(path: Optional[Path] = None) -> Optional[dict]:
    """§23 install_report.json as a dict; None when absent / torn (never raises)."""
    path = path or install_report.REPORT_PATH
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def runtime_python(home: Path) -> Optional[str]:
    """§19 daemon interpreter pin (config/runtime.json ``python``)."""
    try:
        doc = json.loads((home / "config" / "runtime.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    py = doc.get("python") if isinstance(doc, dict) else None
    return str(py) if py else None


def _server_port() -> int:
    try:
        return int(config.load_config().server_port)
    except Exception:  # noqa: BLE001 - a config typo must not hide the summary
        return int(config.DEFAULT_SERVER_PORT)
