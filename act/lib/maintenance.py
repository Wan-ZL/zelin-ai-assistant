"""maintenance — 每日维护：提案列 / 潜在任务列的去重合并 + 过时卡进回收站（CONTRACT §65）。

Owner 决策 D10（docs/design/vnext2-plan.md）：

- **只碰两列**：提案（card_sent）与潜在任务（detected）。running / 待验收 /
  已交付 / raising 一律不动（「Running 就不要去重，毕竟它在跑」）。
- **同主题多卡 → 合成一张新卡**（不是并入主卡）：新卡 `merged_from[]` 记全部
  来源主键、sources 并集、former_titles 记旧名、每张旧卡一行 §38.2 fold note
  （带拆出句柄）；旧卡全部走 `registry.trash(reason="daily-merge: 并入 <new>")`
  ——prev_status 完整保留、回收站可恢复（宪法第 2 条），绝不用 §21 的 merged
  终态（store2 对 system 只放行 →trashed）。
- **过时卡 → 回收站**，reason `stale:<rule>`，可恢复；规则全部确定性、无 LLM
  （§34bis 判例：LLM 只许出报告不许动卡），拿不准（时间戳解析不了）就不动。
- 循环扔进回收站的卡保留期比手动 trash 更长（`daily_loop.trash_retention_days`
  默认 90 vs 60）——owner 没亲眼看过它们进回收站；`purge_at` 投影与
  actd.purge_trash 经同一个 :func:`retention_days` 判决（§40.5 倒计时诚实）。

纯 act.lib：只 import stdlib + act.lib（§58.3）；写 registry 的入口只有
actd 的 pass（act/lib/daily_loop.py 由 actd 调用），符合 §0 第 1 条单写者。
"""
from __future__ import annotations

import datetime as _dt
import itertools
import re
from email.utils import parsedate_to_datetime
from typing import Iterable, Optional

from act.lib import auto_merge, config, fold_receipts, policy, registry
from act.lib.registry import Requirement, State

# 维护只碰的两列（D10：提案 + 潜在任务）
LANE_STATES = (State.DETECTED.value, State.CARD_SENT.value)
# 「已投入」状态：同簇有这样的兄弟卡 = 事情还活着，不判过时
INVESTED_STATES = (State.APPROVED.value, State.EXECUTING.value, State.REVIEW.value)
# 「已完成」状态：同名卡在这里 = 本卡已被别处做掉（stale:superseded）
DONE_STATES = (State.DELIVERED.value, State.MERGED.value, State.ARCHIVED.value)

MERGE_REASON_PREFIX = "daily-merge: 并入 "
STALE_REASON_PREFIX = "stale:"
LOOP_TRASH_PREFIXES = (STALE_REASON_PREFIX, "daily-merge:")
FOLD_KIND = "radar"            # §38.2 冻结文法只认 radar|quick；机器折叠 = radar
RECEIPT_CHANNEL = "daily_loop"  # §44.6 回执的 channel 字面量

# stale 规则常量（Q4：45 天 + 保护罩；数字 truth = 本文件）
DEADLINE_GRACE_DAYS = 7        # deadline 过去 ≥7 天且此后无动静 → deadline_passed
DIAGNOSTIC_STALE_DAYS = 14     # §40.3/§47.2 诊断卡 14 天没动 → diagnostic_expired
PROTECT_MENTIONS = 3           # 提及 ≥3 次的卡不按 idle 判过时
MIN_TITLE_LEN = 6              # 归一标题相等判同题的最短长度（防「跟进」类短题误并）
DIAGNOSTIC_CHANNELS = ("radar-diagnostic", "radar-parse-degraded")

RULE_DEADLINE = "deadline_passed"
RULE_DIAGNOSTIC = "diagnostic_expired"
RULE_SUPERSEDED = "superseded"
RULE_IDLE = "idle"

_TS_SUFFIX_RE = re.compile(r"#\d+$")


# --------------------------------------------------------------------------- #
# time helpers（与 actd._parse_iso 同口径 + 裸日期 + RFC-2822，全函数不 raise）
# --------------------------------------------------------------------------- #
def parse_iso(ts) -> Optional[_dt.datetime]:
    """ISO 8601（含 Z）→ aware UTC datetime；解析不了 → None。"""
    if not ts:
        return None
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)


def _aware(dt: _dt.datetime) -> _dt.datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)


