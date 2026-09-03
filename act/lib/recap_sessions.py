"""act/lib/recap_sessions.py — deterministic meeting-session detection (CONTRACT §63).

Reads ``~/.screenpipe/db.sqlite`` **read-only** and turns two event streams into
meeting sessions without any model call (owner 拍板 2026-09-01, issue #129):

- ``frames`` whose app / window / browser_url hit the meeting-app table
  (Zoom, Microsoft Teams, Webex, FaceTime, meet.google.com, Slack + "Huddle";
  an empty window_name is accepted — a two-hour Zoom left 46 blank rows);
- ``audio_transcriptions`` rows with a non-empty transcription.

Rules (all numbers are config `recap.*`, defaults here):
  gap > 5 min splits a session (frames and audio bridge each other — union);
  eligibility = presence ≥ 3 frames and span ≥ 10 min (audio-only sessions
  only when `recap.audio_only_sessions` is on); CLOSED when the run's wall
  clock is ≥ 5 min past the last event **and** no `audio_chunks` row inside
  the session interval is still `transcription_status = 'pending'`; forced
  close when the last event is > 120 min old (engine died); a session longer
  than 4 h is cut into segments. dedup key = ``meeting:<local start minute>-
  <app>`` (``meeting:2026-08-31T1256-zoom``) — the OPEN verdict at 13:00 and
  the CLOSED one at 13:30 name the same meeting.

Events are read **by id range** (cursor), never by time window, so a Mac that
slept through a cron round loses nothing. Transcript text is never copied
into state — only per-minute presence buckets ``[minute_ts, kind, app, n]``;
the text is re-read from the engine's DB at generation time.

Pure functions + one sqlite reader; the store / orchestration live in
act/lib/recap_store.py and act/recap.py.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:  # stdlib since 3.9; tz database may be missing on Windows → local time
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - 3.9 always ships zoneinfo
    ZoneInfo = None  # type: ignore[assignment,misc]

FRAME = "frame"
AUDIO = "audio"
OPEN = "open"
CLOSED = "closed"

# Owner's machine; the key's timezone is a knob (`recap.timezone`) so a fork
# in another zone names its meetings in local time too.
DEFAULT_TIMEZONE = "America/Los_Angeles"
AUDIO_ONLY_APP = "audio"

# Meeting-app table. A frame matches a rule when every non-empty matcher is a
# case-insensitive substring of the frame's column. `recap.meeting_windows`
# in config.yaml appends rules of the same shape.
DEFAULT_MEETING_RULES: tuple = (
    {"slug": "zoom", "app": "zoom"},
    {"slug": "teams", "app": "microsoft teams"},
    {"slug": "webex", "app": "webex"},
    {"slug": "facetime", "app": "facetime"},
    {"slug": "meet", "url": "meet.google.com"},
    {"slug": "slack-huddle", "app": "slack", "window": "huddle"},
)

# One cron round reads at most this many rows per table; the cursor stops at
# the last row read, so a backlog is drained across rounds, never skipped.
READ_LIMIT = 200_000


@dataclass
class Options:
    gap_s: float = 300.0
    quiet_s: float = 300.0
    min_span_s: float = 600.0
    min_frames: int = 3
    force_close_s: float = 7200.0
    max_session_s: float = 4 * 3600.0
    audio_only: bool = False
    rules: tuple = DEFAULT_MEETING_RULES
    timezone: str = DEFAULT_TIMEZONE

    @classmethod
    def from_mapping(cls, raw) -> "Options":
        """config.yaml `recap:` block → Options; bad values keep the default."""
        blk = raw if isinstance(raw, dict) else {}
        opts = cls()
        opts.gap_s = _minutes(blk.get("gap_minutes"), opts.gap_s)
        opts.quiet_s = _minutes(blk.get("quiet_minutes"), opts.quiet_s)
        opts.min_span_s = _minutes(blk.get("min_span_minutes"), opts.min_span_s)
        opts.min_frames = max(1, int_or(blk.get("min_presence_frames"), opts.min_frames))
        opts.force_close_s = _minutes(blk.get("force_close_minutes"), opts.force_close_s)
        opts.max_session_s = _minutes(blk.get("max_session_minutes"), opts.max_session_s)
        opts.audio_only = blk.get("audio_only_sessions") is True
        opts.rules = DEFAULT_MEETING_RULES + tuple(_clean_rules(blk.get("meeting_windows")))
        tz = str(blk.get("timezone") or "").strip()
        opts.timezone = tz or DEFAULT_TIMEZONE
        return opts


def int_or(value, default: int) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _minutes(value, default_s: float) -> float:
    """A positive minute count → seconds; anything else (incl. bools) → the default."""
    try:
        minutes = float(value) if not isinstance(value, bool) else 0.0
    except (TypeError, ValueError):
        return default_s
    return minutes * 60.0 if minutes > 0 else default_s


def _text(value) -> str:
    """Matcher normalisation: None-safe, trimmed, case-folded."""
    return str(value or "").strip().lower()


def _clean_rule(item) -> Optional[dict]:
    """One config rule → normalized dict (slug + ≥1 matcher) or None."""
    if not isinstance(item, dict):
        return None
    slug = _text(item.get("slug"))
    rule = {k: _text(item.get(k)) for k in ("app", "window", "url") if _text(item.get(k))}
    if not (slug and rule):
        return None
    rule["slug"] = slug
    return rule


def _clean_rules(raw) -> list:
    """Extra rules from config (`recap.meeting_windows`); junk entries drop."""
    items = raw if isinstance(raw, list) else []
    return [r for r in map(_clean_rule, items) if r]


# --------------------------------------------------------------------------- #
# timestamps
# --------------------------------------------------------------------------- #
def parse_ts(raw) -> Optional[float]:
    """screenpipe's ISO timestamps (``2026-08-23T05:35:50.788256+00:00``, a
    trailing ``Z``, or sqlite's ``YYYY-MM-DD HH:MM:SS``) → epoch seconds;
    naive values are UTC. None for anything unparsable."""
    s = str(raw or "").strip()
    if not s:
        return None
    # a "Z" can only be the UTC suffix in these strings; sqlite's space separator → "T"
    s = s.replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.timestamp()


def iso_utc(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tzinfo_for(name: str):
    """ZoneInfo for the knob, or the machine's local zone when the database
    lacks it (Windows without tzdata) — never raises."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError / bad key
            pass
    return _dt.datetime.now().astimezone().tzinfo


