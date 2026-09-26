"""dispatch — moving cards forward: (a'') the §78 一次性归并扫描, (a') the §65
auto-dispatch gate, (b) dispatching approved cards within the concurrency cap,
and the one-per-pass raising expansion.

CONTRACT §4（派发失败台账 + §4.1 风暴刹车：进入 approved 的每条路径重新上膛）/
§51（免批通道 + queued 词表；**hand lane retired**，并入 §78）/ §65（self_improve
lane）/ §65.1（通道总开关关着 = 免批批准过的 lane 卡退回潜在任务，不再派出）/
§71.1（睡眠感知派发：机器不在清醒态时本 pass 一张卡都不派）/ §78（提案车道退役：
免批扫的是 detected，且只有 §65 lane 还能自动提升）。当日花费台账
state/autodispatch_spend.json retired v0.48.7（owner decision D9）：没有预算就
没有账要记。§34bis 的 preset 清理卡 retired（D80.11）——起跑前拍 registry 快照
的机械护栏本身留着，改锚在 owner 的直跑卡上。
"""
from __future__ import annotations

import datetime as _dt
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from act.lib import (analytics, config, failures, notify, policy, power, registry, risk,
                     self_improve)
from act.lib.actd import triage_guard
from act.lib.actd.seam import Daemon, append_note
from act.lib.registry import Requirement, State, load_all


def card_cost(req: Requirement) -> float:
    try:
        return float(str(req.cost_estimate_usd))
    except (TypeError, ValueError):
        return 0.0


def rearm_dispatch(d: Daemon, ex: dict) -> dict:
    """§4.1 storm brake：清掉上一轮派发的失败台账（attempts / 同类连败计数 /
    halted 标记 / 旧 last_error），返回同一个 dict。**进入 approved 的每条路径**
    都必须过这里——不只是 owner 的 approve。审查复现（2026-09-01）：
    auto_dispatch_pass 把 execution 原样带进 approved，`dispatch_halted` 跟着
    过去，卡永远停在「需输入」；owner 再点批准是 approved 上的幂等 no-op，
    UI 上没有任何出口。abort_execution（退回潜在任务）也一并清——那个动词的
    语义本来就是「丢弃这一轮，重新决定」。"""
    for key in (tuple(getattr(d.executor, "DISPATCH_STREAK_KEYS", ()))
                + ("last_error", "last_error_at")):
        ex.pop(key, None)
    return ex


# --------------------------------------------------------------------------- #
# (a'') §78 一次性归并扫描：把退役车道上的存量卡搬进潜在任务
# --------------------------------------------------------------------------- #
# 卡面留痕的行文（日期由调用时现取；count-agnostic：扫的是查询结果，
# 绝不硬编码张数——存量多少张是每台安装自己的事）
_FOLD_NOTE = "[{today} §78] 提案车道退役，卡移入潜在任务（issue #447）"


def fold_retired_lane(d: Daemon) -> int:
    """§78（issue #447 / D80）一次性归并扫描：``card_sent`` → ``detected``。

    提案列退役后没有任何写者再落 card_sent，但**存量卡还在那儿**——看板不再
    渲染那一列的话它们就人间蒸发了（dashboard 侧还有一道把落单卡投进潜在任务
    的兜底，两层都要有：投影只管看得见，卡本身也得真的搬过来才能被批准）。

    幂等 + count-agnostic：搬完的卡不再匹配，下一 pass 自然是 0 张；留痕那一行
    与既有 notes 追加同形（重复 pass 不会重复追加，因为状态已经不匹配了）。
    只在 actd 主循环里跑（§44 单写者），actor 显式 ``system``——这是自主管线
    的搬运，不是 owner 动作。一张卡搬失败不许带走整个 pass（宪法第 11 条）。
    """
    today = _dt.date.today().isoformat()
    folded = 0
    for req in load_all():
        if str(req.status) != State.CARD_SENT.value:
            continue
        try:
            append_note(req, _FOLD_NOTE.format(today=today))
            req.set_status(State.DETECTED)
            with registry.acting_as("system"):
                registry.save(req)
            folded += 1
        except Exception as e:  # noqa: BLE001 - one bad card must not kill the pass
            d.log(f"§78 fold: {getattr(req, 'id', '?')} FAILED: {e}")
    if folded:
        d.log(f"§78 fold: {folded} card(s) card_sent -> detected（提案车道退役）")
        analytics.log_event("retired_lane_folded", count=folded)
    return folded


