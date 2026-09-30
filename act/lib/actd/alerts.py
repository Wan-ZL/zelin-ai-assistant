"""alerts — (d) what the daemon tells the owner after a pass: board transition
notifications, credential-failure scan of executing logs, and the §48 radar
liveness patrol with its sleep/wake grace.

CONTRACT §40（新卡批量通知 ≥3 张合一条；digest 铸的卡由 digest 自己宣布）/
§11 + §30 + §46.3（待验收就绪通知：from_review 回流与 #119 中断收割不发）/
§48 + §48.2 + §48.3（开着的源死了要响、关掉的源全静默、无基线兜底、睡醒宽限）/
§28（通知偏好：两道失败扫描的 ``suppressed`` 形参——失败类被静音时照跑、
不花 anti-nag 台账）/ §76.3（结算信号的三条一次性翻面升级：疑似已完成 /
截止未批 / 被提 N 次仍未处理）/ §78（提案车道退役：新卡与结算通知改看
``debt[]`` = 潜在任务列）/ §45（LIMITED 出生静默：带 ``quiet_birth`` 的新行
落列但不响）。
"""
from __future__ import annotations

import datetime as _dt
import time
from pathlib import Path
from typing import Optional

from act.lib import config, notify, radar_health, sources
from act.lib.actd.seam import Daemon
from act.lib.registry import State, load_all

# --------------------------------------------------------------------------- #
# (d) transition detection
# --------------------------------------------------------------------------- #
# §40: more than this many fresh cards in one pass collapse to one
# notification (msg_new_cards_batch). At 1-2 the per-card copy is still the
# more useful one — it names the ask.
NEW_CARD_BATCH_ABOVE = 2

# §78：机器卡一律落潜在任务列，所以「有新卡了」「这张该拍一下了」这两类通知
# 的差分源从退役的 ``needs_approval[]`` 换成 ``debt[]``。这一行是整次退役里
# 最容易静默失效的一处：键不改名、投影照常出、只是永远空着——不换的话 §40
# 新卡通知与 §76.3 结算升级会**无声地**全部停发。
_CARD_LANE = "debt"


def by_id(items: list) -> dict:
    return {i["id"]: i for i in items if i.get("id")}


def detect_transitions(prev: Optional[dict], curr: dict) -> list:
    """Return (title, body, req_id, kind) notifications for prev->curr transitions.

    req_id is None for the §40 batched new-cards entry (it names no single
    card); every other class carries the card id. kind (v0.46, add-only) tags
    the transition class for per-event user preferences — "review_ready" (the
    完成提醒 off/banner/sound switch) and "proposal" (new cards, 回锅, and the
    §76.3 settlement escalations；持久化的偏好键名不动——§78 退的是车道，
    不是存量偏好 token）。"""
    if prev is None:
        return []
    # §78：机器卡的那一列 = 潜在任务（``debt[]``）。``needs_approval[]`` 仍在
    # wire 上，但恒为空——继续从它差分等于永远没有新卡、永远没有结算升级。
    p_card, c_card = by_id(prev.get(_CARD_LANE, [])), by_id(curr.get(_CARD_LANE, []))
    p_run = by_id(prev.get("running", []))
    p_rev, c_rev = by_id(prev.get("review", [])), by_id(curr.get("review", []))
    # 3-tuples (title, body, req); req is carried for caller compatibility (the
    # phone ✅-reaction approval surface was removed in v0.21 — Mac app only).
    msgs = _new_card_msgs(p_card, c_card)
    msgs.extend(_settlement_msgs(p_card, c_card))
    msgs.extend(_review_ready_msgs(p_run, p_rev, c_rev))
    # 「executing -> blocked」的需输入通知类：retired v0.48.8（#119）。受阻
    # 会话不再投影「需输入」，msg_needs_input 随之退役；仍会出现在
    # needs_input[] 的只剩 §4 派发刹车行（executor 已发 msg_dispatch_halted）。
    return msgs


