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

from act import __version__
from act.lib import config

ANALYTICS_DIR: Path = config.STATE_DIR / "analytics"
EVENTS_PATH: Path = ANALYTICS_DIR / "events.jsonl"

# Hard cap for every user-typed content field (docs/TELEMETRY.md「输入文本
# 收集」): capture text / Ask questions / card comments / instruction
# summaries all pass through clip(text, CONTENT_CLIP). Model OUTPUT is never
# captured at any setting — only what the user typed.
CONTENT_CLIP: int = 500


def content_gate(cfg=None) -> bool:
    """Emit-side gate for user-typed content fields (docs/TELEMETRY.md).

    True only when telemetry.capture_input is on AND level is "detailed"
    (Config.capture_input_active). Loads config lazily so no-cfg call sites
    (actd inbox helpers) can use it; any failure means False — content must
    never leak because a gate check crashed.
    """
    try:
        cfg = cfg or config.load_config()
        return bool(cfg.capture_input_active())
    except Exception:  # noqa: BLE001 - fail closed, never break the pipeline
        return False


def log_event(event: str, **fields) -> None:
    """Append one event. Non-None fields only. Never raises."""
    try:
        ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": str(event),
            # writer-level version stamp (docs/TELEMETRY.md): every python
            # event carries "v", mirroring the Swift writer — no emitter can
            # forget it, so app_version is never "(unset)" on upload.
            "v": __version__,
        }
        for k, v in fields.items():
            if v is not None:
                rec[k] = v
        with open(EVENTS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - analytics must never break the pipeline
        pass


def clip(text, limit: int = 200) -> Optional[str]:
    """Whitespace-collapsed, truncated string for telemetry payload fields
    (docs/TELEMETRY.md; content fields use limit=CONTENT_CLIP) — None when
    empty so log_event drops it.
    """
    s = " ".join(str(text or "").split())
    return s[:limit] or None


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
