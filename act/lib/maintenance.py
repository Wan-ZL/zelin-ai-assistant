"""maintenance — 每日维护：提案列 / 潜在任务列的去重合并 + 过时卡进回收站（CONTRACT §70）。

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

**待验收列的老化（§70.2 追记，issue #312 / owner 决策 D74）**：D10 的「待验收不碰」
自此只保留一半——待验收卡仍**不入簇**（永不被合并），但会被**唯一一条**规则
`review_stale` 判过时：闲置 ≥ `daily_loop.review_stale_days`（默认 14，0 = 关）的
待验收卡先盖一枚 add-only 执行戳 `review_stale_notified_at`（**不在**
:data:`_EXECUTION_STAMPS` 里——它不是活动，盖了也不该把闲置天数清零），并由本轮
**一条**汇总通知（§70.6 追记）告知；下一轮该戳满 20 小时才进回收站
（`stale:review_stale`，prev_status=review，照循环卡的 90 天保留期可恢复）。
待验收卡**只**过这一条规则：deadline_passed / diagnostic_expired / superseded /
idle 四条仍只认提案与潜在任务两列，否则 7 天的 deadline 规则会绕过这道两阶段闸。

纯 act.lib：只 import stdlib + act.lib（§58.3）；写 registry 的入口只有
actd 的 pass（act/lib/daily_loop.py 由 actd 调用），符合 §0 第 1 条单写者。
"""
from __future__ import annotations

import datetime as _dt
import itertools
import re
from email.utils import parsedate_to_datetime
from typing import Iterable, Optional

from act.lib import auto_merge, config, fold_receipts, notify, policy, registry
from act.lib.registry import Requirement, State

# 维护只碰的两列（D10：提案 + 潜在任务）
LANE_STATES = (State.DETECTED.value, State.CARD_SENT.value)
# 过时清扫走到的全部列（D74：两列 + 待验收；待验收只过 review_stale 一条规则，见 _rules）
SWEPT_STATES = LANE_STATES + (State.REVIEW.value,)
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
RULE_REVIEW_STALE = "review_stale"      # D74：待验收列唯一的过时规则（两阶段）

# 两阶段之间的最短间隔：第一遍盖戳 + 发汇总通知，戳满这么久的下一遍才归档。
# 循环一天只跑一次，20 < 24 保证「今天通知、明天归档」不被时钟漂移吃掉。
REVIEW_NOTICE_MIN_HOURS = 20
# 盖在 execution 上的 add-only 戳（**故意不在 _EXECUTION_STAMPS 里**：它不是活动）
REVIEW_NOTICE_STAMP = "review_stale_notified_at"

_TS_SUFFIX_RE = re.compile(r"#\d+$")


# --------------------------------------------------------------------------- #
# time helpers（与 actd._parse_iso 同口径 + 裸日期 + RFC-2822，全函数不 raise）
# --------------------------------------------------------------------------- #
def parse_iso(ts) -> Optional[_dt.datetime]:
    """ISO 8601（含 Z）→ aware UTC datetime；解析不了 → None。与 actd._parse_iso
    逐字同口径：fromisoformat 拒收的未补零月/日（`2026-8-1T10:00:00Z`）走
    strptime 兜底——§40.5 倒计时与 purge 判决共用这一把尺，少了兜底就会有
    「永不清」的卡被投影成有倒计时（或反过来）。"""
    if not ts:
        return None
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = _dt.datetime.strptime(str(ts).strip(), "%Y-%m-%dT%H:%M:%SZ")
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


def _invested_states(status: str) -> tuple:
    """本卡判「同簇有在跑的兄弟」时算数的状态集。

    D74 的挖洞：待验收卡自己就在 :data:`INVESTED_STATES` 里，同一个 thread 上的
    两张待验收卡会互相当成「兄弟还活着」——那样 `review_stale` 对线程孪生永远不
    生效（owner 板上 22 天的 R-245 / R-246 恰是一对）。所以判一张待验收卡时，
    保护罩只认 approved / executing 的兄弟；两列卡的保护罩一字不动。"""
    if status == State.REVIEW.value:
        return tuple(s for s in INVESTED_STATES if s != State.REVIEW.value)
    return INVESTED_STATES


def _has_invested_sibling(req: Requirement, reqs: Iterable[Requirement]) -> bool:
    states = _invested_states(str(req.status))
    return any(r.id != req.id and str(r.status) in states and _same_cluster(req, r)
               for r in reqs)