def _from_weekly_digest(item: dict) -> bool:
    """Cards filed by the weekly digest are skipped entirely: its own
    notification already announced them by count (「另有 N 条自动化建议进了
    潜在任务」) — re-announcing them here (per-card or batched) was a
    duplicate ping every suggestion-bearing Monday. Seam = the row's source
    channel (weekly_digest.SOURCE_CHANNEL rides the dashboard projection).

    追记（§78）：周报早已不再铸卡，这个判据事实上是**死代码**。留着不删是
    因为它零成本、且只要有一张带 weekly-digest 来源的存量卡还在列里（或哪天
    周报再开始铸卡），它就仍是对的；真要退役得连 SOURCE_CHANNEL 一起走
    tombstone（防腐 #6），不在本次退役的范围内。"""
    return any(isinstance(s, dict) and s.get("channel") == "weekly-digest"
               for s in item.get("sources") or [])


def _quiet_birth(item: dict) -> bool:
    """§45 / §78 D80.7：这张卡出生自 LIMITED 信任的来源——落进潜在任务列，
    但**不响**。回声环的那一刀在提案列退役后就靠这一个键继续可观测：卡照样
    可见（没有一张卡因为静默而隐形），只是不来打断 owner。``dashboard``
    的 ``_proposal_extras`` 用 add-only 语义发它（假/缺席 = 整键不出），所以
    FULL 出生与存量卡一律照常走 §40 的新卡通知。"""
    return bool(item.get("quiet_birth"))


def _new_card_msgs(p_card: dict, c_card: dict) -> list:
    """新卡进潜在任务列（§78；此前是提案列）——a re-raised card (v0.20.0
    「回锅」) uses the Returned copy so Zelin knows it's a card he already
    accepted, not a brand-new find.
    §40 batching: >2 fresh (non-reraised) cards in one pass collapse to
    ONE batched ping (文案 truth = ``notify.msg_new_cards_batch``) — a radar
    backfill used to fire n pings in a row. 回锅 stays per-card (each names a prior decision of the user's), as
    do the 待验收 classes. The §28 relay queue's 10-min stale sweep is
    untouched — one batched entry ages out like any other."""
    msgs: list = []
    fresh: list = []
    for rid, item in c_card.items():
        if _no_ping_owed(rid, item, p_card):
            continue
        reraised = _reraised_msg(rid, item)
        if reraised is not None:
            msgs.append(reraised)
        elif not _from_weekly_digest(item):   # digest cards: announced by the digest itself
            fresh.append((rid, item))
    msgs.extend(_fresh_card_msgs(fresh))
    return msgs


def _no_ping_owed(rid: str, item: dict, p_card: dict) -> bool:
    """这一行欠不欠一次打断：上一帧就在了（不是新卡），或者它安静出生。

    ``quiet_birth`` = §45 LIMITED 一类的出生事实（§78 D80.7）——回锅同理：
    静默来源的卡不因为被重述一次就获得打断 owner 的资格。"""
    return rid in p_card or _quiet_birth(item)


def _reraised_msg(rid: str, item: dict):
    """回锅卡的那条通知（v0.20.0「回锅」文案）；不是回锅卡给 None。"""
    if not item.get("reraised"):
        return None
    t, b = notify.msg_reraised(item.get("title", rid),
                               item.get("reraised_note") or "")
    return (t, b, rid, notify.KIND_PROPOSAL)


def _fresh_card_msgs(fresh: list) -> list:
    if len(fresh) > NEW_CARD_BATCH_ABOVE:
        t, b = notify.msg_new_cards_batch(len(fresh))
        return [(t, b, None, notify.KIND_PROPOSAL)]
    msgs = []
    for rid, item in fresh:
        t, b = notify.msg_new_card(item.get("title", rid))
        msgs.append((t, b, rid, notify.KIND_PROPOSAL))
    return msgs


