"""Requirement registry — the source of truth (YAML under act/registry/).

State machine (CONTRACT §1):
    detected -> card_sent -> approved -> executing -> review -> delivered
    branches: rejected  /  merged_into:<parent-id>
    terminal (merge-review 契约 四): merged + merged_into=<primary>

Files may be either a single YAML doc (one requirement) or a YAML list (e.g.
the debt batch R-002..R-006). Both shapes round-trip through ``save``.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Optional, Union

import yaml

from act.lib import config


class State(str, Enum):
    """Canonical linear states. Legacy ``merged_into:<id>`` is stored verbatim
    as the status string (see :meth:`Requirement.is_merged`); the merge-review
    flow (契约 四) instead uses the ``merged`` terminal state below plus the
    ``merged_into`` field."""

    DETECTED = "detected"
    CARD_SENT = "card_sent"
    RAISING = "raising"    # debt -> (AI expanding) -> card_sent
    APPROVED = "approved"
    EXECUTING = "executing"
    REVIEW = "review"
    DELIVERED = "delivered"
    REJECTED = "rejected"
    TRASHED = "trashed"
    # merge-review 终态（契约 四）：副卡并入主卡。可见性语义同回收站（不进任何
    # 看板列、purge 不删），但 merge_or_new 匹配语义同 delivered —— 参与匹配
    # 以压住后续重述（这点与 trashed 相反，决策 6）。
    MERGED = "merged"

    def __str__(self) -> str:  # so f-strings emit the bare value
        return self.value


MERGED_PREFIX = "merged_into:"

# Core fields always serialized (in this order); optional fields appended when set.
_CORE_ORDER = [
    "id",
    "title",
    "type",
    "tier",
    "status",
    "hardness",
    "deadline",
    "repeated_mentions",
    "green_sign_required",
    "disagreement",
    "cost_estimate_usd",
    "sources",
    "plan",
]
# Optional fields serialized only when set (keeps the YAML files clean).
# ``summary`` is placed first so it reads right below the core block.
_OPTIONAL_ORDER = [
    "summary",
    "definition_of_done",
    "outputs",
    "card",
    "execution",
    "improvement_of",
    "merged_into",
    "target_repo",
    "target_kind",
    "delivery_mode",
    "notes",
    # trash / recycle-bin bookkeeping (§9) — only present once trashed/restored
    "trashed_at",
    "prev_status",
    "trash_reason",
    "permanent",
]


@dataclass
class Requirement:
    id: str
    title: str = ""
    type: str = ""
    tier: str = "T1"
    status: str = "detected"
    hardness: str = "soft"
    deadline: Optional[str] = None
    repeated_mentions: int = 1
    green_sign_required: bool = False
    disagreement: Optional[str] = None
    cost_estimate_usd: Optional[float] = None
    sources: list = field(default_factory=list)
    plan: Union[str, list, None] = None
    summary: str = ""  # plain-language one-liner (§7); shown by default in the card
    definition_of_done: Optional[list] = None  # §11 验收标准 — approved WITH the card
    outputs: Optional[list] = None
    card: Optional[dict] = None
    execution: Optional[dict] = None
    improvement_of: Optional[str] = None
    merged_into: Optional[str] = None
    target_repo: Optional[str] = None  # executor override (not in CONTRACT core)
    target_kind: Optional[str] = None  # "new" | "existing" (§7); computed if unset
    delivery_mode: str = "repo"  # "chat"=会话内交付成稿 | "repo"=分支交付 (§20; 缺失视为 repo)
    notes: str = ""

    # trash / recycle-bin bookkeeping (§9) — only populated once trashed
    trashed_at: Optional[str] = None
    prev_status: Optional[str] = None
    trash_reason: Optional[str] = None
    permanent: bool = False

    # internal bookkeeping (never serialized)
    _file: Optional[str] = field(default=None, repr=False, compare=False)
    _in_list: bool = field(default=False, repr=False, compare=False)

    # -- construction ------------------------------------------------------- #
    @classmethod
    def from_dict(cls, d: dict) -> "Requirement":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__ if not f.startswith("_")}
        kwargs = {k: v for k, v in d.items() if k in known}
        # accept `repo` as an alias for target_repo
        if "target_repo" not in kwargs and "repo" in d:
            kwargs["target_repo"] = d["repo"]
        # delivery_mode tolerance (§20): missing / unknown values -> "repo"
        dm = str(kwargs.get("delivery_mode") or "").strip().lower()
        kwargs["delivery_mode"] = dm if dm in ("chat", "repo") else "repo"
        if "id" not in kwargs:
            kwargs["id"] = d.get("id", "")
        return cls(**kwargs)

    def to_dict(self) -> dict:
        out: dict = {}
        for k in _CORE_ORDER:
            out[k] = getattr(self, k)
        for k in _OPTIONAL_ORDER:
            v = getattr(self, k)
            # skip unset optionals (incl. permanent=False) so files stay clean
            if v in (None, "", [], False):
                continue
            # delivery_mode: "repo" is the default (missing == repo, §20), so only
            # the non-default "chat" is serialized — round-trips without loss.
            if k == "delivery_mode" and v == "repo":
                continue
            out[k] = v
        return out

    # -- status helpers ----------------------------------------------------- #
    @property
    def is_merged(self) -> bool:
        return isinstance(self.status, str) and self.status.startswith(MERGED_PREFIX)

    @property
    def merged_parent(self) -> Optional[str]:
        if self.is_merged:
            return self.status[len(MERGED_PREFIX):]
        return self.merged_into

    def set_status(self, status: Union[State, str]) -> None:
        self.status = str(status)


# --------------------------------------------------------------------------- #
# Load
# --------------------------------------------------------------------------- #
def _iter_files() -> Iterable[Path]:
    if not config.REGISTRY_DIR.exists():
        return []
    return sorted(config.REGISTRY_DIR.glob("*.yaml"))


def load_all() -> list[Requirement]:
    """Load every requirement across single-doc and list files."""
    reqs: list[Requirement] = []
    for path in _iter_files():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if data is None:
            continue
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                r = Requirement.from_dict(item)
                r._file = str(path)
                r._in_list = True
                reqs.append(r)
        elif isinstance(data, dict):
            r = Requirement.from_dict(data)
            r._file = str(path)
            r._in_list = False
            reqs.append(r)
    return reqs


def load(req_id: str) -> Optional[Requirement]:
    for r in load_all():
        if r.id == req_id:
            return r
    return None


# --------------------------------------------------------------------------- #
# Save
# --------------------------------------------------------------------------- #
def _dump_yaml(obj: Any) -> str:
    return yaml.safe_dump(obj, allow_unicode=True, sort_keys=False, width=100)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def save(req: Requirement) -> None:
    """Persist a requirement, preserving whether it lives in a list file."""
    if req._file and req._in_list:
        path = Path(req._file)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        except (OSError, yaml.YAMLError):
            data = []
        if not isinstance(data, list):
            data = [data]
        out: list = []
        replaced = False
        for item in data:
            if isinstance(item, dict) and item.get("id") == req.id:
                out.append(req.to_dict())
                replaced = True
            else:
                out.append(item)
        if not replaced:
            out.append(req.to_dict())
        _atomic_write(path, _dump_yaml(out))
    else:
        path = Path(req._file) if req._file else config.REGISTRY_DIR / f"{req.id}.yaml"
        req._file = str(path)
        req._in_list = False
        _atomic_write(path, _dump_yaml(req.to_dict()))


def upsert(req: Requirement) -> Requirement:
    """Insert or update by id. Inherits the on-disk location of an existing id."""
    if not req._file:
        existing = load(req.id)
        if existing is not None:
            req._file = existing._file
            req._in_list = existing._in_list
    save(req)
    return req


# --------------------------------------------------------------------------- #
# Trash / recycle bin (CONTRACT §9)
# --------------------------------------------------------------------------- #
def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def trash(req: Requirement, reason: str) -> Requirement:
    """Move a requirement to the recycle bin (recoverable via :func:`restore`).

    ``reason`` is "rejected" (an approval card the user declined) or "deleted"
    (a debt item the user dropped). The prior status is stashed in
    ``prev_status`` so ``restore`` can put it back exactly where it was.
    """
    if req.status != State.TRASHED.value:
        req.prev_status = req.status
    req.set_status(State.TRASHED)
    req.trashed_at = _iso_now()
    req.trash_reason = reason
    save(req)
    return req


def restore(req: Requirement) -> Requirement:
    """Restore a trashed requirement to its ``prev_status`` and clear trash fields."""
    req.set_status(req.prev_status or State.DETECTED.value)
    req.prev_status = None
    req.trashed_at = None
    req.trash_reason = None
    save(req)
    return req


def pin(req: Requirement) -> Requirement:
    """Mark a trashed item permanent so the retention pass never hard-deletes it."""
    req.permanent = True
    save(req)
    return req


def delete(req: Requirement) -> bool:
    """Hard-delete a requirement (retention purge, §9).

    Single-doc file  -> remove the file.
    List-file member -> drop just this entry; remove the file if it becomes empty.
    Returns True if something was removed.
    """
    if not req._file:
        existing = load(req.id)
        if existing is None or not existing._file:
            return False
        req._file = existing._file
        req._in_list = existing._in_list
    path = Path(req._file)
    if req._in_list:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        except (OSError, yaml.YAMLError):
            return False
        if not isinstance(data, list):
            data = [data]
        remaining = [
            it for it in data
            if not (isinstance(it, dict) and it.get("id") == req.id)
        ]
        if len(remaining) == len(data):
            return False
        if remaining:
            _atomic_write(path, _dump_yaml(remaining))
        else:
            try:
                path.unlink()
            except OSError:
                return False
        return True
    try:
        path.unlink()
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# ID allocation + matching / merge
# --------------------------------------------------------------------------- #
_ID_RE = re.compile(r"^R-(\d+)$")

# "Resolved" = the work behind the card already closed (delivered, or merged
# into a primary — incl. the legacy ``merged_into:<id>`` status). A radar hit
# that relates to a resolved card must NOT be filed as an isolated new card:
# it becomes a follow-up with ``improvement_of`` lineage (统一口径, v0.17).
RESOLVED_STATES = (State.DELIVERED.value, State.MERGED.value)


def is_resolved(req: Requirement) -> bool:
    """Delivered / merged (incl. legacy ``merged_into:<id>``) — work closed."""
    return req.is_merged or req.status in RESOLVED_STATES


def _is_merged_out(req: Requirement) -> bool:
    """Merged into a primary — either the ``merged`` terminal state (契约 四)
    or the legacy ``merged_into:<id>`` status."""
    return req.is_merged or req.status == State.MERGED.value


def _canonical_id(rid: str, by_id: dict) -> str:
    """Follow merge lineage ids to the primary card's id (cycle-safe)."""
    seen: set = set()
    while rid and rid not in seen:
        seen.add(rid)
        r = by_id.get(rid)
        if r is None or not _is_merged_out(r):
            break
        nxt = r.merged_parent
        if not nxt:
            break
        rid = nxt
    return rid


