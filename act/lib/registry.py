"""Requirement registry — the card-ledger facade every caller goes through.

契约：CONTRACT §1（状态机/字段）+ §53（store2 真源与激活协议）+ §44（单写者）
+ §60（两段式卡片编号：`P-` 主键 + `work_id` 工作编号，D21）。

Ids（§60，D21）：``id`` 是终身不变的主键——新卡出生即 ``P-<n>``（provisional，
:func:`next_id`）；``work_id`` 是人看的工作编号 ``R-<m>``，**只在卡进入
approved 时**由 :func:`save` 单点分配（set-once、稠密、单调、永不复用），
detected/card_sent/raising/trash/merge 一律不给。存量卡的 ``R-<n>`` 主键原样
保留（legacy），显示名 = ``work_id or id``（:func:`display_id`）。

Truth（v0.48.8，D2）：``state/store2_truth.json`` 激活标记在（且未被回滚开关
强制回 yaml）时，真源 = SQLite ``state/store2.db``（act/lib/store2）；否则
真源 = ``act/registry/*.yaml``。**公开 API 两后端逐字一致**（判例
tests/test_registry_backend_parity.py）——调用方（actd/雷达/digest/dashboard/
server/boardctl）永远只经这里，永远看不见 SQL。

State machine (CONTRACT §1):
    detected -> card_sent -> approved -> executing -> review -> delivered
    branches: rejected  /  merged_into:<parent-id>
    terminal (merge-review 契约 四): merged + merged_into=<primary>

YAML 后端：files may be either a single YAML doc (one requirement) or a YAML
list (e.g. the debt batch R-002..R-006). Both shapes round-trip through
``save``. SQLite 后端：payload 冷列存 canonical ``to_dict()`` 全文（真源），
热列由 act/lib/store2/hot.py 单点推导；状态机白名单与 agent 墙由 schema
trigger 执法（§53.2）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Optional, Union

import yaml

from act.lib import config, policy


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

# --------------------------------------------------------------------------- #
# storage backend (CONTRACT §53) — YAML files vs store2 SQLite
# --------------------------------------------------------------------------- #
BACKEND_YAML = "yaml"
BACKEND_SQLITE = "sqlite"
STORE2_DB_NAME = "store2.db"
# 真源标记：存在 = store2 已通过「备份→迁移→逐字段比对零差异」的激活协议
# （act/lib/store2/activate.py 是唯一写者）；内容 {activated_at, backup_dir,
# cards, schema_version, app_version}
STORE2_TRUTH_NAME = "store2_truth.json"
# 最近一次激活尝试的结果（activated/refused + diff 摘要）——doctor 的数据源
STORE2_ACTIVATION_NAME = "store2_activation.json"
_BACKEND_ENV = "ZAI_REGISTRY_BACKEND"          # 测试/CI 强制后端（yaml|sqlite）
_ACTOR_TYPES = ("user", "agent", "system")
# agent 出生墙的目标态（schema cards_agent_insert_wall 同一词表）
_AGENT_FORBIDDEN = ("approved", "delivered", "executing", "review")


def store2_db_path() -> Path:
    return config.STATE_DIR / STORE2_DB_NAME


def store2_truth_path() -> Path:
    return config.STATE_DIR / STORE2_TRUTH_NAME


def store2_activation_path() -> Path:
    return config.STATE_DIR / STORE2_ACTIVATION_NAME


def registry_backups_dir() -> Path:
    """整目录 YAML 备份的家（R2.1.3）：state/backups/registry-<ts>/。"""
    return config.STATE_DIR / "backups"


def registry_export_dir() -> Path:
    """每日 YAML 导出镜像（R2.1.2，git-diff/肉眼可读）：state/registry-export/。"""
    return config.STATE_DIR / "registry-export"


_UNSET = object()
_STORE = None                    # 进程内 Store 单例（per-thread 连接在 Store 里）
_CFG_BACKEND_MEMO = _UNSET       # config 面设定的进程内 memo（重启生效，rollback 口径）


def reset_store_cache() -> None:
    """清后端判定与 Store 缓存（激活切换 / 测试沙箱切后端后调用）。"""
    global _STORE, _CFG_BACKEND_MEMO
    if _STORE is not None:
        try:
            _STORE.close()
        except Exception:  # noqa: BLE001 - 缓存清理绝不抛
            pass
    _STORE = None
    _CFG_BACKEND_MEMO = _UNSET


def backend_forced() -> Optional[str]:
    """显式强制值（env > config），无强制 = None（auto，看激活标记）。"""
    env = os.environ.get(_BACKEND_ENV, "").strip().lower()
    if env in (BACKEND_YAML, BACKEND_SQLITE):
        return env
    global _CFG_BACKEND_MEMO
    if _CFG_BACKEND_MEMO is _UNSET:
        _CFG_BACKEND_MEMO = config.registry_backend_setting()
    if _CFG_BACKEND_MEMO in (BACKEND_YAML, BACKEND_SQLITE):
        return _CFG_BACKEND_MEMO
    return None


def backend() -> str:
    """当前真源：强制值优先；auto 下激活标记在 = sqlite，否则 yaml。

    标记在而 DB 文件缺失属于故障半态：backend() 仍答 sqlite（绝不静默退回
    已冻结的 YAML 目录装没事），首次触库时 :func:`_store` 响亮拒绝并指向
    rollback 文档；doctor `store2` 行 FAIL（§53.6）。"""
    forced = backend_forced()
    if forced is not None:
        return forced
    return BACKEND_SQLITE if store2_truth_path().exists() else BACKEND_YAML


def _store():
    """store2 存取层单例（仅 sqlite 后端调用）。DB 缺失 = 响亮拒绝。"""
    global _STORE
    db = store2_db_path()
    if _STORE is not None and _STORE.db_path != str(db):
        reset_store_cache()
    if _STORE is None:
        if not db.exists():
            raise RuntimeError(
                f"store2 truth marker present but {db} is missing — "
                "restore state/backups/registry-<ts>/ or set registry.backend:"
                " yaml (docs/TROUBLESHOOTING.md「store2 回滚」)")
        from act.lib.store2.store import Store
        _STORE = Store(db)
    return _STORE


# --- actor seam（§53.5）----------------------------------------------------- #
# actor = 动作的发起者（不是写库进程）：actd 替用户执行 inbox 决策时 = user，
# radar/triage/digest/reconcile 等自主管线 = system（默认），agent 通道 = agent。
# thread-local 而非 module-global（防腐 #3 的教训适用于注入缝——这里是随调用
# 栈走的身份上下文，且只经 acting_as() 一个受控入口设置）。
_ACTOR_CTX = threading.local()


def current_actor() -> str:
    return getattr(_ACTOR_CTX, "value", "system")


@contextmanager
def acting_as(actor: str):
    """把本调用栈内的 registry 写标为 ``actor`` 发起（user|agent|system）。

    actd 的 inbox 决策漏斗用 ``acting_as("user"/"agent")`` 包住 apply；其余
    管线不包 = system。sqlite 后端把 actor 写进 last_actor_type，schema 的
    transition_whitelist / agent 墙据此执法；yaml 后端由 :func:`_agent_wall`
    在 Python 面执行同一堵墙（两后端行为一致，R2.1.4）。"""
    if actor not in _ACTOR_TYPES:
        raise ValueError(f"unknown actor {actor!r}")
    prev = getattr(_ACTOR_CTX, "value", None)
    _ACTOR_CTX.value = actor
    try:
        yield
    finally:
        if prev is None:
            try:
                del _ACTOR_CTX.value
            except AttributeError:
                pass
        else:
            _ACTOR_CTX.value = prev


def _agent_wall(req: "Requirement", old_status: Optional[str]) -> None:
    """R2.1.4 权限墙的后端无关面：agent 发起的任何状态转移/敏感出生一律拒绝。

    sqlite 后端另有 schema trigger 兜底（AGENT_TRANSITION_FORBIDDEN /
    cards_agent_insert_wall）；这里在两后端共同的入口先拒，保证 yaml 回滚
    窗口内墙不消失、错误形状一致（store2.TransitionDenied）。"""
    if current_actor() != "agent":
        return
    from act.lib.store2.store import TransitionDenied
    new_status = str(req.status)
    if old_status is None:
        if new_status in _AGENT_FORBIDDEN or                 (req.prev_status and str(req.prev_status) in _AGENT_FORBIDDEN):
            raise TransitionDenied(
                "AGENT_TRANSITION_FORBIDDEN",
                f"agent may not mint card {req.id} in/into {new_status!r}")
    elif str(old_status) != new_status:
        raise TransitionDenied(
            "AGENT_TRANSITION_FORBIDDEN",
            f"agent may not move card {req.id} {old_status!r} -> {new_status!r}")

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
    # §62 每日整理的合并血缘（merged_into 的反向）：合成新卡时记下被并入的
    # 旧卡主键列表；旧卡进回收站（reason `daily-merge: 并入 <new>`）、可恢复。
    # 只在合成卡上出现；空列表整键省略。
    "merged_from",
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

    # v-next（amendments §50）：出身信任章；None = 存量卡未盖章（缺章不追溯
    # 抬档、也不授予自动派发资格——两侧 fail-closed 分工见 risk.py/policy.py）。
    origin_trust: Optional[str] = None

    # v0.20.0 thread-level matching (卡片生命周期 §2)
    # thread_id: the thread anchor = the primary ``id`` of the thread-root card
    #   (same namespace as ``id`` — P-/legacy R- keys, never work_id; inherited
    #   on a match, self-rooted on a brand-new card).
    # thread_key: a STRONG deterministic bucket, only from an external thread ref
    #   ("gmail:<X-GM-THRID>" / "slack:<thread_ts>"); None when there is no strong
    #   signal — never fuzzy. See :func:`derive_thread_key`.
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
    # §62 每日整理合成卡的来源卡主键列表（merged_into 的反向指针；lineage 只指
    # 主键）。None/[] = 不是合成卡。
    merged_from: Optional[list] = None

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
        for k in ("id", "title", "tier", "work_id"):
            v = kwargs.get(k)
            if v is not None and not isinstance(v, str):
                kwargs[k] = str(v)
        return cls(**kwargs)

    def to_dict(self) -> dict:
        out: dict = {}
        for k in CORE_ORDER:
            out[k] = getattr(self, k)
        for k in OPTIONAL_ORDER:
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


def registry_yaml_files(include_archived: bool = False) -> list:
    """YAML registry 目录的卡文件清单（不管当前后端——激活协议/doctor 用它
    看「激活后是否还有旁路进程往 YAML 目录写」，§53.6 late_yaml_writes）。"""
    return list(_iter_files(include_archived))


def guard_snapshot() -> dict:
    """§34bis 机械护栏的快照面（backend-aware）：{f"<id>.yaml": token}。

    yaml = 文件名 → "size:mtime_ns"（历史形状原样）；sqlite = 卡 id（拼 .yaml
    后缀保持键形一致，writes journal 也按这个键记）→ "v<version>"（CAS 列，
    任何写都会 bump——含 tombstone 行，会话删卡也逃不过比对）。"""
    if backend() == BACKEND_SQLITE:
        st = _store()
        return {f"{c['id']}.yaml": f"v{c['version']}"
                for c in st.list_cards(include_tombstones=True)}
    snap: dict = {}
    try:
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            try:
                stt = p.stat()
            except OSError:
                continue
            snap[p.name] = f"{stt.st_size}:{stt.st_mtime_ns}"
    except OSError:
        pass
    return snap


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

    sqlite 后端：payload 即 canonical dict，一条 SELECT 全量取回；archived
    过滤按热列 status（与 yaml 的 archive/ 目录语义等价），tombstone 行
    （回收站到期硬删的替身）不出现。
    """
    if backend() == BACKEND_SQLITE:
        out: list[Requirement] = []
        for card in _store().list_cards():
            if not include_archived and card["status"] == State.ARCHIVED.value:
                continue
            r = Requirement.from_dict(card["payload"])
            out.append(r)
        return out
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
    if backend() == BACKEND_SQLITE:
        from act.lib.store2.store import NotFound
        try:
            card = _store().get_card(str(req_id))
        except NotFound:
            return None
        if card.get("tombstone"):
            return None      # 硬删替身：对读方等同不存在（yaml 同义 = 文件已删）
        return Requirement.from_dict(card["payload"])
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


