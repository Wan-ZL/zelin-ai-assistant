"""transcripts — read-only access to claude session transcripts on disk.

Claude Code keeps one JSONL per session under ``~/.claude/projects/<proj>/
<full-session-uuid>.jsonl``. Three consumers need to look inside it, all
read-only (CONTRACT §4 resume / §11 rework — full UUID + LAST cwd; §10 契约 C
delivery harvesting; §37 Mac-local search index), and until v0.48 they all
lived in act/executor.py — which is why act/lib/dashboard.py and
act/merge_review.py had to import the executor (an entry module) and closed
the executor → silent_merge → merge_review → executor import cycle. This
module is the lib-layer home for that plumbing (防腐 #2 import direction);
the executor keeps its private aliases as the test seams they always were.

Two hard rules learned in production (2026-07-06):
- ``claude --resume`` requires the FULL UUID — the picker does not match the
  short id ("No sessions match 'efa635ff'"), and a bg resume with a short id
  opens the interactive picker and crash-loops.
- The lookup is DIRECTORY-scoped, and bg agents isolate into git worktrees
  mid-session — so resume must run in the transcript's LAST cwd (the
  worktree), not the launch cwd (the repo root, which is what the roster
  shows and what the transcript's first lines record).

Parsing is line-tolerant everywhere: transcripts are appended live, so a torn
last line or a non-JSON line must never break the pipeline.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional

PROJECTS_DIR = "~/.claude/projects"
# Session ids are UUIDs; the legitimate short id is the full 8-hex first
# segment. Anything shorter would glob-match EVERY transcript and return the
# alphabetically-first one — a wrong-session binding (2026-07 例4a: cards with
# no session_id got copy_cmds pointing at an unrelated Obsidian-ingest session).
MIN_SHORT_LEN = 8


def short_id(sid) -> str:
    """The short (first-segment) form of a session id; ``""`` for None/empty."""
    return str(sid or "").split("-")[0]


def transcript_paths(short: str) -> list[Path]:
    """Every transcript whose filename starts with ``short``, sorted.
    ``[]`` when the projects dir cannot be read — callers apply their own
    short-id guard first (see :data:`MIN_SHORT_LEN`)."""
    proj_root = Path(PROJECTS_DIR).expanduser()
    try:
        return sorted(proj_root.glob(f"*/{short}*.jsonl"))
    except OSError:
        return []


def _session_paths(sid) -> list[Path]:
    """:func:`transcript_paths` behind the short-id guard: a sid whose first
    segment is shorter than :data:`MIN_SHORT_LEN` matches nothing."""
    short = short_id(sid)
    return transcript_paths(short) if len(short) >= MIN_SHORT_LEN else []


def _last_cwd(path: Path) -> Optional[str]:
    """The LAST ``cwd`` recorded in a transcript (worktree hop), else None.
    Unparseable lines are skipped; an unreadable file raises OSError so the
    caller can move on to the next match."""
    last_cwd: Optional[str] = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            c = d.get("cwd")
            if c:
                last_cwd = str(c)
    return last_cwd


def transcript_info(sid: str) -> Optional[tuple[str, Path]]:
    """(full_session_id, final_cwd) for a session, from its transcript on disk.
    None when the sid is too short to be safe, no transcript matches, or no
    match records a cwd."""
    for f in _session_paths(sid):
        try:
            last_cwd = _last_cwd(f)
        except OSError:
            continue
        if last_cwd:
            return f.stem, Path(last_cwd)   # filename is the full session UUID
    return None


def transcript_cwd(sid: str) -> Optional[Path]:
    """Just the final cwd of :func:`transcript_info` (merge_review's need)."""
    info = transcript_info(sid)
    return info[1] if info else None


# --------------------------------------------------------------------------- #
# conversation text (delivery harvesting + §37 search index)
# --------------------------------------------------------------------------- #
def _records(path: Path) -> Iterator[dict]:
    """Main-thread transcript records: parseable JSON objects that are not
    sidechain (subagent) lines. Raises OSError when the file cannot be read."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and not d.get("isSidechain"):
                yield d


def _text_blocks(content: list) -> str:
    """Joined ``text`` of the text blocks in a content list."""
    return "\n".join(
        b.get("text") or ""
        for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    )


def _content_text(content) -> Optional[str]:
    """A message's plain text: a string as-is, a block list joined; anything
    else (missing / unknown shape) is None."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _text_blocks(content)
    return None


def _message_text(d: dict) -> Optional[str]:
    """Stripped text of a transcript line's ``message``; None when the message
    is not an object, has no text-bearing content, or strips to nothing."""
    msg = d.get("message")
    if not isinstance(msg, dict):
        return None
    text = _content_text(msg.get("content"))
    return (text.strip() or None) if text is not None else None


def _assistant_text(d: dict) -> Optional[str]:
    return _message_text(d) if d.get("type") == "assistant" else None


def _user_content_is_turn(content) -> bool:
    """Whether a user line's content is a REAL turn: non-blank text, or blocks
    with text/image and no tool_result."""
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        kinds = {b.get("type") for b in content if isinstance(b, dict)}
        return "tool_result" not in kinds and bool(kinds & {"text", "image"})
    return False


def _not_a_user_line(d: dict) -> bool:
    """Lines that can never be a user turn whatever their content: other
    roles, sidechain / harness-injected (isMeta) lines, tool results (which
    also arrive as type=="user" with a top-level toolUseResult key)."""
    return (d.get("type") != "user" or bool(d.get("isSidechain"))
            or bool(d.get("isMeta")) or "toolUseResult" in d)


def is_user_turn(d: dict) -> bool:
    """True for a REAL user message line — the dispatch prompt, a rework
    feedback injection, or attach input. Tool results also arrive as
    type=="user" lines (content = tool_result blocks, top-level toolUseResult
    key) and harness-injected lines carry isMeta — neither is a user turn.
    Field shapes verified against live transcripts (2026-07-15)."""
    if _not_a_user_line(d):
        return False
    msg = d.get("message")
    if not isinstance(msg, dict):
        return False
    return _user_content_is_turn(msg.get("content"))


def assistant_texts(path: Path, since_last_user: bool = False) -> list[str]:
    """All non-empty assistant TEXT messages of a transcript JSONL, in order.

    Transcript lines are ``{"type": "assistant", "message": {"content": [...]}}``
    where content is a list of blocks (text / tool_use / ...); join the text
    blocks. Sidechain (subagent) messages are skipped — the delivery summary is
    a main-thread message.

    ``since_last_user=True`` keeps only messages AFTER the last real user turn
    (see :func:`is_user_turn`): a rework resume injects Zelin's feedback as a
    user message, so anything before it belongs to a previous delivery round —
    a 打回-rejected FINAL DRAFT must never be resurrected (audit 2026-07). The
    initial dispatch prompt is also a user turn, so first-delivery transcripts
    behave exactly as before.
    """
    out: list[str] = []
    for d in _records(path):
        if since_last_user and is_user_turn(d):
            out.clear()
            continue
        text = _assistant_text(d)
        if text:
            out.append(text)
    return out


def plain_texts(path: Path) -> list[str]:
    """Main-thread USER + ASSISTANT plain texts of a transcript, in order.

    Same discipline as :func:`assistant_texts` / :func:`is_user_turn`
    (v0.33.1): sidechain/isMeta/tool-result lines are never conversation text.
    The FIRST user turn is skipped too (review fix): it is the injected
    dispatch prompt — pages of near-identical boilerplate (quality gate,
    CARD TITLE/FINAL DRAFT instructions, memory head) shared by EVERY card,
    which would light the 命中会话 badge board-wide for words like 卡片/draft.
    Its real content (title/plan/sources) is already searchable as projected
    row fields; later user turns (rework feedback, attach input) are genuine
    conversation and stay. Used by the §37 Mac-local search index — never by
    delivery harvesting.
    """
    out: list[str] = []
    seen_first_user = False
    for d in _records(path):
        if is_user_turn(d):
            if not seen_first_user:
                seen_first_user = True   # dispatch prompt — boilerplate
                continue
            text = _message_text(d)
        else:
            text = _assistant_text(d)
        if text:
            out.append(text)
    return out


def _tail(joined: str, cap: int) -> str:
    return joined[-cap:] if len(joined) > cap else joined


def plain_text(session_id: str, cap: int = 50_000) -> Optional[str]:
    """Tail-capped main-thread conversation text of a session (§37 search
    index). Locates the transcript the same way delivery harvesting does
    (short-id glob over ``~/.claude/projects``). Never raises; None when the
    transcript is missing/empty."""
    try:
        for f in _session_paths(session_id):   # same guard as transcript_info: no glob-everything
            try:
                texts = plain_texts(f)
            except OSError:
                continue
            if texts:
                return _tail("\n".join(texts), cap)
        return None
    except Exception:  # noqa: BLE001 - indexing must never break the pipeline
        return None