def _owner_invested(req: Requirement) -> bool:
    """preset 按钮卡 / 用户改过名 = owner 亲手碰过，永不自动判过时。"""
    return bool(req.preset) or bool(req.user_titled)


def _protected(req: Requirement, reqs: Iterable[Requirement], today: _dt.date) -> bool:
    """永不判过时的卡：不在三列（两列 + 待验收，D74）/ owner 碰过 / 未来 deadline /
    同簇有在跑的兄弟。"""
    if str(req.status) not in SWEPT_STATES or _owner_invested(req):
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


# --------------------------------------------------------------------------- #
# 待验收列的两阶段老化（D74 / §70.2 追记；issue #312）
# --------------------------------------------------------------------------- #
def _notice_state(req: Requirement) -> "tuple[str, Optional[_dt.datetime]]":
    """待验收老化戳的三态：``("none", None)`` 还没通知过 / ``("set", <when>)``
    通知过且时刻可解析 / ``("bad", None)`` 戳在但解析不了。

    ``bad`` 既不重新通知也不归档——「拿不准就不动」（§70.2）：重盖一次戳会把
    20 小时的闸门永远重置，而拿一个读不懂的时刻去归档是猜。"""
    raw = _dict(req.execution).get(REVIEW_NOTICE_STAMP)
    if raw in (None, ""):
        return "none", None
    when = parse_when(raw)
    return ("bad", None) if when is None else ("set", when)


def review_stale_due(idle: Optional[int], review_days: int) -> bool:
    """这张待验收卡闲置够久了（`review_stale_days <= 0` = 整条规则关掉）。"""
    return review_days > 0 and idle is not None and idle >= review_days


def _review_stale_rule(req: Requirement, idle: int, review_days: int,
                       now: _dt.datetime) -> Optional[str]:
    """第二阶段：闲置够久 **且** 通知戳已满 :data:`REVIEW_NOTICE_MIN_HOURS`。
    第一阶段（盖戳 + 汇总通知）在 :func:`sweep_review_notices` 里。"""
    if not review_stale_due(idle, review_days):
        return None
    state, stamped = _notice_state(req)
    if state != "set":
        return None
    return RULE_REVIEW_STALE if now - stamped >= _dt.timedelta(hours=REVIEW_NOTICE_MIN_HOURS) else None


def _rules(req: Requirement, reqs: list, today: _dt.date, idle: int,
           stale_days: int, review_days: int, now: _dt.datetime) -> Optional[str]:
    """规则链按列分叉（D74）：待验收卡**只**见 review_stale——四条老规则仍绑在
    :data:`LANE_STATES` 上，否则 7 天的 deadline_passed / superseded / 45 天的
    idle 会绕过两阶段闸门，把待验收卡无声归档。"""
    if str(req.status) == State.REVIEW.value:
        return _review_stale_rule(req, idle, review_days, now)
    return (_deadline_rule(req, today, idle)
            or _diagnostic_rule(req, idle)
            or _superseded_rule(req, reqs)
            or _idle_rule(req, idle, stale_days))


def stale_verdict(req: Requirement, reqs: list, today: _dt.date, stale_days: int,
                  review_days: int = 0, now: Optional[_dt.datetime] = None) -> Optional[str]:
    """过时规则 token（deadline_passed / diagnostic_expired / superseded / idle /
    review_stale）或 None（保留）。guards 先判；无可解析活动时间 = 拿不准 = None。
    ``review_days`` / ``now`` 是 D74 待验收老化的参数（缺省 = 那条规则关着）。"""
    if _protected(req, reqs, today):
        return None
    idle = _idle_days(req, today)
    if idle is None:
        return None
    return _rules(req, reqs, today, idle, stale_days, review_days,
                  now or _dt.datetime.now(_dt.timezone.utc))


def _safe_verdict(req, reqs, today, stale_days, review_days, now) -> Optional[str]:
    try:
        return stale_verdict(req, reqs, today, stale_days, review_days, now)
    except Exception:  # noqa: BLE001 - 坏字段 = 拿不准 = 不动
        return None


def _trash_stale(req: Requirement, rule: str) -> Optional[dict]:
    try:
        registry.trash(req, STALE_REASON_PREFIX + rule)
    except Exception:  # noqa: BLE001 - 一张坏卡不许崩整轮
        return None
    return {"id": req.id, "rule": rule, "display_id": registry.display_id(req)}


