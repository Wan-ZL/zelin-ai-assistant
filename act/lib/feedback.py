"""User feedback channel (CONTRACT §29) — 「建议上报」 explicit user reports.

The app writes ``{"action":"feedback","ids":[R-xxx|MS-xxx,…],"text":"…"}`` to
the inbox; actd validates and calls :func:`record_feedback` here. Each report:

1. lands locally FIRST as ``state/feedback/<uuid>.json`` (atomic tmp+rename):
   ts, ids, a per-id type+title snapshot (so the report stays readable after
   the cards themselves change or get purged), the user's text, app version;
2. is then uploaded best-effort to Supabase over the SAME anon INSERT channel
   as telemetry (act/lib/analytics_sync.py conventions: PostgREST POST to
   ``/rest/v1/analytics_events``, key file wins over the built-in publishable
   key) — but as its own event type ``event="feedback"`` with the full record
   in ``props``. No new table: the anon RLS policy only covers
   analytics_events, and the event type keeps feedback rows separable.

Upload lifecycle (CONTRACT §29): the initial attempt happens inline; on
failure the record stays local with ``uploaded: null`` (pending) and a LATER
actd pass retries ONCE via :func:`retry_pending` — a second failure marks
``uploaded: false`` for good (file kept; never retried again). The sweep
skips records younger than ``MIN_RETRY_AGE_SECONDS``: run_once() calls it in
the same pass that did the inline attempt, and retrying seconds into the same
network outage would burn the single retry for nothing.

Deliberate policy (CONTRACT §29): feedback is an EXPLICIT user action, so the
upload ignores ``telemetry.enabled`` and the first-run consent gate. It still
respects the fork hard-off switch: an empty ``telemetry.supabase_url`` means
there is nowhere to send — the record is kept local with ``uploaded: false``.
The payload includes card titles and the user's own words (may contain
sensitive terms — pressing send IS the consent).

Nothing in this module may raise past its public functions — a broken
feedback path must never take the daemon pass down.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import urllib.request
import uuid
from pathlib import Path
from typing import Callable, Optional

from act import __version__
from act.lib import analytics, analytics_sync, config
from act.lib import registry

try:  # merge_review pulls executor/analyze — must never break this module
    from act import merge_review
except Exception:  # noqa: BLE001 - snapshot falls back to "unknown"
    merge_review = None  # type: ignore

FEEDBACK_DIR: Path = config.STATE_DIR / "feedback"

TEXT_CAP = 4000        # bound the on-disk record; the app caps earlier anyway
ERROR_CAP = 200        # like merge_review: first 200 chars of the last error
TIMEOUT_SECONDS = 10   # inline in the daemon pass — keep the block bounded
# §29 "retry next pass" needs REAL time separation: the sweep runs in the same
# run_once() that just did the inline first attempt, so without an age floor
# the final retry would fire seconds later, inside the same network outage,
# and mark uploaded:false for good. A record younger than this stays pending.
MIN_RETRY_AGE_SECONDS = 60

# one row -> None, raises on any transport failure. Injectable for tests.
Transport = Callable[[dict], None]


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Card snapshots — type + title at report time (CONTRACT §29)
# --------------------------------------------------------------------------- #
def clean_ids(raw) -> list:
    """Coerce the inbox ``ids`` value into a deduped list of non-empty strings.
    Anything non-list (or entries that stringify to empty) is tolerated — bad
    ids must never lose the report text."""
    if not isinstance(raw, list):
        return []
    seen: set = set()
    out: list = []
    for item in raw:
        s = str(item or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _snapshot(rid: str) -> dict:
    """Best-effort {id, kind, type, title, status} for one R-/MS- id.

    Unknown/garbage ids degrade to kind="unknown" (title null) instead of
    failing the report — the user's text is the payload that matters.
    """
    snap = {"id": rid, "kind": "unknown", "type": None,
            "title": None, "status": None}
    try:
        if merge_review is not None and rid.startswith("MS-"):
            job = merge_review.load_job(rid)
            if isinstance(job, dict):
                members = [str(i) for i in job.get("ids") or []]
                return {
                    "id": rid,
                    "kind": "merge_suggestion",
                    "type": "merge_suggestion",
                    # merge jobs carry no title of their own — synthesize one
                    # from the member cards so the report stays readable
                    "title": "merge suggestion: " + " + ".join(members),
                    "status": str(job.get("status") or "") or None,
                }
        req = registry.load(rid)
        if req is not None:
            return {
                "id": rid,
                "kind": "requirement",
                "type": str(req.type or "") or None,
                "title": str(req.title or "") or None,
                "status": str(req.status or "") or None,
            }
    except Exception:  # noqa: BLE001 - a bad card must not lose the report
        pass
    return snap


# --------------------------------------------------------------------------- #
# Local record — state/feedback/<uuid>.json, atomic writes
# --------------------------------------------------------------------------- #
def _record_path(record_id: str) -> Path:
    return FEEDBACK_DIR / f"{record_id}.json"


def _write_record(record: dict) -> None:
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    path = _record_path(str(record["id"]))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Upload — the telemetry anon INSERT channel, event type "feedback"
# --------------------------------------------------------------------------- #
def _to_row(record: dict) -> dict:
    """Map a local record to an analytics_events row (same column shape as
    analytics_sync._to_row; props = the report content, no upload bookkeeping).
    """
    props = {k: record.get(k)
             for k in ("id", "ts", "ids", "cards", "text", "app_version")}
    ts = record.get("ts")
    return {
        "device_id": analytics_sync._device_id(),
        "app_version": record.get("app_version"),
        "source": "feedback",
        "event": "feedback",
        "props": props,
        "client_ts": ts if analytics.parse_ts(ts or "") else None,
    }


def _make_transport(supabase_url: str, key: str) -> Transport:
    endpoint = supabase_url.rstrip("/") + "/rest/v1/analytics_events"

    def send(row: dict) -> None:
        body = json.dumps([row], ensure_ascii=False).encode("utf-8")
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
        # B310: endpoint is the https Supabase URL from the user's own config
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # nosec B310
            resp.read()

    return send


def _default_transport(cfg: config.Config) -> Optional[Transport]:
    """None only when there is nowhere to send (empty supabase_url — the fork
    hard-off switch) or no key resolves. telemetry.enabled is deliberately
    NOT consulted: feedback is an explicit user action (CONTRACT §29)."""
    url = str(cfg.telemetry_supabase_url or "").strip()
    if not url:
        return None
    key = analytics_sync._resolve_key(cfg)
    if not key:
        return None
    return _make_transport(url, key)


def _attempt_upload(record: dict, cfg: config.Config,
                    transport: Optional[Transport], final: bool) -> None:
    """One upload attempt; rewrites the record with the outcome.

    - success            -> uploaded=true (+uploaded_at)
    - failure, not final -> uploaded stays null (pending — actd retries once)
    - failure, final     -> uploaded=false (given up; file kept, never retried)
    - no transport       -> uploaded=false immediately (uploads disabled)
    """
    send = transport if transport is not None else _default_transport(cfg)
    if send is None:
        record["uploaded"] = False
        record["upload_error"] = "uploads disabled (empty supabase_url)"
        _write_record(record)
        return
    try:
        send(_to_row(record))
        record["uploaded"] = True
        record["uploaded_at"] = _iso_now()
        record.pop("upload_error", None)
    except Exception as e:  # noqa: BLE001 - upload is best-effort by contract
        record["upload_attempts"] = int(record.get("upload_attempts") or 0) + 1
        record["upload_error"] = str(e)[:ERROR_CAP]
        if final:
            record["uploaded"] = False
    _write_record(record)


# --------------------------------------------------------------------------- #
# Public API — both functions swallow everything (daemon-safe)
# --------------------------------------------------------------------------- #
def record_feedback(ids, text, cfg: Optional[config.Config] = None,
                    transport: Optional[Transport] = None) -> Optional[dict]:
    """Persist one feedback report locally, then try the upload once.

    Returns the record dict (with its upload outcome), or None when the text
    is empty/whitespace or the local write itself failed. Never raises.
    """
    try:
        body = str(text or "").strip()
        if not body:
            return None
        cfg = cfg or config.load_config()
        id_list = clean_ids(ids)
        record = {
            "id": uuid.uuid4().hex,
            "ts": _iso_now(),
            "ids": id_list,
            "cards": [_snapshot(i) for i in id_list],
            "text": body[:TEXT_CAP],
            "app_version": __version__,
            "uploaded": None,       # null = pending; true/false are terminal
            "upload_attempts": 0,
        }
        # local file FIRST — the report must survive any upload trouble
        _write_record(record)
        _attempt_upload(record, cfg, transport, final=False)
        return record
    except Exception:  # noqa: BLE001 - feedback must never break the pass
        return None


def retry_pending(cfg: Optional[config.Config] = None,
                  transport: Optional[Transport] = None) -> int:
    """Retry every pending record ONCE (uploaded=null -> true|false). Called
    each actd pass; terminal records are skipped so the sweep is cheap and a
    record is retried at most one time after its initial failure. Records
    younger than ``MIN_RETRY_AGE_SECONDS`` are left pending untouched — the
    inline first attempt happened in THIS pass, and the §29 contract's whole
    point is that the final retry runs on a genuinely later pass, outside the
    original failure window. Returns the number of records attempted. Never
    raises."""
    try:
        files = sorted(FEEDBACK_DIR.glob("*.json"))
    except OSError:
        return 0
    attempted = 0
    now = _dt.datetime.now(_dt.timezone.utc)
    for path in files:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or not record.get("id"):
                continue
            if record.get("uploaded") is not None:
                continue  # terminal: already uploaded or given up
            ts = analytics.parse_ts(str(record.get("ts") or ""))
            if ts is not None and (now - ts).total_seconds() < MIN_RETRY_AGE_SECONDS:
                continue  # created this pass — retry on a later pass (§29)
            cfg = cfg or config.load_config()
            _attempt_upload(record, cfg, transport, final=True)
            attempted += 1
        except Exception:  # noqa: BLE001 - one bad record must not stop the sweep
            continue
    return attempted