# §34bis 快照护栏的排除表（写入台账）：经本模块写/删过的卡片文件名。
# 起止快照比对时命中台账的变动 = 管线自己的合法写入，不算嫌疑——没有它，
# 清理会话期间任何正常落盘（radar 新卡、fold、状态迁移）都会触发假警。
# **跨进程持久**：radar（slack 180s / gmail 300s / obsidian cron）是独立
# 进程、也经本模块直写 registry，进程内存集合看不见它们；台账落成
# state/registry_writes.jsonl（append-only，一行一条 {"f","ts"}），guard
# 按快照起始 ts 过滤读取——actd 中途重启也不再丢账。进程内映射只留作
# 落盘失败（磁盘满等）时的兜底，且**同样带 ts、同样按快照起始 ts 过滤**：
# 无条件豁免会让本进程写过的每张卡（包括清理会话正在审阅的提案卡——最
# 现实的篡改目标）永久免检，护栏对它们失明。宁多记一笔（漏报该笔）不
# 少记（假警），但绝不豁免快照前的历史写入。
_PROC_WRITES: dict = {}     # 文件名 -> 本进程最近一次写入 ts（UTC 字符串）
_WRITES_JOURNAL_MAX_BYTES = 1 << 20     # 超过 ~1MB 压缩到最近半数行


