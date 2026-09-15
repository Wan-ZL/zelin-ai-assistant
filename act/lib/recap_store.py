"""act/lib/recap_store.py — ``state/recap/`` on disk + the add-only board projection (CONTRACT §63).

Layout (all under ``STATE_DIR/recap/``; the whole directory is disposable):

    sessions.json        cursor (frames/audio high-water ids), first-run marker,
                         per-minute presence buffer of not-yet-closed events,
                         the OPEN sessions the page shows as 进行中, the daily
                         generation counter and per-key failure counts
    recaps/<key>.json    one file per meeting (key = meeting:<start minute>-<app>);
                         **not a registry card** — no id/status/tier, no
                         recipient, no channel: nothing downstream can dispatch
                         or send it (tests/test_recap_no_egress.py pins the
                         absent keys)
    marks.json           server-owned local flags
                         {key: {copied_at, sent_at, dismissed_at}}
                         (web 「复制」/「标记已发送」/「忽略」); read here for
                         the projection, for the 活跃 / 已归档 / 已忽略 budgets
                         and for the dismissed retention window

§63.5 追记（2026-09-15，issue #301）：旧法条那句「无控制流读它 / no control
flow anywhere reads a mark」**自此失效**。marks 参与**恰好两处**判决——
:func:`projection` 的分栏预算（filed 行不再挤掉活跃行）与 :func:`prune` 的
已忽略保留窗——两处都只决定「这份笔记还在不在这台机器上」；marks 仍永不进
registry、永不触发发送 / 派发 / 卡片状态机，`server/recaps.py` 仍是它唯一的
写者（act 只读，读不动 = fail-open，只剩 90 天兜底）。

Writers: ``act/recap.py`` (cron `--once` and the actd-spawned `--generate` /
`--slack-draft` runs, serialized by the flock) owns sessions.json and
recaps/; ``server/recaps.py`` owns marks.json. The daemon only READS this
directory: :func:`attach` adds the add-only top-level ``recaps[]`` to
dashboard.json (history stripped, newest first, capped) — the web 会议纪要
page's data. The one thing actd writes lives OUTSIDE it: the §63.8 generate
request ledger ``state/recap_requests.json`` (act/lib/recap_requests.py),
projected per row as ``generate_request``. The §63.3 追记 ``problems`` /
``repairs`` rows ride on the recap file itself (act/recap.py writes them with
the lines) and reach the wire through :func:`_row` like every other field.

Retention: recaps older than `recap.retention_days` (default 90) are pruned
on every cron round (防腐 #4: every new file family is born with a cap);
dismissed ones go earlier, on `recap.dismissed_retention_days` counted from
the dismissal (§63.3 追记 2026-09-15).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

from act.lib import config, recap_requests, recap_sessions

KEY_RE = re.compile(r"^meeting:\d{4}-\d{2}-\d{2}T\d{4}-[a-z0-9-]{1,32}$")
# Slack conversation ids: C… channel, D… DM, G… private group (uppercase alnum)
CHANNEL_ID_RE = re.compile(r"^[CDG][A-Z0-9]{6,20}$")

PROJECTION_CAP = 60
# §63.5 追记（issue #301）：已归档 / 已忽略 自己的预算——归档一行永不挤掉活跃的一行
FILED_PROJECTION_CAP = 60
LATE_SLICE_WINDOW_S = 48 * 3600
PRIOR_DAYS = 14
PRIOR_LIMIT = 3
DEFAULT_RETENTION_DAYS = 90
DEFAULT_DISMISSED_RETENTION_DAYS = 14
DEFAULT_MAX_PER_RUN = 2
DEFAULT_MAX_PER_DAY = 8
LANGUAGES: tuple = ("auto", "zh", "en")

# quality vocabulary (add-only): ok | needs_review | thin_transcript | no_audio | generation_failed
QUALITY_OK = "ok"
QUALITY_NEEDS_REVIEW = "needs_review"
QUALITY_THIN = "thin_transcript"
QUALITY_NO_AUDIO = "no_audio"
QUALITY_FAILED = "generation_failed"


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
def recap_dir() -> Path:
    return config.STATE_DIR / "recap"


def recaps_dir() -> Path:
    return recap_dir() / "recaps"


def sessions_path() -> Path:
    return recap_dir() / "sessions.json"


def marks_path() -> Path:
    return recap_dir() / "marks.json"


def lock_path() -> Path:
    return recap_dir() / ".lock"


def log_path() -> Path:
    # sibling of weekly_digest.log — the detached runs append here
    return config.STATE_DIR / "recap.log"


def ensure_dirs() -> None:
    recaps_dir().mkdir(parents=True, exist_ok=True)


def valid_key(key) -> bool:
    return isinstance(key, str) and bool(KEY_RE.match(key))


def recap_path(key: str) -> Path:
    if not valid_key(key):
        raise ValueError("bad recap key: %r" % (key,))
    return recaps_dir() / (key.replace(":", "_") + ".json")


# --------------------------------------------------------------------------- #
# json IO (atomic write; unreadable = default)
# --------------------------------------------------------------------------- #
def _read_json(path: Path, default):
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return doc if isinstance(doc, dict) else default


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# settings (config.yaml `recap:` block + the three Settings-overridable flags)
# --------------------------------------------------------------------------- #
def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def slack_targets(raw) -> dict:
    """`recap.slack_draft.targets` {app-slug: channel_id}; entries whose value
    is not a Slack conversation id are dropped (no guessing, §63)."""
    return {str(k).lower(): v for k, v in _dict(raw).items()
            if isinstance(v, str) and CHANNEL_ID_RE.match(v)}


def settings(cfg: Optional[config.Config] = None) -> dict:
    """Effective recap settings: the three flat knobs (config.py, Settings
    overridable) + the tuning block read verbatim from config.yaml."""
    cfg = cfg or config.load_config()
    blk = _dict(_dict(cfg.raw).get("recap"))
    return {
        "enabled": bool(getattr(cfg, "recap_enabled", True)),
        "default_language": str(getattr(cfg, "recap_default_language", "auto")),
        "slack_draft_enabled": bool(getattr(cfg, "recap_slack_draft_enabled", False)),
        "slack_targets": slack_targets(_dict(blk.get("slack_draft")).get("targets")),
        "options": recap_sessions.Options.from_mapping(blk),
        "max_per_run": max(1, recap_sessions.int_or(blk.get("max_per_run"), DEFAULT_MAX_PER_RUN)),
        "max_per_day": max(1, recap_sessions.int_or(blk.get("max_per_day"), DEFAULT_MAX_PER_DAY)),
        "retention_days": max(1, recap_sessions.int_or(blk.get("retention_days"), DEFAULT_RETENTION_DAYS)),
        # §63.3 追记（issue #301）：已忽略的那份走自己的短窗，从忽略那一刻算
        "dismissed_retention_days": max(1, recap_sessions.int_or(
            blk.get("dismissed_retention_days"), DEFAULT_DISMISSED_RETENTION_DAYS)),
        "db_path": str(blk.get("db_path") or "").strip() or None,
    }


# --------------------------------------------------------------------------- #
# sessions.json
# --------------------------------------------------------------------------- #
def load_state() -> Optional[dict]:
    """The cursor/buffer document, None on the very first run (no file)."""
    if not sessions_path().exists():
        return None
    return _read_json(sessions_path(), None)


def new_state(cursor: dict, now_iso: str) -> dict:
    return {"schema": 1, "first_run_at": now_iso, "cursor": dict(cursor),
            "events": [], "open": [], "day": {"date": "", "count": 0},
            "failures": {}, "updated_at": now_iso}


def save_state(state: dict) -> None:
    _write_json(sessions_path(), state)


# --------------------------------------------------------------------------- #
# recaps/<key>.json
# --------------------------------------------------------------------------- #
def new_record(session: recap_sessions.Session, key: str, status: str) -> dict:
    """The recap document skeleton. Deliberately carries NO recipient / channel
    / id / tier / status-machine field — it is a note, not a card (§0 第 4 条)."""
    return {
        "key": key, "app": session.app,
        "start": recap_sessions.iso_utc(session.start),
        "end": recap_sessions.iso_utc(session.end),
        "duration_min": int(round(session.span_s / 60.0)),
        "frames": int(session.frames), "audio_rows": int(session.audio_rows),
        "status": status, "version": 0, "partial": False, "generated_at": None,
        "en": None, "zh": None, "quality": None, "transcript_words": 0,
        "note": None, "history": [], "slack_draft": None,
        # §63.3 追记（add-only）：needs_review 的结构化原因与落地前的长度修剪台账
        "problems": [], "repairs": [],
    }


def load_recap(key: str) -> Optional[dict]:
    if not valid_key(key):
        return None
    return _read_json(recap_path(key), None)


def save_recap(rec: dict) -> None:
    _write_json(recap_path(rec["key"]), rec)


def _start_ts(rec: dict) -> float:
    return recap_sessions.parse_ts(rec.get("start")) or 0.0


def list_recaps() -> list:
    """Every stored recap, newest start first (unreadable files skipped)."""
    ensure_dirs()
    recs = []
    for p in recaps_dir().glob("meeting_*.json"):
        rec = _read_json(p, None)
        if rec and valid_key(rec.get("key")):
            recs.append(rec)
    recs.sort(key=_start_ts, reverse=True)
    return recs


def closed_intervals(now: float, within_s: float = LATE_SLICE_WINDOW_S) -> list:
    """[(key, start_ts, end_ts)] of CLOSED recaps recent enough for a late
    transcript slice to still matter."""
    out = []
    for rec in list_recaps():
        end = recap_sessions.parse_ts(rec.get("end")) or 0.0
        if rec.get("status") == recap_sessions.CLOSED and now - end <= within_s:
            out.append((rec["key"], _start_ts(rec), end))
    return out


def priors_for(start_ts: float, timezone: str) -> list:
    """≤ 3 earlier CLOSED recaps with text within 14 days, newest first —
    the 'Changed since last plan' reference: [{"date", "en"}]."""
    lo = start_ts - PRIOR_DAYS * 86400
    out = []
    for rec in list_recaps():
        s = _start_ts(rec)
        if rec.get("en") and rec.get("status") == recap_sessions.CLOSED and lo <= s < start_ts:
            out.append({"date": recap_sessions.local_dt(s, timezone).strftime("%Y-%m-%d"),
                        "en": list(rec["en"])})
    return out[:PRIOR_LIMIT]


def _dismissed_expired(rec: dict, marks: dict, cutoff: float) -> bool:
    """这份被忽略的 CLOSED 纪要过了短窗吗？没 marks（读不动 / 没传窗口）、OPEN 行、
    时间戳解析不出 = False——fail open，多删一份纪要比留一份贵得多。"""
    if not marks or rec.get("status") != recap_sessions.CLOSED:
        return False
    at = recap_sessions.parse_ts(_dict(marks.get(rec.get("key"))).get("dismissed_at"))
    return at is not None and at <= cutoff


def prune(now: float, retention_days: int, dismissed_days: Optional[int] = None) -> int:
    """Delete recaps past their retention; returns count.

    Two windows, whichever comes first (§63.3 追记 2026-09-15，issue #301):
    the 90-day backstop on the meeting's own start (unchanged, applies to
    everything), and — when ``dismissed_days`` is given — ``dismissed_days``
    counted from the moment the recap was 忽略 (marks.json ``dismissed_at``,
    server-owned; read-only here). Only CLOSED recaps take the short window:
    a mark stamped while a meeting is still OPEN must never delete the text
    that lands afterwards. Unreadable / hand-mangled marks = **fail open**
    (no short window at all), never an extra deletion.
    """
    cutoff = now - retention_days * 86400
    marks = load_marks() if dismissed_days else {}
    dismissed_cutoff = now - max(1, int(dismissed_days or 1)) * 86400
    removed = 0
    for rec in list_recaps():
        if _start_ts(rec) < cutoff or _dismissed_expired(rec, marks, dismissed_cutoff):
            recap_path(rec["key"]).unlink(missing_ok=True)
            removed += 1
    return removed


# --------------------------------------------------------------------------- #
# marks.json (server-owned; read-only here)
# --------------------------------------------------------------------------- #
def load_marks() -> dict:
    return _read_json(marks_path(), {})


# --------------------------------------------------------------------------- #
# projection — dashboard.json top-level `recaps[]` (add-only)
# --------------------------------------------------------------------------- #
def _row(rec: dict, marks: dict, requests: Optional[dict] = None) -> dict:
    row = {k: v for k, v in rec.items() if k != "history"}
    row["history_count"] = len(rec.get("history") or [])
    mark = _dict(marks.get(rec.get("key")))
    row["copied_at"] = mark.get("copied_at")
    row["sent_at"] = mark.get("sent_at")
    # §63.5 追记 add-only（issue #301）：已忽略的时刻（无 = None，键恒在）
    row["dismissed_at"] = mark.get("dismissed_at")
    # §63.8 add-only：「重新生成 / 现在生成」回执（actd 台账 × 本文件的 generated_at；无请求 = None）
    row["generate_request"] = recap_requests.projection(rec.get("key"), rec.get("generated_at"),
                                                        requests if requests is not None else {})
    return row


def filed(row: dict) -> bool:
    """已归档（`sent_at`，标记已发送派生）或已忽略（`dismissed_at`）= 离开活跃栏的行
    （§63.5 追记，issue #301）。"""
    return bool(row.get("sent_at") or row.get("dismissed_at"))