def local_dt(ts: float, timezone: str) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(ts, tzinfo_for(timezone))


def meeting_key(start_ts: float, app: str, timezone: str) -> str:
    """``meeting:<local start minute>-<app>`` — one meeting, one key."""
    return "meeting:%s-%s" % (local_dt(start_ts, timezone).strftime("%Y-%m-%dT%H%M"), app)


def minute_bucket(ts: float) -> int:
    return int(ts // 60) * 60


# --------------------------------------------------------------------------- #
# meeting-app matching
# --------------------------------------------------------------------------- #
def match_app(app_name, window_name, browser_url, rules=DEFAULT_MEETING_RULES) -> Optional[str]:
    """The first rule every matcher of which hits → its slug; None otherwise."""
    cols = {"app": _text(app_name), "window": _text(window_name), "url": _text(browser_url)}
    for rule in rules:
        if all(rule[k] in cols[k] for k in ("app", "window", "url") if k in rule):
            return rule["slug"]
    return None


# --------------------------------------------------------------------------- #
# sqlite reader (read-only URI; the engine's DB is never written)
# --------------------------------------------------------------------------- #
def default_db_path() -> Path:
    return Path.home() / ".screenpipe" / "db.sqlite"


def connect_readonly(path: Optional[Path] = None) -> sqlite3.Connection:
    p = Path(path or default_db_path()).resolve()
    conn = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = None
    return conn


def max_ids(conn: sqlite3.Connection) -> dict:
    """Current high-water ids — the first-run marker (no backfill)."""
    frames = conn.execute("SELECT COALESCE(MAX(id), 0) FROM frames").fetchone()[0]
    audio = conn.execute("SELECT COALESCE(MAX(id), 0) FROM audio_transcriptions").fetchone()[0]
    return {"frames": int(frames or 0), "audio": int(audio or 0)}


def read_frame_events(conn: sqlite3.Connection, after_id: int, rules=DEFAULT_MEETING_RULES,
                      limit: int = READ_LIMIT) -> "tuple[list, int]":
    """Presence buckets from frames with id > after_id: ``[(bucket, FRAME,
    slug, 1), ...]`` + the last id read (cursor)."""
    rows = conn.execute(
        "SELECT id, timestamp, app_name, window_name, browser_url FROM frames "
        "WHERE id > ? ORDER BY id LIMIT ?", (int(after_id), int(limit))).fetchall()
    events, last = [], int(after_id)
    for fid, ts_raw, app, window, url in rows:
        last = int(fid)
        slug = match_app(app, window, url, rules)
        ts = parse_ts(ts_raw)
        if slug and ts is not None:
            events.append((minute_bucket(ts), FRAME, slug, 1))
    return events, last


def read_audio_events(conn: sqlite3.Connection, after_id: int,
                      limit: int = READ_LIMIT) -> "tuple[list, int]":
    """Presence buckets from non-empty transcription rows with id > after_id."""
    rows = conn.execute(
        "SELECT id, timestamp, transcription FROM audio_transcriptions "
        "WHERE id > ? ORDER BY id LIMIT ?", (int(after_id), int(limit))).fetchall()
    events, last = [], int(after_id)
    for aid, ts_raw, text in rows:
        last = int(aid)
        ts = parse_ts(ts_raw)
        if ts is not None and str(text or "").strip():
            events.append((minute_bucket(ts), AUDIO, None, 1))
    return events, last


def _iso_bound(ts: float) -> str:
    # screenpipe writes ISO-8601 UTC with an offset; a same-format bound keeps
    # the SQL prefilter cheap, the exact comparison happens in Python.
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).isoformat()