def _writes_journal_path() -> Path:
    return config.STATE_DIR / "registry_writes.jsonl"


def _journal_write(name: str) -> None:
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _PROC_WRITES[name] = ts     # 兜底映射带 ts（writes_since 按 ts 过滤）
    try:
        path = _writes_journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"f": name, "ts": ts},
                                ensure_ascii=False) + "\n")
        # 压缩（best-effort）：append-only 台账会无限增长；超限时只留后半。
        # 多进程下 rewrite 可能吞掉并发的一条 append——代价只是那笔合法写入
        # 可能被误报（检测型护栏 + 人工核查，可接受），绝不多排除。
        if path.stat().st_size > _WRITES_JOURNAL_MAX_BYTES:
            lines = path.read_text(encoding="utf-8").splitlines()
            keep = lines[len(lines) // 2:]
            tmp = path.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
            tmp.replace(path)
    except OSError:
        pass


def writes_since(ts: str) -> frozenset:
    """§34bis 快照护栏的排除表：``ts`` 起（含）的合法写入文件名集合。

    = 持久台账 ∪ 本进程内存映射（落盘失败的兜底）中 ts 之后（含）的条目。
    **两路都按 ts 过滤**——快照前的历史写入绝不豁免（否则本进程写过的卡被
    会话篡改将永不告警）。ts 与台账条目同为 UTC "%Y-%m-%dT%H:%M:%SZ"——
    字符串比较即时间比较。
    """
    names = {n for n, t in _PROC_WRITES.items() if str(t) >= str(ts)}
    try:
        for line in _writes_journal_path().read_text(
                encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("f") \
                    and str(rec.get("ts", "")) >= str(ts):
                names.add(str(rec["f"]))
    except OSError:
        pass
    return frozenset(names)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    _journal_write(path.name)


def _sqlite_save(req: Requirement) -> None:
    """sqlite 后端的落盘：payload = canonical to_dict 全文（真源），热列经
    act/lib/store2/hot.py 单点推导，一笔事务写卡 + 同步 sources 投影行。

    状态机合法性由 schema transition_whitelist trigger 执法（last_actor_type
    = :func:`current_actor`）；形态装不下（status 词表外且非 legacy 串、
    merged 缺父指针）= store2.StoreError 响亮拒绝，绝不静默失真。"""
    from act.lib.store2 import hot as _hot
    from act.lib.store2.export_yaml import normalize_card
    from act.lib.store2.store import StoreError
    norm = normalize_card(req.to_dict())
    hot_cols, _warnings, errors = _hot.derive(norm)
    if errors:
        raise StoreError("UNREPRESENTABLE",
                         f"card {req.id}: " + "; ".join(errors),
                         {"card": req.id, "errors": errors})
    src_rows, _sw = _hot.source_rows(norm)
    _store().put_card(str(norm.get("id") or req.id), norm, hot_cols, src_rows,
                      actor_type=current_actor())
    _journal_write(f"{req.id}.yaml")   # §34bis 写入台账：键形与 yaml 后端一致


def save(req: Requirement) -> None:
    """Persist a requirement, preserving whether it lives in a list file.

    §60（D21）**工作编号的唯一分配点**：卡以 ``approved`` 落盘且尚无
    ``work_id`` 时，在这里分配 ``R-<m>``（:func:`next_work_id`）。进入
    approved 的每条路径——owner approve、§51 免批、capture[run] 出生即
    approved、restore 按 prev_status 精确复位回 approved——都经 save()，
    所以调用方零改动、零遗漏；detected/card_sent/raising/trashed/merged
    的落盘永不分配。分配失败（序列文件读不了等）不崩 save：编号是显示层
    资产，卡照常落盘，下一次 approved 落盘再补。"""
    if current_actor() == "agent":
        # R2.1.4 权限墙（两后端一致）：agent 不得转移状态/铸敏感出生态。
        # 旧状态从真源现查（agent 路径罕见，额外一读可接受）。
        prior = load(req.id) if req.id else None
        _agent_wall(req, str(prior.status) if prior is not None else None)
    allocated = _allocate_work_id(req)
    try:
        if backend() == BACKEND_SQLITE:
            _sqlite_save(req)
        else:
            _yaml_save(req)
    except BaseException:
        if allocated:
            # 没落盘的编号不占位：清掉内存里的号，下一次 approved 落盘重分
            # ——否则重试会带着同一个号再撞一次 UNIQUE（sqlite）。
            req.work_id = None
        raise
    if allocated:
        _bump_work_seq(allocated)
    _note_first_card(req)


def _yaml_save(req: Requirement) -> None:
    """YAML 后端的落盘（单卡文件 / list 成员两种形状，见模块 docstring）。"""
    if req._file and req._in_list:
        _yaml_save_list_member(req, Path(req._file))
    else:
        _yaml_save_single(req)


def _yaml_save_list_member(req: Requirement, path: Path) -> None:
    """req 是多卡 list 文件的一个成员：整文件读 → 换掉同 id 项 → 原子写回。"""
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
    _atomic_write(path, _dump_yaml(_replace_list_member(data, req)))


def _same_card(item, rid) -> bool:
    # str() both sides: from_dict normalizes hand-written numeric ids
    # (`id: 4` -> "4") but the raw on-disk entry still holds the int —
    # an un-normalized == would append a duplicate instead of replacing.
    return (isinstance(item, dict) and item.get("id") is not None
            and str(item.get("id")) == str(rid))


def _replace_list_member(data: list, req: Requirement) -> list:
    """list 文件内容里换掉 req 的那一项（没有就追加）。"""
    out: list = []
    replaced = False
    for item in data:
        if _same_card(item, req.id):
            out.append(req.to_dict())
            replaced = True
        else:
            out.append(item)
    if not replaced:
        out.append(req.to_dict())
    return out


def _yaml_save_single(req: Requirement) -> None:
    """单卡文件：``<REGISTRY_DIR>/<id>.yaml``（或 req 自带的 _file）。"""
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
    - a candidate containing ``sanitize.MASK`` is rejected here, at the single
      write point — the board and ``former_titles`` never show a redaction
      mask, whichever side path the candidate came in through (§37.1);
    - a user-pinned title (``user_titled``) is NEVER overwritten by an LLM /
      harvest title (``by_user=False``);
    - the previous display_title is appended to ``former_titles`` (deduped,
      newest last, capped at FORMER_TITLES_CAP) so a renamed card stays
      findable under its old name.
    """
    from act.lib import sanitize, titles  # lazy: keep registry import-light
    t = titles.clip_title(title)
    if t is None:
        return False
    # 掩码拒收在唯一落笔点（§37.1）：display_title 的每条便车路径（analyze
    # 扩写、quick_capture capture/triage、CARD TITLE 收割）outbound prompt
    # 都过 sanitize.scrub，LLM 都可能把围栏里的 [脱敏] 抄进标题键——含掩码
    # 的候选一律 no-op（与 clip 后为空同待遇，fail 向保留旧名），保证看板
    # 显示名与 former_titles 永不出现掩码，不管候选从哪条口进来。harvest
    # 侧的同款检查保留为提前拒收（marker 行照剥的语义在那边）。
    if sanitize.MASK in t:
        return False
    if req.user_titled and not by_user:
        return False
    changed = False
    prev = str(req.display_title or "").strip()
    # same-value 判定比较侧同口径规范化（PR #103 review P2）：手编 YAML 的
    # 超长（>64）或含内部空白/换行的存量 display_title，agent 原样重复注入
    # 现值（executor._current_display_name 注入的就是 clip 规范形）不算改名
    # ——否则一次假 rename 把旧值追进 former_titles。经本函数落笔的存量值
    # 本就是 clip 规范形，prev_norm == prev，行为不变；真改名时
    # former_titles 记录的仍是磁盘上的原始 prev（可搜索性不受规范化影响）。
    # 规范化短路只作用于 LLM/harvest 回流（by_user=False）：用户主动改名按
    # 原始形态比较——存量「整理\n合同」、用户给「整理 合同」是真改名（否则
    # 异常存量被永久钉死、user_titled 却已置位），旧形态照记 former_titles。
    prev_norm = titles.clip_title(prev) if prev else None
    if t != prev and (by_user or t != prev_norm):
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

    sqlite 后端：硬删 = tombstone 化（§53.2——行骨架保留进 revision 流，
    增量客户端学到删除；schema CHECK 只许 trashed 卡 purge，与 §9 一致）。
    已 tombstone / 不存在 → False（幂等语义对齐 yaml 的「没删到东西」）。
    """
    if backend() == BACKEND_SQLITE:
        from act.lib.store2.store import NotFound, StoreError
        try:
            row = _store().get_card(str(req.id))
            if row.get("tombstone"):
                return False
            _store().purge_trashed(str(req.id))
        except (NotFound, StoreError):
            return False
        _journal_write(f"{req.id}.yaml")     # §34bis 台账：管线的合法删除
        return True
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
            _journal_write(path.name)    # §34bis 台账：管线的合法删除
        return True
    try:
        path.unlink()
        _journal_write(path.name)        # §34bis 台账：管线的合法删除
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
    is removed — so a crash mid-move leaves the card recoverable, never lost.
    sqlite 后端无目录搬迁：status=archived 即封存（load_all 默认过滤）。"""
    if req.status != State.ARCHIVED.value:
        req.prev_status = req.status
    req.set_status(State.ARCHIVED)
    req.archived_at = _iso_now()
    req.archive_reason = reason
    if backend() == BACKEND_SQLITE:
        save(req)
        return req
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
    active registry dir (§4). Clears the archive bookkeeping.
    sqlite 后端无目录搬迁：状态复位即解封。"""
    req.set_status(req.prev_status or State.DELIVERED.value)
    req.prev_status = None
    req.archived_at = None
    req.archive_reason = None
    if backend() == BACKEND_SQLITE:
        save(req)
        return req
    orig = req._file
    req._file = str(config.REGISTRY_DIR / f"{req.id}.yaml")
    req._in_list = False
    save(req)
    if orig and Path(orig) != Path(req._file):
        try:
            Path(orig).unlink(missing_ok=True)
            _journal_write(Path(orig).name)     # §34bis 台账：搬迁删除原件
        except OSError:
            pass
    return req


# --------------------------------------------------------------------------- #
# ID allocation + matching / merge（§60，D21：两段式编号）
# --------------------------------------------------------------------------- #
# 命名空间：
#   P-<n>  主键（provisional）——v0.48.15 起所有新卡的出生 id（next_id）
#   R-<n>  ①存量卡的主键（legacy，v0.48.15 前雷达检测即分号）
#          ②工作编号 work_id（进入 approved 时分配，next_work_id）
# 两种 R- 用途靠数值区间**构造上不重叠**：工作序列从 max(legacy R 主键 ∪
# 已分配 work_id ∪ state/work_seq.json 高水位) + 1 起，所以任一 R-<n> 要么
# 是 ≤ 存量上界的 legacy 主键，要么是 > 上界的工作编号——resolve() 按
# 「先精确主键、再 work_id」两步查，无歧义。
_ID_RE = re.compile(r"^R-(\d+)$")              # legacy R 主键 / work_id 形
_P_ID_RE = re.compile(r"^P-(\d+)$")
# Filename form of an id — prefix match so "R-042" and "R-042-notes" both
# count as allocating 42 (next_id's unreadable-file guard; conservative).
_FILE_ID_RE = re.compile(r"^R-(\d+)")
_P_FILE_ID_RE = re.compile(r"^P-(\d+)")
WORK_ID_PREFIX = "R-"
PROVISIONAL_ID_PREFIX = "P-"
# 工作序列高水位（§60.2 的第二道保险）：{"work_seq": <int>}——固定大小，
# 防腐 #4 天然满足。yaml 后端硬删 trashed 文件会带走它的 work_id，光扫账本
# 会让被删的最大号复用；高水位在两后端都参与 max()，sqlite 侧 tombstone 行
# 保留热列本就不丢，这里只是同一口径。
WORK_SEQ_NAME = "work_seq.json"

# id_kind 词表（§2 投影 add-only 字段，web 据此灰显 legacy 主键）
ID_KIND_WORK = "work"            # 有 work_id：显示名 = 工作编号
ID_KIND_LEGACY = "legacy"        # 存量 R- 主键、未获工作编号
ID_KIND_PROPOSAL = "proposal"    # P- 主键、未获工作编号（提案/备选/回收站）


def id_number(rid) -> Optional[int]:
    """``R-042`` / ``P-007`` → 42 / 7；其他形状 → None。"""
    m = _ID_RE.match(str(rid or "")) or _P_ID_RE.match(str(rid or ""))
    return int(m.group(1)) if m else None


def is_legacy_key(rid) -> bool:
    """主键是否为 v0.48.15 前的 ``R-<n>`` 形（检测即分号的存量卡）。"""
    return bool(_ID_RE.match(str(rid or "")))


def id_sort_key(rid) -> tuple:
    """跨命名空间的 FIFO 序：legacy R 主键 < P 主键（一切 P 卡都晚于一切
    存量卡出生），同空间按数值；其他形状按字面排最后。
    actd 的公平轮转（process_raising / auto_dispatch_pass）与 auto_merge /
    quick_capture 的「哪张更老」判断都用它——字典序 ``"P-" < "R-"`` 会让
    每张 P 卡插到所有存量卡前面（饿死存量 raising 队列），数值解析把 P 当
    0 会让 P 卡永远「更老」（合并方向反转）。"""
    s = str(rid or "")
    m = _ID_RE.match(s)
    if m:
        return (0, int(m.group(1)), s)
    m = _P_ID_RE.match(s)
    if m:
        return (1, int(m.group(1)), s)
    return (2, 0, s)


def display_id(req: "Requirement") -> str:
    """人看的编号：``work_id``（批准过的卡）否则主键（P-/legacy R-）。
    executor 的 prompt 头/会话名/日志名、oneonone、dashboard ``display_id``
    都从这里取——单一落点。"""
    return str(getattr(req, "work_id", None) or req.id or "")


def _passed_approval(req: "Requirement") -> bool:
    """卡是否已（曾）过批准闸：现态或回程票 prev_status 在 approved 之后各态。"""
    st = str(req.status or "")
    if st in _AGENT_FORBIDDEN:
        return True
    return (st in (State.TRASHED.value, State.ARCHIVED.value)
            and str(getattr(req, "prev_status", None) or "") in _AGENT_FORBIDDEN)


def id_kind(req: "Requirement") -> str:
    """§2 投影的 ``id_kind``：work | legacy | proposal（见词表常量）。

    存量 legacy 卡若已过批准闸（approved/executing/review/delivered，或带这些
    回程票进了回收站/归档）算 ``work``——它的 R 号是批准后跑出来的，不该灰显；
    只有「检测即分号、从未批准」的存量卡才是 ``legacy``（#127 抱怨的那 162 张）。
    """
    if getattr(req, "work_id", None):
        return ID_KIND_WORK
    if is_legacy_key(req.id):
        return ID_KIND_WORK if _passed_approval(req) else ID_KIND_LEGACY
    return ID_KIND_PROPOSAL


def _work_seq_path() -> Path:
    return config.STATE_DIR / WORK_SEQ_NAME


def _read_work_seq() -> int:
    try:
        data = json.loads(_work_seq_path().read_text(encoding="utf-8"))
        return max(0, int(data.get("work_seq") or 0)) if isinstance(data, dict) else 0
    except (OSError, ValueError, TypeError):
        return 0


def _bump_work_seq(work_id: str) -> None:
    """高水位只升不降；写失败静默（序列真源仍是账本，文件只是保险）。"""
    n = id_number(work_id)
    if n is None:
        return
    try:
        cur = _read_work_seq()
        if n <= cur:
            return
        path = _work_seq_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"work_seq": n}), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _sqlite_stored_work_id(req_id: str):
    from act.lib.store2.store import NotFound
    try:
        return _store().get_card(req_id).get("work_id")
    except NotFound:
        return None


