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
import sys
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
    # v0.20.0 sealed-completed state (卡片生命周期 §3.1). Reached from delivered
    # (已验收) or detected (备选) via the archive action, or an auto sweep of
    # cold delivered matters. Semantics mirror trashed for VISIBILITY (in NO
    # kanban lane) but are EXCLUDED from merge_or_new matching + hidden from the
    # triage/capture LLM (same as trashed), NEVER purged, and RELOCATED to the
    # ``archive/`` subdir so the hot registry scan skips them (#10). Later
    # related info opens a NEW card rather than re-raising a sealed one.
    ARCHIVED = "archived"

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
    # v0.20.0 thread-level matching (卡片生命周期 §2) — appended so old YAML that
    # lacks them round-trips clean (to_dict skips None) and lazily backfills.
    "thread_id",
    "thread_key",
    # v0.20.0 archive bookkeeping (§4) — only present once archived; prev_status
    # (above) is reused to remember the restore target for unarchive.
    "archived_at",
    "archive_reason",
# v0.37 living display titles (§37) — the frozen `title` above stays the
    # dedupe/re-raise identity anchor; these carry the human-facing name.
    "display_title",
    "user_titled",
    "former_titles",
    # §38 split lineage — set once on a card minted by the split_note undo;
    # machine-readable so auto_merge never suggests merging the split back
    # (the [拆自 R-xxx] notes breadcrumb is prose, not a signal).
    "split_from",
    # §44 silent merge — how many duplicate cards were folded in silently
    # (distinct from repeated_mentions, which also counts restatements and
    # user-approved merges). Only present once >0.
    "silent_merge_count",
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

    # v0.20.0 thread-level matching (卡片生命周期 §2)
    # thread_id: the thread anchor = the R-id of the thread-root card (reuses the
    #   R- namespace; inherited on a match, self-rooted on a brand-new card).
    # thread_key: a STRONG deterministic bucket, only from an external thread ref
    #   ("gmail:<X-GM-THRID>" / "slack:<thread_ts>"); None when there is no strong
    #   signal — never fuzzy. See :func:`derive_thread_key`.
    thread_id: Optional[str] = None
    thread_key: Optional[str] = None
    # v0.20.0 archive bookkeeping (§4) — set once archived (prev_status reused).
    archived_at: Optional[str] = None
    archive_reason: Optional[str] = None
    # §38 split lineage (see _OPTIONAL_ORDER note) — origin card of a split.
    split_from: Optional[str] = None
    # §44 silent merge counter — fold-in events only (see _OPTIONAL_ORDER
    # note). 0 is skipped by to_dict (0 == False), so files stay clean.
    silent_merge_count: int = 0

    # v0.37 living display titles (§37). `title` above is FROZEN (identity
    # anchor for merge_or_new/_same_source_and_title/re-raise) — display_title
    # is the human-facing name shown on board rows. user_titled=True pins a
    # user-chosen name: LLM/harvest updates never overwrite it. former_titles
    # keeps the last few previous display names (searchable, so a renamed card
    # is still findable by its old name).
    display_title: Optional[str] = None
    user_titled: bool = False
    former_titles: Optional[list] = None

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
        # YAML 类型归一：手写卡把 `id: 4` / `title: 456` / `tier: 7` 写成无引号
        # 数字时 PyYAML 解析成 int —— 一律 str() 归一，否则 next_id 的正则
        # match 抛 TypeError（快速捕获整条链瘫痪），且 dashboard wire 上的 int
        # 会让 Swift 端硬 String decode 把整列清空（CONTRACT §2）。
        for k in ("id", "title", "tier"):
            v = kwargs.get(k)
            if v is not None and not isinstance(v, str):
                kwargs[k] = str(v)
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
# v0.20.0 (§4): archived cards RELOCATE to this subdir so the hot registry scan
# (non-recursive glob) skips them (#10 performance) while they stay recoverable
# and NEVER get purged. glob("*.yaml") is non-recursive, so ``archive/`` files
# are only ever loaded when a caller explicitly opts in (include_archived=True).
ARCHIVE_DIR: Path = config.REGISTRY_DIR / "archive"


