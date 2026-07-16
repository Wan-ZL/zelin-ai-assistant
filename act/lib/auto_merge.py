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

try:
    from act import merge_review
except Exception:  # pragma: no cover - mirrors actd's guarded import
    merge_review = None  # type: ignore

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
# moderate overlap. Tokens counted per match_corpus.score_pair (latin runs +
# CJK bigrams); thresholds are pinned by tests/test_auto_merge.py.
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
    """Deliberately-related cards (lineage/thread) are never duplicate noise."""
    if a.improvement_of == b.id or b.improvement_of == a.id:
        return True
    ta, tb = getattr(a, "thread_id", None), getattr(b, "thread_id", None)
    if ta and tb and ta == tb:
        return True
    tka, tkb = getattr(a, "thread_key", None), getattr(b, "thread_key", None)
    if tka and tkb and tka == tkb:
        return True
    return False


def is_near_dupe(a, b) -> tuple[bool, list[str]]:
    """The §38 deterministic near-dupe signal for two open cards."""
    score, matched = match_corpus.score_pair(
        match_corpus.corpus_tokens(a), match_corpus.corpus_tokens(b))
    if score >= HIGH_SCORE and len(matched) >= HIGH_MIN_TOKENS:
        return True, matched
    if (score >= CONTACT_SCORE and len(matched) >= CONTACT_MIN_TOKENS
            and _contacts(a) & _contacts(b)):
        return True, matched
    return False, []


def _outstanding_auto() -> int:
    if merge_review is None:
        return MAX_OUTSTANDING  # can't create jobs anyway — report saturated
    n = 0
    try:
        files = list(merge_review.MERGE_DIR.glob("*.json"))
    except OSError:
        return 0
    for path in files:
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (isinstance(job, dict) and job.get("auto")
                and str(job.get("status") or "") == "done"):
            n += 1
    return n


def _idnum(rid: str) -> int:
    m = _ID_NUM_RE.match(str(rid or ""))
    return int(m.group(1)) if m else 0


def _make_suggestion(primary, secondary, matched: list) -> Optional[str]:
    """Write the §21-shaped MS- job (status=done, verdict=merge). The existing
    merge_apply / merge_dismiss / dashboard projection paths consume it with
    zero changes; ``auto`` + ``confidence="deterministic"`` mark provenance
    (the apps render an unknown confidence string as a plain badge)."""
    if merge_review is None:
        return None
    words = "、".join(matched[:6])
    job = {
        "id": merge_review.new_suggestion_id(),
        "ids": [str(primary.id), str(secondary.id)],
        "requested_at": merge_review._iso_now(),
        "status": "done",
        "verdict": "merge",
        "primary": str(primary.id),
        "rationale": (
            f"规则判定：{secondary.id} 和 {primary.id} 的标题/内容高度相似"
            f"（重合关键词：{words}），可能是同一件事建了两张卡。"
            "这是确定性规则触发的提示，不是 AI 分析。"),
        "action_plan": [
            f"接受后：副卡 {secondary.id} 并入主卡 {primary.id}——来源去重合并、"
            "提及次数累加、主卡 notes 留痕；副卡置「已合并」（终态，可见性同回收站）。",
            "主卡若在待验收，副卡成果会作为反馈注入其会话；其他状态只在 notes 留痕。",
            "不确定就点「取消」——取消后不会再对这两张卡重复提示。",
        ],
        "confidence": "deterministic",
        "auto": True,
        "expires_at": merge_review._iso_in(merge_review.TTL_HOURS),
    }
    merge_review.write_job(job)
    return str(job["id"])


def scan_new_cards() -> int:
    """One incremental §38 auto-suggestion pass. Returns suggestions created.

    Never raises (actd calls it every pass); all ledger writes are atomic and
    best-effort. Cards that vanished from the open set are dropped from the
    ``scanned`` ledger so a later re-raise re-enters as new — the pair ledger
    still blocks every already-suggested pair.
    """
    try:
        if merge_review is None:
            return 0
        state = _load_state()
        scanned = {str(x) for x in state.get("scanned") or []}
        suggested = {str(x) for x in state.get("suggested") or []}

        open_reqs = [r for r in registry.load_all()
                     if str(r.status) in OPEN_STATES and str(r.id or "").strip()]
        open_ids = {str(r.id) for r in open_reqs}
        new_reqs = [r for r in open_reqs if str(r.id) not in scanned]

        created = 0
        if new_reqs:
            budget = MAX_OUTSTANDING - _outstanding_auto()
            for new in new_reqs:
                for other in open_reqs:
                    if str(other.id) == str(new.id):
                        continue
                    key = pair_key(new.id, other.id)
                    if key in suggested or _linked(new, other):
                        continue
                    dupe, matched = is_near_dupe(new, other)
                    if not dupe:
                        continue
                    if budget <= 0:
                        # throttle: pair NOT recorded — it stays eligible once
                        # the board clears below 3 outstanding suggestions.
                        continue
                    # primary = the older card (smaller id): the existing one
                    # the duplicate should fold into.
                    a, b = ((other, new)
                            if _idnum(other.id) <= _idnum(new.id) else (new, other))
                    sid = _make_suggestion(a, b, matched)
                    if sid is None:
                        continue
                    suggested.add(key)
                    budget -= 1
                    created += 1
                    analytics.log_event("auto_merge_suggested", suggestion=sid,
                                        primary=str(a.id), secondary=str(b.id))

        new_scanned = (scanned & open_ids) | {str(r.id) for r in new_reqs}
        if new_scanned != scanned or created:
            state["scanned"] = sorted(new_scanned)
            state["suggested"] = sorted(suggested)
            _save_state(state)
        return created
    except Exception:  # noqa: BLE001 - must never break the daemon pass
        return 0