def _settlement_msgs(p_card: dict, c_card: dict) -> list:
    """§76.3 三条结算升级：每条都是 false→true 的**一次性**翻面。

    只看**两个快照里都在**的潜在任务行（§78 之前是提案行；新卡由 §40 的新卡
    通知负责，一张出生即带信号的卡不许在新卡通知之外再响第二声）。翻面判据 =
    上一版为假 / 缺席、这一版为真——之后每个 pass 的 dashboard 里信号恒为真，
    却再也不会响：投影是幂等的，通知不是。actd 重启（prev=None）整轮不发
    （`detect_transitions` 的既有约定），所以「重启即重播」不会发生。三条都用
    `KIND_PROPOSAL`——它们催的是同一件事：这张卡该被拍一下了。两个派生 bool
    自 §78 / D80.8 起就长在潜在任务行上（``dashboard._backlog_row``），不换
    车道就是三条升级一起哑掉。
    """
    msgs: list = []
    for rid, item in c_card.items():
        prev = p_card.get(rid)
        if prev is None:
            continue
        name = item.get("title", rid)
        if _flipped(prev, item, "completion_hint"):
            t, b = notify.msg_completion_hint(name)
            msgs.append((t, b, rid, notify.KIND_PROPOSAL))
        if _flipped(prev, item, "decision_due"):
            t, b = notify.msg_deadline_due(name)
            msgs.append((t, b, rid, notify.KIND_PROPOSAL))
        if _flipped(prev, item, "mention_escalated"):
            t, b = notify.msg_repeated_unhandled(name, _repeated(item))
            msgs.append((t, b, rid, notify.KIND_PROPOSAL))
    return msgs


def _flipped(prev: dict, curr: dict, key: str) -> bool:
    """`key` 从假/缺席翻成真（旧 dashboard 没有这些键 = 假，升级后第一个 pass
    照常翻一次面并响一次——这是诚实的「第一次看见」，不是重播）。"""
    return bool(curr.get(key)) and not bool(prev.get(key))


def _repeated(item: dict) -> int:
    try:
        return int(item.get("repeated") or 0)
    except (TypeError, ValueError):
        return 0


def _fresh_delivery(rid, item: dict, p_run: dict, p_rev: dict) -> bool:
    """executing -> review (§11 draft ready, awaiting acceptance) — but not a
    §30 from_review re-run settling back, nor a #119 interrupted harvest."""
    if rid in p_rev or rid not in p_run:
        return False
    # §30 v0.28.1: skip when the previous running row was a `from_review`
    # re-run (an already-delivered 待验收 card whose attach-reactivated
    # session settled back to review). It was NOT a fresh delivery — on
    # main it never left review[] and never notified — so re-firing
    # "待验收：AI 已交付草稿" on every working↔idle bounce is spurious spam.
    if p_run.get(rid, {}).get("from_review"):
        return False
    # #119（§46.3 v0.48.8）：interrupted 收割行（受阻/放弃救活收进
    # 待验收）已由 reconcile 发过精确文案（msg_review_interrupted /
    # msg_resume_storm / msg_auto_resume_exhausted）——「AI 已交付
    # 草稿」对一次中断收割是虚报，跳过。
    return not item.get("interrupted")


def _review_ready_msgs(p_run: dict, p_rev: dict, c_rev: dict) -> list:
    msgs = []
    for rid, item in c_rev.items():
        if _fresh_delivery(rid, item, p_run, p_rev):
            t, b = notify.msg_review_ready(item.get("name") or rid)
            msgs.append((t, b, rid, notify.KIND_REVIEW_READY))
    return msgs


# --------------------------------------------------------------------------- #
# credential failures in executing logs
# --------------------------------------------------------------------------- #
def _executing_log_text(req, notified: set) -> Optional[str]:
    """The log text of an executing card not yet notified; None = skip."""
    if req.status != State.EXECUTING.value or req.id in notified:
        return None
    log = (req.execution or {}).get("log")
    if not log:
        return None
    try:
        return Path(log).read_text(encoding="utf-8")
    except OSError:
        return None