def _iter_files(include_archived: bool = False) -> Iterable[Path]:
    if not config.REGISTRY_DIR.exists():
        return []
    # R-000-example.yaml ships with the repo as documentation — never load
    # it as a real card (it used to surface in the backlog lane on every
    # fresh install).
    files = [p for p in config.REGISTRY_DIR.glob("*.yaml")
             if p.name != "R-000-example.yaml"]
    if include_archived and ARCHIVE_DIR.exists():
        files += list(ARCHIVE_DIR.glob("*.yaml"))
    return sorted(files)


def load_all(include_archived: bool = False) -> list[Requirement]:
    """Load every requirement across single-doc and list files.

    ``include_archived`` pulls in the relocated ``archive/`` cards too — used by
    :func:`next_id` and :func:`load` (id-collision safety, §4) and by
    :func:`load_archived`; the dashboard + matching keep the default False so
    sealed cards stay out of the hot path and out of matching.
    """
    reqs: list[Requirement] = []
    for path in _iter_files(include_archived):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            # 单个损坏/不可读文件（语法坏 YAML、chmod 000 等）只跳过这一个 +
            # log，绝不拖垮 load_all 的所有消费者（dashboard/收件箱/雷达/capture）。
            print(f"registry: skip unreadable card file {path.name}: {e}",
                  file=sys.stderr)
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
    # CRITICAL (§4): scan the archive dir too, or an archived card is invisible
    # to load()/unarchive and — worse — next_id() would reallocate its id and
    # overwrite it (silent data loss). Both funnel through include_archived=True.
    found: Optional[Requirement] = None
    for r in load_all(include_archived=True):
        if r.id != req_id:
            continue
        # Crash-mid-move residue (§4): archive() writes archive/<id>.yaml FIRST
        # and deletes the active original SECOND, so a crash between the two
        # leaves BOTH copies on disk. The archive copy is authoritative — the
        # dashboard already hides the active twin (archived_ids dedup) — so
        # load() must agree, or actd sees the stale active status and the
        # user's unarchive click dead-ends in a silent no-op forever.
        # unarchive() then repairs the residue: save() overwrites the stale
        # active file and the archive copy is unlinked.
        if r._file and Path(r._file).parent == ARCHIVE_DIR:
            return r
        if found is None:
            found = r
    return found


def load_archived() -> list[Requirement]:
    """Every sealed (archived) card — newest handling left to the caller."""
    return [r for r in load_all(include_archived=True)
            if r.status == State.ARCHIVED.value]


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
        except (OSError, yaml.YAMLError) as e:
            # Fail-closed: this req is ONE member of a multi-card list file.
            # Treating an existing-but-unreadable file as empty would rewrite
            # it with just this card — silently destroying every sibling AND
            # the still-recoverable corrupt content. Refuse the write instead
            # (mirrors delete(), which returns False on the same failure).
            print(f"registry: refuse save into unreadable list file "
                  f"{path.name} (member {req.id}): {e}", file=sys.stderr)
            raise
        if not isinstance(data, list):
            data = [data]
        out: list = []
        replaced = False
        for item in data:
            # str() both sides: from_dict normalizes hand-written numeric ids
            # (`id: 4` -> "4") but the raw on-disk entry still holds the int —
            # an un-normalized == would append a duplicate instead of replacing.
            if (isinstance(item, dict) and item.get("id") is not None
                    and str(item.get("id")) == str(req.id)):
                out.append(req.to_dict())
                replaced = True
            else:
                out.append(item)
        if not replaced:
            out.append(req.to_dict())
        _atomic_write(path, _dump_yaml(out))
    else:
        path = Path(req._file) if req._file else config.REGISTRY_DIR / f"{req.id}.yaml"
        if req._file is None and path.exists():
            # Fail-closed: this req was NOT loaded from disk (its _file is
            # unset) yet a file for its id already exists. If that file is
            # unreadable its content was skipped by load_all()/load() — still
            # recoverable by hand — and overwriting would make the loss
            # permanent. Readable files pass through: updating an existing id
            # via a fresh object is a legitimate save.
            try:
                yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as e:
                print(f"registry: refuse to overwrite unreadable card file "
                      f"{path.name} with {req.id}: {e}", file=sys.stderr)
                raise
        req._file = str(path)
        req._in_list = False
        _atomic_write(path, _dump_yaml(req.to_dict()))
    _note_first_card(req)


