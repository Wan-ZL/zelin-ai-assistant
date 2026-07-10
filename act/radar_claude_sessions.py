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
- **Import gate (2026-07 口径; softened to a SOFT gate 2026-07-10).** A card
  means "Zelin still owes this session an action". Closed-loop Q&A — the user
  asked a question and already got the answer in-session, with no pending
  deliverable — should not become a card. But the detector is a cheap regex
  with false positives (a work order phrased with 怎么/为什么/… whose final
  message is a plain completion summary), so the gate must never make a
  session PERMANENTLY unimportable:
  * bulk paths (``run_once``, incl. ``--all``) skip ``answered`` candidates
    and log ``radar_skip`` ``reason=answered`` — the tired-user default;
  * ``scan()`` still RETURNS them, flagged ``answered: true`` and sorted
    last, so the Settings checkbox list keeps an escape hatch (they are
    never pre-checked — only waiting-on-you sessions are);
  * an EXPLICIT selection (Settings checkboxes / ``import_by_ids``)
    overrides the heuristic and imports, logging ``radar_gate_override``.
  ``session_mismatch`` stays a HARD refusal on every path (wrong binding is
  never importable). The answered heuristic is never applied to the
  lastPrompt fallback (that is the LAST user message, not the work order).
- **Session binding.** A card must bind the session its content really came
  from. session_id + cwd are taken from the transcript's own main-chain
  conversation entries (never from bookkeeping/copied lines): if any
  main-chain entry carries a ``sessionId`` different from the filename, the
  file does not own that content and the candidate is skipped
  (``radar_skip`` reason=``session_mismatch``). The verified binding —
  session id in ``ref`` plus the transcript's final cwd — is written into the
  card's source, so downstream resume/attach flows never have to guess.

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

# First user message looks like a QUESTION (info request), not a work order.
# Used by the closed-loop-Q&A gate below — deliberately cheap, both languages.
_QA_PROMPT_RE = re.compile(
    r"[?？]"
    r"|^(what|when|where|which|who|whose|why|how|is|are|was|were|does|do|did"
    r"|can|could|would|should|tell me|find( me)?|look up|remind me)\b"
    r"|帮我(找|查|看|搜)|查一下|找一下|搜一下|是什么|什么是|有没有|多少"
    r"|怎么|如何|为什么|哪|吗[。!！\s]*$|呢[。!！\s]*$",
    re.IGNORECASE,
)

# The final assistant message promises MORE WORK (something left to send /
# finish / follow up) — a pending deliverable keeps the session importable.
_FOLLOWUP_RE = re.compile(
    r"i['’]ll\b|i will\b|will (send|draft|follow|update|prepare|schedule|ping)"
    r"|next step|to-?do|follow[- ]?up|remaining|left to do|once you|when you"
    r"|待(办|发送|跟进|完成|确认)|接下来(我|会)|然后我(会|来)|稍后|回头"
    r"|还需要|尚未|未完成",
    re.IGNORECASE,
)
_FOLLOWUP_TAIL_CHARS = 600


def _answered_qa(first_user: str, last_assistant: str, last_role: str,
                 waiting: bool) -> bool:
    """Closed-loop Q&A: the user asked, the assistant answered, nothing is
    pending. Such sessions must not become cards (import gate, 2026-07 口径:
    卡片门槛 = 现在需要 Zelin 行动；已当场拿到答案的纯问答不成卡)."""
    if waiting or last_role != "assistant" or not last_assistant:
        return False
    if not first_user or not _QA_PROMPT_RE.search(first_user.strip()):
        return False    # work order / task prompt — not a plain info request
    return not _FOLLOWUP_RE.search(last_assistant.rstrip()[-_FOLLOWUP_TAIL_CHARS:])


def _hard_skip_reason(cand: dict) -> Optional[str]:
    """Why a candidate must NEVER be imported, on any path (None = ok).

    Only the session binding gate is hard: a mismatched sessionId means the
    file does not own the content (例4a 张冠李戴). The ``answered`` flag is a
    SOFT gate by contrast — bulk paths skip it, but scan() still offers it
    and an explicit import overrides it (cheap-regex false positives must
    never make a session permanently unimportable)."""
    if cand.get("session_mismatch"):
        return "session_mismatch"
    return None


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


def _is_main_chain(entry: dict) -> bool:
    """Real conversation line of THIS session (user/assistant, not a subagent
    sidechain or meta line). Binding facts — cwd, sessionId — are only trusted
    from these lines, never from bookkeeping (queue-operation, snapshots) or
    copied/attachment lines, which is how a card ends up pointing at somebody
    else's session (例4a 张冠李戴)."""
    return (entry.get("type") in ("user", "assistant")
            and not entry.get("isSidechain") and not entry.get("isMeta"))