def _stored_work_id(req_id: str) -> Optional[str]:
    """真源里这张卡**当前**的工作编号；没有 / 卡不存在 / 读失败 → None。

    sqlite 读的是 ``cards.work_id`` **热列**而不是 payload：payload 可能被
    < v0.48.15 的代码整卡覆写而丢了 ``work_id``（它不认识这个键），热列却仍
    钉着号——正是 set-once 触发器会拒绝的那种「内存 None vs 盘上有号」。
    yaml 后端只有文件一处真源，读 :func:`load`。永不抛（分配钩子的口径）。"""
    try:
        wid = (_sqlite_stored_work_id(str(req_id)) if backend() == BACKEND_SQLITE
               else getattr(load(str(req_id)), "work_id", None))
    except Exception:  # noqa: BLE001 - 读不到就当没号，由调用方按常规路径走
        return None
    return str(wid) if wid else None


def _pick_work_id(req: "Requirement") -> Optional[str]:
    """无号卡这次落盘该带的号（不写回）：采纳优先于铸号。

    - 存量 legacy 卡（主键本就是 ``R-<n>``）已过批准闸 → **采纳自己的主键**，
      不另发新号（一张卡两个 R 号只会添乱：日志 R-175.log 与看板 R-290 对
      不上；legacy 主键 ≤ 序列下界、与新工作号构造上不撞）。采纳时机 = 任何
      已过批准闸的落盘（approved/executing/review/delivered，含带这些回程票
      的 trashed/archived）——存量卡不会再「进入 approved」一次。未批准的
      legacy 卡仍无号（id_kind=legacy，看板灰显）：D21 对存量卡同样成立。
    - P 卡先看真源里有没有已发的号（:func:`_stored_work_id`），**无论现态**
      ——abort 把 approved 卡退回 card_sent 时号是保留的（set-once），一份
      批准前取的陈旧副本在这之后落盘，若只在过闸态才采纳就会把号覆写成
      None（sqlite 打成 ``WORK_ID_SET_ONCE``、yaml 静默丢号后再批准重铸
      = 一卡两号）。真源无号且这次是 approved 落盘 → 铸新号；其余状态原样
      无号（D21 字面：没批准就没编号）。"""
    if is_legacy_key(req.id):
        return req.id if _passed_approval(req) else None
    stored = _stored_work_id(req.id)
    if stored:
        return stored
    if str(req.status) == State.APPROVED.value:
        return next_work_id()
    return None


