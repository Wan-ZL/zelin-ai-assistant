"""Claude Code session import — one-shot cold-start radar (CONTRACT §22).

The target user almost certainly already uses Claude Code, so their recent
sessions are the cheapest possible seed for an empty kanban: scan
``~/.claude/projects/<slug>/*.jsonl`` transcripts, surface "work you were
just doing" (especially sessions that ended with the AI waiting on a reply),
and turn the selected ones into normal proposal cards.

Design notes:
- **One-shot, not a daemon.** Triggered by the Settings section (via the
  ``import_claude_sessions`` inbox action) or the CLI below. Never runs on a
  schedule; never watches the directory.
- **Cheap and local.** No LLM calls. Gist = first user message head + last
  assistant head, truncated. Only the head and tail of each transcript are
  read (sessions can be tens of MB), and only files whose mtime falls inside
  the scan window are opened at all.
- **Privacy.** Everything stays on this machine. Session text becomes card
  text exactly like any other radar source; nothing is uploaded.
- **Dedupe / re-run safety.** Two belts: imported session ids are recorded in
  ``state/claude_sessions_import.json`` (scan skips them), and card creation
  goes through ``registry.merge_or_new`` so a restated title merges instead of
  duplicating. Sessions this product itself dispatched (their session_id is in
  a registry entry's ``execution.session_id``) are excluded — our own agents'
  work must not boomerang back as new cards.

CLI:
    python3 -m act.radar_claude_sessions --once --window 7        # import
    python3 -m act.radar_claude_sessions --once --window 7 --all  # incl. non-waiting
    python3 -m act.radar_claude_sessions --scan --window 7        # JSON preview
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Optional

from act.lib import analytics, config, registry

STATE_FILE = "claude_sessions_import.json"   # {"imported": {session_id: ISO}}
DEFAULT_WINDOW_DAYS = 7
MAX_CANDIDATES = 100

# transcript reading budgets (files can be tens of MB — never read them whole)
_HEAD_MAX_LINES = 200
_HEAD_MAX_BYTES = 512 * 1024
_TAIL_MAX_BYTES = 512 * 1024

_GIST_PART_CHARS = 90
_TITLE_CHARS = 72

# Session ended with the AI asking for input — checked against the tail of the
# final assistant message. Deliberately simple/cheap (no LLM): a question mark
# or a common "over to you" phrase in either language.
_QUESTION_TAIL_CHARS = 240
_ASK_PATTERNS = re.compile(
    r"[?？]"
    r"|let me know|would you like|should i|which (one|option|approach)"
    r"|waiting for your|your call|up to you"
    r"|请确认|请告诉|请选择|需要你|等你|要不要|吗[。!！\s]*$",
    re.IGNORECASE,
)

_TAG_RE = re.compile(r"<[^<>\n]{1,80}>")   # <command-name>… wrappers etc.


# --------------------------------------------------------------------------- #
# where the transcripts live
# --------------------------------------------------------------------------- #
def projects_root() -> Path:
    """``$CLAUDE_CONFIG_DIR/projects`` (Claude Code's own env var), default
    ``~/.claude/projects``. Resolved per call so tests can point it at a
    fixture directory."""
    base = os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")
    return Path(base).expanduser() / "projects"


# --------------------------------------------------------------------------- #
# imported-session marker (state/claude_sessions_import.json)
# --------------------------------------------------------------------------- #
def _marker_path() -> Path:
    return config.STATE_DIR / STATE_FILE


def _load_imported() -> dict:
    try:
        data = json.loads(_marker_path().read_text(encoding="utf-8"))
        imported = data.get("imported")
        return imported if isinstance(imported, dict) else {}
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}


def _mark_imported(session_ids: list) -> None:
    imported = _load_imported()
    now = _iso_now()
    for sid in session_ids:
        imported[str(sid)] = now
    p = _marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"imported": imported}, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(p)


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# transcript parsing (head + tail only)
# --------------------------------------------------------------------------- #
def _parse_lines(chunk: str) -> list:
    out = []
    for line in chunk.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _head_entries(path: Path) -> list:
    entries = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            read = 0
            for i, line in enumerate(fh):
                if i >= _HEAD_MAX_LINES or read >= _HEAD_MAX_BYTES:
                    break
                read += len(line)
                entries.extend(_parse_lines(line))
    except OSError:
        pass
    return entries


def _tail_entries(path: Path) -> list:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > _TAIL_MAX_BYTES:
                fh.seek(size - _TAIL_MAX_BYTES)
                chunk = fh.read().decode("utf-8", "replace")
                # first line is almost certainly partial — drop it
                chunk = chunk.split("\n", 1)[1] if "\n" in chunk else ""
            else:
                chunk = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    return _parse_lines(chunk)


def _entry_text(entry: dict) -> str:
    """Plain conversation text of a user/assistant entry; "" for anything else
    (tool results, thinking, meta/sidechain lines, attachments, …)."""
    etype = entry.get("type")
    if etype not in ("user", "assistant"):
        return ""
    if entry.get("isSidechain") or entry.get("isMeta"):
        return ""
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [it.get("text", "") for it in content
                 if isinstance(it, dict) and it.get("type") == "text"]
        return "\n".join(p for p in parts if p).strip()
    return ""


def _clean_head(text: str, limit: int) -> str:
    """One display line: strip <tag> wrappers, collapse whitespace, truncate."""
    text = _TAG_RE.sub(" ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text


def _looks_like_question(text: str) -> bool:
    tail = (text or "").rstrip()[-_QUESTION_TAIL_CHARS:]
    return bool(_ASK_PATTERNS.search(tail))


def _parse_ts(value) -> Optional[_dt.datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def _candidate_from_file(path: Path) -> Optional[dict]:
    """Build one scan candidate from a transcript. None = not importable
    (no real conversation text found in the head/tail windows)."""
    head = _head_entries(path)
    tail = _tail_entries(path)

    first_user = ""
    ai_title = ""
    cwd = ""
    for e in head:
        if e.get("type") == "ai-title" and isinstance(e.get("aiTitle"), str):
            ai_title = e["aiTitle"].strip()
        if not cwd and isinstance(e.get("cwd"), str):
            cwd = e["cwd"]
        if not first_user and e.get("type") == "user":
            first_user = _entry_text(e)

    last_assistant = ""
    last_role = ""          # role of the LAST real conversation text in the file
    last_ts: Optional[_dt.datetime] = None
    for e in tail:
        ts = _parse_ts(e.get("timestamp"))
        if ts is not None and (last_ts is None or ts > last_ts):
            last_ts = ts
        if not cwd and isinstance(e.get("cwd"), str):
            cwd = e["cwd"]
        text = _entry_text(e)
        if text:
            last_role = e.get("type", "")
            if e.get("type") == "assistant":
                last_assistant = text
    if not first_user:
        # long first prompt pushed out of the head window — fall back to the
        # last-prompt marker Claude Code keeps near the end of the file
        for e in tail:
            if e.get("type") == "last-prompt" and isinstance(e.get("lastPrompt"), str):
                first_user = e["lastPrompt"]
                break

    user_head = _clean_head(first_user, _GIST_PART_CHARS)
    assistant_head = _clean_head(last_assistant, _GIST_PART_CHARS)
    if not user_head and not assistant_head:
        return None   # bookkeeping-only file (queue ops, snapshots, …)

    if user_head and assistant_head:
        gist = f"{user_head} → {assistant_head}"
    else:
        gist = user_head or assistant_head
    title = _clean_head(ai_title, _TITLE_CHARS) or _clean_head(first_user, _TITLE_CHARS) \
        or _clean_head(last_assistant, _TITLE_CHARS)

    if last_ts is None:
        try:
            last_ts = _dt.datetime.fromtimestamp(path.stat().st_mtime,
                                                 _dt.timezone.utc)
        except OSError:
            last_ts = _dt.datetime.now(_dt.timezone.utc)

    waiting = last_role == "assistant" and _looks_like_question(last_assistant)
    project_dir = cwd or ""
    project = Path(project_dir).name if project_dir else path.parent.name

    return {
        "session_id": path.stem,
        "session_file": str(path),
        "project": project,
        "project_dir": project_dir,
        "title": title,
        "gist": gist,
        "last_activity": last_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ended_waiting_on_user": waiting,
    }


# --------------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------------- #
def _registry_session_ids() -> set:
    """Session ids this product itself dispatched (execution.session_id) —
    never re-import our own agents' work as new cards."""
    ids = set()
    try:
        for r in registry.load_all():
            ex = r.execution if isinstance(r.execution, dict) else {}
            for key in ("session_id", "aborted_session_id"):
                sid = ex.get(key)
                if sid:
                    ids.add(str(sid))
    except Exception:  # noqa: BLE001 — a broken registry must not kill the scan
        pass
    return ids


def scan(window_days: int = DEFAULT_WINDOW_DAYS, *,
         include_imported: bool = False,
         root: Optional[Path] = None) -> list:
    """Candidates from the last ``window_days`` days, waiting-on-you first,
    then most recent first. Already-imported and self-dispatched sessions are
    excluded. Returns [] when the Claude directory does not exist."""
    root = root or projects_root()
    if not root.is_dir():
        return []
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=window_days)
    imported = {} if include_imported else _load_imported()
    own_sessions = _registry_session_ids()

    out = []
    for project_dir in sorted(root.iterdir()):
        if not project_dir.is_dir():
            continue
        # top level only — <session>/subagents/**.jsonl are subagent
        # transcripts of a session, not sessions themselves
        for f in project_dir.glob("*.jsonl"):
            sid = f.stem
            if sid in imported or sid in own_sessions:
                continue
            try:
                mtime = _dt.datetime.fromtimestamp(f.stat().st_mtime,
                                                   _dt.timezone.utc)
            except OSError:
                continue
            if mtime < cutoff:
                continue
            cand = _candidate_from_file(f)
            if cand is None:
                continue
            ts = _parse_ts(cand["last_activity"])
            if ts is not None and ts < cutoff:
                continue
            out.append(cand)

    # stable double sort: newest first inside each group, waiting-on-you group first
    out.sort(key=lambda c: c["last_activity"], reverse=True)
    out.sort(key=lambda c: not c["ended_waiting_on_user"])
    return out[:MAX_CANDIDATES]