def canonical(req: Requirement) -> Requirement:
    """The primary card of ``req``'s merge cluster (``req`` itself when it is
    not merged, or when the chain dead-ends on a missing id).

    A merged duplicate and its primary are BOTH visible to the triage LLM
    (registry inventory keeps merged entries so restatements can be related),
    so two radar hits on the same event may point at different lineage nodes.
    Canonicalizing before any fold/follow-up keeps the whole cluster on ONE
    node — otherwise the same event grows parallel follow-ups (R-028/R-029-类
    near-duplicates all over again).
    """
    if not _is_merged_out(req):
        return req
    by_id = {r.id: r for r in load_all()}
    by_id.setdefault(req.id, req)
    return by_id.get(_canonical_id(req.id, by_id), req)


def find_open_follow_up(parent_id: str) -> Optional[Requirement]:
    """The unresolved follow-up already hanging off ``parent_id``'s merge
    cluster, if any.

    This IS the cross-pass / cross-source dedup window: as long as one
    follow-up of a parent is still open (not delivered/merged/rejected/
    trashed), every later radar hit that relates to the same parent folds
    into it (note + source) instead of filing a second card. The window
    closes itself the moment the follow-up resolves — a NEW later mention
    then legitimately opens a fresh follow-up.

    Matching is merge-cluster-wide on both sides: ``parent_id`` and each
    follow-up's ``improvement_of`` are canonicalized (merged duplicates hop
    to their primary), so a follow-up filed against a merged duplicate still
    dedupes a later hit on the primary (and vice versa).
    """
    if not parent_id:
        return None
    reqs = load_all()
    by_id = {r.id: r for r in reqs}
    target = _canonical_id(parent_id, by_id)
    for r in reqs:
        if not r.improvement_of:
            continue
        if is_resolved(r) or r.status in (State.REJECTED.value, State.TRASHED.value):
            continue
        if _canonical_id(r.improvement_of, by_id) == target:
            return r
    return None