def check_auth_failures(notified: set, suppressed: bool = False) -> list:
    """Scan executing items' logs for credential failures (notify once each).

    ``suppressed``（§28 追记 2026-09-12，issue #29）= 失败类此刻被用户的开关
    静音。扫描照跑（返回的消息仍交给 notify，由写方吃掉），但 anti-nag 台账
    **不落笔**：一条没人看见的通知不许把台账花掉，否则开关翻回来时这张卡的
    凭证告警在本进程余生里都不会再响。"""
    msgs: list = []
    for req in load_all():
        text = _executing_log_text(req, notified)
        if text is not None and notify.detect_auth_failure(text):
            if not suppressed:
                notified.add(req.id)
            msgs.append(notify.msg_auth(req.title or "claude"))
    return msgs


# --------------------------------------------------------------------------- #
# §48 radar liveness + sleep/wake grace
# --------------------------------------------------------------------------- #
# §48 睡醒宽限：合盖 ≥ 阈值的睡眠唤醒后，actd 的第一批 pass 必然早于雷达补跑
# （launchd/cron 也刚醒），health 时间戳整体超期 —— 没有宽限就是每天醒来一轮
# 假「源死亡」告警，anti-nag 台账防不了这种每日重置。检测**挂起时长**
# （wall-clock 前进量减去 monotonic 前进量——真睡眠 wall 走 mono 停；长 pass
# 两钟同进、差值 ≈ 0，不会被误判成睡醒），宽限一个最大雷达周期
# （obsidian cron */30 = 1800s）+ 余量，让雷达先补跑再恢复评判。
WAKE_JUMP_FACTOR = 6            # 挂起 > interval×6 视为睡醒
WAKE_JUMP_FLOOR_SECONDS = 300   # interval 很小时的挂起判定下限
WAKE_GRACE_SECONDS = 35 * 60    # 最大雷达周期 1800s + 余量（对齐 Diagnostics）
WAKE_STATE: dict = {"last_pass": None, "last_mono": None, "grace_until": 0.0}

# §48.3 无基线首见台账（进程内，src → wall ts）：源开着、health 却从无任何
# 时间戳时记下首见时刻——持续无基线超 liveness 阈值同样按死亡告警。堵的是
# 「plist 写成但 launchctl load 失败」的安装死角：install.sh 吞掉 load 的
# stderr、修复回执只有设置面板路径会写，App 侧只见 plist 在 → 无修复卡，
# 而 is_stale 无基线返回 False → 告警侧也永久静默。新装机首个阈值窗内仍
# 静默（不能凭空宣布死亡，anti-nag 保留）；进程内存 → actd 重启重置，
# --once/cron 形态不承诺（与冷启动宽限同款免责）。
NO_BASELINE_SINCE: dict = {}


def _pass_interval(cfg: config.Config, interval: Optional[int]) -> int:
    """``--interval`` 优先于 config 的 poll_interval_seconds（缺省才回退）."""
    if interval is not None:
        return interval
    return int(getattr(cfg, "poll_interval_seconds", 10) or 10)


def _suspended_seconds(wall: float, mono: float, last, last_mono) -> float:
    """≈ 真实挂起时长：wall 前进量减去 mono 前进量；无 mono 基线回退纯 wall 差值."""
    elapsed = wall - (last or wall)
    if last_mono is not None:
        return elapsed - (mono - last_mono)
    return elapsed   # 旧判据兜底（无 mono 基线可比）


