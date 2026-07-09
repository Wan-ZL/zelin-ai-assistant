"""Radar health file — per-source last attempt / last success / skip reason.

One JSON file at ``state/radar_health.json`` (contract §E, v0.10):
    {"gmail": {"last_attempt": iso, "last_ok": iso|null, "skip_reason": str|null},
     "slack": {...}}

Written by the radars (act/radar_gmail.py, act/radar_slack.py) on every scan
attempt; read by the Mac app to surface "radar 多久没成功跑过了" in the UI.

Semantics of :func:`update_radar_health`:
- every call bumps ``last_attempt`` to now;
- ``ok=True``  -> ``last_ok`` = now, ``skip_reason`` = None;
- ``ok=False`` -> ``skip_reason`` recorded, previous ``last_ok`` preserved.

Health must NEVER break a radar pass — every failure here is swallowed, and
the write is atomic (.tmp + os.replace) so the app never reads a torn file.
"""
from __future__ import annotations

import datetime as _dt
import fcntl
import json
import os
from pathlib import Path
from typing import Optional

from act.lib import config

HEALTH_PATH: Path = config.STATE_DIR / "radar_health.json"
# Sidecar lock (NOT the json itself: os.replace swaps the inode, so flocking
# the data file would let a second process lock the stale inode). gmail/slack
# radars are separate launchd agents that can run concurrently — without the
# lock the read-modify-write below loses one side's update (classic lost
# update). flock auto-releases on process exit, so no stale-lock hang risk.
_LOCK_PATH: Path = config.STATE_DIR / "radar_health.lock"


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> dict:
    """Current health dict; unreadable/corrupt/missing file -> empty dict."""
    try:
        data = json.loads(HEALTH_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - start fresh rather than crash a radar
        return {}


def update_radar_health(source: str, ok: bool,
                        skip_reason: Optional[str] = None) -> None:
    """Record one radar attempt for ``source`` ("gmail"/"slack"). Never raises."""
    try:
        HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Serialize the whole read-modify-write across processes; blocking is
        # fine (holders finish in milliseconds, and the OS drops the lock even
        # if a holder crashes).
        with open(_LOCK_PATH, "w") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            data = _load()
            entry = data.get(source)
            if not isinstance(entry, dict):
                entry = {"last_attempt": None, "last_ok": None, "skip_reason": None}
            entry["last_attempt"] = _iso_now()
            if ok:
                entry["last_ok"] = _iso_now()
                entry["skip_reason"] = None
            else:
                entry["skip_reason"] = skip_reason
            data[source] = entry
            tmp = HEALTH_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, HEALTH_PATH)
    except Exception:  # noqa: BLE001 - health must never break a radar pass
        pass