def _note_first_card(req: Requirement) -> None:
    """Fire the once-per-install ``milestone_first_card`` event the first time
    ANY requirement is persisted in the 提案 (card_sent) lane. ``save()`` is the
    single choke every producer funnels through — ``analyze.py``, quick_capture
    ``apply_triage``, self-DM follow-ups, and ``merge_or_new`` (which writes
    ``card_sent`` directly, bypassing ``set_status``) — so guarding on the saved
    status here catches them all without touching the hot path in each. Lazy
    import keeps registry import-light; ``log_first`` is idempotent and never
    raises, so this is safe on the write path."""
    try:
        if str(req.status) != State.CARD_SENT.value:
            return
        from act.lib import analytics  # lazy: keep registry import-light
        analytics.log_first("milestone_first_card", req=req.id)
    except Exception:  # noqa: BLE001 - telemetry must never break a save
        pass


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


# --------------------------------------------------------------------------- #
# Display title (CONTRACT §37) — the frozen `title` never changes; this does.
# --------------------------------------------------------------------------- #
FORMER_TITLES_CAP = 3


def set_display_title(req: Requirement, title, *, by_user: bool = False) -> bool:
    """Set ``req.display_title`` (in memory only — the caller saves).

    Returns True when the requirement changed. Rules (§37):
    - fail-closed input: non-str / empty-after-collapse / no-op values change
      nothing; anything accepted is whitespace-collapsed + clipped to
      ``titles.MAX_DISPLAY_TITLE``;
    - a user-pinned title (``user_titled``) is NEVER overwritten by an LLM /
      harvest title (``by_user=False``);
    - the previous display_title is appended to ``former_titles`` (deduped,
      newest last, capped at FORMER_TITLES_CAP) so a renamed card stays
      findable under its old name.
    """
    from act.lib import titles  # lazy: keep registry import-light
    t = titles.clip_title(title)
    if t is None:
        return False
    if req.user_titled and not by_user:
        return False
    changed = False
    prev = str(req.display_title or "").strip()
    if t != prev:
        if prev:
            former = [str(x) for x in (req.former_titles or []) if str(x).strip()]
            former = [x for x in former if x != prev]
            former.append(prev)
            req.former_titles = former[-FORMER_TITLES_CAP:]
        req.display_title = t
        changed = True
    if by_user and not req.user_titled:
        req.user_titled = True
        changed = True
    return changed


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
            # str() both sides — same normalization as save(): an on-disk
            # hand-written int id must match its str-normalized in-memory twin,
            # or delete() drops the wrong row / nothing at all.
            if not (isinstance(it, dict) and it.get("id") is not None
                    and str(it.get("id")) == str(req.id))
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
# Archive / unarchive (卡片生命周期 §4) — RELOCATE model (the single impl).
# --------------------------------------------------------------------------- #
def _delete_original(orig_file: str, in_list: bool, rid: str) -> None:
    """Drop ``rid`` from its ORIGINAL location after it was relocated.

    Reuses :func:`delete`'s tested single-doc / list-member extraction by
    pointing a throwaway stub at the original file (``req._file`` has already
    been repointed at the archive path by the time we get here)."""
    stub = Requirement(id=rid)
    stub._file = orig_file
    stub._in_list = in_list
    try:
        delete(stub)
    except Exception:  # noqa: BLE001 - the relocated copy is already safe on disk
        pass