# --------------------------------------------------------------------------- #
# import
# --------------------------------------------------------------------------- #
def _import_candidates(cands: list) -> int:
    """Selected candidates -> normal proposal cards. Waiting-on-you sessions
    land in 待审批 (card_sent); merely-recent ones in 欠账 (detected) — the
    same confidence split the other radars use. Returns cards created/merged."""
    created = 0
    done_ids = []
    for c in cands:
        target = c.get("project_dir") or None
        if target and not Path(target).expanduser().is_dir():
            target = None
        new = registry.Requirement(
            id=registry.next_id(),
            title=(c.get("title") or c.get("gist") or "")[:80],
            summary=c.get("gist") or "",
            type="code",
            tier="T1",
            status=(registry.State.CARD_SENT.value
                    if c.get("ended_waiting_on_user")
                    else registry.State.DETECTED.value),
            hardness="soft",
            plan=[],
            sources=[{
                "who": "claude-code",
                "channel": "claude_code",
                "date": (c.get("last_activity") or "")[:10],
                "quote": c.get("gist") or "",
                "ref": c.get("session_id"),
            }],
            target_repo=target,
            notes="claude-code 导入 / imported from Claude Code session "
                  f"{(c.get('session_id') or '')[:8]}",
        )
        registry.merge_or_new(new)
        created += 1
        done_ids.append(c.get("session_id"))
    if done_ids:
        _mark_imported(done_ids)
    return created


