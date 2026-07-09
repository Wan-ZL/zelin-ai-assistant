"""Usage analytics — append-only event log for every feature use.

One JSONL line per event in ``state/analytics/events.jsonl``:
    {"ts": "2026-07-06T23:01:02Z", "event": "inbox_approve", "req": "R-004", ...}

``python -m act.report`` aggregates: per-feature frequency (7d/30d), hour-of-day
and day-of-week heat, health signals (rework rate = unclear proposals, resume
failures = ineffective repetition, approval latency), and repetition storms.

Analytics must NEVER break the pipeline — every failure here is swallowed.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Iterator, Optional

from act.lib import config

ANALYTICS_DIR: Path = config.STATE_DIR / "analytics"
EVENTS_PATH: Path = ANALYTICS_DIR / "events.jsonl"


def log_event(event: str, **fields) -> None:
    """Append one event. Non-None fields only. Never raises."""
    try:
        ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": str(event),
        }
        for k, v in fields.items():
            if v is not None:
                rec[k] = v
        with open(EVENTS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - analytics must never break the pipeline
        pass


def parse_ts(s: str) -> Optional[_dt.datetime]:
    """Parse an event 'ts' (UTC) -> aware datetime, or None."""
    try:
        return _dt.datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc)
    except (ValueError, TypeError):
        return None


def read_events(since: Optional[_dt.datetime] = None) -> Iterator[dict]:
    """Yield parsed events (optionally only those newer than ``since``, UTC)."""
    try:
        fh = open(EVENTS_PATH, encoding="utf-8")
    except OSError:
        return
    with fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since is not None:
                try:
                    ts = _dt.datetime.strptime(d.get("ts", ""), "%Y-%m-%dT%H:%M:%SZ")
                    ts = ts.replace(tzinfo=_dt.timezone.utc)
                except ValueError:
                    continue
                if ts < since:
                    continue
            yield d