def archive(req: Requirement, reason: str) -> Requirement:
    """Seal a completed card and RELOCATE it to ``archive/`` (§4).

    ``reason`` is "user" (点归档：已验收/备选) or "auto" (archive_stale 冷扫).
    The prior status is stashed in ``prev_status`` so :func:`unarchive` restores
    it. The file is written into ``ARCHIVE_DIR`` first, then the original entry
    is removed — so a crash mid-move leaves the card recoverable, never lost."""
    if req.status != State.ARCHIVED.value:
        req.prev_status = req.status
    req.set_status(State.ARCHIVED)
    req.archived_at = _iso_now()
    req.archive_reason = reason
    orig, in_list = req._file, req._in_list
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    req._file = str(ARCHIVE_DIR / f"{req.id}.yaml")
    req._in_list = False
    save(req)
    if orig and Path(orig) != Path(req._file):
        _delete_original(orig, in_list, req.id)
    return req


def unarchive(req: Requirement) -> Requirement:
    """Restore an archived card to ``prev_status`` and move it back to the
    active registry dir (§4). Clears the archive bookkeeping."""
    req.set_status(req.prev_status or State.DELIVERED.value)
    req.prev_status = None
    req.archived_at = None
    req.archive_reason = None
    orig = req._file
    req._file = str(config.REGISTRY_DIR / f"{req.id}.yaml")
    req._in_list = False
    save(req)
    if orig and Path(orig) != Path(req._file):
        try:
            Path(orig).unlink(missing_ok=True)
        except OSError:
            pass
    return req


# --------------------------------------------------------------------------- #
# ID allocation + matching / merge
# --------------------------------------------------------------------------- #
_ID_RE = re.compile(r"^R-(\d+)$")
# Filename form of an id — prefix match so "R-042" and "R-042-notes" both
# count as allocating 42 (next_id's unreadable-file guard; conservative).
_FILE_ID_RE = re.compile(r"^R-(\d+)")

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
    # CRITICAL (§4): include archived cards, or a freshly allocated id could
    # collide with a sealed R-050 and overwrite it (silent data loss).
    for r in load_all(include_archived=True):
        # str() 防御第二层（from_dict 已归一 YAML 路径）：直接构造的
        # Requirement 仍可能带 int id —— 正则 match 对 int 抛 TypeError。
        m = _ID_RE.match(str(r.id or ""))
        if m:
            mx = max(mx, int(m.group(1)))
    # Fail-closed vs unreadable files: load_all() SKIPS a corrupt/unreadable
    # card file (hand-edit YAML typo, transient OSError), so its id would
    # otherwise be re-allocated here and the still-recoverable file overwritten
    # by the next save(). Filenames stay readable even when content isn't —
    # count R-<n>*.yaml names in both the active and archive dirs as allocated.
    # (Over-counting is harmless: worst case an id number is skipped.)
    for p in _iter_files(include_archived=True):
        m = _FILE_ID_RE.match(p.stem)
        if m:
            mx = max(mx, int(m.group(1)))
    return f"R-{mx + 1:03d}"


def derive_thread_key(source: Optional[dict]) -> Optional[str]:
    """The STRONG deterministic thread bucket for a single source dict (§2).

    Only an external thread ref counts: ``gmail:<gmail_thread_id>`` /
    ``slack:<slack_thread_ts>``. Everything else (obsidian / meeting notes with
    no external ref) returns None → honest degrade to title-only matching, never
    a fuzzy thread guess. Radars (worktree B) populate the two source keys."""
    if not isinstance(source, dict):
        return None
    gt = source.get("gmail_thread_id")
    if gt:
        return f"gmail:{gt}"
    st = source.get("slack_thread_ts")
    if st:
        return f"slack:{st}"
    return None


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