def _allocate_work_id(req: "Requirement") -> Optional[str]:
    """save() 的分配钩子：无 work_id 的卡按 :func:`_pick_work_id` 采纳/铸号并
    写回 req；有号或不该有号 → None。

    **陈旧内存副本防御（§60.2）**：P 卡已拿号而这份内存副本没带号（副本
    取自拿号之前——跨进程 fold 撞上 approve 的真实形状；或 payload 被
    < v0.48.15 的代码剥掉了 ``work_id`` 而 sqlite 热列还留着——§53.1 降级
    警告点名的形状）→ **采纳真源里已发的号**，绝不再铸、绝不清空。没有这
    一步：sqlite 的 set-once 触发器把这类落盘打成 ``WORK_ID_SET_ONCE`` 硬
    失败（inbox 决策文件被当 poison 丢弃），yaml 后端更会静默换号（重铸）
    或丢号（覆写成 None）。
    永不抛（宪法第 11 条）——分配失败时卡照常落盘、编号下次再补。"""
    if getattr(req, "work_id", None):
        return None
    try:
        req.work_id = _pick_work_id(req)
    except Exception as e:  # noqa: BLE001 - 编号是显示层资产，不许拖垮落盘
        print(f"registry: work_id allocation failed for {req.id}: {e}",
              file=sys.stderr)
        return None
    return req.work_id

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


