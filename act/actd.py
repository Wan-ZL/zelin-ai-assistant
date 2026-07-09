"""actd — the assistant daemon loop.

Each pass:
  (a) drain STATE/inbox/*.json decisions
        approve  -> status=approved
        reject   -> status=rejected
        comment  -> fold text into plan/notes, keep card_sent (re-approval)
      delete the decision file after reading it.
  (b) dispatch every status=approved requirement that has no execution yet.
  (c) build + atomically write dashboard.json.
  (d) diff against the previous dashboard; notify on state transitions.

Robust: a single exception never kills the loop; everything is logged to
STATE/actd.log. ``--once`` runs exactly one pass then exits (for tests/cron).

Run: ``python -m act.actd`` (or ``python -m act.actd --once``).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import time
import traceback
from pathlib import Path
from typing import Optional

from act.lib import analytics, config, notify, registry
from act.lib.agent_states import (
    _BLOCKED_STATES,
    _DONE_STATES,
    _LIVE_STATES,
    _RUNNING_STATES,
)
from act.lib.dashboard import (
    build_dashboard,
    write_dashboard,
    _run_claude_agents,
    _index_agents,
)
from act.lib.registry import Requirement, State, load, load_all, save

try:
    from act import executor
except Exception:  # pragma: no cover - executor import must not kill daemon
    executor = None  # type: ignore

try:
    from act import analyze
except Exception:  # pragma: no cover - analyze import must not kill daemon
    analyze = None  # type: ignore


# --------------------------------------------------------------------------- #
# logging
# --------------------------------------------------------------------------- #
def _log(msg: str) -> None:
    config.ensure_state_dirs()
    line = f"{_dt.datetime.now().isoformat(timespec='seconds')}  {msg}\n"
    try:
        with (config.STATE_DIR / "actd.log").open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def _iso_now() -> str:
    """UTC ISO stamp — the registry-side timestamp format (dashboard 转 epoch)."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# (a) inbox