# --------------------------------------------------------------------------- #
# fold notes (§38) — timestamped so a fold is REVERSIBLE (inbox split_note).
# Line shape: "[radar|quick] <text> [@<ts>]" (+ " [已拆出 R-yyy]" once split).
# The "[kind] <text>" prefix is FROZEN (pre-§38 tests anchor it); the ts tag
# rides at the END so legacy substring assertions keep passing. Swift's fold-
# note parser in mac/Sources/Cards.swift mirrors this shape — keep in lockstep.
# --------------------------------------------------------------------------- #
_FOLD_LINE_RE = re.compile(r"^\[(?P<kind>radar|quick)\] (?P<body>.*)$")
_FOLD_TS_RE = re.compile(r" \[@(?P<ts>[^\]\s]+)\]$")
_FOLD_SPLIT_RE = re.compile(r" \[已拆出 (?P<rid>[^\]\s]+)\]$")


def parse_fold_notes(notes) -> list[dict]:
    """Parse the fold-note lines out of a notes blob.

    Returns ``[{"kind", "text", "ts", "split_into"}, ...]`` in line order;
    legacy un-timestamped lines come back with ``ts=None`` (they predate §38
    and cannot be split — no stable handle). Non-fold lines are skipped."""
    out: list[dict] = []
    for line in str(notes or "").split("\n"):
        m = _FOLD_LINE_RE.match(line.strip())
        if not m:
            continue
        body = m.group("body")
        split_into = None
        sm = _FOLD_SPLIT_RE.search(body)
        if sm:
            split_into = sm.group("rid")
            body = body[: sm.start()]
        ts = None
        tm = _FOLD_TS_RE.search(body)
        if tm:
            ts = tm.group("ts")
            body = body[: tm.start()]
        out.append({"kind": m.group("kind"), "text": body.strip(),
                    "ts": ts, "split_into": split_into})
    return out


def append_fold_note(req: Requirement, note, kind: str = "radar") -> Optional[str]:
    """Append a timestamped fold-note line to ``req.notes`` (in memory only —
    the caller saves). Returns the ts tag used, or None when nothing was added.

    Dedup is on ``(kind, note text)`` — the radar's failed-note retry queue
    re-folds the same hit on every retry, and an identical note must not
    accumulate on the user-visible card ("retry is harmless", pre-§38
    invariant). Legacy un-timestamped ``[kind] note`` lines count as already
    present. Same-second folds get a ``#n`` suffix so every ts tag on one card
    stays a unique split handle."""
    text = " ".join(str(note or "").split()).strip()
    if not text:
        return None
    existing = parse_fold_notes(req.notes)
    if any(e["kind"] == kind and e["text"] == text for e in existing):
        return None
    base = _iso_now()
    used = {e["ts"] for e in existing if e["ts"]}
    ts, n = base, 2
    while ts in used:
        ts = f"{base}#{n}"
        n += 1
    line = f"[{kind}] {text} [@{ts}]"
    req.notes = (req.notes + "\n" + line).strip() if req.notes else line
    return ts


def mark_note_split(req: Requirement, note_ts, new_id: str) -> bool:
    """Tag the fold-note line carrying ``[@note_ts]`` as 已拆出 → ``new_id``
    (append-only — the original text stays as history; in memory only, the
    caller saves). False when no un-split line carries that ts (unknown ts,
    legacy line, or an idempotent replay of an already-split note)."""
    ts = str(note_ts or "").strip()
    if not ts:
        return False
    lines = str(req.notes or "").split("\n")
    for i, line in enumerate(lines):
        m = _FOLD_LINE_RE.match(line.strip())
        if m is None or f"[@{ts}]" not in line or "[已拆出 " in line:
            continue
        lines[i] = f"{line} [已拆出 {new_id}]"
        req.notes = "\n".join(lines)
        return True
    return False


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