def _max_number(values: Iterable, regex) -> int:
    mx = 0
    for v in values:
        # str() 防御第二层（from_dict 已归一 YAML 路径）：直接构造的
        # Requirement 仍可能带 int id —— 正则 match 对 int 抛 TypeError。
        m = regex.match(str(v or ""))
        if m:
            mx = max(mx, int(m.group(1)))
    return mx


def next_id() -> str:
    """下一张新卡的**主键** ``P-<n>``（§60，D21）——出生即分、终身不变。

    公开名保留（§53 点名、12 个铸卡点全部经它）：v0.48.15 前它发 ``R-<n>``
    （检测即消耗工作号，issue #127），现在发 provisional ``P-<n>``；工作编号
    改由 :func:`next_work_id` 在进入 approved 时分配。
    """
    if backend() == BACKEND_SQLITE:
        # tombstone 行也计入（id 是 PK 且永不复用——复用会撞 PK/复活死号）
        ids = [c.get("id") for c in _store().list_cards(include_tombstones=True)]
        return f"{PROVISIONAL_ID_PREFIX}{_max_number(ids, _P_FILE_ID_RE) + 1:03d}"
    # CRITICAL (§4): include archived cards, or a freshly allocated id could
    # collide with a sealed P-050 and overwrite it (silent data loss).
    mx = _max_number((r.id for r in load_all(include_archived=True)), _P_ID_RE)
    # Fail-closed vs unreadable files: load_all() SKIPS a corrupt/unreadable
    # card file (hand-edit YAML typo, transient OSError), so its id would
    # otherwise be re-allocated here and the still-recoverable file overwritten
    # by the next save(). Filenames stay readable even when content isn't —
    # count P-<n>*.yaml names in both the active and archive dirs as allocated.
    # (Over-counting is harmless: worst case an id number is skipped.)
    mx = max(mx, _max_number((p.stem for p in _iter_files(include_archived=True)),
                             _P_FILE_ID_RE))
    return f"{PROVISIONAL_ID_PREFIX}{mx + 1:03d}"