def wake_grace(cfg: config.Config, wall: float,
               interval: Optional[int] = None,
               mono: Optional[float] = None) -> bool:
    """记录本 pass 的时钟并判断是否处于睡醒/冷启动宽限期。

    进程首 pass（``last_pass`` 为 None）同睡醒对待：``WAKE_STATE`` 是进程内
    存，actd 重启后没有跳变可测，而关机 ≥ 阈值后开机（RunAtLoad）的第一个
    pass 同样必然早于雷达落笔——不宽限就是每源一条假死亡通知。代价只是
    重启/升级后真死亡多等一个宽限窗才报，可接受。

    ``interval`` = 主循环的**真实** pass 间隔（main 里 ``--interval`` 优先于
    config）——挂起判定必须吃它：只按 config 的 poll_interval_seconds 算的话，
    ``--interval 600`` 形态下每个正常 pass 都被判成睡醒、宽限永不结束、
    liveness 被静默饿死。缺省才回退 config 值。

    ``mono`` = 本 pass 的 monotonic 时钟读数（``time.monotonic()``，macOS 走
    mach_absolute_time，**睡眠期间停摆**；测试注入缝）。睡醒判据 = wall 前进
    量与 mono 前进量的**差值**（≈ 真实挂起时长）超过 max(interval×6, 300s)。
    只看 wall 跳变的旧判据会把「长 pass」（如 process_raising 的 claude 调用
    连续吃满 420s 超时）误判成睡醒——每轮都重置 ``grace_until``，宽限永不
    结束，真死亡的源永远不告警。长 pass 两个时钟同步前进，差值 ≈ 0，照常
    评判。任一侧 mono 读数缺失（首 pass / 旧状态）回退 wall 差值判据。
    """
    interval = _pass_interval(cfg, interval)
    if mono is None:
        mono = time.monotonic()
    last = WAKE_STATE["last_pass"]
    last_mono = WAKE_STATE.get("last_mono")
    WAKE_STATE["last_pass"] = wall
    WAKE_STATE["last_mono"] = mono
    jump = max(interval * WAKE_JUMP_FACTOR, WAKE_JUMP_FLOOR_SECONDS)
    if last is None or _suspended_seconds(wall, mono, last, last_mono) > jump:
        WAKE_STATE["grace_until"] = wall + WAKE_GRACE_SECONDS
    return wall < WAKE_STATE["grace_until"]


def _source_dead(src: str, entry, now: _dt.datetime, missing_since: dict) -> bool:
    if sources.has_baseline(entry):
        missing_since.pop(src, None)
        return sources.is_stale(src, entry, now)
    # 无基线兜底（§48.3）：is_stale 对无基线诚实地返回 False，
    # 但源开着却**持续**无基线本身就是死亡形态——首见即记账，
    # 超过同一 liveness 阈值仍无落笔则告警；首个阈值窗内静默
    # （新装机不误报，anti-nag）。
    first = missing_since.setdefault(src, now.timestamp())
    return (now.timestamp() - first) > sources.LIVENESS_THRESHOLDS[src]


def _judge_source(cfg: config.Config, src: str, entry, now: _dt.datetime, graced: bool,
                  notified: set, missing_since: dict, suppressed: bool = False) -> list:
    """One source's verdict this pass → [] or [one radar-dead message]."""
    if not sources.enabled(cfg, src):
        # 关着：清残留条目（条目不存在时 no-op、不写文件），出账。
        # 纪律豁免（radar.py _owns_health 的 cron 单写者门）：那道门
        # 防的是手动/launchd 语境误删 cron 的**真实健康**；源 disabled
        # 时 cron 写者自己也已静默（§48.2 入口 gate），条目只剩僵尸
        # ——actd 作为清理仲裁者收尾不与单写者门冲突。
        radar_health.remove_radar_health(src)
        notified.discard(src)
        missing_since.pop(src, None)
        return []
    if graced:
        return []    # 睡醒宽限：雷达还没来得及补跑，本 pass 不评判
    if not _source_dead(src, entry, now, missing_since):
        notified.discard(src)   # 恢复（或基线/无基线未超窗）→ 出账
        return []
    if src in notified:
        return []
    return _raise_radar_death(src, notified, suppressed)