def _fold_hit(target: Requirement, new_req: Optional[Requirement],
              note: str = "", sources: Optional[list] = None) -> Requirement:
    """Fold a hit into ``target``: note + deduped sources + mentions bump.

    Mirrors quick_capture's ``_fold_into`` so both the deterministic and LLM
    re-raise paths add the same ``[radar]`` note tag and dedupe identically."""
    src = sources if sources is not None else (
        new_req.sources if new_req is not None else None)
    merged, added = _dedupe_sources(target.sources or [], src or [])
    target.sources = merged
    if added:
        target.repeated_mentions = int(target.repeated_mentions or 1) + added
    append_fold_note(target, note, "radar")   # §38: timestamped + deduped
    save(target)
    return target


def reraise_or_followup(parent: Requirement, new_req: Requirement, *,
                        same_task: bool, actionable: Optional[bool] = None,
                        sources: Optional[list] = None,
                        note: str = "") -> tuple[Optional[str], Optional[Requirement]]:
    """Unified re-raise / follow-up for a candidate matching a RESOLVED card
    (卡片生命周期 §3.3). Shared by ``merge_or_new`` (deterministic) and
    ``apply_triage`` / ``_apply_relates_to`` (LLM) so both apply ONE门槛.

    Returns ``(kind, saved)``:
      - ``(None, None)``            dead-end — canonical primary is
                                    rejected/trashed/archived → caller opens a
                                    fresh card (never bury in a dead card);
      - ``("reraised", parent)``    same-task + new actionable ask → the ORIGINAL
                                    card flips back to card_sent (提案), source
                                    folded, repeated_mentions+1, execution
                                    .reraised_at/_note set, summary "· 新增:…";
      - ``("follow_up", child)``    different task in the SAME thread → a distinct
                                    child (card_sent) inheriting thread lineage,
                                    NEVER polluting the old card's title;
      - ``("folded", card)``        pure restatement/no new ask (bump only, no
                                    flip), OR a fold into an already-open
                                    follow-up (cross-pass/source dedup), OR a
                                    fold into a live/open canonical primary.

    ``actionable``: None → decide deterministically via ``_carries_increment``
    (the merge_or_new path); an explicit bool is the LLM's ``needs_action``.
    ``same_task`` = the titles align (a genuine restatement of the same task),
    vs a thread-only match (same email/slack thread, different matter/task).
    """
    parent = canonical(parent)                       # merged 副卡 -> 主卡
    if parent.status in (State.REJECTED.value, State.TRASHED.value,
                         State.ARCHIVED.value):
        # canonical dead-end — caller re-cards from scratch (决策6 / 归档语义).
        return None, None
    parent.thread_id = parent.thread_id or parent.id
    if not is_resolved(parent):
        # canonical hopped to a LIVE/open primary (a merged duplicate whose
        # primary is card_sent/approved/executing/review): never pull running/
        # queued work back to card_sent — just fold the note + source.
        return "folded", _fold_hit(parent, new_req, note, sources)

    # resolved parent (delivered / merged, NOT archived):
    acts = _carries_increment(parent, new_req) if actionable is None else bool(actionable)
    if same_task and not acts:
        # Q3 pure-restatement gate: a closed thread re-mentioned with NO new
        # actionable content → bump repeated_mentions, do NOT flip (kills the
        # hot-thread 提案 noise that LLM-recall jitter would otherwise create).
        merged, _added = _dedupe_sources(
            parent.sources or [],
            (sources if sources is not None else new_req.sources) or [])
        parent.sources = merged
        parent.repeated_mentions = int(parent.repeated_mentions or 1) + 1
        save(parent)
        return "folded", parent

    # actionable (or a different-task hit): first dedupe into an already-open
    # follow-up of this cluster (cross-pass / cross-source window) so a second
    # radar source of the same event never produces a second card.
    existing_child = find_open_follow_up(parent.id)
    if existing_child is not None:
        return "folded", _fold_hit(existing_child, new_req, note, sources)

    if same_task:
        # in-place re-raise: flip the ORIGINAL card back to 提案 (Q3 ownership).
        merged, _added = _dedupe_sources(
            parent.sources or [],
            (sources if sources is not None else new_req.sources) or [])
        parent.sources = merged
        parent.repeated_mentions = int(parent.repeated_mentions or 1) + 1
        if note:
            tag = f"[re-raised] {note}"
            parent.notes = (parent.notes + "\n" + tag).strip() if parent.notes else tag
            parent.summary = (f"{parent.summary} · 新增:{note}").strip()
        ex = dict(parent.execution or {})
        ex["reraised_at"] = _iso_now()
        ex["reraised_note"] = note or ""
        # The flip starts a NEW round: the resolved parent still carries the
        # FINISHED round's session_id, and actd.dispatch_approved skips any
        # approved card with one ("already dispatched") — left in place, the
        # re-raised round would sit queued forever after approval, with no
        # agent behind it and no error anywhere. Archive it (audit trail,
        # mirrors abort's aborted_session_id) and drop the stale done flag;
        # the round's other bookkeeping (accepted_at/delivered_summary/…) is
        # history and stays.
        sid = ex.get("session_id")
        if sid:
            ex["reraised_session_id"] = sid
            ex.pop("session_id", None)
        ex.pop("done", None)
        parent.execution = ex
        parent.set_status(State.CARD_SENT)
        return "reraised", upsert(parent)

    # different task, same thread -> distinct follow-up child (card_sent),
    # inheriting the thread lineage; the old card's title is left untouched.
    summary = str(new_req.summary or new_req.title or note).strip()
    child = Requirement(
        id=next_id(),
        title=(new_req.title or note or parent.title)[:80],
        type=new_req.type or parent.type,
        tier=new_req.tier or parent.tier,
        status=State.CARD_SENT.value,
        hardness=new_req.hardness or "soft",
        deadline=new_req.deadline,
        repeated_mentions=1,
        cost_estimate_usd=new_req.cost_estimate_usd,
        sources=list(new_req.sources or []),
        plan=new_req.plan or [],
        improvement_of=parent.id,
        thread_id=parent.thread_id or parent.id,
        thread_key=new_req.thread_key or parent.thread_key,
        summary=f"既往卡 {parent.id} 的后续：{summary}",
        # §37: the candidate's LLM display title carries over (fresh card,
        # no user pin / former names inherited)
        display_title=new_req.display_title,
        notes=(f"[radar] {note}" if note else ""),
    )
    return "follow_up", upsert(child)