# --------------------------------------------------------------------------- #
def process_inbox() -> int:
    """Apply and delete every inbox decision file. Returns count processed."""
    if not config.INBOX_DIR.exists():
        return 0
    processed = 0
    for path in sorted(config.INBOX_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            decision = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            _log(f"inbox: bad decision file {path.name}: {e}")
            _safe_unlink(path)
            continue

        req_id = decision.get("id")
        action = decision.get("action")
        comment = decision.get("comment")

        # §10 capture: no req id — the app popover's one-liner quick capture.
        if action == "capture":
            _apply_capture(decision.get("text"))
            processed += 1
            _safe_unlink(path)
            continue

        req = load(req_id) if req_id else None

        if req is None:
            _log(f"inbox: decision for unknown req {req_id!r} ({action}) — dropped")
        else:
            _apply_decision(req, action, comment)
            analytics.log_event(f"inbox_{action or 'unknown'}", req=req.id,
                                status=str(req.status))
            processed += 1

        _safe_unlink(path)
    return processed


def _apply_capture(text: Optional[str]) -> None:
    """Quick capture from the app popover (CONTRACT §10/§15).

    ``{"action":"capture","text":"...","ts":"..."}`` -> registry.merge_or_new
    (title=text, channel=quick_capture, 原话进 sources) -> status=raising, so the
    existing process_raising() expands it (one per pass) into a card_sent
    proposal. Fast: no LLM call here, the poll loop is never blocked.

    Idempotent: merge_or_new dedupes by title, so the same text arriving twice
    merges into the existing entry instead of creating a second card; an entry
    already raised past 'detected' is left in whatever state it reached.
    """
    t = " ".join(str(text or "").split()).strip()
    if not t:
        _log("inbox: capture with empty text — ignored")
        return
    req = Requirement(
        id=registry.next_id(),
        title=t[:80],
        type="other",
        tier="T1",
        status=State.DETECTED.value,
        hardness="soft",
        sources=[{
            "who": "zelin",
            "channel": "quick_capture",
            "date": _dt.date.today().isoformat(),
            "quote": t,
        }],
        notes="from app quick capture",
    )
    saved = registry.merge_or_new(req)
    if saved.status == State.DETECTED.value:
        saved.set_status(State.RAISING)
        save(saved)
        _log(f"inbox: capture -> {saved.id} raising (queued for AI expansion)")
    else:
        _log(f"inbox: capture merged into {saved.id} (status={saved.status})")
    analytics.log_event("inbox_capture", req=saved.id, status=str(saved.status))


def _apply_decision(req: Requirement, action: Optional[str], comment: Optional[str]) -> None:
    # Full inbox action set (CONTRACT §10) — this elif chain IS the action
    # whitelist/validation; anything else falls through to the logged no-op else:
    #   approve | reject(->trash) | comment | raise(debt->proposal)
    #   | trash(->recycle) | restore(recycle->prev) | pin(recycle->permanent)
    #   | accept(review->delivered) | rework(review->executing)
    #   | done_external(card_sent|review->delivered)          (v0.10.2)
    #   | abort_execution(approved|executing->card_sent)      (v0.10.2)
    #   | revert_review(delivered->review)                    (v0.10.2)
    # v0.10.2 公共规则：状态不匹配的逆向动作 = 幂等 no-op + log（防连点/迟到 inbox）。
    if action == "approve":
        # idempotent: a double-click (or re-approve while already running) must
        # not re-dispatch and spawn a duplicate agent.
        if str(req.status) in (State.APPROVED.value, State.EXECUTING.value,
                               State.REVIEW.value, State.DELIVERED.value):
            _log(f"inbox: {req.id} approve ignored (already {req.status})")
            return
        req.set_status(State.APPROVED)
        save(req)
        _log(f"inbox: {req.id} approved")
    elif action == "reject":
        registry.trash(req, "rejected")  # recoverable, not a bare rejected status
        _log(f"inbox: {req.id} rejected -> trash")
    elif action == "comment":
        _fold_comment(req, comment)
        req.set_status(State.CARD_SENT)  # stays pending, re-approval
        save(req)
        _log(f"inbox: {req.id} comment folded — re-approval pending")
    elif action == "raise":
        if analyze is None:
            _log(f"inbox: {req.id} raise requested but analyze unavailable — ignored")
            return
        # Fast: just mark it 'raising' so it shows a processing spinner in 待审批
        # immediately. The slow claude -p expansion happens in process_raising(),
        # one item per loop pass, so 4 raises don't freeze the daemon for minutes.
        req.set_status(State.RAISING)
        save(req)
        _log(f"inbox: {req.id} -> raising (queued for AI expansion)")
    elif action == "trash":
        registry.trash(req, "deleted")
        _log(f"inbox: {req.id} trashed (deleted)")
    elif action == "restore":
        registry.restore(req)
        _log(f"inbox: {req.id} restored -> {req.status}")
    elif action == "pin":
        registry.pin(req)
        _log(f"inbox: {req.id} pinned permanent")
    elif action == "accept":
        # §11 验收通过 -> delivered（归档）；accepted_at 供 completed 行显示（§2）
        req.set_status(State.DELIVERED)
        ex = dict(req.execution or {})
        ex["accepted_at"] = _iso_now()
        req.execution = ex
        save(req)
        _log(f"inbox: {req.id} accepted -> delivered")
    elif action == "rework":
        # §11 打回：把 Zelin 的反馈送回原 session 继续（executor.rework 处理
        # stop-idle-then-resume），状态回 executing
        if executor is None:
            _log(f"inbox: {req.id} rework requested but executor unavailable — ignored")
            return
        if not (comment or "").strip():
            _log(f"inbox: {req.id} rework with empty feedback — ignored")
            return
        ok = executor.rework(req, comment)
        _log(f"inbox: {req.id} rework sent (ok={ok}) — back to executing")
    elif action == "done_external":
        # v0.10.2 已办完（系统外完成）：card_sent|review -> delivered。有活
        # session 不动它 —— 人做完了，AI 会话自然闲置。
        if str(req.status) not in (State.CARD_SENT.value, State.REVIEW.value):
            _log(f"inbox: {req.id} done_external ignored (status={req.status}) — no-op")
            return
        req.set_status(State.DELIVERED)
        ex = dict(req.execution or {})
        ex["accepted_at"] = _iso_now()
        req.execution = ex
        tag = "[done outside] Zelin 在系统外完成"
        req.notes = (req.notes + "\n" + tag).strip() if req.notes else tag
        save(req)
        _log(f"inbox: {req.id} done_external -> delivered")
    elif action == "abort_execution":
        # v0.10.2 停止并退回待审批：approved|executing -> card_sent。活 session
        # 先 best-effort 停止（stop 失败只记日志，绝不阻塞状态回退）；session_id
        # 归档到 aborted_session_id 后删除，保证重新批准时干净重派发。
        if str(req.status) not in (State.APPROVED.value, State.EXECUTING.value):
            _log(f"inbox: {req.id} abort_execution ignored (status={req.status}) — no-op")
            return
        ex = dict(req.execution or {})
        sid = ex.get("session_id")
        if sid and executor is not None:
            try:
                stopped = executor.stop_session(str(sid))
                _log(f"inbox: {req.id} abort — stop_session({sid}) -> {stopped}")
            except Exception as e:  # noqa: BLE001 - best-effort, never block rollback
                _log(f"inbox: {req.id} abort — stop_session({sid}) failed (ignored): {e}")
        if sid:
            ex["aborted_session_id"] = sid
            ex.pop("session_id", None)
        ex.pop("done", None)
        ex["aborted_at"] = _iso_now()
        req.execution = ex
        req.set_status(State.CARD_SENT)
        save(req)
        _log(f"inbox: {req.id} abort_execution -> card_sent")
    elif action == "revert_review":
        # v0.10.2 退回待验收：delivered -> review（验收撤回）。
        if str(req.status) != State.DELIVERED.value:
            _log(f"inbox: {req.id} revert_review ignored (status={req.status}) — no-op")
            return
        ex = dict(req.execution or {})
        ex.pop("accepted_at", None)
        ex["reverted_at"] = _iso_now()
        req.execution = ex
        req.set_status(State.REVIEW)
        save(req)
        _log(f"inbox: {req.id} revert_review -> review")
    else:
        _log(f"inbox: {req.id} unknown action {action!r} — ignored")


def _fold_comment(req: Requirement, comment: Optional[str]) -> None:
    if not comment:
        return
    stamp = _dt.date.today().isoformat()
    tag = f"[{stamp} 修改方向] {comment}"
    # fold into notes; also append as a plan addendum so the executor sees it
    req.notes = (req.notes + "\n" + tag).strip() if req.notes else tag
    if isinstance(req.plan, list):
        req.plan = req.plan + [tag]
    elif req.plan:
        req.plan = str(req.plan) + "\n" + tag
    else:
        req.plan = tag


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# (b) dispatch approved
# --------------------------------------------------------------------------- #
def dispatch_approved(cfg: config.Config) -> int:
    count = 0
    for req in load_all():
        if req.status != State.APPROVED.value:
            continue
        if req.execution and req.execution.get("session_id"):
            continue  # already dispatched
        if executor is None:
            _log(f"dispatch: executor unavailable, cannot dispatch {req.id}")
            continue
        try:
            executor.dispatch(req, cfg)
            _log(f"dispatch: {req.id} -> executing "
                 f"(session={ (req.execution or {}).get('session_id') })")
            count += 1
            # retry succeeded -> clear the failure left by a previous attempt.
            # Only when a session actually exists: a dispatch that "succeeded"
            # without a session_id must keep its error visible on the queued card.
            ex = dict(req.execution or {})
            if ex.get("session_id") and ("last_error" in ex or "last_error_at" in ex):
                ex.pop("last_error", None)
                ex.pop("last_error_at", None)
                req.execution = ex
                save(req)
        except Exception as e:  # noqa: BLE001 - keep the loop alive
            _log(f"dispatch: {req.id} FAILED: {e}\n{traceback.format_exc()}")
            # leave a trace on execution so the dashboard's queued item can show
            # dispatch_error (§2); status stays approved -> auto-retry next pass.
            err = str(e)[:300]
            try:
                ex = dict(req.execution or {})
                ex["last_error"] = err
                ex["last_error_at"] = _iso_now()
                req.execution = ex
                save(req)
            except Exception:  # noqa: BLE001 - bookkeeping must not block retry
                pass
            analytics.log_event("dispatch_failed", req=req.id, error=err[:120])
    return count


# --------------------------------------------------------------------------- #
# (c') trash retention purge (CONTRACT §9)
# --------------------------------------------------------------------------- #
def _parse_iso(ts: Optional[str]) -> Optional[_dt.datetime]:
    if not ts:
        return None
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = _dt.datetime.strptime(str(ts).strip(), "%Y-%m-%dT%H:%M:%SZ")
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def purge_trash(cfg: config.Config) -> int:
    """Hard-delete trashed items older than the retention window.

    Skips items with ``permanent`` set. ``retention_days <= 0`` disables the
    auto-purge entirely. A single bad item never aborts the pass.
    """
    days = int(cfg.trash_retention_days or 0)
    if days <= 0:
        return 0
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    purged = 0
    for req in load_all():
        try:
            if req.status != State.TRASHED.value:
                continue
            if req.permanent:
                continue
            trashed = _parse_iso(req.trashed_at)
            if trashed is None or trashed >= cutoff:
                continue
            if registry.delete(req):
                purged += 1
                _log(f"trash: purged {req.id} (trashed_at={req.trashed_at})")
        except Exception as e:  # noqa: BLE001 - one bad item must not abort the pass
            _log(f"trash: purge failed for {getattr(req, 'id', '?')}: {e}")
    return purged


# --------------------------------------------------------------------------- #
# (d) transition detection
# --------------------------------------------------------------------------- #
def _by_id(items: list[dict]) -> dict[str, dict]:
    return {i["id"]: i for i in items if i.get("id")}


def detect_transitions(prev: Optional[dict], curr: dict) -> list[tuple[str, str]]:
    """Return (title, body) notifications for prev->curr transitions."""
    msgs: list[tuple[str, str]] = []
    if prev is None:
        return msgs

    p_na, c_na = _by_id(prev.get("needs_approval", [])), _by_id(curr.get("needs_approval", []))
    p_run = _by_id(prev.get("running", []))
    p_ni, c_ni = _by_id(prev.get("needs_input", [])), _by_id(curr.get("needs_input", []))
    p_rev, c_rev = _by_id(prev.get("review", [])), _by_id(curr.get("review", []))

    # 3-tuples (title, body, req) so Slack ✅-reaction knows which R-id to approve
    # new card_sent
    for rid, item in c_na.items():
        if rid not in p_na:
            t, b = notify.msg_new_card(item.get("title", rid))
            msgs.append((t, b, rid))

    # executing -> review (§11 draft ready, awaiting acceptance)
    for rid, item in c_rev.items():
        if rid not in p_rev and rid in p_run:
            msgs.append(("待验收：AI 已交付草稿", item.get("name") or rid, rid))

    # executing -> blocked (newly needs_input, previously running)
    for rid, item in c_ni.items():
        if rid not in p_ni and rid in p_run:
            t, b = notify.msg_needs_input(item.get("name") or rid)
            msgs.append((t, b, rid))

    return msgs


def _check_auth_failures(notified: set[str]) -> list[tuple[str, str]]:
    """Scan executing items' logs for credential failures (notify once each)."""
    msgs: list[tuple[str, str]] = []
    for req in load_all():
        if req.status != State.EXECUTING.value:
            continue
        if req.id in notified:
            continue
        log = (req.execution or {}).get("log")
        if not log:
            continue
        try:
            text = Path(log).read_text(encoding="utf-8")
        except OSError:
            continue
        if notify.detect_auth_failure(text):
            notified.add(req.id)
            msgs.append(notify.msg_auth(req.title or "claude"))
    return msgs


# --------------------------------------------------------------------------- #
# auto-resume interrupted executing tasks
# --------------------------------------------------------------------------- #
def _reconcile_review_attach(req: Requirement, agents: dict[str, dict]) -> None:
    """待验收任务的 attach 回流 —— 只动投影层，不动状态机（registry 仍是 review）。

    Zelin 可能 ``claude attach`` 回原 session 继续输入，agent 重新 working：
    - roster working -> 在 execution 里记 ``_review_active=True``。dashboard 的
      分流看的是 roster 实况，这个标记只给 actd 自己做「返工轮结束」判断用；
    - 此前 ``_review_active`` 且现在 done/缺席 -> 这轮返工收工了：重新
      harvest_delivery 刷新 delivered_summary/final_draft（非空才覆盖旧值），
      并清掉标记。blocked 时标记保留（返工中途等输入，还没收工）。
    Best-effort：任何异常吞掉并记日志，绝不影响主循环。
    """
    try:
        ex = dict(req.execution or {})
        sid = ex.get("session_id")
        if not sid:
            return
        agent = agents.get(str(sid))
        state = (agent or {}).get("state", "") if agent else ""

        if agent and state in _RUNNING_STATES:
            if not ex.get("_review_active"):
                ex["_review_active"] = True
                req.execution = ex
                registry.save(req)
                _log(f"reconcile: {req.id} review-active（attach 回流，agent 重新工作）")
                analytics.log_event("review_active", req=req.id)
            return

        if ex.get("_review_active") and (agent is None or state in _DONE_STATES):
            # 返工轮结束 -> 重新收割交付物（收割失败/为空不覆盖旧值）
            if executor is not None:
                try:
                    harvested = executor.harvest_delivery(str(sid)) or {}
                except Exception as e:  # noqa: BLE001 - harvest is best-effort
                    harvested = {}
                    _log(f"reconcile: re-harvest {req.id} failed: {e}")
                if harvested.get("delivered_summary"):
                    ex["delivered_summary"] = harvested["delivered_summary"]
                if harvested.get("final_draft"):
                    ex["final_draft"] = harvested["final_draft"]
            ex.pop("_review_active", None)
            req.execution = ex
            registry.save(req)
            _log(f"reconcile: {req.id} 返工轮结束，已重新收割交付物")
            analytics.log_event("review_reharvested", req=req.id)
    except Exception as e:  # noqa: BLE001 - must never break the daemon pass
        _log(f"reconcile: review attach check {getattr(req, 'id', '?')} failed: {e}")


def reconcile_executing(cfg: config.Config, resume_notified: set[str]) -> int:
    """Auto-resume executing tasks whose background agent died (sleep / network
    loss / crash). Skips tasks that already finished. Exponential backoff so a
    long offline period (laptop closed, commute with no wifi) resumes cleanly
    once connectivity returns, instead of hammering.
    """
    try:
        agents = _index_agents(_run_claude_agents())
    except Exception:  # noqa: BLE001
        return 0

    # 待验收 attach 回流（§11 补充）：与 auto_resume 开关无关，所以放在开关之前。
    for req in registry.load_all():
        if req.status == registry.State.REVIEW.value:
            _reconcile_review_attach(req, agents)

    if not getattr(cfg, "auto_resume", True):
        return 0

    resumed = 0
    for req in registry.load_all():
        if req.status != registry.State.EXECUTING.value:
            continue
        ex = dict(req.execution or {})
        sid = ex.get("session_id")
        if not sid:
            continue  # can't safely auto-resume without a session id
        agent = agents.get(str(sid))
        state = (agent or {}).get("state", "") if agent else ""

        if agent and state in _LIVE_STATES:
            if ex.get("resume_attempts"):            # recovered — reset backoff
                ex["resume_attempts"] = 0
                req.execution = ex
                registry.save(req)
            resume_notified.discard(req.id)
            continue
        if agent and state in _BLOCKED_STATES:
            # waiting for the USER to answer (needs input) — NOT dead. Do NOT
            # resume (resuming a blocked agent spawns duplicates). Leave it be.
            if ex.get("resume_attempts"):
                ex["resume_attempts"] = 0
                req.execution = ex
                registry.save(req)
            resume_notified.discard(req.id)
            continue
        if agent and state in _DONE_STATES:
            if not ex.get("done"):                   # mark finished so a later
                ex["done"] = True                    # purge isn't mistaken for a crash
                ex["review_at"] = _iso_now()         # 进入待验收的时间（§2）
                # 收割交付物：transcript 最后一条 assistant 消息 -> delivered_summary
                # （chat 模式还有 FINAL DRAFT 全文）。收割失败绝不阻塞提升。
                try:
                    harvested = executor.harvest_delivery(str(sid)) or {}
                    if harvested.get("delivered_summary"):
                        ex["delivered_summary"] = harvested["delivered_summary"]
                    if harvested.get("final_draft"):
                        ex["final_draft"] = harvested["final_draft"]
                except Exception as e:  # noqa: BLE001 - harvest is best-effort
                    _log(f"reconcile: harvest_delivery {req.id} failed: {e}")
                req.execution = ex
                # §11: agent done = 草稿就绪，进入待验收（Zelin ✓验收/↩︎打回）。
                # 通知由 detect_transitions 的 running->review diff 发，避免双发。
                req.set_status(registry.State.REVIEW)
                registry.save(req)
                analytics.log_event("review_promoted", req=req.id)
            continue
        if ex.get("done"):
            # finished earlier; agent purged from the list — promote if missed
            if req.status == registry.State.EXECUTING.value:
                req.set_status(registry.State.REVIEW)
                registry.save(req)
            continue

        # dead (failed/stopped) or vanished-before-completing -> resume w/ backoff
        if ex.get("resume_exhausted"):
            continue
        attempts = int(ex.get("resume_attempts", 0))
        if attempts >= 5:
            ex["resume_exhausted"] = True
            req.execution = ex
            registry.save(req)
            notify.notify("自动恢复已放弃（连续失败 5 次），需要人工处理",
                          req.title or req.id)
            analytics.log_event("auto_resume_exhausted", req=req.id)
            continue
        backoff = min(600, 30 * (2 ** min(attempts, 5)))
        last = ex.get("last_resume_at")
        if last:
            try:
                prev = _dt.datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                elapsed = (_dt.datetime.now(_dt.timezone.utc) - prev).total_seconds()
                if elapsed < backoff:
                    continue
            except (ValueError, TypeError):
                pass
        if executor is None:
            continue
        try:
            ok = executor.resume(req, cfg)
            resumed += 1
            _log(f"reconcile: resume {req.id} attempt {attempts + 1} ok={ok}")
            analytics.log_event("auto_resume", req=req.id, ok=ok, attempt=attempts + 1)
            if attempts + 1 >= 3 and req.id not in resume_notified:
                resume_notified.add(req.id)
                notify.notify("任务疑似中断，正在自动恢复", req.title or req.id)
        except Exception as e:  # noqa: BLE001
            _log(f"reconcile: resume {req.id} FAILED: {e}")
    return resumed


# --------------------------------------------------------------------------- #
# raise expansion — ONE per pass (a slow claude -p; don't block on a batch)
# --------------------------------------------------------------------------- #
def process_raising(cfg: config.Config) -> int:
    if analyze is None:
        return 0
    pending = [r for r in registry.load_all()
               if r.status == registry.State.RAISING.value]
    if not pending:
        return 0
    req = sorted(pending, key=lambda r: r.id)[0]
    try:
        analyze.expand_debt(req)  # -> card_sent (or detected+note on failure)
        _log(f"raising: {req.id} expanded -> {req.status}")
        analytics.log_event("raise_expanded", req=req.id, status=str(req.status))
    except Exception as e:  # noqa: BLE001 - one bad expansion can't kill the loop
        _log(f"raising: {req.id} expand FAILED: {e}")
        req.set_status(registry.State.DETECTED)   # fall back so it's not stuck
        req.notes = ((req.notes or "") + " (raise 展开失败，退回欠账)").strip()
        registry.save(req)
    return 1


# --------------------------------------------------------------------------- #
# one pass + loop
# --------------------------------------------------------------------------- #
def run_once(
    cfg: config.Config,
    prev_dash: Optional[dict],
    auth_notified: set[str],
    resume_notified: Optional[set[str]] = None,
) -> dict:
    config.ensure_state_dirs()
    n_inbox = process_inbox()
    n_dispatched = dispatch_approved(cfg)
    # write-early：审批/派发刚落账就先写一次 dashboard，app 立刻看到 queued/executing
    # 回显，不用等 reconcile/raising（都可能慢）跑完；pass 尾部照常再写最终版。
    # 仅在真有变化时才写 —— 空闲 pass 不额外跑一次 build_dashboard（内含
    # `claude agents` 子进程 + 全量 registry 加载，白白翻倍热路径开销）。
    if n_inbox or n_dispatched:
        try:
            write_dashboard(build_dashboard(cfg=cfg))
        except Exception as e:  # noqa: BLE001 - early write is best-effort
            _log(f"early dashboard write FAILED: {e}")
    reconcile_executing(cfg, resume_notified if resume_notified is not None else set())
    process_raising(cfg)     # expand ONE 'raising' debt per pass (bounded block)
    purge_trash(cfg)
    dash = build_dashboard(cfg=cfg)
    write_dashboard(dash)

    for title, body, rid in detect_transitions(prev_dash, dash):
        notify.notify(title, body, req=rid)
    for title, body in _check_auth_failures(auth_notified):
        notify.notify(title, body)

    return dash


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="actd", description="assistant daemon loop")
    parser.add_argument("--once", action="store_true", help="one pass then exit")
    parser.add_argument("--interval", type=int, default=None, help="override poll seconds")
    args = parser.parse_args(argv)

    cfg = config.load_config()
    interval = args.interval or cfg.poll_interval_seconds or 10
    auth_notified: set[str] = set()
    resume_notified: set[str] = set()

    if args.once:
        try:
            run_once(cfg, None, auth_notified, resume_notified)
        except Exception as e:  # noqa: BLE001
            _log(f"run_once FAILED: {e}\n{traceback.format_exc()}")
            return 1
        return 0

    _log(f"actd starting (interval={interval}s, home={config.HOME})")
    prev_dash: Optional[dict] = None
    while True:
        try:
            prev_dash = run_once(cfg, prev_dash, auth_notified, resume_notified)
        except Exception as e:  # noqa: BLE001 - one bad pass must not kill loop
            _log(f"loop pass FAILED: {e}\n{traceback.format_exc()}")
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