# --------------------------------------------------------------------------- #
# (a') auto-dispatch（§65 lane · vnext-amendments M1.b/C-6）
# --------------------------------------------------------------------------- #
def auto_dispatch_pass(d: Daemon, cfg: config.Config) -> int:
    """§65 self_improve lane 的免批通道：潜在任务（``detected``）里**只有**全
    self_improve 出身的卡参与资格裁决，全部天花板通过 → 直接 approved
    （actor=policy，token ``ok:self_improve``）。任一不过 → 留在潜在任务列，
    原因 token 上卡（``execution.auto_dispatch_block``，C-6 定名；origin:*/
    disabled 两类常态原因不上卡不留痕）。并发上限不在资格闸里——那是排队问题，
    归 dispatch_approved / queued_reason（M1.b）。预算不存在（D9）：一天派多少
    张、累计多少钱都不拦。

    §51 hand lane retired v-next（并入 §78，owner decision D80.4）：hand 出身卡
    免批的唯一喂料口是提案捕获框，那个框随提案列一起删了；owner 亲手发起的工作
    走「运行中」直跑框（§34 ``mode:"run"``），出生即 approved，压根不进这个闸。
    所以这里在 ``_admission`` **之前**先按 sources 过一道 §65 闸——policy 仍是
    纯资格函数（hand 卡它照旧判「可以」），退役的是**入口**，不是裁决表。"""
    ad = policy.autodispatch_config(cfg)
    approved = 0
    paused = self_improve.lane_paused()
    # §60 跨命名空间 FIFO（legacy R < P，同空间按数值）——字典序会让 P 卡全体插队
    for req in sorted(load_all(), key=lambda r: registry.id_sort_key(r.id)):
        if req.status != State.DETECTED.value:
            continue
        if not policy.is_self_improve_sources(req.sources):
            _clear_stale_block(d, req)   # §51 退役：hand 卡再不会被这个闸拦
            continue
        try:
            ok, reason = _admission(req, cfg, paused)
            ex = dict(req.execution or {})
            if not ok:
                _record_block(d, req, ex, reason)
                continue
            cost = _approve_auto(d, req, ex, reason)
            approved += 1
            _announce_auto(d, req, reason, cost, ad["notify"])
        except Exception as e:  # noqa: BLE001 - one bad card must not kill the pass
            d.log(f"autodispatch: {getattr(req, 'id', '?')} FAILED: {e}")
    return approved


def _admission(req: Requirement, cfg: config.Config, paused: bool) -> tuple:
    ok, reason = policy.may_auto_dispatch(req, cfg, lane_paused=paused)
    # W17 belt-and-braces：显式 external 章可能比 sources 现算更严
    # （手改 YAML 等）——forced_expand 的卡绝不自动派发。
    if ok and risk.effective_tier(req).forced_expand:
        return False, "origin:external"
    return ok, reason


def _clear_stale_block(d: Daemon, req: Requirement) -> None:
    """§78：hand lane 退役之后，非 §65 卡连资格闸都不进——上一轮留在卡上的
    ``auto_dispatch_block`` token 就成了永不更新的假话（卡面会一直挂着
    「auto-dispatch 拦下 …」的 chip）。见到就清，没有就零开销（与
    :func:`_record_block` 的「过期 token 清掉」同一条纪律）。"""
    ex = dict(req.execution or {})
    if "auto_dispatch_block" not in ex:
        return
    ex.pop("auto_dispatch_block", None)
    req.execution = ex
    d.save(req)


def _record_block(d: Daemon, req: Requirement, ex: dict, reason: str) -> None:
    """Blocked card: routine reasons leave no trace (and clear a stale token);
    the rest land on the card once（token 变了才写）."""
    if policy.is_routine_reason(reason):
        if "auto_dispatch_block" in ex:
            ex.pop("auto_dispatch_block", None)   # 过期 token 清掉
            req.execution = ex
            d.save(req)
        return
    if ex.get("auto_dispatch_block") == reason:
        return
    ex["auto_dispatch_block"] = reason
    req.execution = ex
    append_note(req, f"[{_dt.date.today().isoformat()} auto-dispatch 拦下] {reason}")
    d.save(req)
    d.log(f"autodispatch: {req.id} blocked ({reason})")
    analytics.log_event("auto_dispatch_blocked", req=req.id, reason=reason)