def merge_or_new(new_req: Union[Requirement, dict], *, high_confidence: bool = False) -> Requirement:
    """Reconcile a freshly-extracted requirement against the registry.

    Signature frozen (pre-§40); pure delegate — callers that need the
    reconciliation OUTCOME use :func:`merge_or_new_with_kind`.
    """
    return merge_or_new_with_kind(new_req, high_confidence=high_confidence)[1]


def merge_or_new_with_kind(
    new_req: Union[Requirement, dict], *, high_confidence: bool = False,
) -> tuple[str, Requirement]:
    """:func:`merge_or_new` plus the reconciliation OUTCOME (§40 additive seam).

    Parent selection (v0.20.0 §3.4): a STRONG external ``thread_key`` match wins
    first, then the legacy title heuristic. When the matched parent is RESOLVED
    (delivered/merged, non-archived) the reconciliation is delegated to
    :func:`reraise_or_followup` (re-raise the card, or open a thread-lineage
    follow-up for a different task in the same thread). Open parents keep the
    existing increment-child / restatement-bump behavior (never pulled back).

    - Pure restatement of an OPEN entry (same source+title, no increment):
      merge sources into the parent, bump ``repeated_mentions``, status unchanged.
    - Carries an increment on an OPEN entry: an ``improvement_of`` child.
    - No match: a brand-new self-rooted entry (status=detected, or card_sent when
      high-confidence + a hard deadline).

    Returns ``(kind, saved)`` — :func:`reraise_or_followup`'s vocabulary,
    which only this function can report truthfully (a ``new_proposal``
    capture can internally RE-RAISE a resolved parent; the §40.2 receipt
    must read ↩️, not 📥):

    - ``("proposed", saved)``   — a NEW card was filed (fresh self-rooted
      entry, an increment child, or the fresh card after a reraise dead-end);
    - ``("folded", parent)``    — pure restatement absorbed into an open (or
      live-canonical) entry, no new card;
    - ``("follow_up", child)``  — new lineage card under a resolved parent;
    - ``("reraised", parent)``  — a resolved card flipped back to 提案.
    """
    if isinstance(new_req, dict):
        new_req = Requirement.from_dict(new_req)
    # Derive the strong thread_key from the primary source when the caller
    # (a radar) did not set it — keeps A self-sufficient before B lands.
    if not new_req.thread_key and new_req.sources:
        new_req.thread_key = derive_thread_key(new_req.sources[0])

    existing = load_all()

    def matchable(r: Requirement) -> bool:
        # Never match: legacy merged_into:<id>, rejected, trashed, ARCHIVED
        # (决策 6 / 归档语义). MERGED (契约 四) DOES match — treated like delivered.
        return not (r.is_merged or r.status in (
            State.REJECTED.value, State.TRASHED.value, State.ARCHIVED.value))

    parent: Optional[Requirement] = None
    same_task = False
    if new_req.thread_key:
        parent = next((r for r in existing
                       if matchable(r) and r.thread_key == new_req.thread_key), None)
        # thread_key alone is a GROUPING key, not a same-task signal: only a
        # title match on top of it means the same task (else = different matter).
        same_task = bool(parent and _same_source_and_title(parent, new_req))
    if parent is None:
        parent = next((r for r in existing
                       if matchable(r) and _same_source_and_title(r, new_req)), None)
        same_task = parent is not None                # a title match IS same-task

    if parent is not None:
        if is_resolved(parent):
            # is_resolved MUST be decided here (before _carries_increment).
            kind, res = reraise_or_followup(
                parent, new_req, same_task=same_task,
                sources=new_req.sources,
                note=(new_req.summary or new_req.title))
            if res is not None:
                return kind or "folded", res
            # dead-end (canonical trashed/rejected/archived) -> fresh card below
        else:
            parent.thread_id = parent.thread_id or parent.id
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
                    thread_id=parent.thread_id or parent.id,
                    thread_key=new_req.thread_key or parent.thread_key,
                    # §37: keep the candidate's LLM display title on the
                    # increment child (fresh card, no pin/former inherited)
                    display_title=new_req.display_title,
                    notes=new_req.notes or "",
                )
                return "proposed", upsert(child)
            # pure restatement -> merge sources, bump count, keep status
            merged, added = _dedupe_sources(parent.sources or [], new_req.sources or [])
            parent.sources = merged
            if added:
                parent.repeated_mentions = int(parent.repeated_mentions or 1) + added
            return "folded", upsert(parent)

    # brand new — self-root the thread on its own id
    new_req.id = new_req.id or next_id()
    new_req.thread_id = new_req.thread_id or new_req.id
    if not new_req.status or new_req.status == State.DETECTED.value:
        if high_confidence and new_req.hardness == "hard" and new_req.deadline:
            new_req.status = State.CARD_SENT.value
        else:
            new_req.status = State.DETECTED.value
    new_req.repeated_mentions = int(new_req.repeated_mentions or 1)
    return "proposed", upsert(new_req)