def next_work_id() -> str:
    """下一个**工作编号** ``R-<m>``（§60.2）：稠密、单调、永不复用。

    序列上界 = max(存量 legacy ``R-<n>`` 主键 ∪ 已分配 ``work_id`` ∪
    ``state/work_seq.json`` 高水位)。legacy 主键计入 = 新工作号一定大于
    任何存量卡号，两种 R- 用途在数值上不重叠（resolve 无歧义、老 log/
    通知里的 R 号不会被新工作号「顶替」）。sqlite 侧 tombstone 行保留
    ``work_id`` 热列（purge 只清 payload），已硬删卡的编号照样占位；yaml
    侧文件被删后靠高水位补位。唯一调用者 = :func:`save` 的分配钩子；
    单写者纪律下（只有 actd 把卡送进 approved）序列不会并发分配，sqlite
    的 UNIQUE 索引是万一并发时的响亮兜底（撞号 = StoreError，不静默复用）。
    """
    if backend() == BACKEND_SQLITE:
        cards = _store().list_cards(include_tombstones=True)
        mx = max(_max_number((c.get("id") for c in cards), _FILE_ID_RE),
                 _max_number((c.get("work_id") for c in cards), _ID_RE))
    else:
        reqs = load_all(include_archived=True)
        mx = max(_max_number((r.id for r in reqs), _ID_RE),
                 _max_number((r.work_id for r in reqs), _ID_RE),
                 # 文件名守卫同 next_id：不可读的存量 R-*.yaml 也占号
                 _max_number((p.stem for p in _iter_files(include_archived=True)),
                             _FILE_ID_RE))
    mx = max(mx, _read_work_seq())
    return f"{WORK_ID_PREFIX}{mx + 1:03d}"


def _from_live_card(card: Optional[dict]) -> Optional[Requirement]:
    """store2 行 → Requirement；缺席 / tombstone（硬删替身）→ None。"""
    if card is None or card.get("tombstone"):
        return None
    return Requirement.from_dict(card["payload"])


def _yaml_load_by_work_id(wid: str) -> Optional[Requirement]:
    for r in load_all(include_archived=True):
        if r.work_id == wid:
            return r
    return None


def load_by_work_id(work_id: str) -> Optional[Requirement]:
    """按工作编号取卡（§60.3）；无/不像 R- 号 → None。"""
    wid = str(work_id or "").strip()
    if not _ID_RE.match(wid):
        return None
    if backend() == BACKEND_SQLITE:
        return _from_live_card(_store().get_card_by_work_id(wid))
    return _yaml_load_by_work_id(wid)


def resolve(ref: str) -> Optional[Requirement]:
    """按「主键或工作编号」取卡（§60.3）——inbox / boardctl / server 收到的
    ``id`` 字段可能是两者之一（web 显示工作编号，用户复制的就是它）。
    顺序：精确主键 → work_id；两种 R- 用途数值不重叠，无歧义。"""
    rid = str(ref or "").strip()
    if not rid:
        return None
    req = load(rid)
    if req is not None:
        return req
    return load_by_work_id(rid)