def _raise_radar_death(src: str, notified: set, suppressed: bool) -> list:
    """已判定死亡、且不在 anti-nag 台账里的源 → [] 或 [一条源死亡通知]。

    告警落笔前复核 enabled（TOCTOU 收窄）：巡检开头读的 cfg 与 notify 之间
    用户可能刚关掉本源——关掉的源全静默是 §48.2 的硬承诺，宁可多读一次盘
    也不发这条。复核只走「即将告警」的罕见分支（源死亡 + 未在台账），稳态零
    额外 IO；关了就本 pass 静默，残留 health 条目留给下一 pass 的清理分支收尾。

    ``suppressed``（§28 追记 2026-09-12）：失败类被用户的开关静音时不落 anti-nag
    台账——本 pass 的清理 / 出账 / 无基线记账已经在 ``_judge_source`` 里做完，只有
    「报过了」这一笔不许记，否则用户把失败通知翻回来时这个源的死亡告警在本
    进程余生里都不会再响。"""
    if not sources.enabled(config.load_config(), src):
        return []
    if not suppressed:
        notified.add(src)
    hours = sources.LIVENESS_THRESHOLDS[src] // 3600
    return [notify.msg_radar_dead(src, hours)]


def check_radar_liveness(d: Daemon, notified: set,
                         now: Optional[_dt.datetime] = None,
                         interval: Optional[int] = None,
                         mono: Optional[float] = None,
                         missing_since: Optional[dict] = None,
                         suppressed: bool = False) -> list:
    """§48 雷达 liveness 巡检：开着的源死了要响，关掉的源全静默。

    配置**每次调用现读**（load_config 自身防崩）——actd 启动时冻结的 cfg 在
    App 翻开关后双向失真：关→开会每 pass 清掉活雷达刚写的 health 还复活假
    存活信号，开→关会对用户刚关的源发死亡告警。对每个 ``sources.enabled()``
    为真的源，比较 health 的 last_ok/last_attempt（取较新者）与
    ``sources.LIVENESS_THRESHOLDS``，超期 = 源死亡 → notify 一次。anti-nag
    台账（``notified``，与 auth_notified 同款进程内 set）：同一源只在**跨过**
    阈值那一刻报一次，恢复（不再 stale）即出账，下次再死才会再响。告警
    **落笔前再复核一次 enabled**（现读 config）——巡检开头到 notify 之间
    用户可能刚关掉该源（TOCTOU），关掉的源全静默优先于省一次盘读。睡醒宽限
    （``wake_grace``）期间不评判 stale、也不动台账。关掉的源不进循环，且
    顺手清掉残留 health 条目（生产上手删 plist 留下的僵尸 last_attempt
    记录）。**无基线兜底**：开着却从无 health 时间戳的源记首见时刻
    （``NO_BASELINE_SINCE``），持续无基线超同一阈值也按死亡告警——覆盖
    「plist 写成但 launchctl load 失败、雷达从未落笔」的安装死角。
    ``suppressed``（§28 追记 2026-09-12，issue #29）= 失败类此刻被用户的开关静音：
    **巡检照跑**（关着的源的僵尸 health 清理、恢复出账、无基线首见台账全在这条
    路径上，跳过扫描会让它们在开关关着期间停摆、开关翻回来时无基线时钟还从头
    起算），只是 anti-nag 台账不落笔，开关翻回来那一 pass 就能重报。
    ``now`` / ``mono`` / ``missing_since`` 是测试注入缝。Never raises。
    """
    msgs: list = []
    if missing_since is None:
        missing_since = NO_BASELINE_SINCE
    try:
        cfg = config.load_config()
        if now is None:
            now = _dt.datetime.now(_dt.timezone.utc)
        graced = wake_grace(cfg, now.timestamp(), interval, mono)
        data = radar_health.load_radar_health()
        for src in sources.SOURCES:
            msgs.extend(_judge_source(cfg, src, data.get(src), now, graced,
                                      notified, missing_since, suppressed))
    except Exception as e:  # noqa: BLE001 - 巡检绝不干掉主循环
        d.log(f"radar liveness check FAILED: {e}")
    return msgs
