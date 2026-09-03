"""dispatch — moving cards forward: (a') the §51 / §65 auto-dispatch gate,
(b) dispatching approved cards within the concurrency cap, and the one-per-pass
raising expansion.

CONTRACT §4（派发失败台账 + §4.1 风暴刹车：进入 approved 的每条路径重新上膛）/
§34bis（preset 清理卡起跑前拍 registry 快照）/ §51（免批通道 + queued 词表）/
§65（self_improve lane）。当日花费台账 state/autodispatch_spend.json retired
v0.48.7（owner decision D9）：没有预算就没有账要记。
"""
from __future__ import annotations

import datetime as _dt
import traceback
from pathlib import Path
from typing import Optional

from act.lib import analytics, config, failures, notify, policy, registry, risk, self_improve
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
    UI 上没有任何出口。abort_execution（退回提案）也一并清——那个动词的
    语义本来就是「丢弃这一轮，重新决定」。"""
    for key in (tuple(getattr(d.executor, "DISPATCH_STREAK_KEYS", ()))
                + ("last_error", "last_error_at")):
        ex.pop(key, None)
    return ex


# --------------------------------------------------------------------------- #
# (a') auto-dispatch（§51 · vnext-amendments M1.b/C-6）
# --------------------------------------------------------------------------- #
def auto_dispatch_pass(d: Daemon, cfg: config.Config) -> int:
    """信任矩阵免批通道（owner 拍板 + amendments §51）：hand 出身的 card_sent
    卡全部天花板通过 → 直接 approved（actor=policy）。任一不过 → 留在待审批，
    原因 token 上卡（``execution.auto_dispatch_block``，C-6 定名；origin:*/
    disabled 两类常态原因不上卡不留痕）。并发上限不在资格闸里——那是排队问题，
    归 dispatch_approved / queued_reason（M1.b）。预算不存在（D9）：一天派多少
    张、累计多少钱都不拦。§65 self_improve lane 同样免批（token ``ok:self_improve``）。"""
    ad = policy.autodispatch_config(cfg)
    approved = 0
    paused = self_improve.lane_paused()
    # §60 跨命名空间 FIFO（legacy R < P，同空间按数值）——字典序会让 P 卡全体插队
    for req in sorted(load_all(), key=lambda r: registry.id_sort_key(r.id)):
        if req.status != State.CARD_SENT.value:
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
    """card_sent → approved by policy（saved）; returns the disclosed cost."""
    cost = card_cost(req)
    ex.pop("auto_dispatch_block", None)
    ex["auto_dispatched"] = True          # add-only：审计痕（policy 批的，非 owner 点头）
    # §4.1：policy 批准与 owner 批准同权——进入 approved 即重新上膛。
    # 不清的话，刹车停下 → 退回提案 → 本 pass 免批再推进 approved 的
    # 卡会带着 dispatch_halted 直接停回「需输入」，无 UI 出口。
    req.execution = rearm_dispatch(d, ex)
    append_note(req, policy.auto_dispatch_note(reason, cost, _dt.date.today().isoformat()))
    req.set_status(State.APPROVED)
    d.save(req)
    return cost


def _announce_auto(d: Daemon, req: Requirement, reason: str, cost: float, notify_on: bool) -> None:
    d.log(f"autodispatch: {req.id} card_sent -> approved ({reason}, est ${cost:g})")
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
    count = 0
    ad = policy.autodispatch_config(cfg)
    reqs = load_all()
    live = _live_count(reqs)
    for req in reqs:
        if not _awaiting_dispatch(req):
            continue
        if d.executor is None:
            d.log(f"dispatch: executor unavailable, cannot dispatch {req.id}")
            continue
        if _held_this_pass(req, live, int(ad["max_concurrent"])):
            continue
        if _dispatch_one(d, req, cfg):
            count += 1
            live += 1                    # 本 pass 内并发口径同步推进
    return count


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
    v0.48.7，D9：并发是唯一的排队原因。）"""
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
    """§34bis 机械护栏起点：preset 清理卡在会话启动**之前**拍 registry
    快照（落 state/triage_snapshots/，卡上只留引用）——启动后再拍有
    TOCTOU 窗口：会话起跑即写，篡改会被拍进基线。启动前的管线合法
    写入由 writes_since(快照 ts) 排除，快照提前拍不产生假警。引用
    要等 dispatch 成功后补挂：executor.dispatch 的成功路径整个
    重建了 execution。"""
    if getattr(req, "preset", None) == triage_guard.PROPOSALS_TRIAGE_PRESET:
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
        d.analyze.expand_debt(req)  # -> card_sent (or detected+note on failure)
        d.log(f"raising: {req.id} expanded -> {req.status}")
        analytics.log_event("raise_expanded", req=req.id, status=str(req.status))
    except Exception as e:  # noqa: BLE001 - one bad expansion can't kill the loop
        d.log(f"raising: {req.id} expand FAILED: {e}")
        req.set_status(registry.State.DETECTED)   # fall back so it's not stuck
        req.notes = ((req.notes or "") + " (raise 展开失败，退回欠账)").strip()
        registry.save(req)
    return 1