def _approve_auto(d: Daemon, req: Requirement, ex: dict, reason: str) -> float:
    """detected → approved by policy（saved）; returns the disclosed cost."""
    cost = card_cost(req)
    ex.pop("auto_dispatch_block", None)
    ex["auto_dispatched"] = True          # add-only：审计痕（policy 批的，非 owner 点头）
    # §4.1：policy 批准与 owner 批准同权——进入 approved 即重新上膛。
    # 不清的话，刹车停下 → 退回潜在任务 → 本 pass 免批再推进 approved 的
    # 卡会带着 dispatch_halted 直接停回「需输入」，无 UI 出口。
    req.execution = rearm_dispatch(d, ex)
    append_note(req, policy.auto_dispatch_note(reason, cost, _dt.date.today().isoformat()))
    req.set_status(State.APPROVED)
    d.save(req)
    return cost


def _announce_auto(d: Daemon, req: Requirement, reason: str, cost: float, notify_on: bool) -> None:
    d.log(f"autodispatch: {req.id} detected -> approved ({reason}, est ${cost:g})")
    analytics.log_event("auto_dispatch", req=req.id, cost=cost, lane=reason)
    if notify_on:
        # 观察模式：每次免批派发都出一条通知，owner 随时可关
        # （autodispatch.notify=false）或全关（enabled=false）。
        notify.notify(*notify.msg_auto_dispatched(reason, req.title or req.id), req=req.id)


# --------------------------------------------------------------------------- #
# (b) dispatch approved
# --------------------------------------------------------------------------- #
def _live_count(reqs: list) -> int:
    """并发口径（M1.b 接线点③）：EXECUTING 且带 session 的卡数。roster 实况
    reconcile 才查（子进程贵）；按状态机计数是保守方向——死会话短暂占位只
    会让排队多等一个 pass。"""
    return sum(1 for r in reqs
               if r.status == State.EXECUTING.value
               and (r.execution or {}).get("session_id"))


def dispatch_approved(d: Daemon, cfg: config.Config) -> int:
    ad = policy.autodispatch_config(cfg)
    reqs = load_all()
    gate = _PassGate(d=d, cfg=cfg, live=_live_count(reqs),
                     cap=int(ad["max_concurrent"]))
    count = 0
    for req in reqs:
        if not _awaiting_dispatch(req):
            continue
        if _withdraw_frozen_lane(d, req, cfg):
            continue
        if gate.holds(req):
            continue
        if _dispatch_one(d, req, cfg):
            count += 1
            gate.live += 1               # 本 pass 内并发口径同步推进
    return count


def _withdraw_frozen_lane(d: Daemon, req: Requirement, cfg: config.Config) -> bool:
    """§65.1（issue #307 第 4 条「关闭开关时至少不再续派」）：通道被关掉之后，
    **policy 免批批准**（`execution.auto_dispatched`）但还没派出的 self_improve
    卡不再起跑——退回潜在任务列（§78 前落提案列；`auto_dispatched` 痕一并清掉，
    approved 那一刻的资格判定已经过期），下一 pass 的资格闸照常报既有的
    `self_improve:disabled`（常态回落、不上卡），维护者把开关打开后它照常重新免批。

    审查复现（#335 review）：`_held_this_pass` 让并发满时的 lane 卡留在 approved
    排队（§51 queued），这些卡在关开关几 pass / 几小时之后仍会被派出去烧执行器
    与 API 额度——开关只挡了「铸卡 / 批准」那一端。

    **owner 亲手批准的 self_improve 卡不动**（没有 `auto_dispatched` 痕）：开关
    管的是自动化，显式动作永远不被静默吞掉。True = 本卡已处理完，别再派。"""
    ex = dict(req.execution or {})
    if not (ex.get("auto_dispatched") and self_improve.frozen_in_flight(req, cfg)):
        return False
    ex.pop("auto_dispatched", None)
    req.execution = ex
    append_note(req, f"[{_dt.date.today().isoformat()} 通道已关] "
                     "self_improve 免批派发撤回，卡退回潜在任务（§65.1）")
    req.set_status(State.DETECTED)
    d.save(req)
    d.log(f"dispatch: {req.id} withdrawn (self_improve:disabled)")
    return True


