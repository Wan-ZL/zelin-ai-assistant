"""Telemetry sync — default-ON batched upload of analytics events to Supabase.

The local JSONL log (``state/analytics/events.jsonl``, see act/lib/analytics.py)
stays the source of truth and is NEVER modified or deleted here. This module
tails it with a persistent byte-offset cursor (``state/analytics_sync.json``)
and POSTs new events to Supabase via PostgREST:

    POST {supabase_url}/rest/v1/analytics_events   (apikey + key)

Default ON (docs/TELEMETRY.md): ``telemetry.enabled`` defaults true and
``telemetry.supabase_url`` defaults to the maintainer's project, uploading with
the built-in PUBLISHABLE key (anon role, RLS INSERT-only — it can never read
data back). A key file per CONTRACT §19 (``config/secrets/
supabase-service-key.txt`` → explicit ``telemetry.key_path``) still WINS when
present, so a self-hosted/service-key setup keeps working unchanged. Opt out
with the Settings toggle or ``telemetry.enabled: false``; an empty
``supabase_url`` disables uploads entirely -> silent cheap no-op.

Exactly-once for appends: the cursor is saved atomically (.tmp + os.replace)
after EVERY successfully uploaded batch, so a mid-run failure resumes at the
last good batch. A half-written trailing line (no "\\n" yet — the Swift and
Python writers both append whole lines) is left for the next run.

Sync must NEVER break anything — every failure is swallowed and reported as a
``telemetry_sync`` analytics event. That event lands AFTER the cursor was
saved, so it is uploaded by the NEXT run — one pass per invocation, never a
recursive upload of its own event in the same run (and it doubles as the
decision-15 heartbeat: a dead sync and "nothing to upload" stay distinguishable).
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Tuple

from act.lib import analytics, config, secrets

CURSOR_PATH: Path = config.STATE_DIR / "analytics_sync.json"
DEVICE_ID_PATH: Path = config.STATE_DIR / "device_id"

# Fixed secrets file name (CONTRACT §19 pattern, like slack-user-token.txt).
SUPABASE_SERVICE_KEY_FILE = "supabase-service-key.txt"


def _resolve_key(cfg: config.Config) -> str:
    """Upload key: key file (CONTRACT §19 / telemetry.key_path) wins; else the
    built-in publishable key (public by design — RLS allows INSERT only)."""
    key = secrets.resolve_credential(
        SUPABASE_SERVICE_KEY_FILE, explicit_path=cfg.telemetry_key_path)
    return key or config.DEFAULT_TELEMETRY_PUBLISHABLE_KEY

BATCH_SIZE = 500
TIMEOUT_SECONDS = 15

# rows -> None, raises on any transport failure. Injectable for tests.
Transport = Callable[[List[dict]], None]


# --------------------------------------------------------------------------- #
# Device id — stable per-install uuid4, generated once into state/device_id.
# --------------------------------------------------------------------------- #
def _device_id() -> str:
    try:
        val = DEVICE_ID_PATH.read_text(encoding="utf-8").strip()
        if val:
            return val
    except OSError:
        pass
    val = str(uuid.uuid4())
    DEVICE_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DEVICE_ID_PATH.with_suffix(".tmp")
    tmp.write_text(val + "\n", encoding="utf-8")
    os.replace(tmp, DEVICE_ID_PATH)
    return val


# --------------------------------------------------------------------------- #
# Cursor file — {"files": {"events.jsonl": <byte offset>}}, atomic writes.
# --------------------------------------------------------------------------- #
def _load_cursor() -> dict:
    try:
        data = json.loads(CURSOR_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - corrupt cursor -> start over, never crash
        return {}


def _save_cursor(file_name: str, offset: int) -> None:
    CURSOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = _load_cursor()
    files = data.get("files")
    if not isinstance(files, dict):
        files = {}
    files[file_name] = int(offset)
    data["files"] = files
    tmp = CURSOR_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, CURSOR_PATH)


def _cursor_offset(file_name: str) -> int:
    files = _load_cursor().get("files")
    if not isinstance(files, dict):
        return 0
    try:
        return max(0, int(files.get(file_name, 0)))
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------- #
# Reading — complete lines only, with the byte offset after each line.
# --------------------------------------------------------------------------- #
def _complete_lines(path: Path, offset: int) -> Iterator[Tuple[bytes, int]]:
    """Yield (raw_line, end_offset) for each COMPLETE line past ``offset``.

    A trailing chunk without "\\n" is a line still being written — it is not
    yielded, so the cursor stays before it and the next run picks it up whole.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return
    if offset > size:  # file replaced/truncated — old bytes are gone, restart
        offset = 0
    with open(path, "rb") as fh:
        fh.seek(offset)
        pos = offset  # track manually: fh.tell() lies during buffered iteration
        for raw in fh:
            if not raw.endswith(b"\n"):
                return
            pos += len(raw)
            yield raw, pos