def projection(limit: int = PROJECTION_CAP, filed_limit: int = FILED_PROJECTION_CAP) -> list:
    """Stored recaps + OPEN sessions (from sessions.json) not yet having a file
    (a partial 现在生成 wins over the bare OPEN row), newest first, capped;
    history stripped, local marks and the §63.8 generate receipts merged in.

    Two budgets, not one (§63.5 追记，issue #301): the active rows get
    ``limit`` and the filed ones (已归档 / 已忽略) get ``filed_limit`` — filing
    a recap away must never push a live one off the page, and 已归档 must not
    silently lose rows long before the retention deletes them.
    """
    marks = load_marks()
    requests = recap_requests.load()
    rows = {r["key"]: _row(r, marks, requests) for r in list_recaps()}
    for o in open_rows(load_state() or {}):
        rows.setdefault(o["key"], _row(o, marks, requests))
    ordered = sorted(rows.values(), key=_start_ts, reverse=True)
    kept = [r for r in ordered if not filed(r)][:limit] + [r for r in ordered if filed(r)][:filed_limit]
    return sorted(kept, key=_start_ts, reverse=True)


def open_rows(state: dict) -> list:
    """The OPEN session rows of sessions.json that are well-formed."""
    return [o for o in (state.get("open") or [])
            if isinstance(o, dict) and valid_key(o.get("key"))]