@dataclass
class _PassGate:
    """本 pass 的派发闸（executor 缺席 / §71.1 机器在睡 / §4 刹车 / §51 并发）。

    机器状态**懒算一次**：没有待派发卡的 pass 一个子进程都不起（探针 60 s
    memo 见 act/lib/power.py），本 pass 内所有 approved 卡共用同一个判决——
    半 pass 睡半 pass 醒会让「为什么这张派了那张没派」无法解释。
    """
    d: Daemon
    cfg: config.Config
    live: int
    cap: int
    asleep: Optional[bool] = None

    def holds(self, req: Requirement) -> bool:
        if self.d.executor is None:
            self.d.log(f"dispatch: executor unavailable, cannot dispatch {req.id}")
            return True
        if self.asleep is None:
            self.asleep = power.machine_asleep(self.cfg, log=self.d.log)
        return bool(self.asleep) or _held_this_pass(req, self.live, self.cap)


def _awaiting_dispatch(req: Requirement) -> bool:
    """approved and never dispatched (no session yet)."""
    if req.status != State.APPROVED.value:
        return False
    return not (req.execution and req.execution.get("session_id"))  # already dispatched


def _held_this_pass(req: Requirement, live: int, cap: int) -> bool:
    """§4 派发风暴刹车已触发：不再重试、不占并发槽、不写卡、不打日志——卡在
    「需输入」列等 owner 退回重批（approve 清台账）。§51 合并运行列 queued
    子状态：并发满 → 卡留 approved 排队（原因 chip 由 dashboard 的
    queued_reason 投影），槽位空出即派发。（auto 卡派发时刻的预算复核 retired
    v0.48.7，D9；§71.1 起「机器在睡」是第二个排队原因，住在 _PassGate 里——
    它按住的是**整个 pass**，不是某一张卡。）"""
    return bool((req.execution or {}).get("dispatch_halted")) or live >= cap


def _dispatch_one(d: Daemon, req: Requirement, cfg: config.Config) -> bool:
    """Launch one approved card. True iff ``executor.dispatch`` returned (the
    pass's count/live tick) — a crash in the follow-up bookkeeping is recorded
    through the same failure path but does not un-count the launch."""
    launched = False
    snap_ref = None
    try:
        snap_ref = _pre_dispatch_snapshot(d, req)
        d.executor.dispatch(req, cfg)
        d.log(f"dispatch: {req.id} -> executing "
              f"(session={ (req.execution or {}).get('session_id') })")
        launched = True
        _after_dispatch(d, req, snap_ref)
    except Exception as e:  # noqa: BLE001 - keep the loop alive
        _on_dispatch_failure(d, req, e, snap_ref)
    return launched


def _pre_dispatch_snapshot(d: Daemon, req: Requirement) -> Optional[str]:
    """§34bis 机械护栏起点：**直跑卡**（§78/D80.11 起的新锚点，见
    triage_guard.guarded_card）在会话启动**之前**拍 registry 快照
    （落 state/triage_snapshots/，卡上只留引用）——启动后再拍有
    TOCTOU 窗口：会话起跑即写，篡改会被拍进基线。启动前的管线合法
    写入由 writes_since(快照 ts) 排除，快照提前拍不产生假警。引用
    要等 dispatch 成功后补挂：executor.dispatch 的成功路径整个
    重建了 execution。"""
    if triage_guard.guarded_card(req):
        return triage_guard.stamp_triage_snapshot(d, req.id)
    return None


def _after_dispatch(d: Daemon, req: Requirement, snap_ref: Optional[str]) -> None:
    ex = dict(req.execution or {})
    changed = _clear_stale_error(ex)
    if snap_ref:
        changed = _attach_snapshot(d, ex, snap_ref) or changed
    if changed:
        req.execution = ex
        d.save(req)


def _clear_stale_error(ex: dict) -> bool:
    """retry succeeded -> clear the failure left by a previous attempt.
    (dispatch rebuilds execution so this is usually a no-op; kept as a
    belt-and-braces so a stale last_error never lingers on a live run.)
    Gated on session_id: a non-raising dispatch that produced no
    session is a FAILURE, and wiping last_error here would erase the
    only trace the queued card can show as dispatch_error."""
    if ex.get("session_id") and ("last_error" in ex or "last_error_at" in ex):
        ex.pop("last_error", None)
        ex.pop("last_error_at", None)
        return True
    return False


