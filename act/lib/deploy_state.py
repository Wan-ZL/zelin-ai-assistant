"""``deploy_state.json`` readers (CONTRACT §56 合并即上岗).

Two files, one writer (scripts/auto-deploy.sh; one flat object of strings,
atomic tmp+rename, rewritten every run):

- ``state/deploy_state.json`` in the repo — the PROJECTION, written
  best-effort. Read here for the dashboard (add-only top-level key
  ``deploy_state``, §2 sibling field, same convention as ``update_available``
  / ``device_label``: absent when the file is absent or unreadable) and for
  the ``act.doctor`` ``auto-deploy`` row (OK for deployed/up_to_date, WARN for
  every other outcome, no row when the file does not exist).
- ``~/Library/Application Support/ZelinAIAssistant/deploy_state.json`` — the
  HOME MIRROR, the script's own truth (v0.48.17, §56.4). macOS gates
  background access to removable volumes per responsible executable (TCC)
  and never gates ``$HOME``, so a launchd-fired run that cannot touch the
  repo still records what happened here. It carries every projected field
  plus the run's identity (``trigger`` / ``interpreter`` / ``volume`` /
  ``repo``) and the ``unattended_*`` triple — the last verdict of a run NOT
  started from a terminal, which the doctor's ``launchd volume access`` row
  reads (a green ``--force`` from the owner's terminal inherits the
  terminal's grants and proves nothing about the job).

Field by field type-checking, never raising: the writer is a shell script and
a torn/half-edited file must not take the dashboard pass down (§0 第 11 条).
Unknown keys are dropped, unknown ``status`` values are kept verbatim
(add-only: readers tolerate what they do not know).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Optional

from act.lib import config, failures

PATH: Path = config.STATE_DIR / "deploy_state.json"
# Same directory as the §19 home pointer (home.txt): one app-support dir.
MIRROR_PATH: Path = (Path.home() / "Library" / "Application Support"
                     / "ZelinAIAssistant" / "deploy_state.json")

# Projected keys (all strings). v0.48.17 add-only: `running_version` (what
# state/actd.heartbeat says is in memory), `install_report_version` (what
# install.sh last finished on), `reason` (machine tokens behind a non-healthy
# status). The script also keeps private bookkeeping (`notified_sha`,
# `incomplete_runs`, `incomplete_sha`, `tcc_notified_day`); not projected.
FIELDS = ("status", "version", "head", "prev", "last_deployed", "last_run",
          "detail", "failed_sha", "running_version", "install_report_version",
          "reason")

# Mirror-only keys (never in the dashboard: local paths and the unattended
# triple are diagnostics for the doctor, not board content).
MIRROR_FIELDS = FIELDS + ("trigger", "interpreter", "volume", "repo", "denied_path",
                          "unattended_status", "unattended_last_run",
                          "unattended_detail")

# Outcomes that mean "nothing needs a human" — everything else is a WARN row.
HEALTHY = frozenset({"deployed", "up_to_date"})

# §56.4 open vocabulary, v0.48.17 additions: `install_incomplete` (HEAD is at
# origin/main but install_report / heartbeat disagree — install.sh re-run),
# `blocked_tcc` (the volume-access probe got EPERM before any git call).
INSTALL_INCOMPLETE = "install_incomplete"
BLOCKED_TCC = "blocked_tcc"


def _load_object(target: Path) -> Optional[dict]:
    """The file as a dict, or None when absent / unreadable / not an object."""
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _read_fields(target: Path, fields) -> Optional[dict]:
    raw = _load_object(target)
    if raw is None:
        return None
    out = {}
    for key in fields:
        value = raw.get(key)
        if isinstance(value, str) and value:
            out[key] = value
    return out or None


def read(path: Optional[Path] = None) -> Optional[dict]:
    """The sanitized deploy state, or None when absent/unreadable/not a dict."""
    return _read_fields(path or PATH, FIELDS)


def read_mirror(path: Optional[Path] = None) -> Optional[dict]:
    """The sanitized HOME mirror (superset of :func:`read`), or None."""
    return _read_fields(path or MIRROR_PATH, MIRROR_FIELDS)


def attach(dash: dict, path: Optional[Path] = None) -> dict:
    """Set ``dash["deploy_state"]`` when a readable state exists (add-only)."""
    state = read(path)
    if state:
        dash["deploy_state"] = state
    return dash


# --------------------------------------------------------------------------- #
# §56.3 第 1 步 — the unattended verdict the doctor's `launchd volume access`
# row renders (pure functions: no file IO, no CheckResult; act/doctor.py wraps)
# --------------------------------------------------------------------------- #
# What EPERM looks like in launchd's stderr (2026-09-02 verbatim): python's
# PermissionError [Errno 1], rm/bash "Operation not permitted", and the python
# launcher failing to import act at all (`python3 -m act.auto_deploy` dies
# before the script runs — the mirror never gets written, this log is the only
# witness).
# Two regexes, searched in order: an EPERM spelling anywhere in the tail is
# unambiguous and wins; the import failure alone is the fallback (ambiguous).
EPERM_RE = re.compile(r"PermissionError: \[Errno 1\]|Operation not permitted|\bEPERM\b")
IMPORT_RE = re.compile(r"No module named 'act'")
# launchd's stderr file has no timestamps: "written in the last 24h" can only
# mean the file's mtime.
LAUNCHD_LOG_EVIDENCE_S = 24 * 3600

# verdict kinds (add-only)
BLOCKED_MIRROR = "blocked_mirror"   # the last unattended run recorded blocked_tcc
BLOCKED_LOG = "blocked_log"         # launchd stderr shows EPERM / import failure < 24h
OK_RECORDED = "ok_recorded"         # an unattended run reached the repo
OK_NONE = "ok_none"                 # nothing recorded yet
# `No module named 'act'` on its own is ambiguous (§55): a TCC-blind
# interpreter OR a plist whose PYTHONPATH is wrong/symlink-shaped look
# identical in the log; only the EPERM spellings are unambiguous.
IMPORT_ONLY = "No module named 'act'"


def parse_iso_utc(text) -> Optional[float]:
    """`2026-09-02T00:48:54Z` → epoch seconds; None when not that shape."""
    try:
        return _dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def same_repo(path, repo: str) -> bool:
    """Empty path = unknown = assume ours; otherwise compare physical paths."""
    return not path or os.path.realpath(str(path)) == os.path.realpath(repo)


def mirror_for_repo(mirror, repo: str) -> dict:
    """The mirror when it describes THIS checkout, else {} (another clone's
    verdict is not evidence about ours)."""
    mirror = dict(mirror or {})
    if same_repo(mirror.get("repo", ""), repo):
        return mirror
    return {}


def _superseded(last_unattended: str, log_mtime: float) -> bool:
    """A later unattended run that reached the repo retires older log lines."""
    later = parse_iso_utc(last_unattended)
    return later is not None and later > log_mtime


def _denial_match(log_tail: str):
    """The unambiguous EPERM spelling if any, else the import-only line, else None."""
    return EPERM_RE.search(log_tail) or IMPORT_RE.search(log_tail)


def log_evidence(log_tail: str, log_mtime, now: float, last_unattended: str):
    """(matched text, age seconds) when launchd's stderr still counts as
    evidence of a blocked job; None otherwise."""
    if not log_tail or log_mtime is None:
        return None
    age = now - log_mtime
    if not 0 <= age < LAUNCHD_LOG_EVIDENCE_S:
        return None
    hit = _denial_match(log_tail)
    if hit is None or _superseded(last_unattended, log_mtime):
        return None
    return hit.group(0), age


def _nz(value: str, default: str) -> str:
    return value if value else default


def unattended_verdict(mirror, repo: str, log_tail: str, log_mtime, now: float) -> dict:
    """Combine the HOME mirror's unattended triple with the launchd stderr
    evidence into one dict: ``{"kind": <BLOCKED_MIRROR|BLOCKED_LOG|OK_RECORDED|
    OK_NONE>, ...}``. Never raises on odd inputs."""
    m = mirror_for_repo(mirror, repo)
    last = str(m.get("unattended_last_run", ""))
    if m.get("unattended_status") == BLOCKED_TCC:
        return {"kind": BLOCKED_MIRROR, "last_run": _nz(last, "?"),
                "volume": _nz(str(m.get("volume", "")), repo),
                "denied_path": _nz(str(m.get("denied_path", "")), repo),
                "detail": _nz(str(m.get("unattended_detail", "")), BLOCKED_TCC)}
    evidence = log_evidence(log_tail, log_mtime, now, last)
    if evidence is not None:
        return {"kind": BLOCKED_LOG, "match": evidence[0], "age_s": evidence[1],
                "ambiguous": evidence[0] == IMPORT_ONLY}
    if last:
        return {"kind": OK_RECORDED, "last_run": last,
                "status": _nz(str(m.get("unattended_status", "")), "?")}
    return {"kind": OK_NONE}


def volume_access_fix(interp: str) -> str:
    """The doctor's remediation for a TCC-blind deploy job — both grants named
    (the plist's ProgramArguments[0] and the absolute claude link), and the
    2026-09-02 observation spelled out: runs started from a terminal (even a
    `launchctl kickstart` typed there) inherit the terminal's grant, so a green
    one proves nothing about timer-fired runs; wait for the timer."""
    claude_link = str(Path.home() / ".local" / "bin" / "claude")
    return failures.pick(
        "系统设置 → 隐私与安全性 → 完全磁盘访问 → +（Command-Shift-G 粘贴路径），加两条："
        "① 后台任务的解释器 %s（plist 的 ProgramArguments[0]）；② %s（claude，实体在 "
        "~/.local/share/claude/versions/<v>，claude 每次更新后重做）。然后**等 timer 自己"
        "触发一轮（≤ 10 min）**再重跑 doctor 看本行。从终端起的运行——bash scripts/"
        "auto-deploy.sh、python3 -m act.auto_deploy、乃至在终端里敲的 launchctl kickstart"
        "——都继承终端的授权，绿了对 timer 触发的运行什么都不证明"
        % (interp, claude_link),
        "System Settings > Privacy & Security > Full Disk Access > + (Command-Shift-G, paste "
        "the path), two entries: (1) the job's interpreter %s (the plist's "
        "ProgramArguments[0]); (2) %s (claude; the binary lives in "
        "~/.local/share/claude/versions/<v>, redo after every claude update). Then WAIT for "
        "the timer to fire one run (<= 10 min) and re-run doctor. Runs started from a "
        "terminal - bash scripts/auto-deploy.sh, python3 -m act.auto_deploy, even a "
        "launchctl kickstart typed in that terminal - inherit the terminal's grant: a green "
        "one proves nothing about timer-fired runs" % (interp, claude_link))