def _to_row(raw: bytes, device_id: str) -> Optional[dict]:
    """Map one JSONL line to an analytics_events row, or None if malformed."""
    try:
        rec = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(rec, dict) or not rec.get("event"):
        return None

    def _s(v) -> Optional[str]:
        return str(v) if v is not None else None

    ts = rec.get("ts")
    return {
        "device_id": device_id,
        "sid": _s(rec.get("sid")),
        "app_version": _s(rec.get("v")),  # Swift writer's version field
        "source": _s(rec.get("source")),
        "event": str(rec["event"]),
        "props": rec,  # full original record
        # timestamptz column: send the ISO string only if it really parses
        "client_ts": ts if analytics.parse_ts(ts or "") else None,
    }


# --------------------------------------------------------------------------- #
# Transport — stdlib urllib only (repo has no third-party deps beyond PyYAML).
# --------------------------------------------------------------------------- #
def _make_transport(supabase_url: str, key: str) -> Transport:
    endpoint = supabase_url.rstrip("/") + "/rest/v1/analytics_events"

    def send(rows: List[dict]) -> None:
        body = json.dumps(rows, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "apikey": key,
                "Authorization": "Bearer " + key,
                "Prefer": "return=minimal",
            },
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            resp.read()

    return send


# --------------------------------------------------------------------------- #
# One sync pass
# --------------------------------------------------------------------------- #
def sync_once(cfg: Optional[config.Config] = None,
              transport: Optional[Transport] = None) -> dict:
    """Upload all new complete events in batches. Never raises.

    Returns a stats dict: {"ok", "uploaded", "batches", "malformed", "error"}
    plus "skipped" when telemetry is disabled/unconfigured (silent no-op).
    """
    stats: dict = {"ok": True, "uploaded": 0, "batches": 0,
                   "malformed": 0, "error": None}
    try:
        cfg = cfg or config.load_config()
        url = str(cfg.telemetry_supabase_url or "").strip()
        if not (cfg.telemetry_enabled and url):
            stats["skipped"] = "disabled"
            return stats
        key = _resolve_key(cfg)
        if not key:
            stats["skipped"] = "no_key"
            return stats

        send = transport or _make_transport(url, key)
        device_id = _device_id()
        file_name = analytics.EVENTS_PATH.name
        offset = _cursor_offset(file_name)

        batch: List[dict] = []
        batch_end = offset
        for raw, end in _complete_lines(analytics.EVENTS_PATH, offset):
            row = _to_row(raw, device_id)
            if row is None:
                stats["malformed"] += 1
            else:
                batch.append(row)
            batch_end = end
            if len(batch) >= BATCH_SIZE:
                send(batch)
                _save_cursor(file_name, batch_end)
                stats["uploaded"] += len(batch)
                stats["batches"] += 1
                batch = []
        if batch:
            send(batch)
            stats["uploaded"] += len(batch)
            stats["batches"] += 1
        if batch_end > offset:  # also past a malformed-only tail
            _save_cursor(file_name, batch_end)
    except Exception as exc:  # noqa: BLE001 - telemetry must never break anything
        stats["ok"] = False
        stats["error"] = str(exc)[:120]

    # Logged AFTER the cursor writes: this event is picked up by the NEXT run,
    # never re-entered in this one.
    analytics.log_event("telemetry_sync", ok=stats["ok"],
                        uploaded=stats["uploaded"],
                        malformed=stats["malformed"] or None,
                        error=stats["error"])
    return stats