def _attach_snapshot(d: Daemon, ex: dict, snap_ref: str) -> bool:
    """§34bis：起跑成功才补挂快照引用（收割提升时由
    check_triage_registry_guard 比对）；无 session = 起跑失败，
    快照无主即焚——下轮重试会重拍。"""
    if ex.get("session_id"):
        ex["registry_snapshot_ref"] = snap_ref
        return True
    d.safe_unlink(Path(snap_ref))
    return False


def _on_dispatch_failure(d: Daemon, req: Requirement, e: Exception, snap_ref: Optional[str]) -> None:
    # §34bis：起跑崩了 → 预拍的快照无主即焚（重试下轮重拍）。
    if snap_ref:
        d.safe_unlink(Path(snap_ref))
    is_dispatch_error = (d.executor is not None
                         and isinstance(e, d.executor.DispatchError))
    # getattr 兜底：测试注入的最小 executor 替身可能只带 DispatchError
    backing_off = getattr(d.executor, "DispatchBackingOff", ())
    if is_dispatch_error and isinstance(e, backing_off):
        # 退避窗口内：什么都没发生——不写卡、不打 traceback（2026-08-31
        # 事故：这条 no-op 每 pass 重写一次 last_error_at + 28 行
        # traceback，一张卡占了 98% 的 registry 写入、954 条 traceback）。
        return
    _log_dispatch_failure(d, req, e, is_dispatch_error)
    err = str(e)[:300]
    _trace_last_error(d, req, err)
    # executor.dispatch already emits dispatch_failed (with reason/attempt)
    # for DispatchError. Only log unexpected crashes here so analytics
    # is not double-counted for a single failed launch (issue #12).
    if not is_dispatch_error:
        analytics.log_event(
            "dispatch_failed",
            req=req.id,
            failure_id=failures.classify(err),   # id only (#37)
            reason="dispatch_crashed",
        )


def _log_dispatch_failure(d: Daemon, req: Requirement, e: Exception, is_dispatch_error: bool) -> None:
    if is_dispatch_error:
        # executor 已落账（last_error/attempts/halted），只留一行日志
        halted_cls = getattr(d.executor, "DispatchHalted", ())
        d.log(f"dispatch: {req.id} FAILED: {(str(e).splitlines() or [''])[0][:300]}"
              + (" — halted (storm brake)"
                 if isinstance(e, halted_cls) else ""))
    else:
        d.log(f"dispatch: {req.id} FAILED: {e}\n{traceback.format_exc()}")


def _trace_last_error(d: Daemon, req: Requirement, err: str) -> None:
    """leave a trace on execution so the dashboard's queued item can show
    dispatch_error (§2); status stays approved -> auto-retry next pass.
    只在文本真变了时才写（executor 正常路径已写过同一段——重写只会
    刷新 last_error_at，让 registry_writes 台账每 pass 多一行）。"""
    try:
        ex = dict(req.execution or {})
        # prefix compare: executor keeps 500 chars, this trace 300
        if not str(ex.get("last_error") or "").startswith(err):
            ex["last_error"] = err
            ex["last_error_at"] = d.iso_now()
            req.execution = ex
            d.save(req)
    except Exception:  # noqa: BLE001 - bookkeeping must not block retry
        pass


# --------------------------------------------------------------------------- #
# raise expansion — ONE per pass (a slow claude -p; don't block on a batch)
# --------------------------------------------------------------------------- #
def process_raising(d: Daemon, cfg: config.Config) -> int:
    if d.analyze is None:
        return 0
    pending = [r for r in registry.load_all()
               if r.status == registry.State.RAISING.value]
    if not pending:
        return 0
    # §60 跨命名空间 FIFO 取最老的一张——字典序 "P-" < "R-" 会饿死存量 raising 队列
    req = sorted(pending, key=lambda r: registry.id_sort_key(r.id))[0]
    try:
        d.analyze.expand_debt(req)  # -> detected（§78：扩写完成/失败都落潜在任务）
        d.log(f"raising: {req.id} expanded -> {req.status}")
        analytics.log_event("raise_expanded", req=req.id, status=str(req.status))
    except Exception as e:  # noqa: BLE001 - one bad expansion can't kill the loop
        d.log(f"raising: {req.id} expand FAILED: {e}")
        req.set_status(registry.State.DETECTED)   # fall back so it's not stuck
        req.notes = ((req.notes or "") + " (raise 展开失败，退回欠账)").strip()
        registry.save(req)
    return 1
