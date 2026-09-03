"""alerts — (d) what the daemon tells the owner after a pass: board transition
notifications, credential-failure scan of executing logs, and the §48 radar
liveness patrol with its sleep/wake grace.

CONTRACT §40（新卡批量通知 ≥3 张合一条；digest 铸的卡由 digest 自己宣布）/
§11 + §30 + §46.3（待验收就绪通知：from_review 回流与 #119 中断收割不发）/
§48 + §48.2 + §48.3（开着的源死了要响、关掉的源全静默、无基线兜底、睡醒宽限）。
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
# §40: more than this many fresh proposals in one pass collapse to one
# notification (msg_new_cards_batch). At 1-2 the per-card copy is still the
# more useful one — it names the ask.
NEW_CARD_BATCH_ABOVE = 2


def by_id(items: list) -> dict:
    return {i["id"]: i for i in items if i.get("id")}


def detect_transitions(prev: Optional[dict], curr: dict) -> list:
    """Return (title, body, req_id, kind) notifications for prev->curr transitions.

    req_id is None for the §40 batched new-cards entry (it names no single
    card); every other class carries the card id. kind (v0.46, add-only) tags
    the transition class for per-event user preferences — today only
    "review_ready" (the 完成提醒 off/banner/sound switch); the rest ride None."""
    if prev is None:
        return []
    p_na, c_na = by_id(prev.get("needs_approval", [])), by_id(curr.get("needs_approval", []))
    p_run = by_id(prev.get("running", []))
    p_rev, c_rev = by_id(prev.get("review", [])), by_id(curr.get("review", []))
    # 3-tuples (title, body, req); req is carried for caller compatibility (the
    # phone ✅-reaction approval surface was removed in v0.21 — Mac app only).
    msgs = _new_card_msgs(p_na, c_na)
    msgs.extend(_review_ready_msgs(p_run, p_rev, c_rev))
    # 「executing -> blocked」的需输入通知类：retired v0.48.8（#119）。受阻
    # 会话不再投影「需输入」，msg_needs_input 随之退役；仍会出现在
    # needs_input[] 的只剩 §4 派发刹车行（executor 已发 msg_dispatch_halted）。
    return msgs


def _from_weekly_digest(item: dict) -> bool:
    """Cards filed by the weekly digest are skipped entirely: its own
    notification already announced them by count (「另有 N 条自动化建议进了
    待审批」) — re-announcing them here (per-card or batched) was a
    duplicate ping every suggestion-bearing Monday. Seam = the row's source
    channel (weekly_digest.SOURCE_CHANNEL rides the dashboard projection)."""
    return any(isinstance(s, dict) and s.get("channel") == "weekly-digest"
               for s in item.get("sources") or [])


def _new_card_msgs(p_na: dict, c_na: dict) -> list:
    """new card_sent — a re-raised card (v0.20.0「回锅」) uses the Returned copy
    so Zelin knows it's a card he already accepted, not a brand-new find.
    §40 batching: >2 fresh (non-reraised) proposals in one pass collapse to
    ONE 「新增 N 张待审批卡」 — a radar backfill used to fire n pings in a
    row. 回锅 stays per-card (each names a prior decision of the user's), as
    do the 待验收 classes. The §28 relay queue's 10-min stale sweep is
    untouched — one batched entry ages out like any other."""
    msgs: list = []
    fresh: list = []
    for rid, item in c_na.items():
        if rid in p_na:
            continue
        if item.get("reraised"):
            t, b = notify.msg_reraised(item.get("title", rid),
                                       item.get("reraised_note") or "")
            msgs.append((t, b, rid, None))
        elif not _from_weekly_digest(item):   # digest cards: announced by the digest itself
            fresh.append((rid, item))
    msgs.extend(_fresh_card_msgs(fresh))
    return msgs


def _fresh_card_msgs(fresh: list) -> list:
    if len(fresh) > NEW_CARD_BATCH_ABOVE:
        t, b = notify.msg_new_cards_batch(len(fresh))
        return [(t, b, None, None)]
    msgs = []
    for rid, item in fresh:
        t, b = notify.msg_new_card(item.get("title", rid))
        msgs.append((t, b, rid, None))
    return msgs


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
            msgs.append((t, b, rid, "review_ready"))
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


def check_auth_failures(notified: set) -> list:
    """Scan executing items' logs for credential failures (notify once each)."""
    msgs: list = []
    for req in load_all():
        text = _executing_log_text(req, notified)
        if text is not None and notify.detect_auth_failure(text):
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
                  notified: set, missing_since: dict) -> list:
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
    # 告警落笔前复核 enabled（TOCTOU 收窄）：巡检开头读的
    # cfg 与 notify 之间用户可能刚关掉本源——关掉的源全
    # 静默是 §48.2 的硬承诺，宁可多读一次盘也不发这条。
    # 复核只走「即将告警」的罕见分支（源死亡 + 未在台账），
    # 稳态零额外 IO；关了就本 pass 静默，残留 health 条目
    # 留给下一 pass 的清理分支收尾。
    if not sources.enabled(config.load_config(), src):
        return []
    notified.add(src)
    hours = sources.LIVENESS_THRESHOLDS[src] // 3600
    return [notify.msg_radar_dead(src, hours)]


def check_radar_liveness(d: Daemon, notified: set,
                         now: Optional[_dt.datetime] = None,
                         interval: Optional[int] = None,
                         mono: Optional[float] = None,
                         missing_since: Optional[dict] = None) -> list:
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
                                      notified, missing_since))
    except Exception as e:  # noqa: BLE001 - 巡检绝不干掉主循环
        d.log(f"radar liveness check FAILED: {e}")
    return msgs