def _from_native(value) -> Optional[_dt.datetime]:
    """PyYAML 把裸 YYYY-MM-DD 解析成 date/datetime——两种原生对象直接收。"""
    if isinstance(value, _dt.datetime):
        return _aware(value)
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day, tzinfo=_dt.timezone.utc)
    return None


def parse_when(value) -> Optional[_dt.datetime]:
    """卡上任何时间字面量 → aware datetime：ISO / 裸 YYYY-MM-DD / RFC-2822
    （gmail 来源的 date）/ fold-note 的 `<ts>#n` 句柄 / PyYAML 原生 date。
    None = 解析不了。"""
    native = _from_native(value)
    if native is not None:
        return native
    text = _TS_SUFFIX_RE.sub("", str(value or "").strip())
    got = parse_iso(text)
    return got if got is not None else _parse_rfc2822(text)


def _parse_rfc2822(text: str) -> Optional[_dt.datetime]:
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    return _aware(dt) if dt is not None else None


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


_EXECUTION_STAMPS = ("approved_at", "dispatched_at", "review_at", "reraised_at", "accepted_at")


def _activity_candidates(req: Requirement) -> list:
    """卡上所有可能的活动时间字面量（来源日期 / 发卡 / 执行戳 / 折叠句柄）。"""
    out = [_dict(s).get("date") for s in (req.sources or [])]
    out.append(_dict(req.card).get("sent_at"))
    ex = _dict(req.execution)
    out.extend(ex.get(k) for k in _EXECUTION_STAMPS)
    out.extend(n.get("ts") for n in registry.parse_fold_notes(req.notes))
    return out


def last_activity(req: Requirement) -> Optional[_dt.datetime]:
    """卡的最近一次活动（来源日期 / 发卡 / 执行时间戳 / 折叠备注句柄的最大值）。
    None = 一个都解析不了 → 调用方按「拿不准就不动」处理。"""
    dts = [d for d in (parse_when(c) for c in _activity_candidates(req)) if d is not None]
    return max(dts) if dts else None


# --------------------------------------------------------------------------- #
# retention（§9 / §40.5：purge 与倒计时同一判决）
# --------------------------------------------------------------------------- #
def is_loop_trash(req: Requirement) -> bool:
    """循环自动扔进回收站的卡（stale:* / daily-merge:*）。"""
    return str(req.trash_reason or "").startswith(LOOP_TRASH_PREFIXES)


def _int_attr(cfg, name: str, default: int = 0) -> int:
    try:
        return int(getattr(cfg, name, default) or 0)
    except (TypeError, ValueError):
        return default


def purge_enabled(cfg: config.Config) -> bool:
    """§9 总开关：`trash.retention_days <= 0` = 永不自动硬删（循环卡也不清）。"""
    return _int_attr(cfg, "trash_retention_days") > 0


def retention_days(req: Requirement, cfg: config.Config) -> int:
    """这张回收站卡的保留天数；0 = 永不自动硬删。`trash.retention_days <= 0`
    是总开关：关掉后循环卡也不清。"""
    base = _int_attr(cfg, "trash_retention_days")
    if base <= 0:
        return 0
    if is_loop_trash(req):
        return _int_attr(cfg, "daily_loop_trash_retention_days", base)
    return base


def _purge_cutoff(req: Requirement, cfg: config.Config,
                  now: _dt.datetime) -> Optional[_dt.datetime]:
    """这张卡的硬删时刻（trashed_at + 保留期）；None = 永不（pinned / 保留期 0 /
    trashed_at 解析不了——与 §40.5 `purge_at` 为 null 的条件逐字一致）。"""
    days = retention_days(req, cfg)
    trashed = parse_iso(req.trashed_at)
    if req.permanent or days <= 0 or trashed is None:
        return None
    return trashed + _dt.timedelta(days=days)


def purge_at(req: Requirement, cfg: config.Config) -> Optional[str]:
    """§40.5 投影：ISO 硬删时刻或 None。"""
    when = _purge_cutoff(req, cfg, _dt.datetime.now(_dt.timezone.utc))
    return _iso_utc(when) if when is not None else None


