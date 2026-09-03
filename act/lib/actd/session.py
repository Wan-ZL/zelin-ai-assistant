"""session — live-session plumbing shared by the inbox verbs, merge and reconcile.

CONTRACT §11（收割交付物进待验收）/ §37（CARD TITLE 收割 + Mac 本地会话内容搜索层）
/ §46（确认式停止 + 失败台账）。All best-effort: nothing here may raise into the
caller's state write — a stop that fails leaves a ledger, never a crash.
"""
from __future__ import annotations

from typing import Optional

from act.lib import analytics, failures, notify
from act.lib.actd.seam import Daemon, append_note
from act.lib.registry import Requirement, State


def stop_session_tracked(d: Daemon, req: Requirement, ex: dict, sid, why: str,
                         log_prefix: str = "inbox") -> tuple:
    """确认式停止 + 失败台账（§46）——所有 actd 侧 stop_session 调用点的统一外壳。

    走 executor.stop_session_confirmed（有限重试 + roster 验证），仍是 best-effort
    （吞异常、绝不阻塞调用方的状态落账），但失败不再只打一行日志：
    - execution.stop_failed_at / stop_failed_error 落台账（add-only 字段）；
    - notes 追加 [stop-failed] 标签（notes_text 投影，看板上可见可搜）；
    - notify.msg_stop_failed 通知 + analytics `stop_failed` 打点。
    确认停掉时清掉旧台账字段。只改内存里的 req/ex——落盘仍由调用方 save（单写者
    路径不变，§44）。Returns (stopped, issued)，语义见 stop_session_confirmed。
    """
    if d.executor is None:
        return False, False
    stopped, issued, detail = _confirmed_stop(d, req, sid, why, log_prefix)
    if stopped:
        # 这次确认停掉了：清掉此前留下的失败台账（台账只描述当前事实）
        ex.pop("stop_failed_at", None)
        ex.pop("stop_failed_error", None)
        return True, issued
    _record_stop_failure(d, req, ex, sid, detail)
    return False, issued


def _confirmed_stop(d: Daemon, req: Requirement, sid, why: str, log_prefix: str) -> tuple:
    """executor.stop_session_confirmed with the exception folded into ``detail``."""
    try:
        stopped, issued, detail = d.executor.stop_session_confirmed(str(sid))
        d.log(f"{log_prefix}: {req.id} {why} — stop_session({sid}) -> {stopped}"
              f" ({detail})")
        return stopped, issued, detail
    except Exception as e:  # noqa: BLE001 - best-effort, never block the caller
        d.log(f"{log_prefix}: {req.id} {why} — stop_session({sid}) failed "
              f"(ignored): {e}")
        return False, False, f"{type(e).__name__}: {e}"


def _record_stop_failure(d: Daemon, req: Requirement, ex: dict, sid, detail) -> None:
    ex["stop_failed_at"] = d.iso_now()
    ex["stop_failed_error"] = str(detail)[:300] or "stop failed"
    append_note(req, f"[stop-failed] 停止会话 {sid} 失败（重试后进程仍存活），"
                     f"可能仍在后台运行——请在终端 `claude stop` 手动停止")
    notify.notify(*notify.msg_stop_failed(req.title or req.id), req=req.id)
    # TELEMETRY 红线（issue #37）：事件只带 req + 分类 id，原文（会话 UUID、
    # PID）一个字节都不出机——全量 detail 只进本机台账（stop_failed_error/notes）。
    analytics.log_event("stop_failed", req=req.id,
                        failure_id=failures.classify(str(detail)))


_STOPPABLE = (State.APPROVED.value, State.EXECUTING.value, State.REVIEW.value)


def stop_live_session(d: Daemon, req: Requirement, why: str) -> None:
    """Best-effort stop of a card's live agent before a destructive action
    (reject/trash on an approved/executing/review card — nightly audit
    2026-07-14: the old path binned the card while its agent kept running,
    burning tokens into a worktree nobody would ever look at). Mirrors the
    abort_execution recipe: stop, archive the sid, never block the action."""
    if str(req.status) not in _STOPPABLE:
        return
    ex = dict(req.execution or {})
    sid = ex.get("session_id")
    if not sid:
        return
    stopped, issued = _stop_if_available(d, req, ex, sid, why)
    ex["aborted_session_id"] = sid
    if stopped and issued:
        # only a session we actually killed loses its id — when the stop
        # failed (or executor is unavailable) the agent may still be alive,
        # and a later trash→restore round-trip must be able to re-attach
        # (audit review 2026-07-14: unconditional pop made restore lossy).
        # §46: issued 门再收紧一档——「本来就死」的会话保留 id，restore 后
        # 还能凭 transcript 复活（旧 stop_session 对这种情况返回 False，行为一致）。
        ex.pop("session_id", None)
    req.execution = ex


def _stop_if_available(d: Daemon, req: Requirement, ex: dict, sid, why: str) -> tuple:
    if d.executor is None:
        return False, False
    return d.stop_session_tracked(req, ex, sid, why)


def apply_harvest_title(d: Daemon, req: Requirement, harvested: dict) -> None:
    """§37: apply a harvested ``CARD TITLE:`` line at the same promotion points
    where delivered_summary lands (round boundaries only). Best-effort; a
    user-pinned title wins inside set_display_title. Caller saves ``req``."""
    from act.lib import registry
    try:
        t = (harvested or {}).get("card_title")
        if t and registry.set_display_title(req, t):
            d.log(f"inbox/reconcile: {req.id} display title refreshed from "
                  f"CARD TITLE line: {str(t)[:64]!r}")
    except Exception as e:  # noqa: BLE001 - titles must never block delivery
        d.log(f"harvest title apply failed for {getattr(req, 'id', '?')}: {e}")


def update_search_index(d: Daemon, card_id, session_id) -> None:
    """§37 Mac-local session-content search layer: refresh one card's entry at
    the existing settle/harvest touchpoints. Best-effort, never raises."""
    if not session_id:
        return
    try:
        from act.lib import search_index
        search_index.update_card(str(card_id), str(session_id))
    except Exception as e:  # noqa: BLE001 - indexing must never break the pass
        d.log(f"search index update failed for {card_id}: {e}")


def fold_harvest(ex: dict, harvested: dict) -> None:
    """Copy the non-empty deliverable fields of a harvest into ``ex``（非空才写）."""
    if harvested.get("delivered_summary"):
        ex["delivered_summary"] = harvested["delivered_summary"]
    if harvested.get("final_draft"):
        ex["final_draft"] = harvested["final_draft"]


def harvest_into(d: Daemon, req: Requirement, ex: dict, sid) -> Optional[Exception]:
    """executor.harvest_delivery(sid) → ``ex`` deliverable fields + §37 title.

    The whole harvest is one guarded step (§11 收割失败绝不阻塞提升): the
    exception is RETURNED so each call site keeps logging its own line, exactly
    as the five inline copies did before P3b. Requires ``d.executor``.
    """
    try:
        harvested = d.executor.harvest_delivery(str(sid)) or {}
        fold_harvest(ex, harvested)
        apply_harvest_title(d, req, harvested)   # §37, round boundary
    except Exception as e:  # noqa: BLE001 - harvest is best-effort
        return e
    return None