def sweep_stale(cfg: config.Config, today: Optional[_dt.date] = None,
                reqs: Optional[list] = None, now: Optional[_dt.datetime] = None) -> list:
    """三列里的过时卡 → 回收站（reason `stale:<rule>`）。返回
    ``[{"id", "rule", "display_id"}]``；单卡失败只丢那一张（宪法 11）。
    行形状与本函数的返回契约一字不变——D74 的 `review_stale` 因此直接落进
    daily_loop 审计行的 `trashed[]` 里（issue #312 第 1 条诉求）。"""
    today = today or _dt.date.today()
    now = now or _dt.datetime.now(_dt.timezone.utc)
    reqs = registry.load_all() if reqs is None else reqs
    stale_days = _int_attr(cfg, "daily_loop_stale_days")
    review_days = _int_attr(cfg, "daily_loop_review_stale_days")
    verdicts = [(r, _safe_verdict(r, reqs, today, stale_days, review_days, now)) for r in reqs]
    results = [_trash_stale(r, rule) for r, rule in verdicts if rule is not None]
    return [x for x in results if x is not None]


def _needs_review_notice(req: Requirement, reqs: list, today: _dt.date,
                         review_days: int) -> bool:
    """第一阶段的候选：待验收、没被保护罩挡住、闲置够久、还没盖过通知戳。"""
    if str(req.status) != State.REVIEW.value or _protected(req, reqs, today):
        return False
    if not review_stale_due(_idle_days(req, today), review_days):
        return False
    return _notice_state(req)[0] == "none"


def _safe_needs_notice(req: Requirement, reqs: list, today: _dt.date,
                       review_days: int) -> bool:
    try:
        return _needs_review_notice(req, reqs, today, review_days)
    except Exception:  # noqa: BLE001 - 一张坏卡不许崩整轮
        return False


def review_notice_candidates(cfg: config.Config, today: Optional[_dt.date] = None,
                             reqs: Optional[list] = None) -> list:
    """本轮该被「明天归档」通知点名的待验收卡（纯读，CLI 的 --plan 也用它）。"""
    today = today or _dt.date.today()
    reqs = registry.load_all() if reqs is None else reqs
    days = _int_attr(cfg, "daily_loop_review_stale_days")
    return [r for r in reqs if _safe_needs_notice(r, reqs, today, days)]


def _stamp_notice(req: Requirement, now: _dt.datetime) -> Optional[dict]:
    """盖 add-only 执行戳并落盘；失败只丢这一张。"""
    try:
        ex = dict(_dict(req.execution))
        ex[REVIEW_NOTICE_STAMP] = _iso_utc(now)
        req.execution = ex
        registry.save(req)
    except Exception:  # noqa: BLE001 - 一张坏卡不许崩整轮
        return None
    return {"id": req.id, "display_id": registry.display_id(req),
            "title": str(req.display_title or req.title or "")[:120]}


def _announce_review_stale(count: int, review_days: int, notifier) -> None:
    """整轮**一条**汇总通知（§70.6 追记）。不打 kind = §28 目录里的 `general`：
    不新增分类，安静时段照旧管得着它。永不 raise。"""
    title, body = notify.msg_review_stale(count, review_days)
    try:
        (notifier or notify.notify)(title, body)
    except Exception:  # noqa: BLE001 - 通知绝不崩循环
        pass


def sweep_review_notices(cfg: config.Config, today: Optional[_dt.date] = None,
                         reqs: Optional[list] = None, now: Optional[_dt.datetime] = None,
                         notifier=None) -> list:
    """第一阶段：给够久没动的待验收卡盖戳，并发**一条**汇总通知（§70.6 追记）。

    一卡一条横幅在 owner 的真板上是 19 条（宪法第 10 条「打扰要有资格」），所以
    通知按轮汇总：「N 张待验收卡 M 天没动，明天归档（可恢复）」。``notifier`` 是
    注入缝（防腐 #3：参数注入，绝不 module-global），缺省 :func:`notify.notify`。
    通知发不出去不回滚戳——闸门是戳，不是横幅（§70.6 追记的取舍）。"""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    cards = review_notice_candidates(cfg, today=today, reqs=reqs)
    rows = [x for x in (_stamp_notice(r, now) for r in cards) if x is not None]
    if rows:
        _announce_review_stale(len(rows), _int_attr(cfg, "daily_loop_review_stale_days"), notifier)
    return rows


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
