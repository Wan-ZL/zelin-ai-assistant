"""auto_merge — the daemon-pass near-duplicate scan (§38.3 rule → §44 silent check).

Layer 3 of 少建卡: when a NEW open card looks like a near-duplicate of another
open card (act/lib/near_dupe.is_near_dupe — high normalized-token overlap, or
a shared non-owner contact plus moderate overlap, NO LLM anywhere), actd asks
for a §44 silent two-card check (act/lib/silent_merge.request): a detached
judge whose outcome is silent either way — same-thing → reversible fold +
trash, different/unsure → nothing. Nobody is asked; no suggestion card ever
reaches the board.

Throttles (hard rules, in this order):

- **one check per unordered card pair EVER** — checked pairs persist in
  ``state/auto_merge_seen.json`` (SM- job files are TTL-purged after 24h, so
  the pair ledger cannot be derived from them); a judge verdict is therefore
  final for the pair;
- **max 3 outstanding checks** at any time (outstanding = pending silent
  checks — concurrent LLM subprocesses are the budgeted resource);
- **never across terminal/sealed cards** — only OPEN cards
  (detected/raising/card_sent/approved/executing/review) are compared, and
  cards already linked by lineage (improvement_of / same thread) are skipped:
  a follow-up child is deliberately related, not an accidental duplicate.

Scanning is incremental: only cards not yet in the ``scanned`` ledger are
compared (against every open card), so the steady-state actd pass costs one
set diff. Everything here is best-effort and never raises into the daemon.

The rule itself (``is_near_dupe`` / ``linked`` / ``OPEN_STATES`` and the
thresholds) lives in act/lib/near_dupe.py so silent_merge can share it
without importing this module back (P3a cycle break); the names are
re-exported here unchanged for callers and tests.
"""
from __future__ import annotations

import json
from typing import Optional

from act.lib import analytics, config, near_dupe, registry
from act.lib.near_dupe import (  # noqa: F401 - re-exported API (callers + tests)
    CONTACT_MIN_TOKENS,
    CONTACT_SCORE,
    HIGH_MIN_TOKENS,
    HIGH_SCORE,
    OPEN_STATES,
    is_near_dupe,
)

STATE_PATH = config.STATE_DIR / "auto_merge_seen.json"

MAX_OUTSTANDING = 3

_linked = near_dupe.linked
_contacts = near_dupe.contacts


def _load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(state: dict) -> None:
    try:
        config.ensure_state_dirs()
        tmp = STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(STATE_PATH)
    except OSError:
        pass


def pair_key(a: str, b: str) -> str:
    """Unordered pair key — stable regardless of which card came second."""
    return "|".join(sorted((str(a), str(b))))


def _outstanding_auto() -> int:
    """§44: outstanding = pending silent checks (concurrent LLM subprocesses
    are the budgeted resource now — no suggestion cards sit on a board)."""
    try:
        from act.lib import silent_merge
        return silent_merge.pending_count()
    except Exception:  # noqa: BLE001 - can't create checks anyway
        return MAX_OUTSTANDING


def _idnum(rid: str) -> tuple:
    r"""「哪张更老」的序键（§60 跨命名空间：legacy R 主键 < P 主键，同空间按
    数值）。曾是 ``^R-(\d+)`` 取数——P 卡会解析成 0、永远「更老」，合并方向
    反转（新 P 卡成主卡、存量 R 卡被折进去）。"""
    return registry.id_sort_key(rid)


def record_pair_final(a_id: str, b_id: str) -> None:
    """§44.2: a pair already judged (separate) at triage time enters the
    ledger so the daemon scan never re-judges it. Best-effort, never raises
    (a miss costs one duplicate LLM check, not data)."""
    try:
        state = _load_state()
        suggested = {str(x) for x in state.get("suggested") or []}
        suggested.add(pair_key(a_id, b_id))
        state["suggested"] = sorted(suggested)
        _save_state(state)
    except Exception:  # noqa: BLE001
        pass


def _request_silent_check(primary, secondary) -> Optional[str]:
    """§44: rule hit → detached two-card LLM check (act/lib/silent_merge).

    Replaces §38.3 step 2 (the human-confirm MS- suggestion card). The
    check's outcome is silent either way: same-thing → reversible fold +
    trash, different/unsure → nothing. Nobody is asked."""
    try:
        from act.lib import silent_merge
        return silent_merge.request(str(primary.id), str(secondary.id))
    except Exception:  # noqa: BLE001 - never raise into the daemon pass
        return None