def _distinct_strs(values) -> list:
    """str() + strip、去空、去重、保序。"""
    out: list = []
    for v in values:
        s = str(v or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def canonical_ids(refs) -> "tuple[list, list]":
    """§60.3：把 inbox 送来的「主键或工作编号」列表归一成**主键**列表（去空、
    去重、保序；同一张卡的两种写法折成一项）；解析不到的原样进 missing。
    merge 作业文件与 ``merged_into`` 父指针只认主键——工作编号进 lineage 会让
    canonical()/thread 跳链落空。"""
    keys: list = []
    missing: list = []
    for ref in _distinct_strs(refs):
        req = resolve(ref)
        if req is None:
            missing.append(ref)
        elif req.id not in keys:
            keys.append(req.id)
    return keys, missing


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
    Source de-duplication happens separately in :func:`dedupe_sources`.
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


def capture_source(who: str, channel: str, quote: str,
                   capture_id: Optional[str] = None) -> dict:
    """The birth ``sources[]`` row of an owner/agent capture (§10).

    ``capture_id`` (issue #7, add-only) = the inbox file stem of the capture
    that minted the card (``capture-<uuid>``; the server hands the same stem
    back to the web as ``file`` minus ``.json``). It rides on the source row so
    the dashboard can project a card-level ``capture_id`` and a client can match
    "the card born from MY input" by id instead of guessing from a title prefix.
    Absent (Slack self-DM, no inbox file) → key omitted. Not part of
    :func:`dedupe_sources`' key (channel/date/ref|quote) — fold semantics
    are unchanged."""
    row = {"who": who, "channel": channel,
           "date": _dt.date.today().isoformat(), "quote": quote}
    if capture_id:
        row["capture_id"] = capture_id
    return row


def dedupe_sources(existing: list, incoming: list) -> tuple[list, int]:
    """Append incoming sources not already present. Returns (merged, added_count).

    Public since §62 (防腐 #2：跨模块引用 `_私名` = 当场升 public)——the
    fold/merge sites in actd, quick_capture, silent_merge and maintenance all
    union sources through this one key."""
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


def _stamp_origin(req: Requirement) -> None:
    """盖/刷新出身信任章（amendments §50 / M1.a）：铸卡与一切 fold/re-raise
    都经过本文件的漏斗，sources 一变章就重算——最小信任者定卡（手打卡被
    slack/gmail 来源并入即降 external）。调度侧不读章、每次从 sources 现算
    （policy.may_auto_dispatch）；章只服务投影/审计。"""
    req.origin_trust = policy.classify_origin(req.sources)


def _fold_hit(target: Requirement, new_req: Optional[Requirement],
              note: str = "", sources: Optional[list] = None) -> Requirement:
    """Fold a hit into ``target``: note + deduped sources + mentions bump.

    Mirrors quick_capture's ``_fold_into`` so both the deterministic and LLM
    re-raise paths add the same ``[radar]`` note tag and dedupe identically."""
    src = sources if sources is not None else (
        new_req.sources if new_req is not None else None)
    merged, added = dedupe_sources(target.sources or [], src or [])
    target.sources = merged
    if added:
        target.repeated_mentions = int(target.repeated_mentions or 1) + added
    _stamp_origin(target)                     # 并入新来源 → 章过期，重盖
    append_fold_note(target, note, "radar")   # §38: timestamped + deduped
    save(target)
    return target


def reraise_or_followup(parent: Requirement, new_req: Requirement, *,
                        same_task: bool, actionable: Optional[bool] = None,
                        sources: Optional[list] = None, note: str = "",
                        cap_detected: bool = False,
                        ) -> tuple[Optional[str], Optional[Requirement]]:
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
    ``cap_detected`` = §45 LIMITED 天花板：re-raise 的翻回与 follow-up 子卡都
    只落 detected/备选（不通知、自然过期），不得借完结卡命中把候选抬进提案列
    ——出生资格 gate 非 FULL 时由调用方传 True，fold 类结果不受影响。
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
        merged, _added = dedupe_sources(
            parent.sources or [],
            (sources if sources is not None else new_req.sources) or [])
        parent.sources = merged
        parent.repeated_mentions = int(parent.repeated_mentions or 1) + 1
        _stamp_origin(parent)          # M1.a：fold 并入新来源后刷新信任章
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
        merged, _added = dedupe_sources(
            parent.sources or [],
            (sources if sources is not None else new_req.sources) or [])
        parent.sources = merged
        parent.repeated_mentions = int(parent.repeated_mentions or 1) + 1
        _stamp_origin(parent)          # M1.a：re-raise 折入新来源同样重盖
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
        parent.set_status(State.DETECTED if cap_detected else State.CARD_SENT)
        return "reraised", upsert(parent)

    # different task, same thread -> distinct follow-up child (card_sent),
    # inheriting the thread lineage; the old card's title is left untouched.
    summary = str(new_req.summary or new_req.title or note).strip()
    child = Requirement(
        id=next_id(),
        title=(new_req.title or note or parent.title)[:80],
        type=new_req.type or parent.type,
        tier=new_req.tier or parent.tier,
        status=State.DETECTED.value if cap_detected else State.CARD_SENT.value,
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
    _stamp_origin(child)               # §50：follow-up 子卡按自身 sources 盖章
    return "follow_up", upsert(child)


def merge_or_new(new_req: Union[Requirement, dict], *, high_confidence: bool = False,
                 cap_detected: bool = False) -> Requirement:
    """Reconcile a freshly-extracted requirement against the registry.

    Signature frozen (pre-§40; ``cap_detected`` is a §45 add-only kwarg with
    a no-op default); pure delegate — callers that need the reconciliation
    OUTCOME use :func:`merge_or_new_with_kind`.
    """
    return merge_or_new_with_kind(
        new_req, high_confidence=high_confidence, cap_detected=cap_detected)[1]


def merge_or_new_with_kind(
    new_req: Union[Requirement, dict], *, high_confidence: bool = False,
    cap_detected: bool = False,
) -> tuple[str, Requirement]:
    """:func:`merge_or_new` plus the reconciliation OUTCOME (§40 additive seam).

    Parent selection (v0.20.0 §3.4): a STRONG external ``thread_key`` match wins
    first, then the legacy title heuristic. When the matched parent is RESOLVED
    (delivered/merged, non-archived) the reconciliation is delegated to
    :func:`reraise_or_followup` (re-raise the card, or open a thread-lineage
    follow-up for a different task in the same thread; ``cap_detected`` rides
    along — §45 非 FULL 来源的天花板同样约束这条内部路径). Open parents keep the
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
            # cap_detected 必须跟进：§45 LIMITED 的 new_proposal 命中完结卡标题
            # 时走的就是这条内部 re-raise/follow-up——没有它，LIMITED 的天花板
            # 会被这条路径穿透（P1-2b，正好复活 R-020/R-093 回声环）。
            kind, res = reraise_or_followup(
                parent, new_req, same_task=same_task,
                sources=new_req.sources,
                note=(new_req.summary or new_req.title),
                cap_detected=cap_detected)
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
                _stamp_origin(child)   # §50：增量子卡按自身 sources 盖章
                return "proposed", upsert(child)
            # pure restatement -> merge sources, bump count, keep status
            merged, added = dedupe_sources(parent.sources or [], new_req.sources or [])
            parent.sources = merged
            if added:
                parent.repeated_mentions = int(parent.repeated_mentions or 1) + added
            # 盖章刷新（amendments M1.a）：fold 并入新来源后章会过期——最小
            # 信任者定卡（手打卡被 slack/gmail 来源并入即降 external）。铸卡
            # 与 fold 都走这个漏斗，章集中在这里盖：调度侧仍每次从 sources
            # 现算，章只服务投影/审计。
            _stamp_origin(parent)
            return "folded", upsert(parent)

    # brand new — self-root the thread on its own id
    new_req.id = new_req.id or next_id()
    new_req.thread_id = new_req.thread_id or new_req.id
    _stamp_origin(new_req)             # §50：铸卡即盖出身信任章
    if not new_req.status or new_req.status == State.DETECTED.value:
        if high_confidence and new_req.hardness == "hard" and new_req.deadline:
            new_req.status = State.CARD_SENT.value
        else:
            new_req.status = State.DETECTED.value
    new_req.repeated_mentions = int(new_req.repeated_mentions or 1)
    return "proposed", upsert(new_req)
