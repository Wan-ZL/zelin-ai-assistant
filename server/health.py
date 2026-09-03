"""GET /api/health — the pipeline liveness the web board shows as a banner.

CONTRACT §47.4 / §49. Three read-only files, one verdict:

- ``state/actd.heartbeat`` (§47.4) — mtime = last time the actd loop touched a
  phase boundary; the body carries the WRITER's ``stale_after_s`` so this
  module never re-derives the stall threshold (single owner: act/lib/heartbeat).
- ``state/dashboard.json`` ``generated_at`` — what the board renders from.
- ``state/loop_health.json`` (§47.3) — consecutive pass crashes.

Verdict ladder (first match wins; mirrors what doctor's ``actd heartbeat`` +
``dashboard`` rows would say, minus process probing — the server has no
launchctl and must not spawn):

    "stalled"  heartbeat present but older than stale_after_s   (2026-08-31 22:31)
    "failing"  loop_health.consecutive_failures >= 3            (§47.3 alarm)
    "stale"    no heartbeat file AND dashboard older than 90 s  (pre-v0.48.4 daemon / dead)
    "unknown"  no heartbeat file, dashboard fresh                (old daemon still writing)
    "ok"       heartbeat fresh

server/ is stdlib-only and never imports act (§49); the file layout is mirrored
in server/paths.py and pinned by tests/test_server_paths_mirror.py.
"""
from __future__ import annotations

import datetime as _dt
import json
import time
from pathlib import Path
from typing import Optional

from server import paths

# mirrors act/lib/heartbeat.STALE_FLOOR_SECONDS and doctor.DASHBOARD_FRESH_SECONDS
DASHBOARD_FRESH_SECONDS = 90
LOOP_ALARM_AFTER = 3   # mirrors act/actd.LOOP_ALARM_AFTER (§47.3)


def _read_json(p: Path) -> Optional[dict]:
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _age(p: Path, now: float) -> Optional[float]:
    try:
        return max(0.0, now - p.stat().st_mtime)
    except OSError:
        return None


def _parse_iso(ts) -> Optional[float]:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return _dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc).timestamp()
    except ValueError:
        return None


def _stale_after(body: dict) -> int:
    """The writer's own ``stale_after_s`` (torn/absent → the dashboard floor)."""
    try:
        return int(body.get("stale_after_s") or 0) or DASHBOARD_FRESH_SECONDS
    except (TypeError, ValueError):
        return DASHBOARD_FRESH_SECONDS


def _heartbeat_view(home: Path, now: float) -> Optional[dict]:
    """``heartbeat`` block, or None when the file is missing (pre-v0.48.4 daemon)."""
    hb_path = paths.heartbeat_path(home)
    hb_age = _age(hb_path, now)
    if hb_age is None:
        return None
    hb_body = _read_json(hb_path) or {}
    stale_after = _stale_after(hb_body)
    return {
        "age_s": round(hb_age, 1),
        "phase": hb_body.get("phase"),
        "pid": hb_body.get("pid"),
        "interval": hb_body.get("interval"),
        "stale_after_s": stale_after,
        "stale": hb_age > stale_after,
    }


def _dashboard_view(home: Path, now: float) -> Optional[dict]:
    """``dashboard`` block, or None when there is no parseable ``generated_at``."""
    dash_body = _read_json(paths.dashboard_path(home)) or {}
    gen_ts = _parse_iso(dash_body.get("generated_at"))
    if gen_ts is None:
        return None
    d_age = max(0.0, now - gen_ts)
    return {"generated_at": dash_body.get("generated_at"),
            "age_s": round(d_age, 1),
            "stale": d_age > DASHBOARD_FRESH_SECONDS}


def _loop_health_view(home: Path) -> dict:
    """``loop_health`` block; a non-int / bool / negative counter reads as 0."""
    lh = _read_json(paths.loop_health_path(home)) or {}
    failures = lh.get("consecutive_failures")
    if not isinstance(failures, int) or isinstance(failures, bool) or failures < 0:
        failures = 0
    return {"consecutive_failures": failures,
            "last_error": lh.get("last_error") if failures else None}


def _beating_verdict(heartbeat: dict, failures: int) -> str:
    """Heartbeat file present: stalled beats failing beats ok."""
    if heartbeat["stale"]:
        return "stalled"
    if failures >= LOOP_ALARM_AFTER:
        return "failing"
    return "ok"


def _verdict(heartbeat: Optional[dict], dashboard: Optional[dict],
             failures: int) -> str:
    """The ladder from the module docstring — first match wins."""
    if heartbeat is not None:
        return _beating_verdict(heartbeat, failures)
    if failures >= LOOP_ALARM_AFTER:
        return "failing"
    if dashboard is None or dashboard["stale"]:
        return "stale"
    return "unknown"


def snapshot(home: Path, now: Optional[float] = None) -> dict:
    """The /api/health body. Never raises; missing files are reported as such."""
    now = time.time() if now is None else now
    heartbeat = _heartbeat_view(home, now)
    dashboard = _dashboard_view(home, now)
    loop_health = _loop_health_view(home)
    return {
        "verdict": _verdict(heartbeat, dashboard, loop_health["consecutive_failures"]),
        "heartbeat": heartbeat,
        "dashboard": dashboard,
        "loop_health": loop_health,
        "checked_at": _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
