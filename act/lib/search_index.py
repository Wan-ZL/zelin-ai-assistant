"""search_index — Mac-local session-content search layer (CONTRACT §37).

``state/search_index.json`` maps ``{card_id: {"updated_at": ISO, "text": str}}``
where ``text`` is the tail-capped main-thread user+assistant plain text of the
card's session transcript (``executor.transcript_plain_text``). The Mac app
lazy-loads it as the LAST board-search match layer (hits get a 「命中会话」
badge); it is deliberately Mac-local and NEVER part of dashboard.json — the
E2E board payload must not grow by megabytes of transcripts.

Update discipline: actd refreshes an entry ONLY at the existing settle/harvest/
reconcile touchpoints (zero new LLM calls, zero new transcript scans in the hot
loop) and prunes IRREVERSIBLY-gone cards once per pass (recoverable trashed/
archived entries survive — see :func:`prune`). Everything here is
best-effort and never raises into the daemon; a missing/corrupt file simply
means the layer is absent (board search still works on the projected fields).
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Optional

from act.lib import config

INDEX_PATH = config.STATE_DIR / "search_index.json"

# per-card transcript tail cap (~50KB) — keeps the whole index small enough
# for the app to reload on mtime change without noticing.
TEXT_CAP = 50_000


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_index() -> dict:
    """The index dict; {} when missing/corrupt (the layer is simply absent)."""
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    """Atomic write (.tmp then rename) — same convention as dashboard.json."""
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_suffix(INDEX_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(INDEX_PATH)


def update_card(card_id: str, session_id: Optional[str]) -> bool:
    """Refresh one card's entry from its session transcript. Returns True when
    the index changed. Never raises; no transcript/empty text keeps whatever
    entry already exists (an earlier round's text is better than nothing)."""
    try:
        cid = str(card_id or "").strip()
        if not cid or not session_id:
            return False
        from act import executor  # lazy: keep the module import-light
        text = executor.transcript_plain_text(str(session_id), cap=TEXT_CAP)
        if not text:
            return False
        data = load_index()
        prev = data.get(cid)
        if isinstance(prev, dict) and prev.get("text") == text:
            return False
        data[cid] = {"updated_at": _iso_now(), "text": text}
        _write(data)
        return True
    except Exception:  # noqa: BLE001 - indexing must never break the pipeline
        return False


def prune() -> int:
    """Drop entries for IRREVERSIBLY-gone cards only. Returns removed count.

    Cheap when the index doesn't exist (no registry scan at all). Review fix
    (v0.37): trashed and archived cards are RECOVERABLE (restore / unarchive)
    — actd prunes every ~10s pass, so treating trashed as terminal meant one
    accidental 删除 + 恢复 permanently killed session search for that card.
    Only truly-gone shapes leave the index: merged (terminal, no undo, incl.
    legacy ``merged_into:``), legacy bare ``rejected``, and cards absent from
    the registry entirely (hard-purged). Never raises into the daemon pass.
    """
    try:
        if not INDEX_PATH.exists():
            return 0
        data = load_index()
        if not data:
            return 0
        from act.lib import registry  # lazy import, same reason as above
        live: set = set()
        for r in registry.load_all(include_archived=True):
            if r.is_merged or str(r.status) in (
                    registry.State.REJECTED.value,
                    registry.State.MERGED.value):
                continue
            live.add(str(r.id))
        stale = [k for k in data if k not in live]
        if not stale:
            return 0
        for k in stale:
            data.pop(k, None)
        _write(data)
        return len(stale)
    except Exception:  # noqa: BLE001 - housekeeping must never break the pass
        return 0