def pending_chunks_between(conn: sqlite3.Connection, lo: float, hi: float) -> int:
    """`audio_chunks` still `pending` whose timestamp falls in [lo, hi] —
    legacy pending rows from before the first run sit outside every session
    interval and therefore never hold a CLOSE hostage."""
    rows = conn.execute(
        "SELECT timestamp FROM audio_chunks WHERE transcription_status = 'pending' "
        "AND timestamp >= ?", (_iso_bound(lo - 86400),)).fetchall()
    count = 0
    for (ts_raw,) in rows:
        ts = parse_ts(ts_raw)
        if ts is not None and lo <= ts <= hi:
            count += 1
    return count


def transcript_between(conn: sqlite3.Connection, lo: float, hi: float) -> str:
    """Transcription text inside [lo, hi] in time order (the only place the
    engine's words are read; they go straight into the fenced prompt)."""
    rows = conn.execute(
        "SELECT timestamp, transcription FROM audio_transcriptions "
        "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp, id",
        (_iso_bound(lo - 3600), _iso_bound(hi + 3600))).fetchall()
    parts = []
    for ts_raw, text in rows:
        ts = parse_ts(ts_raw)
        if ts is not None and lo <= ts <= hi and _text(text):
            parts.append(str(text).strip())
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# clustering (pure)
# --------------------------------------------------------------------------- #
def merge_buckets(events: list) -> list:
    """Sum same (minute, kind, app) rows; sorted by minute then kind/app."""
    acc: dict = {}
    for bucket, kind, app, n in events:
        k = (int(bucket), str(kind), app)
        acc[k] = acc.get(k, 0) + int(n)
    return [[k[0], k[1], k[2], n] for k, n in sorted(acc.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or ""))]


def cluster(events: list, gap_s: float) -> list:
    """Split time-sorted buckets where the silence exceeds gap_s."""
    out: list = []
    for ev in sorted(events, key=lambda e: (e[0], e[1], e[2] or "")):
        if not out or ev[0] - out[-1][-1][0] > gap_s:
            out.append([ev])
        else:
            out[-1].append(ev)
    return out


def split_long(clusters: list, max_session_s: float) -> list:
    """Cut every cluster longer than max_session_s into consecutive segments."""
    out = []
    for c in clusters:
        segment, base = [], c[0][0]
        for ev in c:
            if ev[0] - base >= max_session_s:
                out.append(segment)
                segment, base = [], ev[0]
            segment.append(ev)
        out.append(segment)
    return out


@dataclass
class Session:
    start: float
    end: float
    frames: int
    audio_rows: int
    app: str
    events: list

    @property
    def span_s(self) -> float:
        return self.end - self.start

    def key(self, timezone: str) -> str:
        return meeting_key(self.start, self.app, timezone)

    def eligible(self, opts: Options) -> bool:
        if self.span_s < opts.min_span_s:
            return False
        if self.frames >= opts.min_frames:
            return True
        return opts.audio_only and self.frames == 0 and self.audio_rows >= opts.min_frames


def _count(events: list, kind: str) -> int:
    return sum(int(n) for _, k, _, n in events if k == kind)


def _dominant_app(events: list) -> str:
    """The frame slug with the most presence (ties → alphabetical); ``audio``
    when the cluster has no frame at all."""
    tally: dict = {}
    for _, kind, app, n in events:
        if kind == FRAME:
            tally[app] = tally.get(app, 0) + int(n)
    if not tally:
        return AUDIO_ONLY_APP
    return max(sorted(tally), key=tally.__getitem__)


def describe(events: list) -> Session:
    """One cluster → Session (end = last bucket minute + 60 s)."""
    return Session(start=float(events[0][0]), end=float(events[-1][0]) + 60.0,
                   frames=_count(events, FRAME), audio_rows=_count(events, AUDIO),
                   app=_dominant_app(events), events=list(events))


def sessions_from(events: list, opts: Options) -> list:
    return [describe(seg) for seg in split_long(cluster(events, opts.gap_s), opts.max_session_s)]


def verdict(session: Session, now: float, pending: int, opts: Options) -> str:
    """CLOSED / OPEN per §63 — forced close ignores pending transcripts."""
    idle = now - session.end
    if idle >= opts.force_close_s:
        return CLOSED
    if idle >= opts.quiet_s and pending == 0:
        return CLOSED
    return OPEN


def late_slices(new_events: list, intervals: list, gap_s: float) -> "tuple[dict, list]":
    """Audio buckets that land inside an already-CLOSED interval are late
    slices: ``({key: n}, remaining_events)``. ``intervals`` = [(key, start,
    end), ...]; tolerance = gap on both sides."""
    hits: dict = {}
    rest = []
    for ev in new_events:
        target = None
        if ev[1] == AUDIO:
            target = next((k for k, lo, hi in intervals if lo - gap_s <= ev[0] <= hi + gap_s), None)
        if target is None:
            rest.append(ev)
        else:
            hits[target] = hits.get(target, 0) + int(ev[3])
    return hits, rest