def _iso_utc(dt: _dt.datetime) -> str:
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def purge_due(req: Requirement, cfg: config.Config,
              now: Optional[_dt.datetime] = None) -> bool:
    """actd.purge_trash 的逐卡判决：trashed 且硬删时刻已过。"""
    if req.status != State.TRASHED.value:
        return False
    now = now or _dt.datetime.now(_dt.timezone.utc)
    when = _purge_cutoff(req, cfg, now)
    return when is not None and when < now


# --------------------------------------------------------------------------- #
# stale rules（确定性；guards 先于规则）
# --------------------------------------------------------------------------- #
def _norm_title(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _same_title(a: Requirement, b: Requirement) -> bool:
    ta, tb = _norm_title(a.title), _norm_title(b.title)
    return len(ta) >= MIN_TITLE_LEN and ta == tb


def _has_future_deadline(req: Requirement, today: _dt.date) -> bool:
    try:
        return _dt.date.fromisoformat(str(req.deadline)) >= today
    except (TypeError, ValueError):
        return False


def _thread_root(r: Requirement) -> str:
    return str(r.thread_id or r.id)


def _lineage(a: Requirement, b: Requirement) -> bool:
    return a.improvement_of == b.id or b.improvement_of == a.id


def _same_cluster(a: Requirement, b: Requirement) -> bool:
    return _thread_root(a) == _thread_root(b) or _lineage(a, b)


def _has_invested_sibling(req: Requirement, reqs: Iterable[Requirement]) -> bool:
    return any(r.id != req.id and str(r.status) in INVESTED_STATES and _same_cluster(req, r)
               for r in reqs)


def _owner_invested(req: Requirement) -> bool:
    """preset 按钮卡 / 用户改过名 = owner 亲手碰过，永不自动判过时。"""
    return bool(req.preset) or bool(req.user_titled)


def _protected(req: Requirement, reqs: Iterable[Requirement], today: _dt.date) -> bool:
    """永不判过时的卡：不在两列 / owner 碰过 / 未来 deadline / 同簇有在跑的兄弟。"""
    if str(req.status) not in LANE_STATES or _owner_invested(req):
        return True
    return _has_future_deadline(req, today) or _has_invested_sibling(req, reqs)


def _idle_days(req: Requirement, today: _dt.date) -> Optional[int]:
    last = last_activity(req)
    if last is None:
        return None
    return (today - last.date()).days


def _deadline_rule(req: Requirement, today: _dt.date, idle: int) -> Optional[str]:
    try:
        deadline = _dt.date.fromisoformat(str(req.deadline))
    except (TypeError, ValueError):
        return None
    passed = (today - deadline).days
    if passed >= DEADLINE_GRACE_DAYS and idle >= DEADLINE_GRACE_DAYS:
        return RULE_DEADLINE
    return None


def _channels(req: Requirement) -> list:
    return [str(s.get("channel") or "") for s in (req.sources or []) if isinstance(s, dict)]


def _diagnostic_rule(req: Requirement, idle: int) -> Optional[str]:
    chans = _channels(req)
    if chans and all(c in DIAGNOSTIC_CHANNELS for c in chans) and idle >= DIAGNOSTIC_STALE_DAYS:
        return RULE_DIAGNOSTIC
    return None


def _done_twin(req: Requirement, other: Requirement) -> bool:
    if other.id == req.id or str(other.status) not in DONE_STATES:
        return False
    return _same_title(req, other) and not _lineage(req, other)


def _superseded_rule(req: Requirement, reqs: Iterable[Requirement]) -> Optional[str]:
    """同名卡已 delivered/merged/archived（且不是本卡的增量血缘）= 事情在别处做完了。"""
    return RULE_SUPERSEDED if any(_done_twin(req, r) for r in reqs) else None


def _mentions(req: Requirement) -> int:
    try:
        return int(req.repeated_mentions or 1)
    except (TypeError, ValueError):
        return 1


def _idle_rule(req: Requirement, idle: int, stale_days: int) -> Optional[str]:
    if stale_days <= 0 or _mentions(req) >= PROTECT_MENTIONS:
        return None
    return RULE_IDLE if idle > stale_days else None


def _rules(req: Requirement, reqs: list, today: _dt.date, idle: int,
           stale_days: int) -> Optional[str]:
    return (_deadline_rule(req, today, idle)
            or _diagnostic_rule(req, idle)
            or _superseded_rule(req, reqs)
            or _idle_rule(req, idle, stale_days))


def stale_verdict(req: Requirement, reqs: list, today: _dt.date,
                  stale_days: int) -> Optional[str]:
    """过时规则 token（deadline_passed / diagnostic_expired / superseded / idle）
    或 None（保留）。guards 先判；无可解析活动时间 = 拿不准 = None。"""
    if _protected(req, reqs, today):
        return None
    idle = _idle_days(req, today)
    return _rules(req, reqs, today, idle, stale_days) if idle is not None else None


def _safe_verdict(req, reqs, today, stale_days) -> Optional[str]:
    try:
        return stale_verdict(req, reqs, today, stale_days)
    except Exception:  # noqa: BLE001 - 坏字段 = 拿不准 = 不动
        return None


def _trash_stale(req: Requirement, rule: str) -> Optional[dict]:
    try:
        registry.trash(req, STALE_REASON_PREFIX + rule)
    except Exception:  # noqa: BLE001 - 一张坏卡不许崩整轮
        return None
    return {"id": req.id, "rule": rule, "display_id": registry.display_id(req)}


def sweep_stale(cfg: config.Config, today: Optional[_dt.date] = None,
                reqs: Optional[list] = None) -> list:
    """两列里的过时卡 → 回收站（reason `stale:<rule>`）。返回
    ``[{"id", "rule", "display_id"}]``；单卡失败只丢那一张（宪法 11）。"""
    today = today or _dt.date.today()
    reqs = registry.load_all() if reqs is None else reqs
    stale_days = _int_attr(cfg, "daily_loop_stale_days")
    verdicts = [(r, _safe_verdict(r, reqs, today, stale_days)) for r in reqs]
    results = [_trash_stale(r, rule) for r, rule in verdicts if rule is not None]
    return [x for x in results if x is not None]


# --------------------------------------------------------------------------- #
# dedup clusters → one synthesized card
# --------------------------------------------------------------------------- #
def _mergeable(req: Requirement) -> bool:
    return str(req.status) in LANE_STATES and not req.preset


def _same_topic(a: Requirement, b: Requirement, cfg) -> bool:
    """同题 = 归一标题相等，或 §38.3 的「高度相似」信号（score ≥ HIGH_SCORE +
    ≥3 个强证据 token）。「同一联系人 + 中等重合」那条只够触发 LLM 复核，不够
    直接合并——这里没有判官，宁可留重复卡，不可错并（§44.1 同一保守原则）。"""
    if auto_merge.linked(a, b) or _same_cluster(a, b):
        return False   # 血缘/同 thread/拆出卡是刻意相关，不是重复（thread 缺省 = 自根）
    if _same_title(a, b):
        return True
    return auto_merge.is_near_dupe(a, b, cfg)[2] == "high"


def _union_find(cands: list, cfg) -> dict:
    parent = {r.id: r.id for r in cands}

    def root(x):
        while parent[x] != x:
            x = parent[x]
        return x

    for a, b in itertools.combinations(cands, 2):
        if _same_topic(a, b, cfg):
            parent[root(a.id)] = root(b.id)
    return {r.id: root(r.id) for r in cands}


def find_clusters(reqs: Iterable[Requirement], cfg=None) -> list:
    """两列内的同题簇（≥2 张），簇内按主键序号升序。确定性：归一标题相等，
    或 §38.3 的 is_near_dupe 双信号；血缘相连的卡永不同簇。"""
    cands = sorted((r for r in reqs if _mergeable(r)), key=lambda r: registry.id_sort_key(r.id))
    roots = _union_find(cands, cfg)
    groups: dict = {}
    for r in cands:
        groups.setdefault(roots[r.id], []).append(r)
    return [g for g in groups.values() if len(g) >= 2]


def _primary(cluster: list) -> Requirement:
    """新卡的「主稿」：用户改过名 > 提及最多 > 最新出生。"""
    return max(cluster, key=lambda r: (bool(r.user_titled), int(r.repeated_mentions or 1),
                                       registry.id_sort_key(r.id)))


def _merged_status(cluster: list) -> str:
    if any(str(r.status) == State.CARD_SENT.value for r in cluster):
        return State.CARD_SENT.value
    return State.DETECTED.value


def _merged_hardness(cluster: list) -> str:
    return "hard" if any(r.hardness == "hard" for r in cluster) else "soft"


def _earliest_deadline(cluster: list) -> Optional[str]:
    ds = sorted(str(r.deadline) for r in cluster if r.deadline)
    return ds[0] if ds else None


def _first(cluster: list, attr: str):
    for r in cluster:
        v = getattr(r, attr, None)
        if v not in (None, "", []):
            return v
    return None


def _common_thread_key(cluster: list) -> Optional[str]:
    keys = {r.thread_key for r in cluster}
    return keys.pop() if len(keys) == 1 else None


def _union_sources(cluster: list) -> list:
    merged: list = []
    for r in cluster:
        merged, _ = registry.dedupe_sources(merged, r.sources or [])
    return merged


def _names_of(r: Requirement) -> list:
    raw = [r.display_title, r.title] + list(r.former_titles or [])
    return [t for t in (str(x or "").strip() for x in raw) if t]


def _former_titles(cluster: list, primary: Requirement) -> Optional[list]:
    """旧名并集（去重、保序、cap = registry.FORMER_TITLES_CAP）；超出 cap 的旧名
    仍逐字活在 fold note 里（§37「旧名仍可搜索」）。"""
    keep_out = _shown_title(primary)
    names: list = []
    for t in itertools.chain.from_iterable(_names_of(r) for r in cluster):
        if t != keep_out and t not in names:
            names.append(t)
    return names[-registry.FORMER_TITLES_CAP:] or None


def _shown_title(r: Requirement) -> str:
    return str(r.display_title or r.title or "").strip()


def _fold_line(old: Requirement) -> str:
    title = _shown_title(old)
    body = str(old.summary or "").strip() or title
    return f"每日整理并入 {old.id}「{title}」：{body}"


def plan_merge(cluster: list) -> Requirement:
    """把一簇旧卡合成一张**未落盘**的新卡（纯函数，除 next_id 一次读）。"""
    olds = sorted(cluster, key=lambda r: registry.id_sort_key(r.id))
    primary = _primary(olds)
    oldest = olds[0]
    new = Requirement(
        id=registry.next_id(), title=primary.title, type=primary.type, tier=primary.tier,
        status=_merged_status(olds), hardness=_merged_hardness(olds),
        deadline=_earliest_deadline(olds),
        repeated_mentions=sum(int(r.repeated_mentions or 1) for r in olds),
        green_sign_required=any(bool(r.green_sign_required) for r in olds),
        cost_estimate_usd=_first(olds, "cost_estimate_usd"), sources=_union_sources(olds),
        plan=_first([primary] + olds, "plan"), summary=str(primary.summary or ""),
        definition_of_done=_first([primary] + olds, "definition_of_done"),
        target_repo=_first([primary] + olds, "target_repo"), delivery_mode=primary.delivery_mode,
        improvement_of=_first(olds, "improvement_of"), thread_id=_thread_root(oldest),
        thread_key=_common_thread_key(olds), display_title=primary.display_title,
        user_titled=bool(primary.user_titled), former_titles=_former_titles(olds, primary),
        merged_from=[r.id for r in olds],
    )
    new.origin_trust = policy.classify_origin(new.sources)   # §50：最小信任者定卡
    for old in olds:
        registry.append_fold_note(new, _fold_line(old), FOLD_KIND)   # §38.2 拆出句柄
    return new


def apply_merge(cluster: list) -> dict:
    """落盘一次合并：新卡先写（crash 只会多一张、绝不丢），旧卡逐张进回收站，
    卡对进 auto_merge 终局台账（恢复旧卡后 §38.3 不再建议并回），留 §44.6 回执。"""
    new = plan_merge(cluster)
    registry.upsert(new)
    for old in cluster:
        registry.trash(old, MERGE_REASON_PREFIX + new.id)
        auto_merge.record_pair_final(new.id, old.id)
    fold_receipts.record(new.id, RECEIPT_CHANNEL, note="|".join(r.id for r in cluster))
    return {"new": new.id, "from": [r.id for r in cluster],
            "title": str(new.display_title or new.title)}


def dedup_lanes(cfg: config.Config, reqs: Optional[list] = None) -> list:
    """两列去重：每簇 → 一张新卡 + 旧卡进回收站。返回 apply_merge 结果列表；
    单簇失败只丢那一簇。"""
    reqs = registry.load_all() if reqs is None else reqs
    out = []
    for cluster in find_clusters(reqs, cfg):
        try:
            out.append(apply_merge(cluster))
        except Exception:  # noqa: BLE001 - 一簇坏卡不许崩整轮
            continue
    return out