def next_id() -> str:
    mx = 0
    for r in load_all():
        m = _ID_RE.match(r.id or "")
        if m:
            mx = max(mx, int(m.group(1)))
    return f"R-{mx + 1:03d}"


def _norm_title(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip().lower()


def _same_source_and_title(a: Requirement, b: Requirement) -> bool:
    """Restatement heuristic: same requirement (near-identical title).

    Matching is title-based on purpose: the same ask restated in a *different*
    channel (meeting -> slack -> confluence) is exactly the multi-source case
    that should merge and bump ``repeated_mentions`` (see R-001's 3 sources).
    Source de-duplication happens separately in :func:`_dedupe_sources`.
    """
    ta, tb = _norm_title(a.title), _norm_title(b.title)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    # containment match, guarded against short/ambiguous titles
    if (ta in tb or tb in ta) and min(len(ta), len(tb)) >= 12:
        return True
    return False


def _dedupe_sources(existing: list, incoming: list) -> tuple[list, int]:
    """Append incoming sources not already present. Returns (merged, added_count)."""
    def key(s: dict) -> tuple:
        return (
            (s.get("channel") or "").lower(),
            str(s.get("date") or ""),
            (s.get("ref") or s.get("quote") or "").strip().lower(),
        )

    seen = {key(s) for s in existing if isinstance(s, dict)}
    merged = list(existing)
    added = 0
    for s in incoming or []:
        if not isinstance(s, dict):
            continue
        k = key(s)
        if k not in seen:
            merged.append(s)
            seen.add(k)
            added += 1
    return merged, added


def _carries_increment(parent: Requirement, new: Requirement) -> bool:
    """Does the new mention add a real increment vs. a pure restatement?

    Increment = a new/earlier deadline, or a cost estimate the parent lacked,
    or an explicit escalation to a harder directive.
    """
    if new.deadline and (parent.deadline is None or str(new.deadline) < str(parent.deadline)):
        return True
    if new.cost_estimate_usd is not None and parent.cost_estimate_usd is None:
        return True
    if new.hardness == "hard" and parent.hardness == "soft":
        return True
    if new.improvement_of:
        return True
    return False


def merge_or_new(new_req: Union[Requirement, dict], *, high_confidence: bool = False) -> Requirement:
    """Reconcile a freshly-extracted requirement against the registry.

    - Pure restatement of an existing entry (same source+title, no increment):
      merge sources into the parent, bump ``repeated_mentions``, **status
      unchanged**. Returns the (updated) parent.
    - Carries an increment (new/earlier deadline or improvement): create an
      "improvement card" with ``improvement_of=<parent-id>`` and return it.
    - No match: create a brand-new entry (status=detected, or card_sent when
      high-confidence + a hard deadline). Returns the new entry.
    """
    if isinstance(new_req, dict):
        new_req = Requirement.from_dict(new_req)

    existing = load_all()
    parent: Optional[Requirement] = None
    for r in existing:
        # Never match: legacy merged_into:<id> statuses, rejected, trashed
        # (决策 6: 拒绝 ≠ 已办完 — a trashed ask restated must re-card).
        # DOES match: State.MERGED (merge-review 契约 四) — treated exactly like
        # DELIVERED so later restatements are silently absorbed instead of
        # producing a fresh card for work that already merged into a primary.
        if r.is_merged or r.status in (State.REJECTED.value, State.TRASHED.value):
            continue
        if _same_source_and_title(r, new_req):
            parent = r
            break

    if parent is not None:
        if _carries_increment(parent, new_req):
            child = Requirement(
                id=next_id(),
                title=new_req.title or parent.title,
                type=new_req.type or parent.type,
                tier=new_req.tier or parent.tier,
                status=State.CARD_SENT.value if high_confidence else State.DETECTED.value,
                hardness=new_req.hardness or parent.hardness,
                deadline=new_req.deadline or parent.deadline,
                repeated_mentions=1,
                cost_estimate_usd=new_req.cost_estimate_usd,
                sources=list(new_req.sources or []),
                plan=new_req.plan or parent.plan,
                improvement_of=parent.id,
                notes=new_req.notes or "",
            )
            return upsert(child)
        # pure restatement -> merge sources, bump count, keep status
        merged, added = _dedupe_sources(parent.sources or [], new_req.sources or [])
        parent.sources = merged
        if added:
            parent.repeated_mentions = int(parent.repeated_mentions or 1) + added
        return upsert(parent)

    # brand new
    new_req.id = new_req.id or next_id()
    if not new_req.status or new_req.status == State.DETECTED.value:
        if high_confidence and new_req.hardness == "hard" and new_req.deadline:
            new_req.status = State.CARD_SENT.value
        else:
            new_req.status = State.DETECTED.value
    new_req.repeated_mentions = int(new_req.repeated_mentions or 1)
    return upsert(new_req)
