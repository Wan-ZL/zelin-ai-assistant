"""Card model — the ``Requirement`` dataclass, its ``State`` enum and the
serialized field vocabulary (``CORE_ORDER`` / ``OPTIONAL_ORDER``).

契约：CONTRACT §1（状态机/字段）+ §2（投影 add-only 字段）+ §60（两段式编号：
``id`` 主键 + ``work_id`` 工作编号）。

Leaf module on purpose (stdlib only, imports nothing from act/): the facade
``act/lib/registry.py`` re-exports every name here under the same spelling, so
callers keep writing ``registry.Requirement`` / ``registry.State`` /
``registry.CORE_ORDER``; store2's export/migrate import the vocabulary from
HERE instead of from the facade, which is what removes the
``registry → store2.export_yaml → registry`` import cycle (P3b). The two field
lists are PUBLIC on purpose: they are the card field vocabulary's single source
— a new field can never be add-only on the dataclass and silently missing in
the export (tests/test_store2_field_parity.py pins it).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Union


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
# PUBLIC on purpose: these two lists are the card field vocabulary's single
# source — store2's export/migrate import them so a new field can never be
# add-only here and silently missing there (see act/lib/store2/export_yaml.py
# and tests/test_store2_field_parity.py).
CORE_ORDER = [
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
OPTIONAL_ORDER = [
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
    # v-next add-only（amendments §50 / M8.3 C-1）：出身信任章，铸卡/fold 时由
    # policy.classify_origin 盖（hand/proposed/meeting/external）。调度侧不读
    # 章、每次从 sources 现算（M1.a）；章只服务投影/审计。
    "origin_trust",
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
    # §34bis preset 卡标记 — 按钮注入固定 plan 的卡（词表目前仅
    # "proposals_triage"）。顶层字段而非 execution 键：executor.dispatch
    # 成功路径会整个重建 execution，标记放那里活不过起跑。
    "preset",
    # §60（D21）工作编号 ``R-<m>``：进入 approved 时由 save() 分配，set-once。
    # None = 从未批准过（提案/备选/回收站卡）或存量 legacy 卡——整键省略，
    # 旧 YAML 逐字节 round-trip 不受影响。
    "work_id",
    # §64（issue #128）AI 一句话摘要 + 完成度评语：dict
    # {summary, verdict, verdict_reason, at, source_hash | error}，只由
    # act/lib/card_summary.py 在 actd 写者线程里落；**只是建议**，永不改 status。
    "assessment",
    # §65 自动草稿 PR 通道：卡显式声明需要 MCP（Slack/Gmail 等外部工具）。
    # 只会让卡**更不自主**——self_improve lane 见到即拒（self_improve:needs_mcp，
    # 只能走 owner 亲批），executor 对 self_improve 卡的 MCP 封锁据此放开。
    # 默认 False 整键省略。
    "needs_mcp",
    # §70 每日整理的合并血缘（merged_into 的反向）：合成新卡时记下被并入的
    # 旧卡主键列表；旧卡进回收站（reason `daily-merge: 并入 <new>`）、可恢复。
    # 只在合成卡上出现；空列表整键省略。
    "merged_from",
]

# from_dict 归一为 str 的标量键（手写 YAML 的无引号数字会被 PyYAML 读成 int）
_STR_SCALARS = ("id", "title", "tier", "work_id")


def _known_kwargs(d: dict) -> dict:
    """The subset of ``d`` that names a public dataclass field."""
    known = {f for f in Requirement.__dataclass_fields__ if not f.startswith("_")}
    return {k: v for k, v in d.items() if k in known}


def _coerce_delivery_mode(value) -> str:
    """§20 tolerance: missing / unknown values -> "repo"."""
    dm = str(value or "").strip().lower()
    return dm if dm in ("chat", "repo") else "repo"


def _stringify_scalars(kwargs: dict) -> None:
    """YAML 类型归一：手写卡把 `id: 4` / `title: 456` / `tier: 7` 写成无引号
    数字时 PyYAML 解析成 int —— 一律 str() 归一，否则 next_id 的正则
    match 抛 TypeError（快速捕获整条链瘫痪），且 dashboard wire 上的 int
    会让 Swift 端硬 String decode 把整列清空（CONTRACT §2）。"""
    for k in _STR_SCALARS:
        v = kwargs.get(k)
        if v is not None and not isinstance(v, str):
            kwargs[k] = str(v)


def _skip_optional(key: str, value) -> bool:
    """Unset optionals (incl. permanent=False) are not serialized so files
    stay clean; delivery_mode "repo" is the default (missing == repo, §20), so
    only the non-default "chat" is serialized — round-trips without loss."""
    if value in (None, "", [], False):
        return True
    return key == "delivery_mode" and value == "repo"


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

    # v-next（amendments §50）：出身信任章；None = 存量卡未盖章（缺章不追溯
    # 抬档、也不授予自动派发资格——两侧 fail-closed 分工见 risk.py/policy.py）。
    origin_trust: Optional[str] = None

    # v0.20.0 thread-level matching (卡片生命周期 §2)
    # thread_id: the thread anchor = the primary ``id`` of the thread-root card
    #   (same namespace as ``id`` — P-/legacy R- keys, never work_id; inherited
    #   on a match, self-rooted on a brand-new card).
    # thread_key: a STRONG deterministic bucket, only from an external thread ref
    #   ("gmail:<X-GM-THRID>" / "slack:<thread_ts>"); None when there is no strong
    #   signal — never fuzzy. See :func:`registry.derive_thread_key`.
    thread_id: Optional[str] = None
    thread_key: Optional[str] = None
    # v0.20.0 archive bookkeeping (§4) — set once archived (prev_status reused).
    archived_at: Optional[str] = None
    archive_reason: Optional[str] = None
    # §38 split lineage (see OPTIONAL_ORDER note) — origin card of a split.
    split_from: Optional[str] = None
    # §44 silent merge counter — fold-in events only (see OPTIONAL_ORDER
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

    # §34bis preset 卡标记（add-only，见 OPTIONAL_ORDER 注）——快照护栏靠它
    # 在 dispatch/收割时认出提案清理卡。
    preset: Optional[str] = None

    # §60（D21）工作编号：``R-<m>``，进入 approved 时 save() 分配、此后永不
    # 改写；``id`` 仍是唯一主键/lineage 锚点（merged_into/thread_id/
    # improvement_of/split_from 全部指 id，绝不指 work_id）。
    work_id: Optional[str] = None

    # §64 AI 摘要 + 评语（见 OPTIONAL_ORDER 注）。None = 还没评 / 不是 review 卡。
    assessment: Optional[dict] = None
    # §70 每日整理合成卡的来源卡主键列表（merged_into 的反向指针；lineage 只指
    # 主键）。None/[] = 不是合成卡。
    merged_from: Optional[list] = None

    # §65：self_improve 卡显式声明需要 MCP（见 OPTIONAL_ORDER 注）。
    needs_mcp: bool = False

    # internal bookkeeping (never serialized)
    _file: Optional[str] = field(default=None, repr=False, compare=False)
    _in_list: bool = field(default=False, repr=False, compare=False)

    # -- construction ------------------------------------------------------- #
    @classmethod
    def from_dict(cls, d: dict) -> "Requirement":
        d = dict(d or {})
        kwargs = _known_kwargs(d)
        # accept `repo` as an alias for target_repo
        if "target_repo" not in kwargs and "repo" in d:
            kwargs["target_repo"] = d["repo"]
        kwargs["delivery_mode"] = _coerce_delivery_mode(kwargs.get("delivery_mode"))
        if "id" not in kwargs:
            kwargs["id"] = d.get("id", "")
        _stringify_scalars(kwargs)
        return cls(**kwargs)

    def to_dict(self) -> dict:
        out: dict = {}
        for k in CORE_ORDER:
            out[k] = getattr(self, k)
        for k in OPTIONAL_ORDER:
            v = getattr(self, k)
            if not _skip_optional(k, v):
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
