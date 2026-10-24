"""GET /api/health — the liveness line the web shows as a banner (CONTRACT §49).

v2.0.0: the server answering IS the first half of liveness (the shell's
connection probe hits this route); the other two are read-only files:

- ``state/cron_probe.json`` (§25) — written by every run of the §18 cron ingest
  chain; its ``ts`` says when the chain last fired.
- ``state/recap/sessions.json`` ``updated_at`` (§63) — when ``act.recap`` last
  completed a round (cron ``--once`` or a server-spawned ``--generate``).

Verdict ladder (first match wins):

    "stale"    the cron probe is older than CRON_FRESH_SECONDS (chain stopped firing)
    "unknown"  no cron probe yet (fresh install, or the chain never ran)
    "ok"       the chain fired recently

The v1 actd heartbeat / dashboard freshness / loop-health blocks retired with
actd (full text in tag ``full``). Never raises; server/ never imports act here
(the file layout is mirrored in server/paths.py, pinned by
tests/test_server_paths_mirror.py).
"""
from __future__ import annotations

import datetime as _dt
import json
import time
from pathlib import Path
from typing import Optional

from server import paths

# mirrors act/lib/checks/cron.CRON_PROBE_FRESH_SECONDS (the chain fires every 30 min)
CRON_FRESH_SECONDS = 2 * 3600


def _read_json(p: Path) -> Optional[dict]:
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _parse_iso(ts) -> Optional[float]:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return _dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc).timestamp()
    except ValueError:
        return None


def _stamp(value) -> Optional[float]:
    """A cron-probe ``ts``: epoch seconds (int/float) or ISO-Z; else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return _parse_iso(value)


def _cron_view(home: Path, now: float) -> Optional[dict]:
    """``cron`` block, or None when the chain never wrote its probe."""
    doc = _read_json(paths.cron_probe_path(home))
    ts = _stamp((doc or {}).get("ts"))
    if ts is None:
        return None
    age = max(0.0, now - ts)
    return {"age_s": round(age, 1), "read_ok": (doc or {}).get("read_ok"),
            "stale": age > CRON_FRESH_SECONDS}


def _recap_view(home: Path, now: float) -> Optional[dict]:
    """``recap`` block: when the last act.recap round finished; None = never."""
    doc = _read_json(home / "state" / "recap" / "sessions.json")
    ts = _parse_iso((doc or {}).get("updated_at"))
    if ts is None:
        return None
    return {"updated_at": (doc or {}).get("updated_at"), "age_s": round(max(0.0, now - ts), 1)}


def _verdict(cron: Optional[dict]) -> str:
    if cron is None:
        return "unknown"
    return "stale" if cron["stale"] else "ok"


def snapshot(home: Path, now: Optional[float] = None) -> dict:
    """The /api/health body. Never raises; missing files are reported as such."""
    now = time.time() if now is None else now
    cron = _cron_view(home, now)
    return {
        "verdict": _verdict(cron),
        "server": {"ok": True},
        "cron": cron,
        "recap": _recap_view(home, now),
        "checked_at": _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
