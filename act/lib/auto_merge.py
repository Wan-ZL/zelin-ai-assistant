"""auto_merge — deterministic near-duplicate merge suggestions (§38).

Layer 3 of 少建卡: when a NEW open card looks like a near-duplicate of another
open card (high normalized-token overlap, or a shared non-owner contact plus
moderate overlap — act/lib/match_corpus.score_pair, NO LLM anywhere), actd
auto-creates a §21 merge suggestion: a ``state/merge/MS-*.json`` job written
directly as ``status="done"``, ``verdict="merge"`` with the older card as
primary, ``confidence="deterministic"`` and an ``auto: true`` marker. It
surfaces on the board exactly like an AI suggestion — 采纳 runs the existing
deterministic ``merge_apply`` path unchanged, 取消 is the existing
``merge_dismiss``.

Throttles (hard rules, in this order):

- **one suggestion per unordered card pair EVER** — created pairs persist in
  ``state/auto_merge_seen.json`` (MS- job files are TTL-purged after 24h, so
  the pair ledger cannot be derived from them); a dismissal is therefore
  final for the pair;
- **max 3 outstanding auto suggestions** at any time (outstanding = an
  ``auto`` job still in ``done``, i.e. still on the board);
- **never across terminal/sealed cards** — only OPEN cards
  (detected/raising/card_sent/approved/executing/review) are compared, and
  cards already linked by lineage (improvement_of / same thread) are skipped:
  a follow-up child is deliberately related, not an accidental duplicate.

Scanning is incremental: only cards not yet in the ``scanned`` ledger are
compared (against every open card), so the steady-state actd pass costs one
set diff. Everything here is best-effort and never raises into the daemon.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from act.lib import analytics, config, match_corpus, registry

STATE_PATH = config.STATE_DIR / "auto_merge_seen.json"

# Open (non-terminal) card states — the only ones auto suggestions may touch.
# Local copy of actd._OPEN_STATES (importing actd here would be a cycle).
OPEN_STATES = (
    registry.State.DETECTED.value, registry.State.RAISING.value,
    registry.State.CARD_SENT.value, registry.State.APPROVED.value,
    registry.State.EXECUTING.value, registry.State.REVIEW.value,
)

MAX_OUTSTANDING = 3

# Strong signal: high overlap on its own, or a shared external contact plus
# moderate overlap. Scores per match_corpus.score_pair; the token MINIMUMS
# count only strong evidence (match_corpus.strong_evidence — 2-char CJK grams
# score but never count, review blocker 2). Pinned by tests/test_auto_merge.py.
HIGH_SCORE = 0.6
HIGH_MIN_TOKENS = 3
CONTACT_SCORE = 0.4
CONTACT_MIN_TOKENS = 2

# source "who" values that identify nobody: the owner is on every quick
# capture, and empties carry no signal.
_GENERIC_WHO = frozenset({"", "zelin"})

_ID_NUM_RE = re.compile(r"^R-(\d+)")


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


def _contacts(req) -> set:
    out = set()
    for s in (getattr(req, "sources", None) or []):
        if isinstance(s, dict):
            who = str(s.get("who") or "").strip().lower()
            if who not in _GENERIC_WHO:
                out.add(who)
    return out


def _linked(a, b) -> bool:
    """Deliberately-related cards (lineage/thread/split) are never duplicate
    noise. Split lineage is CRITICAL: a just-split card's text ≈ its origin
    note by construction — suggesting the merge back would undo the undo
    (review blocker 7)."""
    if a.improvement_of == b.id or b.improvement_of == a.id:
        return True
    if (getattr(a, "split_from", None) == b.id
            or getattr(b, "split_from", None) == a.id):
        return True
    ta, tb = getattr(a, "thread_id", None), getattr(b, "thread_id", None)
    if ta and tb and ta == tb:
        return True
    tka, tkb = getattr(a, "thread_key", None), getattr(b, "thread_key", None)
    if tka and tkb and tka == tkb:
        return True
    return False


def is_near_dupe(a, b, cfg=None) -> tuple[bool, list[str], str]:
    """The §38 deterministic near-dupe signal for two open cards.

    Returns ``(dupe, matched_tokens, reason)`` with reason ∈ ("high",
    "contact", "") — the suggestion's rationale must say WHICH rule fired
    (中等重合+同一联系人 is not 高度相似)."""
    score, matched = match_corpus.score_pair(
        match_corpus.corpus_tokens(a, cfg), match_corpus.corpus_tokens(b, cfg))
    strong = match_corpus.strong_evidence(matched)
    if score >= HIGH_SCORE and len(strong) >= HIGH_MIN_TOKENS:
        return True, matched, "high"
    if (score >= CONTACT_SCORE and len(strong) >= CONTACT_MIN_TOKENS
            and _contacts(a) & _contacts(b)):
        return True, matched, "contact"
    return False, [], ""


def _outstanding_auto() -> int:
    """§44: outstanding = pending silent checks (concurrent LLM subprocesses
    are the budgeted resource now — no suggestion cards sit on a board)."""
    try:
        from act.lib import silent_merge
        return silent_merge.pending_count()
    except Exception:  # noqa: BLE001 - can't create checks anyway
        return MAX_OUTSTANDING


def _idnum(rid: str) -> int:
    m = _ID_NUM_RE.match(str(rid or ""))
    return int(m.group(1)) if m else 0


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
        cfg = config.load_config()   # one scrub config for the whole pass
        state = _load_state()
        scanned = {str(x) for x in state.get("scanned") or []}
        suggested = {str(x) for x in state.get("suggested") or []}

        open_reqs = [r for r in registry.load_all()
                     if str(r.status) in OPEN_STATES and str(r.id or "").strip()]
        open_ids = {str(r.id) for r in open_reqs}
        new_reqs = [r for r in open_reqs if str(r.id) not in scanned]

        created = 0
        deferred: set[str] = set()
        if new_reqs:
            budget = MAX_OUTSTANDING - _outstanding_auto()
            for new in new_reqs:
                if budget <= 0:
                    # not evaluated at all this pass — stays new next pass
                    deferred.add(str(new.id))
                    continue
                for other in open_reqs:
                    if str(other.id) == str(new.id):
                        continue
                    key = pair_key(new.id, other.id)
                    if key in suggested or _linked(new, other):
                        continue
                    dupe, matched, reason = is_near_dupe(new, other, cfg)
                    if not dupe:
                        continue
                    if budget <= 0:
                        # mid-card exhaustion: this card's remaining pairs
                        # were never judged — defer the whole card (already-
                        # suggested pairs are ledger-blocked on re-scan).
                        deferred.add(str(new.id))
                        break
                    # primary = the older card (smaller id): the existing one
                    # the duplicate should fold into.
                    a, b = ((other, new)
                            if _idnum(other.id) <= _idnum(new.id) else (new, other))
                    # §44: the folded-away secondary must be a LIGHT card
                    # (nothing invested). Prefer keeping the invested side;
                    # both invested → leave both alone, pair final.
                    from act.lib import silent_merge as _sm
                    if b.status not in _sm.LIGHT_STATES:
                        if a.status in _sm.LIGHT_STATES:
                            a, b = b, a
                        else:
                            suggested.add(key)
                            continue
                    sid = _request_silent_check(a, b)
                    if sid is None:
                        continue
                    suggested.add(key)
                    budget -= 1
                    created += 1
                    analytics.log_event("silent_merge_requested", job=sid,
                                        primary=str(a.id), secondary=str(b.id))

        new_scanned = ((scanned & open_ids)
                       | {str(r.id) for r in new_reqs
                          if str(r.id) not in deferred})
        if new_scanned != scanned or created:
            state["scanned"] = sorted(new_scanned)
            state["suggested"] = sorted(suggested)
            _save_state(state)
        return created
    except Exception:  # noqa: BLE001 - must never break the daemon pass
        return 0