def _candidate_from_file(path: Path) -> Optional[dict]:
    """Build one scan candidate from a transcript. None = not importable
    (no real conversation text found in the head/tail windows).

    Binding contract: ``session_id`` is the filename stem, verified against
    the ``sessionId`` carried by the main-chain entries themselves — any
    disagreement flags the candidate ``session_mismatch`` (the file does not
    own this content; callers skip it). ``project_dir`` is the LAST main-chain
    cwd (sessions migrate into worktrees mid-flight; resume is scoped to the
    final cwd), falling back to the first one seen in the head."""
    head = _head_entries(path)
    tail = _tail_entries(path)
    stem = path.stem

    first_user = ""
    ai_title = ""
    head_cwd = ""
    mismatch = False
    for e in head:
        if e.get("type") == "ai-title" and isinstance(e.get("aiTitle"), str):
            ai_title = e["aiTitle"].strip()
        if not _is_main_chain(e):
            continue
        sid = e.get("sessionId")
        if isinstance(sid, str) and sid and sid != stem:
            mismatch = True
        if not head_cwd and isinstance(e.get("cwd"), str):
            head_cwd = e["cwd"]
        if not first_user and e.get("type") == "user":
            first_user = _entry_text(e)

    last_assistant = ""
    last_role = ""          # role of the LAST real conversation text in the file
    last_ts: Optional[_dt.datetime] = None
    tail_cwd = ""
    for e in tail:
        ts = _parse_ts(e.get("timestamp"))
        if ts is not None and (last_ts is None or ts > last_ts):
            last_ts = ts
        if not _is_main_chain(e):
            continue
        sid = e.get("sessionId")
        if isinstance(sid, str) and sid and sid != stem:
            mismatch = True
        if isinstance(e.get("cwd"), str) and e["cwd"]:
            tail_cwd = e["cwd"]
        text = _entry_text(e)
        if text:
            last_role = e.get("type", "")
            if e.get("type") == "assistant":
                last_assistant = text
    cwd = tail_cwd or head_cwd
    # The answered-Q&A heuristic only ever judges the REAL first prompt (from
    # the head window). The lastPrompt fallback below is the LAST user message
    # (e.g. a closing question), not the work order — judging it would widen
    # the false-positive surface, so it feeds gist/title only.
    head_first_user = first_user
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
        "session_id": stem,
        "session_file": str(path),
        "project": project,
        "project_dir": project_dir,
        "title": title,
        "gist": gist,
        "last_activity": last_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ended_waiting_on_user": waiting,
        # import-gate flags: session_mismatch is hard (never import);
        # answered is soft (bulk paths skip, explicit import overrides)
        "answered": _answered_qa(head_first_user, last_assistant, last_role,
                                 waiting),
        "session_mismatch": mismatch,
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
    then most recent first, answered Q&A last. Already-imported and
    self-dispatched sessions are excluded; session-id mismatches (hard gate)
    are dropped with one aggregate ``radar_skip`` event. Answered closed-loop
    Q&A candidates are RETURNED, flagged ``answered: true`` and sorted to the
    bottom — the Settings list keeps them as an (unchecked) escape hatch
    because the heuristic has false positives; the bulk import paths skip
    them instead (see ``run_once``). Returns [] when the Claude directory
    does not exist."""
    root = root or projects_root()
    if not root.is_dir():
        return []
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=window_days)
    imported = {} if include_imported else _load_imported()
    own_sessions = _registry_session_ids()

    out = []
    skipped: dict = {}
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
            reason = _hard_skip_reason(cand)
            if reason:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            out.append(cand)
    for reason, n in sorted(skipped.items()):
        analytics.log_event("radar_skip", source="claude_code", reason=reason,
                            count=n)

    # stable triple sort: newest first inside each group; answered Q&A sinks
    # to the bottom; waiting-on-you group first
    out.sort(key=lambda c: c["last_activity"], reverse=True)
    out.sort(key=lambda c: bool(c.get("answered")))
    out.sort(key=lambda c: not c["ended_waiting_on_user"])
    return out[:MAX_CANDIDATES]


# --------------------------------------------------------------------------- #
# import
# --------------------------------------------------------------------------- #
def _import_candidates(cands: list) -> int:
    """Selected candidates -> normal proposal cards. Waiting-on-you sessions
    land in 待审批 (card_sent); merely-recent ones in 备选 (detected) — the
    same confidence split the other radars use. Returns cards created/merged.
    Hard-gated candidates (session_mismatch) are dropped here too as a last
    belt — callers log the analytics; the soft ``answered`` flag is decided
    by the callers (bulk skips, explicit import overrides)."""
    created = 0
    done_ids = []
    for c in cands:
        if _hard_skip_reason(c):
            continue
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
                # verified binding (main-chain cwd of THIS transcript) — so
                # resume/attach flows read it from the card instead of
                # glob-guessing across ~/.claude/projects (例4a 张冠李戴)
                "cwd": c.get("project_dir") or "",
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
    ids are skipped (re-run safe). Session-id mismatches are refused even
    when explicitly requested (hard gate; per-session ``radar_skip`` event).
    The soft ``answered`` heuristic is OVERRIDDEN here: the user explicitly
    picked the session, and the cheap regex has false positives that must
    never make a session permanently unimportable — the override is logged
    as ``radar_gate_override``."""
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
                reason = _hard_skip_reason(cand)
                if reason:
                    analytics.log_event("radar_skip", source="claude_code",
                                        reason=reason, session=sid[:8])
                else:
                    if cand.get("answered"):
                        analytics.log_event("radar_gate_override",
                                            source="claude_code",
                                            reason="answered", session=sid[:8])
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
    as the Settings checkboxes; ``include_all`` imports every candidate.
    Session mismatches are already dropped by ``scan`` (hard gate), and this
    BULK path also skips ``answered`` closed-loop Q&A — ``include_all`` does
    NOT resurrect those (only an explicit ``import_by_ids`` selection can,
    since it is a per-session user decision)."""
    cands = scan(window_days, root=root)
    answered = [c for c in cands if c.get("answered")]
    if answered:
        analytics.log_event("radar_skip", source="claude_code",
                            reason="answered", count=len(answered))
    cands = [c for c in cands if not c.get("answered")]
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