def attach(dash: dict) -> dict:
    """Set ``dash["recaps"]`` (add-only; a failure leaves the key absent —
    the board must never die for a recap file)."""
    try:
        dash["recaps"] = projection()
    except Exception:  # noqa: BLE001 - projection is best-effort
        pass
    return dash


# --------------------------------------------------------------------------- #
# inbox special forms → `python -m act.recap` argv tail (actd spawns detached)
# --------------------------------------------------------------------------- #
def _generate_argv(decision: dict) -> Optional[list]:
    argv = ["--generate", decision["meeting_key"]]
    note = decision.get("note")
    if note is not None:
        if not isinstance(note, str) or len(note) > 500:
            return None
        argv += ["--note", note]
    if decision.get("partial") is True:
        argv.append("--partial")
    return argv


def _slack_draft_argv(decision: dict) -> Optional[list]:
    channel = decision.get("channel_id")
    if not (isinstance(channel, str) and CHANNEL_ID_RE.match(channel)):
        return None
    return ["--slack-draft", decision["meeting_key"], "--channel-id", channel]


_INBOX_BUILDERS = {"recap_generate": _generate_argv, "recap_slack_draft": _slack_draft_argv}
INBOX_ACTIONS = frozenset(_INBOX_BUILDERS)


def inbox_argv(decision) -> Optional[list]:
    """``recap_generate {meeting_key, note?, partial?}`` / ``recap_slack_draft
    {meeting_key, channel_id}`` → argv tail, or None when malformed (actd
    acks noop). Neither form carries a recipient: the channel_id of a draft
    names the owner's own Slack draft box target, never a send."""
    if not isinstance(decision, dict) or not valid_key(decision.get("meeting_key")):
        return None
    builder = _INBOX_BUILDERS.get(str(decision.get("action")))
    return builder(decision) if builder else None