def _orient_pair(new, other) -> Optional[tuple]:
    """(primary, secondary) for a rule hit, or None when the pair must be left
    alone (both invested). primary = the older card (smaller id) — the
    existing one the duplicate should fold into; §44: the folded-away
    secondary must be a LIGHT card (nothing invested), so the invested side
    is kept whichever is older."""
    from act.lib import silent_merge as _sm
    a, b = ((other, new) if _idnum(other.id) <= _idnum(new.id) else (new, other))
    if b.status in _sm.LIGHT_STATES:
        return a, b
    if a.status in _sm.LIGHT_STATES:
        return b, a
    return None


class _Scan:
    """One pass's mutable bookkeeping (the ledger sets + the check budget)."""

    def __init__(self, scanned: set, suggested: set, budget: int, cfg):
        self.scanned = scanned
        self.suggested = suggested
        self.budget = budget
        self.cfg = cfg
        self.created = 0
        self.deferred: set = set()

    def request(self, key: str, a, b) -> None:
        """Fire the silent check for an oriented pair; ledger it on success."""
        sid = _request_silent_check(a, b)
        if sid is None:
            return
        self.suggested.add(key)
        self.budget -= 1
        self.created += 1
        analytics.log_event("silent_merge_requested", job=sid,
                            primary=str(a.id), secondary=str(b.id))

    def _blocked(self, new, other, key: str) -> bool:
        """Pairs that are never judged: the card against itself, pairs already
        in the ledger, lineage/thread-linked cards."""
        return (str(other.id) == str(new.id) or key in self.suggested
                or _linked(new, other))

    def judge_pair(self, new, other) -> bool:
        """Compare one (new, other) pair. Returns False when the card's scan
        must stop because the budget ran out mid-card (review blocker 5:
        the card is deferred — its remaining pairs were never judged)."""
        key = pair_key(new.id, other.id)
        if self._blocked(new, other, key):
            return True
        dupe, _matched, _reason = is_near_dupe(new, other, self.cfg)
        if not dupe:
            return True
        if self.budget <= 0:
            self.deferred.add(str(new.id))
            return False
        oriented = _orient_pair(new, other)
        if oriented is None:
            self.suggested.add(key)   # both invested → pair final, no check
            return True
        self.request(key, *oriented)
        return True

    def scan_card(self, new, open_reqs: list) -> None:
        """Compare one new card against every open card (budget permitting)."""
        if self.budget <= 0:
            # not evaluated at all this pass — stays new next pass
            self.deferred.add(str(new.id))
            return
        for other in open_reqs:
            if not self.judge_pair(new, other):
                break


def _open_cards() -> list:
    return [r for r in registry.load_all()
            if str(r.status) in OPEN_STATES and str(r.id or "").strip()]


def _run_scan(state: dict, open_reqs: list, new_reqs: list) -> _Scan:
    scan = _Scan(scanned={str(x) for x in state.get("scanned") or []},
                 suggested={str(x) for x in state.get("suggested") or []},
                 budget=MAX_OUTSTANDING - _outstanding_auto() if new_reqs else 0,
                 cfg=config.load_config())   # one scrub config for the whole pass
    for new in new_reqs:
        scan.scan_card(new, open_reqs)
    return scan


def _persist_scan(state: dict, scan: _Scan, open_ids: set, new_reqs: list) -> None:
    """Retire fully-evaluated new cards into the ``scanned`` ledger (deferred
    ones re-enter as new), drop vanished cards, keep every checked pair."""
    new_scanned = ((scan.scanned & open_ids)
                   | {str(r.id) for r in new_reqs if str(r.id) not in scan.deferred})
    if new_scanned != scan.scanned or scan.created:
        state["scanned"] = sorted(new_scanned)
        state["suggested"] = sorted(scan.suggested)
        _save_state(state)


def scan_new_cards() -> int:
    """One incremental §38/§44 pass. Returns silent checks requested.

    Never raises (actd calls it every pass); all ledger writes are atomic and
    best-effort. Cards that vanished from the open set are dropped from the
    ``scanned`` ledger so a later re-raise re-enters as new — the pair ledger
    still blocks every already-checked pair (a check is one-shot per pair
    EVER, whatever its outcome: merged, judged-separate, or judge failure).

    Budget deferral (review blocker 5): a card whose comparisons were cut
    short by the max-outstanding budget is NOT marked scanned — it re-enters
    as new on the next pass, so its pairs genuinely stay eligible until the
    checks drain; only fully-evaluated cards retire into the ledger. (Cheap:
    once the budget is gone the remaining new cards defer WITHOUT comparing.)
    """
    try:
        state = _load_state()
        scanned = {str(x) for x in state.get("scanned") or []}
        open_reqs = _open_cards()
        open_ids = {str(r.id) for r in open_reqs}
        new_reqs = [r for r in open_reqs if str(r.id) not in scanned]
        scan = _run_scan(state, open_reqs, new_reqs)
        _persist_scan(state, scan, open_ids, new_reqs)
        return scan.created
    except Exception:  # noqa: BLE001 - must never break the daemon pass
        return 0
