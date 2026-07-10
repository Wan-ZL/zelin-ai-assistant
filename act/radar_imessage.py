"""iMessage phone channel — the §13 command surface for users without Slack.

v0.12 (CONTRACT §13, channel-pluggable): with ``phone_channel: imessage`` in
config, the "message yourself" iMessage thread becomes the phone command
channel, mirroring act/radar_slack.py's self-DM surface:
  - approval commands  批准/拒绝/打回/验收 R-xxx [反馈]  -> state/inbox/<uuid>.json
    (the regex / action map / inbox writer are REUSED from radar_slack, so the
    two channels can never drift apart)
  - anything else (text)  -> quick capture (act/lib/quick_capture.py three-way),
    with the result replied back into the thread
  - a 👍/❤️ tapback on an outbound 🔔 notification we sent (tracked in
    state/imessage_outbox.json, mirroring slack_outbox) approves that R-xxx.

Reading side: polls ``~/Library/Messages/chat.db`` with the sqlite3 stdlib,
opened STRICTLY READ-ONLY (``file:`` URI with ``mode=ro``) — this pipeline must
never write to Apple's database. Marker = last seen message ROWID in
state/imessage_radar.json (atomic tmp+replace). Sending side: osascript ->
Messages.app, with the text passed via argv (no AppleScript string escaping).

Design notes / landmines:
- chat.db is Apple PRIVATE API. The tables used here (chat, chat_handle_join,
  handle, chat_message_join, message) have been stable for years but can shift
  across macOS versions — every DB access is wrapped so a schema change is a
  health-logged no-op, never a crash. scan() NEVER raises.
- FULL DISK ACCESS: ~/Library/Messages is TCC-protected. The python binary the
  launchd agent runs needs FDA or every pass no-ops with skip_reason
  db_open_failed. See docs/IMESSAGE_SETUP.md.
- Newer macOS often leaves message.text NULL and stores the body only in
  attributedBody (a typedstream blob). :func:`decode_attributed_body` uses the
  well-known NSString-marker heuristic — best-effort, returns "" on anything odd.
- The self-thread should only contain the user (is_from_me=1); rows with
  is_from_me=0 there (multi-device echo edge cases) are skipped. Our own posts
  are prefixed 🔔 (notify mirror) or 🤖 (replies here) and are skipped on
  re-scan — the radar must never quick-capture its own output.
- Tapbacks are message rows with associated_message_type 2000..2005. ONLY the
  positive ones approve (2000 ❤️, 2001 👍) — same red line as Slack's
  "white_check_mark only": a 👎/haha/!!/? tapback must never approve anything.
- Marker discipline (same as radar_slack): the marker advances to the max
  ROWID actually FETCHED this pass. Replies we send mid-scan land at higher
  ROWIDs, get fetched next pass, and die on the prefix check — a message the
  user sends while a slow capture runs still survives to the next pass.
- No attachment support in v1 — text only (the Slack self-DM keeps the
  photo/video path). Attachment-only rows have no text and are skipped.

Run standalone:  python -m act.radar_imessage --once
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Callable, Optional

from act import radar_slack
from act.lib import analytics, config, failures, health, platform

# §13 command surface — single source of truth lives in radar_slack.
_CMD_RE = radar_slack._CMD_RE
_CMD_MAP = radar_slack._CMD_MAP
_ASSISTANT_PREFIXES = radar_slack._ASSISTANT_PREFIXES
_write_inbox = radar_slack._write_inbox

CHAT_DB: Path = Path.home() / "Library" / "Messages" / "chat.db"
STATE_FILE = "imessage_radar.json"     # {"last_rowid": N}
OUTBOX_FILE = "imessage_outbox.json"   # outbound 🔔 notifications awaiting a tapback

# associated_message_type: 2000..2005 = tapback added, 3000..3005 = removed.
_TAPBACK_MIN, _TAPBACK_MAX = 2000, 2005
_APPROVE_TAPBACKS = {2000, 2001}       # ❤️ love, 👍 like — the only consent forms

_REQ_RE = re.compile(r"#(R-\d+)")
_OUTBOX_MAX_AGE_S = 14 * 86400         # same retention as the slack outbox
_DB_TIMEOUT_S = 2.0                    # busy wait before "database is locked"


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# sending (osascript -> Messages.app)
# --------------------------------------------------------------------------- #
# Text and handle ride in argv — AppleScript never sees them as source, so no
# quote/backslash/newline escaping problems.
_SEND_SCRIPT = "\n".join([
    "on run argv",
    'tell application "Messages"',
    "set s to 1st account whose service type = iMessage",
    "send (item 2 of argv) to participant (item 1 of argv) of s",
    "end tell",
    "end run",
])


def _default_send_runner(handle: str, text: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["osascript", "-e", _SEND_SCRIPT, handle, text],
        capture_output=True, text=True, timeout=30,
    )


def send_imessage(handle: str, text: str,
                  runner: Optional[Callable[[str, str], subprocess.CompletedProcess]] = None
                  ) -> bool:
    """Send an iMessage to ``handle`` via Messages.app. Never raises.

    First use may pop a TCC Automation consent ("osascript wants to control
    Messages"). Used ONLY for the user's own handle — this channel, like the
    Slack one, never messages anyone else (CONTRACT §13 red line).
    """
    if not str(handle or "").strip() or not str(text or "").strip():
        return False
    if runner is None:
        runner = _default_send_runner
    try:
        proc = runner(str(handle), str(text))
        return getattr(proc, "returncode", 1) == 0
    except Exception:  # noqa: BLE001 - the phone mirror must never break a caller
        return False


# --------------------------------------------------------------------------- #
# marker (last seen message ROWID)
# --------------------------------------------------------------------------- #
def _marker_path() -> Path:
    return config.STATE_DIR / STATE_FILE


def load_marker() -> int:
    try:
        data = json.loads(_marker_path().read_text(encoding="utf-8"))
        return int(data.get("last_rowid") or 0)
    except (OSError, ValueError, json.JSONDecodeError, AttributeError):
        return 0


def save_marker(last_rowid: int) -> None:
    p = _marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"last_rowid": int(last_rowid)}), encoding="utf-8")
    tmp.replace(p)


# --------------------------------------------------------------------------- #
# outbox — outbound 🔔 notifications awaiting a 👍/❤️ tapback (§13d analog)
# --------------------------------------------------------------------------- #
def _outbox_path() -> Path:
    return config.STATE_DIR / OUTBOX_FILE


def load_outbox() -> list:
    try:
        data = json.loads(_outbox_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_outbox(items: list) -> None:
    p = _outbox_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def record_outbox(req: Optional[str]) -> None:
    """Track an outbound 🔔 notification so a 👍/❤️ tapback can approve it.
    Called by act/lib/notify.imessage_notify. Never raises.

    Unlike Slack (whose chat.postMessage returns a ts to key on), osascript
    gives us no message id back — so entries carry only the req id, and the
    tapback is matched by reading the ``#R-xxx`` out of the tapback TARGET's
    text in chat.db, then checking it against this outbox (approve once)."""
    if not req:
        return
    try:
        items = load_outbox()
        items.append({"req": str(req), "sent_at": _iso_now()})
        save_outbox(items)
    except Exception:  # noqa: BLE001
        pass


def _outbox_expired(sent_at) -> bool:
    try:
        ts = _dt.datetime.strptime(str(sent_at), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc)
    except (ValueError, TypeError):
        return True
    return (_dt.datetime.now(_dt.timezone.utc) - ts).total_seconds() > _OUTBOX_MAX_AGE_S


def _prune_outbox() -> None:
    """Drop malformed/expired entries — bounds the file like the slack outbox
    prune. Never raises."""
    try:
        items = load_outbox()
        keep = [it for it in items
                if isinstance(it, dict) and not _outbox_expired(it.get("sent_at"))]
        if keep != items:
            save_outbox(keep)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# chat.db access (READ-ONLY, tolerant of schema drift)
# --------------------------------------------------------------------------- #
def _connect(db_path: Path) -> sqlite3.Connection:
    """mode=ro URI connect — sqlite cannot create or write the file through it."""
    return sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True,
                           timeout=_DB_TIMEOUT_S)


def _self_chat_ids(conn: sqlite3.Connection, handle: str) -> list[int]:
    """The message-yourself thread(s): chats whose ONLY participant is the
    user's own handle, plus chats registered directly under the handle as
    chat_identifier (how some macOS versions store the Note-to-Self chat)."""
    ids: set = set()
    cur = conn.execute("SELECT ROWID FROM chat WHERE chat_identifier = ?", (handle,))
    ids.update(r[0] for r in cur.fetchall())
    cur = conn.execute(
        "SELECT chj.chat_id FROM chat_handle_join chj "
        "JOIN handle h ON h.ROWID = chj.handle_id "
        "GROUP BY chj.chat_id "
        "HAVING COUNT(DISTINCT h.id) = 1 AND MAX(h.id) = ?", (handle,))
    ids.update(r[0] for r in cur.fetchall())
    return sorted(ids)


def _fetch_new(conn: sqlite3.Connection, chat_ids: list, last_rowid: int) -> list[dict]:
    placeholders = ",".join("?" * len(chat_ids))
    cur = conn.execute(
        "SELECT m.ROWID, m.guid, m.text, m.attributedBody, m.is_from_me, "
        "m.associated_message_type, m.associated_message_guid "
        "FROM message m "
        "JOIN chat_message_join cmj ON cmj.message_id = m.ROWID "
        f"WHERE cmj.chat_id IN ({placeholders}) AND m.ROWID > ? "
        "ORDER BY m.ROWID",
        (*chat_ids, int(last_rowid)))
    out: list[dict] = []
    for rowid, guid, text, body, from_me, assoc_type, assoc_guid in cur.fetchall():
        out.append({
            "rowid": int(rowid),
            "guid": guid,
            "text": text if text else decode_attributed_body(body),
            "from_me": bool(from_me),
            "assoc_type": int(assoc_type or 0),
            "assoc_guid": assoc_guid,
        })
    return out


def decode_attributed_body(blob) -> str:
    """Best-effort text out of a typedstream ``attributedBody`` blob.

    Full parsing needs Apple's private archive format; the reliable shortcut
    (used by the imessage-exporter family) is: the payload string follows the
    b"NSString" class marker and a '+' tag, length-prefixed — 1 raw byte, or
    0x81 + uint16le / 0x82 + uint32le for longer strings. Returns "" on
    anything unexpected."""
    if not blob:
        return ""
    try:
        raw = bytes(blob)
        i = raw.find(b"NSString")
        if i == -1:
            return ""
        i = raw.index(b"+", i) + 1
        n = raw[i]
        i += 1
        if n == 0x81:
            n = int.from_bytes(raw[i:i + 2], "little")
            i += 2
        elif n == 0x82:
            n = int.from_bytes(raw[i:i + 4], "little")
            i += 4
        return raw[i:i + n].decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001 - private format; tolerate any shape
        return ""


def _target_guid(assoc_guid) -> str:
    """Tapback target: associated_message_guid comes as ``p:0/<GUID>``,
    ``bp:<GUID>`` or a bare GUID."""
    g = str(assoc_guid or "")
    if "/" in g:
        return g.rsplit("/", 1)[1]
    if ":" in g:
        return g.rsplit(":", 1)[1]
    return g


# --------------------------------------------------------------------------- #
# handlers
# --------------------------------------------------------------------------- #
def _handle_tapback(m: dict, conn: sqlite3.Connection,
                    reply: Callable[[str], None]) -> bool:
    """👍/❤️ on one of OUR tracked 🔔 notifications -> inbox approve (once)."""
    if m.get("assoc_type") not in _APPROVE_TAPBACKS:
        return False   # 👎/haha/!!/? — a tapback, but not consent (✅-only rule)
    guid = _target_guid(m.get("assoc_guid"))
    if not guid:
        return False
    try:
        row = conn.execute(
            "SELECT text, attributedBody, is_from_me FROM message WHERE guid = ?",
            (guid,)).fetchone()
    except sqlite3.Error:
        return False
    if not row or not row[2]:
        return False
    text = str(row[0] or decode_attributed_body(row[1]) or "").strip()
    if not text.startswith("🔔"):
        return False   # tapback on something that isn't our notification
    match = _REQ_RE.search(text)
    if not match:
        return False
    rid = match.group(1)
    items = load_outbox()
    hit = next((it for it in items if isinstance(it, dict)
                and it.get("req") == rid and not it.get("consumed")), None)
    if hit is None:
        return False   # untracked req, or already consumed — approve once only
    _write_inbox("approve", rid, None)
    hit["consumed"] = True
    try:
        save_outbox(items)
    except OSError:
        pass
    analytics.log_event("imessage_tapback_approve", req=rid)
    reply(f"✅ 已批准 {rid}（tapback）")
    return True


def _handle_self_message(m: dict, cfg: config.Config, reply: Callable[[str], None],
                         extractor: Optional[Callable] = None) -> None:
    """One self-thread message: approval command FIRST (no LLM), else quick
    capture — byte-for-byte the same grammar and inbox files as the Slack
    self-DM (radar_slack._handle_self_message)."""
    text = (m.get("text") or "").strip()
    cmd = _CMD_RE.match(text)
    if cmd:
        verb, rid, rest = cmd.group(1), cmd.group(2), (cmd.group(3) or "").strip()
        action = _CMD_MAP[verb]
        if action == "rework" and not rest:
            reply(f"打回需要带反馈，例如：打回 {rid} 参数表要补上依据")
            return
        _write_inbox(action, rid, rest or None)
        analytics.log_event("imessage_command", action=action, req=rid)
        reply(f"收到：{verb} {rid}（已写入处理队列）")
        return

    from act.lib import quick_capture
    try:
        res = quick_capture.capture(text, cfg, extractor=extractor)
        out = quick_capture.apply_result(res, cfg)
    except Exception as e:  # noqa: BLE001 - reply the failure, don't kill the scan
        out = f"快速捕获失败：{e}"
    reply(out)


# --------------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------------- #
def _note_skip(reason: str) -> None:
    """radar_skip beacon + health mark on an early exit. Both helpers already
    never raise; swallow anyway — a skipped pass must never become a crash."""
    try:
        analytics.log_event("radar_skip", source="imessage", reason=reason)
        health.update_radar_health("imessage", ok=False, skip_reason=reason)
    except Exception:  # noqa: BLE001
        pass


def scan(cfg: Optional[config.Config] = None,
         extractor: Optional[Callable] = None,
         send_runner: Optional[Callable] = None,
         db_path: Optional[Path] = None) -> int:
    """One pass over the message-yourself thread. Returns the number of user
    messages handled (commands + captures + tapback approvals).

    NEVER raises: a missing/locked/corrupt chat.db, a schema change, or one
    bad message all degrade to a health-logged no-op — the launchd agent must
    keep ticking regardless.
    """
    try:
        return _scan(cfg, extractor, send_runner, db_path)
    except Exception as e:  # noqa: BLE001 - absolute belt (contract: never raise)
        _note_skip(f"scan_error: {type(e).__name__}: {e}"[:120])
        return 0


def _scan(cfg, extractor, send_runner, db_path) -> int:
    if cfg is None:
        cfg = config.load_config()
    if getattr(cfg, "phone_channel", "none") != "imessage":
        _note_skip("disabled")
        return 0
    # This channel is darwin-only by nature (Messages.app + chat.db) — on any
    # other OS the ENABLED channel skips with a classified reason instead of
    # limping into misleading db_missing/no_self_handle territory. Tests that
    # inject a fake chat.db via db_path stay cross-platform.
    if db_path is None and not platform.is_darwin():
        _note_skip("platform_unsupported")
        return 0
    handle = str(getattr(cfg, "imessage_self_handle", None) or "").strip()
    if not handle:
        _note_skip("no_self_handle")
        return 0
    db = Path(db_path) if db_path else CHAT_DB
    if not db.exists():
        _note_skip("db_missing")
        return 0

    def reply(text: str) -> None:
        # 🤖 marks assistant output so the next pass's prefix check skips it
        send_imessage(handle, f"🤖 {text}", runner=send_runner)

    try:
        conn = _connect(db)
    except sqlite3.Error as e:
        _note_skip(f"db_open_failed: {e}"[:120])
        return 0
    try:
        try:
            chat_ids = _self_chat_ids(conn, handle)
            if not chat_ids:
                _note_skip("self_chat_not_found")
                return 0
            last = load_marker()
            rows = _fetch_new(conn, chat_ids, last)
        except sqlite3.Error as e:
            _note_skip(f"db_read_failed: {e}"[:120])
            return 0

        handled = 0
        newest = last
        for m in rows:
            newest = max(newest, m["rowid"])
            try:
                if not m.get("from_me"):
                    continue   # self-thread edge case (device echo) — not ours
                if _TAPBACK_MIN <= (m.get("assoc_type") or 0) <= _TAPBACK_MAX:
                    if _handle_tapback(m, conn, reply):
                        handled += 1
                    continue
                text = (m.get("text") or "").strip()
                if not text or text.startswith(_ASSISTANT_PREFIXES):
                    continue   # our own 🔔/🤖 posts, or attachment-only rows
                _handle_self_message(m, cfg, reply, extractor=extractor)
                handled += 1
            except Exception:  # noqa: BLE001 - one bad message must not kill the pass
                pass
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    if newest > last:
        save_marker(newest)
    _prune_outbox()
    try:
        health.update_radar_health("imessage", ok=True)
    except Exception:  # noqa: BLE001 - health must never break the pass
        pass
    analytics.log_event("radar_scan", source="imessage", messages=len(rows),
                        handled=handled or None)
    return handled


def _main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="radar_imessage")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true",
                        help="verify config + chat.db access only")
    args = parser.parse_args(argv)
    cfg = config.load_config()
    if args.check:
        if not platform.is_darwin():
            print(failures.pick(
                "iMessage 渠道只支持 macOS——它依赖 Messages.app 和 "
                "~/Library/Messages/chat.db。这台机器请把 phone_channel 设为 "
                "slack 或 none。",
                "The iMessage channel is macOS-only — it needs Messages.app "
                "and ~/Library/Messages/chat.db. On this machine set "
                "phone_channel to slack or none."))
            return 1
        pc = getattr(cfg, "phone_channel", "none")
        handle = str(getattr(cfg, "imessage_self_handle", None) or "").strip()
        print(f"phone_channel: {pc}")
        print(f"self_handle: {handle or 'NOT SET (config imessage.self_handle)'}")
        if pc != "imessage" or not handle:
            return 1
        if not CHAT_DB.exists():
            print(f"chat.db not found at {CHAT_DB}")
            return 1
        try:
            conn = _connect(CHAT_DB)
            try:
                chats = _self_chat_ids(conn, handle)
            finally:
                conn.close()
        except sqlite3.Error as e:
            print(f"chat.db unreadable ({e}) — grant Full Disk Access to the "
                  "radar's python binary, see docs/IMESSAGE_SETUP.md")
            return 1
        print(f"self chat: {chats or 'NOT FOUND (message yourself once first)'}")
        return 0 if chats else 1
    n = scan(cfg)
    print(f"imessage radar: {n} message(s) handled")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
