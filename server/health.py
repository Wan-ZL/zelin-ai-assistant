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


def snapshot(home: Path, now: Optional[float] = None) -> dict:
    """The /api/health body. Never raises; missing files are reported as such."""
    now = time.time() if now is None else now

    hb_path = paths.heartbeat_path(home)
    hb_age = _age(hb_path, now)
    hb_body = _read_json(hb_path) or {}
    heartbeat: Optional[dict] = None
    if hb_age is not None:
        try:
            stale_after = int(hb_body.get("stale_after_s") or 0) or DASHBOARD_FRESH_SECONDS
        except (TypeError, ValueError):
            stale_after = DASHBOARD_FRESH_SECONDS
        heartbeat = {
            "age_s": round(hb_age, 1),
            "phase": hb_body.get("phase"),
            "pid": hb_body.get("pid"),
            "interval": hb_body.get("interval"),
            "stale_after_s": stale_after,
            "stale": hb_age > stale_after,
        }

    dash_path = paths.dashboard_path(home)
    dash_body = _read_json(dash_path) or {}
    gen_ts = _parse_iso(dash_body.get("generated_at"))
    dashboard: Optional[dict] = None
    if gen_ts is not None:
        d_age = max(0.0, now - gen_ts)
        dashboard = {"generated_at": dash_body.get("generated_at"),
                     "age_s": round(d_age, 1),
                     "stale": d_age > DASHBOARD_FRESH_SECONDS}

    lh = _read_json(paths.loop_health_path(home)) or {}
    failures = lh.get("consecutive_failures")
    if not isinstance(failures, int) or isinstance(failures, bool) or failures < 0:
        failures = 0
    loop_health = {"consecutive_failures": failures,
                   "last_error": lh.get("last_error") if failures else None}

    if heartbeat is not None and heartbeat["stale"]:
        verdict = "stalled"
    elif failures >= LOOP_ALARM_AFTER:
        verdict = "failing"
    elif heartbeat is None and (dashboard is None or dashboard["stale"]):
        verdict = "stale"
    elif heartbeat is None:
        verdict = "unknown"
    else:
        verdict = "ok"

    return {
        "verdict": verdict,
        "heartbeat": heartbeat,
        "dashboard": dashboard,
        "loop_health": loop_health,
        "checked_at": _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
