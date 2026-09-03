"""Fixture screenpipe database for the §63 recap tests (not a test module).

Replicates the three engine tables the recap reader touches, with the column
subset it reads (schema copied from a live ~/.screenpipe/db.sqlite, 2026-08):
``frames(id, timestamp, app_name, window_name, browser_url)``,
``audio_transcriptions(id, audio_chunk_id, timestamp, transcription)`` and
``audio_chunks(id, timestamp, transcription_status)``. Timestamps are written
the way screenpipe writes them: ISO-8601 UTC with a ``+00:00`` offset and
microseconds.

Time base: T0 = 2026-08-31 19:56:00 UTC = 12:56 America/Los_Angeles (PDT) —
the meeting from issue #129 (``meeting:2026-08-31T1256-zoom``).
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path

T0 = _dt.datetime(2026, 8, 31, 19, 56, tzinfo=_dt.timezone.utc).timestamp()
MIN = 60.0
TZ = "America/Los_Angeles"

try:  # Windows runners ship no tz database: keys then fall back to local time
    from zoneinfo import ZoneInfo
    ZoneInfo(TZ)
    HAS_TZDATA = True
except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError or missing module
    HAS_TZDATA = False


def key_at(ts: float, app: str = "zoom") -> str:
    """The dedup key the code under test will compute for ``ts`` (tz-aware)."""
    from act.lib import recap_sessions
    return recap_sessions.meeting_key(ts, app, TZ)


KEY = key_at(T0)   # meeting:2026-08-31T1256-zoom wherever tzdata exists

SCHEMA = """
CREATE TABLE frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP NOT NULL,
    app_name TEXT DEFAULT NULL,
    window_name TEXT DEFAULT NULL,
    browser_url TEXT DEFAULT NULL
);
CREATE TABLE audio_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP,
    transcription_status TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE audio_transcriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audio_chunk_id INTEGER NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    transcription TEXT NOT NULL
);
"""

# ~12 words per row; 30 rows ≈ 360 words (over the 300-word floor)
SENTENCE = "We agreed the training run moves to the new data mix starting Monday morning. "


def iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).isoformat()


def make_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def add_frames(conn: sqlite3.Connection, start: float, minutes: int, app: str = "zoom.us",
               window: str = "Zoom Meeting", url=None, per_minute: int = 2) -> None:
    """``minutes`` consecutive minutes of ``per_minute`` frames each."""
    rows = []
    for m in range(minutes):
        for k in range(per_minute):
            rows.append((iso(start + m * MIN + k * (MIN / per_minute)), app, window, url))
    conn.executemany("INSERT INTO frames(timestamp, app_name, window_name, browser_url) "
                     "VALUES (?, ?, ?, ?)", rows)
    conn.commit()


def add_audio(conn: sqlite3.Connection, start: float, minutes: int, status: str = "transcribed",
              text: str = SENTENCE, rows_per_minute: int = 2) -> None:
    """Transcribed audio: one chunk + ``rows_per_minute`` transcription rows per minute."""
    for m in range(minutes):
        ts = start + m * MIN
        cur = conn.execute("INSERT INTO audio_chunks(timestamp, transcription_status) VALUES (?, ?)",
                           (iso(ts), status))
        chunk_id = cur.lastrowid
        for k in range(rows_per_minute):
            conn.execute("INSERT INTO audio_transcriptions(audio_chunk_id, timestamp, transcription) "
                         "VALUES (?, ?, ?)", (chunk_id, iso(ts + k * (MIN / rows_per_minute)), text))
    conn.commit()


def add_pending_chunk(conn: sqlite3.Connection, ts: float) -> int:
    cur = conn.execute("INSERT INTO audio_chunks(timestamp, transcription_status) VALUES (?, 'pending')",
                       (iso(ts),))
    conn.commit()
    return int(cur.lastrowid)


def settle_chunk(conn: sqlite3.Connection, chunk_id: int, status: str = "transcribed") -> None:
    conn.execute("UPDATE audio_chunks SET transcription_status = ? WHERE id = ?", (status, chunk_id))
    conn.commit()


def good_output(en_tail: str = "", zh_tail: str = "") -> str:
    """A validator-clean model reply (JSON in a code fence, like claude prints)."""
    import json
    en = ["Decided: the training run moves to the new data mix from Monday" + en_tail,
          "Split: not assigned",
          "Deadline: none set",
          "Changed since last plan: none recorded",
          "Open: none"]
    zh = ["定了：训练从周一起改用新数据配比" + zh_tail,
          "分工：未分配",
          "截止：未定",
          "较上次变化：无记录",
          "待定：无"]
    return "```json\n" + json.dumps({"en": en, "zh": zh}, ensure_ascii=False) + "\n```"
