"""near_dupe — the deterministic near-duplicate rule for two open cards (§38.3).

Pure functions over Requirement objects, NO LLM anywhere: the rule
(``is_near_dupe`` — high normalized-token overlap, or a shared non-owner
contact plus moderate overlap, scored by act/lib/match_corpus), the lineage
guard (``linked`` — improvement_of / split_from / thread_id / thread_key pairs
are deliberately related, never duplicate noise) and the OPEN state set the
rule may look at. Two consumers sit above this module: act/lib/auto_merge.py
(the daemon pass — every new open card vs every open card, §44 silent check
on a hit) and act/lib/silent_merge.find_fold_target (the §44.2 triage-time
pre-filing check). Both used to import each other for it, which closed the
auto_merge ↔ silent_merge import cycle; the rule now lives one layer down
(防腐 #2). auto_merge re-exports every name so its callers and tests are
untouched.

Thresholds (pinned by tests/test_auto_merge.py + tests/test_auto_merge_scan_edges.py):
the token MINIMUMS count only strong evidence (match_corpus.strong_evidence —
2-char CJK grams score but never count, review blocker 2), and one shared
identifier must never carry a merge on its own (review blocker 6).
"""
from __future__ import annotations

from act.lib import match_corpus, registry

# Open (non-terminal) card states — the only ones the rule may touch.
# Local copy of actd._OPEN_STATES (importing actd here would be a cycle).
OPEN_STATES = (
    registry.State.DETECTED.value, registry.State.RAISING.value,
    registry.State.CARD_SENT.value, registry.State.APPROVED.value,
    registry.State.EXECUTING.value, registry.State.REVIEW.value,
)

# Strong signal: high overlap on its own, or a shared external contact plus
# moderate overlap. Scores per match_corpus.score_pair.
HIGH_SCORE = 0.6
HIGH_MIN_TOKENS = 3
CONTACT_SCORE = 0.4
CONTACT_MIN_TOKENS = 2

# source "who" values that identify nobody: the owner is on every quick
# capture, and empties carry no signal.
_GENERIC_WHO = frozenset({"", "zelin"})


def _who(source) -> str:
    """Normalized ``who`` of one source entry; ``""`` for anything nameless."""
    return str(source.get("who") or "").strip().lower() if isinstance(source, dict) else ""


def contacts(req) -> set:
    """Lower-cased non-owner ``who`` values across a card's sources."""
    return {_who(s) for s in (getattr(req, "sources", None) or [])} - _GENERIC_WHO


def _same_lineage(a, b) -> bool:
    """improvement_of / split_from in either direction."""
    return (a.improvement_of == b.id or b.improvement_of == a.id
            or getattr(a, "split_from", None) == b.id
            or getattr(b, "split_from", None) == a.id)


def _same_key(x, y) -> bool:
    """Both present and equal (an absent key on either side never links)."""
    return bool(x and y and x == y)


def _same_thread(a, b) -> bool:
    """A shared thread anchor (thread_id) or deterministic bucket (thread_key)."""
    return (_same_key(getattr(a, "thread_id", None), getattr(b, "thread_id", None))
            or _same_key(getattr(a, "thread_key", None), getattr(b, "thread_key", None)))


def linked(a, b) -> bool:
    """Deliberately-related cards (lineage/thread/split) are never duplicate
    noise. Split lineage is CRITICAL: a just-split card's text ≈ its origin
    note by construction — suggesting the merge back would undo the undo
    (review blocker 7)."""
    if _same_lineage(a, b):
        return True
    return _same_thread(a, b)


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
    if _contact_rule(a, b, score, strong):
        return True, matched, "contact"
    return False, [], ""


def _contact_rule(a, b, score: float, strong: list) -> bool:
    """Moderate overlap + a shared non-owner contact."""
    return bool(score >= CONTACT_SCORE and len(strong) >= CONTACT_MIN_TOKENS
                and contacts(a) & contacts(b))