def import_by_ids(session_ids: list, root: Optional[Path] = None) -> int:
    """Import specific sessions (the Settings checkbox flow). Ids are resolved
    straight to ``<projects>/*/<id>.jsonl`` — no full scan. Already-imported
    ids are skipped (re-run safe)."""
    root = root or projects_root()
    imported = _load_imported()
    cands = []
    for sid in session_ids:
        sid = str(sid or "").strip()
        # ids come from an inbox file — never let one form a path traversal
        if not sid or "/" in sid or sid in imported:
            continue
        for f in root.glob(f"*/{sid}.jsonl"):
            cand = _candidate_from_file(f)
            if cand is not None:
                cands.append(cand)
            break
    n = _import_candidates(cands)
    analytics.log_event("claude_sessions_import", requested=len(session_ids),
                        imported=n)
    return n


def run_once(window_days: int = DEFAULT_WINDOW_DAYS, *,
             include_all: bool = False,
             root: Optional[Path] = None) -> int:
    """Scan + import in one shot (CLI / inbox action without explicit ids).
    Default imports only waiting-on-you sessions — the same tired-user default
    as the Settings checkboxes; ``include_all`` imports every candidate."""
    cands = scan(window_days, root=root)
    if not include_all:
        cands = [c for c in cands if c["ended_waiting_on_user"]]
    n = _import_candidates(cands)
    analytics.log_event("claude_sessions_import", requested=len(cands),
                        imported=n, window_days=window_days)
    return n


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="radar_claude_sessions")
    parser.add_argument("--once", action="store_true",
                        help="scan + import in one shot")
    parser.add_argument("--scan", action="store_true",
                        help="scan only; print candidates as JSON (app preview)")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW_DAYS,
                        metavar="DAYS", help="scan window in days (default 7)")
    parser.add_argument("--all", action="store_true",
                        help="with --once: import merely-recent sessions too "
                             "(default: only waiting-on-you)")
    args = parser.parse_args(argv)

    if args.scan:
        root = projects_root()
        if not root.is_dir():
            print(json.dumps({"ok": False, "reason": "no_claude_dir",
                              "root": str(root)}, ensure_ascii=False))
            return 0
        cands = scan(args.window)
        print(json.dumps({"ok": True, "root": str(root), "candidates": cands},
                         ensure_ascii=False))
        return 0

    if args.once:
        n = run_once(args.window, include_all=args.all)
        print(f"claude sessions import: {n} card(s)")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
