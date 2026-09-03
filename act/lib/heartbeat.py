"""actd heartbeat — ``state/actd.heartbeat`` (CONTRACT §47.4).

The 2026-08-31 silent stall: actd (pid alive, no children, parked in
``time.sleep``) stopped writing dashboard.json at 22:31:56 and stayed that way
for 2.5 hours. Nothing the product itself watched could tell "alive" from
"looping": ``launchctl list`` showed a pid, ``loop_health.json`` only counts
pass *crashes*, and the one detector — the Mac app's staleness banner — is
retiring (D3). This file is the honest liveness signal: the loop touches it at
every phase boundary, so its **mtime** is the truth and the JSON body only
explains *where* the loop was last seen.

Readers: ``act/doctor.py`` (``actd heartbeat`` probe — alive process + stale
beat = FAIL ``actd_stalled`` with the kickstart hint) and ``server/health.py``
(``GET /api/health`` for the web banner; server/ is stdlib-only and mirrors the
file layout instead of importing this module — ``tests/test_server_paths_mirror.py``
pins the path, ``tests/test_actd_heartbeat.py`` pins the body shape).

Shape (add-only)::

    {"ts": "2026-09-01T08:00:00Z", "phase": "idle", "pid": 4242,
     "interval": 10, "stale_after_s": 90, "version": "0.48.4"}

``stale_after_s`` is computed by the WRITER (``max(3 × interval, 90)``) so every
reader agrees on the threshold without re-deriving it: three missed passes is
the stall definition, and the 90 s floor (= the dashboard freshness threshold)
keeps a legitimately long single pass on a 10 s interval from reading as dead.
Writes never raise — a heartbeat that can take the daemon down is worse than
none (constitution art. 11).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
from pathlib import Path
from typing import Optional

from act.lib import config

HEARTBEAT_NAME = "actd.heartbeat"
HEARTBEAT_PATH = config.STATE_DIR / HEARTBEAT_NAME

# "stale" = this many pass intervals without a beat …
STALE_AFTER_INTERVALS = 3
# … but never sooner than this (mirrors doctor.DASHBOARD_FRESH_SECONDS; a
# single pass legitimately runs `claude agents --json` + a `claude --bg`
# launch, which on a loaded machine can exceed 30 s).
STALE_FLOOR_SECONDS = 90


def stale_after_seconds(interval: Optional[int]) -> int:
    try:
        iv = int(interval or 0)
    except (TypeError, ValueError):
        iv = 0
    return max(STALE_AFTER_INTERVALS * max(iv, 0), STALE_FLOOR_SECONDS)


def beat(phase: str, interval: Optional[int] = None,
         path: Optional[Path] = None) -> None:
    """Touch the heartbeat with the current phase. Atomic (.tmp + rename),
    never raises."""
    try:
        from act import __version__ as _version
    except Exception:  # noqa: BLE001 - version is decoration, not signal
        _version = ""
    target = path or HEARTBEAT_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "ts": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "phase": str(phase)[:40],
            "pid": os.getpid(),
            "interval": int(interval) if interval else None,
            "stale_after_s": stale_after_seconds(interval),
            "version": _version,
        }
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
    except Exception:  # noqa: BLE001 - the heartbeat must never kill the loop
        pass


def read(path: Optional[Path] = None) -> Optional[dict]:
    """The last beat's body plus ``age_s`` (from the file's mtime), or None
    when the file is missing/unreadable. Torn/invalid JSON still returns the
    mtime-derived age (``{"age_s": …}`` only) — the mtime is the signal."""
    target = path or HEARTBEAT_PATH
    try:
        mtime = target.stat().st_mtime
    except OSError:
        return None
    out: dict = {"age_s": max(0.0, time.time() - mtime)}
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(body, dict):
            out.update(body)
    except (OSError, ValueError):
        pass
    return out


def stale_limit(hb: dict) -> int:
    """The writer's own ``stale_after_s``; torn/absent → the floor."""
    try:
        return int(hb.get("stale_after_s") or 0) or STALE_FLOOR_SECONDS
    except (TypeError, ValueError):
        return STALE_FLOOR_SECONDS


def is_stale(hb: Optional[dict]) -> Optional[bool]:
    """None = no heartbeat at all; else whether its age exceeds the writer's
    own ``stale_after_s`` (falling back to the floor when the body is torn)."""
    if not hb:
        return None
    return float(hb.get("age_s") or 0) > stale_limit(hb)
