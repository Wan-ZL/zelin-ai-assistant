"""decisions — the card-level inbox verbs (CONTRACT §10 action set; §5.4 stale
guard; §11 accept/rework; §32.2 terminal-state doctrine; §44.3-S steer relay;
§4.1 re-arm on every path into approved; T-28 ingress 落款).

Full inbox action set — the ``_VERBS`` table IS the action whitelist/validation;
anything else is the logged ``unknown``:
  approve | reject(->trash) | comment | raise(debt->proposal)
    approve：v-next W17 —— 外部出身未扩写的卡转 raising（先扩写再复批）
    comment：v-next §44.3-S —— EXECUTING 卡 = steer 入队（owner ingress
      限定；agent/remote 只上卡记录，T-28）
  | trash(->recycle) | restore(recycle->prev) | pin(recycle->permanent)
  | accept(review->delivered) | rework(review->executing)
  | done_external(card_sent|review|approved|executing->delivered)
                                            (v0.10.2, 扩展 v0.12)
  | abort_execution(approved|executing->card_sent)      (v0.10.2)
  | stop_to_review(executing|approved->review, 收下成果待验收)
  | revert_review(delivered->review)                    (v0.10.2)
  | defer(card_sent->detected, back to the backlog)     (v0.18)
  | archive(delivered|detected->archived, relocate)     (v0.20.0)
  | unarchive(archived->prev_status, back to active)    (v0.20.0)
v0.10.2 公共规则：状态不匹配的逆向动作 = 幂等 no-op + log（防连点/迟到 inbox）。

Every verb returns a §5.4 result_status for the sync ack ledger:
  "running" = applied a real state change; "noop" = guarded/idempotent/
  stale no-op; "unknown" = unrecognised action. (Local Mac-app callers may
  ignore the return.) The board_seq precondition rides in the AAD + inbox
  file for provenance; expected_status is the enforced stale-guard (§5.4).
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Optional

from act.lib import analytics, registry, risk, self_improve, steer
from act.lib.actd import dispatch as _dispatch
from act.lib.actd.inbox import is_owner_ingress
from act.lib.actd.seam import Daemon, append_note
from act.lib.actd.session import harvest_into, update_search_index
from act.lib.actd.triage_guard import check_triage_registry_guard
from act.lib.registry import Requirement, State


@dataclass(frozen=True)
class _Input:
    """The per-decision fields a verb may read besides the card itself."""
    comment: Optional[str]
    expected_status: Optional[str]
    ts: Optional[object]
    via: Optional[object]
    stem: Optional[str]


def precondition_ok(req: Requirement, expected_status: Optional[str],
                    action: Optional[str] = None) -> bool:
    """§5.4 stale-guard: True unless the phone pinned an ``expected_status`` that
    no longer matches the card's current status (the card moved since the phone
    saw it — apply would rip a running/moved card, so caller no-ops).

    Projection alias (integration audit 2026-07-15): the 待验收 lane the phone
    renders is NOT a pure status view — the dashboard also projects an
    on-disk-EXECUTING card there once its roster agent is done (and with
    ``auto_resume: false`` nothing ever promotes it to on-disk review, so that
    shape can persist indefinitely). The phone pins expected_status="review" on
    every accept/rework from that lane, so for those two verbs "review" must be
    satisfied by the SAME relaxed surface the no-expected local path accepts
    (review OR executing — the accept branch's exact status whitelist; NOT
    gated on execution.done, which is never stamped in the auto_resume:false
    shape). Every other mismatch (trashed/card_sent/delivered/…) stays a stale
    no-op. The other pinned verbs have no such alias: the phone renders 修改
    only on non-processing 提案 cards (on-disk card_sent exactly — raising
    cards hide the action bar) and 研究并提议 only in the debt lane (detected
    exactly), so their pins always match at render time.
    """
    if expected_status is None:
        return True
    if str(req.status) == str(expected_status):
        return True
    if (action in ("accept", "rework")
            and str(expected_status) == State.REVIEW.value
            and str(req.status) == State.EXECUTING.value):
        return True
    return False


def apply_decision(d: Daemon, req: Requirement, action: Optional[str],
                   comment: Optional[str],
                   expected_status: Optional[str] = None,
                   board_seq=None,
                   ts: Optional[object] = None,
                   via: Optional[object] = None,
                   stem: Optional[str] = None) -> str:
    """Apply one card verb（see module docstring for the whitelist）."""
    # ---- central archived gate (nightly audit 2026-07-14) ----
    # An archived card's FILE lives in archive/ — any status write except
    # unarchive would strand a live-status card inside the archive dir (split
    # brain: dashboard shows it nowhere, purge rules stop applying). Every
    # action but unarchive is a guarded no-op.
    if str(req.status) == State.ARCHIVED.value and action != "unarchive":
        d.log(f"inbox: {req.id} {action} on archived card — no-op (unarchive first)")
        return "noop"
    verb = _VERBS.get(action) if isinstance(action, str) else None
    if verb is None:
        d.log(f"inbox: {req.id} unknown action {action!r} — ignored")
        return "unknown"
    return verb(d, req, _Input(comment, expected_status, ts, via, stem))


# --------------------------------------------------------------------------- #
# approve / reject / raise
# --------------------------------------------------------------------------- #
def _approve(d: Daemon, req: Requirement, inp: _Input) -> str:
    # idempotent: a double-click (or re-approve while already running) must
    # not re-dispatch and spawn a duplicate agent. WHITELIST (nightly audit
    # 2026-07-14): the old blacklist let a late/replayed approve flip
    # trashed/merged/raising cards straight to approved — dispatching
    # deleted or mid-expansion work. Only a live proposal may be approved.
    if str(req.status) not in (State.DETECTED.value, State.CARD_SENT.value):
        d.log(f"inbox: {req.id} approve ignored (status={req.status})")
        return "noop"
    # W17（amendments §W17/§50）：外部出身卡强制 plan expansion——未经展开
    # （plan/DoD 双空）的 approve 转 raise 走既有扩写管线，绝不裸批。
    et = risk.effective_tier(req)
    if et.forced_expand and not (req.plan or req.definition_of_done):
        return _approve_forced_expand(d, req, et)
    req.set_status(State.APPROVED)
    # approval timestamp (add-only bookkeeping, like accepted_at) — lets
    # the dispatch event report wait_s (approve -> launch latency).
    ex = dict(req.execution or {})
    ex["approved_at"] = d.iso_now()
    # §4.1 storm brake：批准 = 重新上膛。上一轮派发的失败台账随新批准
    # 清零，否则退回提案再批准的卡会带着旧刹车直接停在原地。
    req.execution = _dispatch.rearm_dispatch(d, ex)
    d.save(req)
    # lifecycle milestone (docs/TELEMETRY.md): first genuine approval on
    # this install. The idempotent guard above means re-approvals of an
    # already-running card never reach here, so only real approvals count.
    analytics.log_first("milestone_first_approval", req=req.id)
    d.log(f"inbox: {req.id} approved")
    return "running"


def _approve_forced_expand(d: Daemon, req: Requirement, et) -> str:
    if d.analyze is None:
        # fail-closed：扩写管线不可用时宁可不批（外部卡裸跑正是 W17
        # 要堵的洞）
        d.log(f"inbox: {req.id} approve blocked (W17 forced expansion, "
              f"analyze unavailable) — stays {req.status}")
        return "noop"
    if "[W17]" not in (req.notes or ""):
        append_note(req, "[W17] 外部来源强制展开：批准已转为先扩写、复批后才执行")
    req.set_status(State.RAISING)
    d.save(req)
    d.log(f"inbox: {req.id} approve -> raising (W17 forced expansion, "
          f"{et.reason})")
    return "running"


def _reject(d: Daemon, req: Requirement, inp: _Input) -> str:
    d.stop_live_session(req, "reject")  # nightly audit: never orphan a live agent
    registry.trash(req, "rejected")  # recoverable, not a bare rejected status
    d.log(f"inbox: {req.id} rejected -> trash")
    return "running"


def _raise(d: Daemon, req: Requirement, inp: _Input) -> str:
    if d.analyze is None:
        d.log(f"inbox: {req.id} raise requested but analyze unavailable — ignored")
        return "noop"
    # §5.4 stale-guard (SYNC only): a phone-pinned expected_status that no
    # longer matches → no-op (never re-raise a card the board already moved
    # past the backlog). LOCAL callers send no expected_status, so this
    # passes and raise applies unconditionally as on main.
    if not precondition_ok(req, inp.expected_status):
        d.log(f"inbox: {req.id} raise stale "
              f"(expected {inp.expected_status}, is {req.status}) — no-op")
        return "noop"
    if str(req.status) == State.RAISING.value:
        d.log(f"inbox: {req.id} raise already raising — no-op")
        return "noop"
    if str(req.status) not in (State.DETECTED.value, State.CARD_SENT.value):
        # CONTRACT §32.2 (audit 2026-07-15): a late/replayed raise from a
        # stale board must never rip a card past approval back to raising
        # (approved→raising silently cancels the approval: dispatch never
        # picks it up) nor resurrect a terminal card. Backlog/proposal only;
        # card_sent stays allowed — the local web/board deliberately offers
        # 研究并提议 there (see test_actd_sync raise cases).
        d.log(f"inbox: {req.id} raise ignored (status={req.status}) — no-op")
        return "noop"
    # Fast: just mark it 'raising' so it shows a processing spinner in 待审批
    # immediately. The slow claude -p expansion happens in process_raising(),
    # one item per loop pass, so 4 raises don't freeze the daemon for minutes.
    req.set_status(State.RAISING)
    d.save(req)
    d.log(f"inbox: {req.id} -> raising (queued for AI expansion)")
    return "running"


# --------------------------------------------------------------------------- #
# comment（fold / steer / record）
# --------------------------------------------------------------------------- #
_COMMENT_TERMINAL = (State.TRASHED.value, State.MERGED.value, State.REJECTED.value)


def _comment(d: Daemon, req: Requirement, inp: _Input) -> str:
    # T-28 ingress 落款：steer（OWNER UPDATE 直发 live session）与「折叠 +
    # 退回重批」都是 owner 专属动作——agent/remote ingress 的评论只上卡
    # 记录，绝不 steer、绝不动状态机（trust-grant 时刻按 ingress 裁决）。
    if not is_owner_ingress(inp.via):
        return record_nonowner_comment(d, req, inp.comment, inp.via)
    # §5.4 stale-guard (SYNC only): when the phone pinned an expected_status
    # that no longer matches, a stale 修改 must not rip a moved card back to
    # card_sent. LOCAL callers (Mac app / web) send no expected_status, so
    # this passes and comment applies unconditionally exactly as on main —
    # the web renders 修改 on RAISING/processing cards too, and folding one
    # back to card_sent for re-approval is the intended local behavior.
    if not precondition_ok(req, inp.expected_status):
        d.log(f"inbox: {req.id} comment stale "
              f"(expected {inp.expected_status}, is {req.status}) — no-op")
        return "noop"
    if str(req.status) in _COMMENT_TERMINAL:
        # CONTRACT §32.2 (audit 2026-07-15): a late comment on a terminal
        # card must not fall through to the card_sent write below — that
        # resurrects a rejected/merged card as a live proposal with its
        # trash/merge bookkeeping still attached.
        d.log(f"inbox: {req.id} comment ignored (status={req.status} is "
              f"terminal) — no-op")
        return "noop"
    if str(req.status) == State.EXECUTING.value:
        return _steer(d, req, inp)
    fold_comment(req, inp.comment)
    return _fold_ack(d, req)


def _steer(d: Daemon, req: Requirement, inp: _Input) -> str:
    """§44.3-S steer relay：运行中卡上的评论是对 live session 的中途
    转向指令，不再「折叠 + 记录即止」。入队等 reconcile 的安全窗口
    （roster blocked / dead-resume）flush；状态机零改动。"""
    ts_str = (str(inp.ts) if isinstance(inp.ts, (str, int, float))
              and str(inp.ts).strip() else None)
    note = steer.enqueue_steer(req, inp.comment, ts=ts_str, stem=inp.stem)
    if note is None:
        d.log(f"inbox: {req.id} steer noop（重放/空文本，未入队）")
        return "noop"
    # v0.47 判例保全（test_inbox_guards LateComment）：owner 在运行中
    # 卡上打的字要在卡面（notes）留永久记录——steer 台账是环形（会
    # 轮转掉），notes 不轮转。行文法刻意避开 [修改方向]：那是 fold
    # 的印记，§44.3-S 明确 steer 不折叠、不触发重批。
    append_note(req, f"[{_dt.date.today().isoformat()} 追加指令] {note['text']}")
    d.save(req)
    d.log(f"inbox: {req.id} comment -> steer queued (key={note['key']})")
    analytics.log_event("inbox_steer", req=req.id)
    return "running"


def _fold_ack(d: Daemon, req: Requirement) -> str:
    """nightly audit 2026-07-14: a comment landing on a card that is
    already past approval must NOT rip it back to card_sent — that
    orphans a live agent (execution.session_id survives, and the next
    approve re-dispatches against a stale session). Past-approval
    states keep their status; the note is folded for the record (review
    has its own formal channel: rework)."""
    if str(req.status) == State.APPROVED.value:
        # pre-dispatch: the folded note rides into the dispatch prompt —
        # the direction change genuinely lands, so "running" is honest.
        d.save(req)
        d.log(f"inbox: {req.id} comment folded (approved kept, pre-dispatch)")
        return "running"
    if str(req.status) in (State.REVIEW.value, State.DELIVERED.value):
        # post-dispatch: nothing consumes the folded note — the live agent
        # never sees it. Fold for the record but ack "noop" so a phone's
        # §5.4 ledger never shows 已生效 for a direction change that had
        # no effect (audit review 2026-07-14). review 的正式改方向通道是
        # rework（打回）。EXECUTING 不再走这条记录即止——§44.3-S
        # steer 分支把它接进了 live session。
        d.save(req)
        d.log(f"inbox: {req.id} comment folded (status {req.status} kept — "
              f"note is record-only, acking noop)")
        return "noop"
    req.set_status(State.CARD_SENT)  # stays pending, re-approval
    d.save(req)
    d.log(f"inbox: {req.id} comment folded — re-approval pending")
    return "running"


def record_nonowner_comment(d: Daemon, req: Requirement, comment: Optional[str],
                            via: object) -> str:
    """agent/remote 评论的记录面（T-28）：上卡可见（notes），但不折进 plan
    （plan 是喂给 executor 的指令面——非 owner 文本进 plan 就是绕道 steer）、
    不 enqueue steer、不改状态。空文本只记日志；via 进日志供取证。返回 §5.4
    ack：记录落卡 = "running"（这就是该动作的全部效果），空文本 = "noop"。"""
    body = comment.strip() if isinstance(comment, str) else ""
    if not body:
        d.log(f"inbox: {req.id} comment (via={via!r}) empty — ignored")
        return "noop"
    label = "agent" if via == "agent" else "remote"
    append_note(req, f"[{_dt.date.today().isoformat()} {label} 备注] {body}")
    d.save(req)
    d.log(f"inbox: {req.id} comment recorded (via={label}, no steer, "
          f"status stays {req.status})")
    return "running"


def fold_comment(req: Requirement, comment: Optional[str]) -> None:
    if not comment:
        return
    stamp = _dt.date.today().isoformat()
    tag = f"[{stamp} 修改方向] {comment}"
    # fold into notes; also append as a plan addendum so the executor sees it
    append_note(req, tag)
    if isinstance(req.plan, list):
        req.plan = req.plan + [tag]
    elif req.plan:
        req.plan = str(req.plan) + "\n" + tag
    else:
        req.plan = tag


# --------------------------------------------------------------------------- #
# trash lane: trash / restore / pin
# --------------------------------------------------------------------------- #
def _trash(d: Daemon, req: Requirement, inp: _Input) -> str:
    d.stop_live_session(req, "trash")  # nightly audit: never orphan a live agent
    registry.trash(req, "deleted")
    d.log(f"inbox: {req.id} trashed (deleted)")
    return "running"


def _restore(d: Daemon, req: Requirement, inp: _Input) -> str:
    # nightly audit 2026-07-14: restore is trash-lane-only — replayed on a
    # live card it would rewrite status to prev_status-or-detected (an
    # executing card silently became detected while its agent kept running).
    if str(req.status) != State.TRASHED.value:
        d.log(f"inbox: {req.id} restore ignored (status={req.status}, not trashed)")
        return "noop"
    registry.restore(req)
    d.log(f"inbox: {req.id} restored -> {req.status}")
    return "running"


def _pin(d: Daemon, req: Requirement, inp: _Input) -> str:
    registry.pin(req)
    d.log(f"inbox: {req.id} pinned permanent")
    return "running"


# --------------------------------------------------------------------------- #
# review lane: accept / rework / revert_review
# --------------------------------------------------------------------------- #
def _stop_for(d: Daemon, req: Requirement, ex: dict, sid, why: str) -> None:
    """§46 确认式停止（失败落台账，绝不阻塞调用方的状态落账）——仅当有 session
    且 executor 可用。"""
    if sid and d.executor is not None:
        d.stop_session_tracked(req, ex, sid, why)


def _accept(d: Daemon, req: Requirement, inp: _Input) -> str:
    # §11 验收通过 -> delivered（归档）；accepted_at 供 completed 行显示（§2）
    # §5.4 stale-guard (SYNC only): a phone-pinned expected_status mismatch
    # → no-op (a stale tap must not re-deliver a card that already moved).
    # LOCAL callers send no expected_status, so accept applies exactly as on
    # main — CRUCIALLY the 待验收 lane also holds cards whose on-disk status
    # is still EXECUTING (agent done, not yet promoted: process_inbox runs
    # BEFORE reconcile_executing), so a local 验收 must land regardless of
    # the current status. A hard REVIEW-only precondition would silently
    # no-op those and, with auto_resume:false, break accept forever. The
    # phone pins expected_status="review" from that same projected lane, so
    # precondition_ok grants the review⇄executing alias for this verb.
    if not precondition_ok(req, inp.expected_status, "accept"):
        d.log(f"inbox: {req.id} accept stale "
              f"(expected {inp.expected_status}, is {req.status}) — no-op")
        return "noop"
    # nightly audit 2026-07-14: accept needs work to accept. The 待验收
    # lane can hold on-disk EXECUTING cards (see above), so executing and
    # review are both legal; delivered is an idempotent double-click. But
    # a replayed accept on a never-dispatched card (detected/card_sent/
    # raising/…) must not teleport it to delivered.
    if str(req.status) == State.DELIVERED.value:
        d.log(f"inbox: {req.id} accept ignored (already delivered)")
        return "noop"
    if str(req.status) not in (State.EXECUTING.value, State.REVIEW.value):
        d.log(f"inbox: {req.id} accept ignored (status={req.status}, no delivery to accept)")
        return "noop"
    ex = dict(req.execution or {})
    # a chat-mode delivery promoted from blocked leaves its bg session
    # alive waiting for input FOREVER (a bg session never exits on its
    # own) — mirror done_external: best-effort stop the reaped agent,
    # never block the delivered write (audit 2026-07-15). §46 确认式：
    # 停不掉的落台账，不再静默。
    _stop_for(d, req, ex, ex.get("session_id"), "accept")
    req.set_status(State.DELIVERED)
    ex["accepted_at"] = d.iso_now()
    req.execution = ex
    d.save(req)
    d.log(f"inbox: {req.id} accepted -> delivered")
    return "running"


def _rework(d: Daemon, req: Requirement, inp: _Input) -> str:
    # §11 打回：把 Zelin 的反馈送回原 session 继续（executor.rework 处理
    # stop-idle-then-resume），状态回 executing
    if d.executor is None:
        d.log(f"inbox: {req.id} rework requested but executor unavailable — ignored")
        return "noop"
    if not (inp.comment or "").strip():
        d.log(f"inbox: {req.id} rework with empty feedback — ignored")
        return "noop"
    # §5.4 stale-guard (SYNC only): a phone-pinned expected_status mismatch
    # → no-op (a stale tap must not reopen/double-run a card that moved).
    # LOCAL callers send no expected_status, so rework applies as on main —
    # including the 待验收 EXECUTING-done case (process_inbox runs BEFORE
    # reconcile_executing promotes it to review). executor.rework itself
    # handles stop-idle-then-resume, so an on-disk EXECUTING card is safe.
    # The phone pins expected_status="review" from that same projected
    # lane, so precondition_ok grants the review⇄executing alias here too.
    if not precondition_ok(req, inp.expected_status, "rework"):
        d.log(f"inbox: {req.id} rework stale "
              f"(expected {inp.expected_status}, is {req.status}) — no-op")
        return "noop"
    ok = d.executor.rework(req, inp.comment)
    if not ok:
        # executor.rework bailed (no session / transcript purged / launch
        # failed): the card did NOT go back to executing, so acking
        # "running" would show 已生效 for a 打回 that never started
        # (§5.4 honesty, audit 2026-07-15).
        d.log(f"inbox: {req.id} rework NOT sent (ok=False) — card unchanged")
        return "noop"
    d.log(f"inbox: {req.id} rework sent — back to executing")
    return "running"


def _revert_review(d: Daemon, req: Requirement, inp: _Input) -> str:
    # v0.10.2 退回待验收：delivered -> review（验收撤回）。
    if str(req.status) != State.DELIVERED.value:
        d.log(f"inbox: {req.id} revert_review ignored (status={req.status}) — no-op")
        return "noop"
    ex = dict(req.execution or {})
    ex.pop("accepted_at", None)
    ex["reverted_at"] = d.iso_now()
    req.execution = ex
    req.set_status(State.REVIEW)
    d.save(req)
    d.log(f"inbox: {req.id} revert_review -> review")
    return "running"


# --------------------------------------------------------------------------- #
# the three「停」verbs: done_external / abort_execution / stop_to_review
# --------------------------------------------------------------------------- #
# 三个「停」动作的分工：done_external =「我在系统外做完了」直接落
# delivered 跳过验收；abort_execution =「不要了」丢弃成果退回待审批；
# stop_to_review =「停下来我看看它做了什么」—— 停 agent、收下成果、
# 落 待验收 让 Zelin ✓验收/↩︎打回，绝不跳过验收。
def _harvest_and_stop(d: Daemon, req: Requirement, ex: dict, sid, verb: str) -> None:
    """executing 且有 session：先 best-effort 收割交付物（非空才写
    delivered_summary/final_draft，失败只 log），再 best-effort
    stop_session 清掉挂着的 agent（§46 确认式：失败落台账，不阻塞落账），
    再刷 §37 session-content layer。"""
    err = harvest_into(d, req, ex, sid)
    if err is not None:
        d.log(f"inbox: {req.id} {verb} — "
              f"harvest_delivery({sid}) failed (ignored): {err}")
    d.stop_session_tracked(req, ex, sid, verb)
    update_search_index(d, req.id, sid)


_DONE_EXTERNAL_FROM = (State.CARD_SENT.value, State.REVIEW.value,
                       State.APPROVED.value, State.EXECUTING.value)


def _done_external(d: Daemon, req: Requirement, inp: _Input) -> str:
    # v0.10.2 已办完（系统外完成）：card_sent|review -> delivered。有活
    # session 不动它 —— 人做完了，AI 会话自然闲置。
    # v0.12 扩展：approved|executing 也允许 —— agent 停在 blocked 等输入、
    # 但 Zelin 已在 attach 会话里拿到交付时，这是唯一的完成出口。
    #   executing 且有 session：先 best-effort 收割交付物，再 best-effort
    #   stop_session 清掉挂着的 blocked agent（失败只 log，不阻塞落账）；
    #   approved（排队未派发）：直接落账，无 harvest/stop。
    prev_status = str(req.status)
    if prev_status not in _DONE_EXTERNAL_FROM:
        d.log(f"inbox: {req.id} done_external ignored (status={req.status}) — no-op")
        return "noop"
    ex = dict(req.execution or {})
    sid = ex.get("session_id")
    if prev_status == State.EXECUTING.value and sid and d.executor is not None:
        _harvest_and_stop(d, req, ex, sid, "done_external")
    ex["accepted_at"] = d.iso_now()
    req.execution = ex
    append_note(req, "[done outside] Zelin 在系统外完成")
    req.set_status(State.DELIVERED)
    d.save(req)
    d.log(f"inbox: {req.id} done_external ({prev_status}) -> delivered")
    return "running"


_ABORTABLE = (State.APPROVED.value, State.EXECUTING.value, State.REVIEW.value)


def _abort_execution(d: Daemon, req: Requirement, inp: _Input) -> str:
    # v0.10.2 停止并退回待审批：approved|executing -> card_sent。活 session
    # 先 best-effort 停止（stop 失败只记日志，绝不阻塞状态回退）；session_id
    # 归档到 aborted_session_id 后删除，保证重新批准时干净重派发。
    # v0.28.1 §30: review is allowed too — a 待验收 card routed into 运行中
    # by attach-reactivated session activity; 「退回提案」 discards this
    # reattached run and kicks it back to card_sent for a fresh decision.
    if str(req.status) not in _ABORTABLE:
        d.log(f"inbox: {req.id} abort_execution ignored (status={req.status}) — no-op")
        return "noop"
    ex = dict(req.execution or {})
    sid = ex.get("session_id")
    _stop_for(d, req, ex, sid, "abort")
    if sid:
        ex["aborted_session_id"] = sid
        ex.pop("session_id", None)
    ex.pop("done", None)
    ex["aborted_at"] = d.iso_now()
    # §4.1：退回提案 = 丢弃这一轮，派发失败台账（含 dispatch_halted）一并
    # 清掉——否则 card_sent 卡带着刹车回到待审批，policy 免批通道会把它
    # 原样再推进 approved，永远停在「需输入」（审查复现 2026-09-01）。
    req.execution = _dispatch.rearm_dispatch(d, ex)
    req.set_status(State.CARD_SENT)
    d.save(req)
    d.log(f"inbox: {req.id} abort_execution -> card_sent")
    return "running"


_STOPPABLE_TO_REVIEW = (State.EXECUTING.value, State.APPROVED.value, State.REVIEW.value)


def _stop_to_review(d: Daemon, req: Requirement, inp: _Input) -> str:
    # 手动停止转待验收（「去待验收」）：executing（+ approved）-> review。
    #   executing 且有 session：先 best-effort harvest_delivery（非空才写
    #   delivered_summary/final_draft），再 best-effort stop_session 停掉
    #   跑着的 agent；两步都吞异常只记日志，绝不阻塞状态落 review。
    #   approved（排队未派发，无 session）：harvest 为空，直接落 review
    #   （空交付物，待验收卡照常渲染，不崩）。
    #   review（v0.28.1 §30：会话有新活动被路由进「运行中」的卡，registry
    #   仍是 review、带活 session）：停掉 attach 回流的 session、重新收割成果、
    #   留在 review —— 「去待验收」在这种卡上就是「停下我看看它这轮跑了什么」。
    prev_status = str(req.status)
    if prev_status not in _STOPPABLE_TO_REVIEW:
        d.log(f"inbox: {req.id} stop_to_review ignored (status={req.status}) — no-op")
        return "noop"
    ex = dict(req.execution or {})
    sid = ex.get("session_id")
    # harvest whenever a live session exists (executing OR a review card with
    # an attach-reactivated session); approved has no sid so this skips.
    if sid and d.executor is not None:
        _harvest_and_stop(d, req, ex, sid, "stop_to_review")
    # §34bis 机械护栏终点：手动「去待验收」也是一次收割提升 —— preset 清理卡同样比对
    # 起止快照（少了这一刀，手动停出的卡永不检查、快照侧文件永不消费；无 ref 零开销）。
    check_triage_registry_guard(d, req, ex)
    self_improve.harvest_hook(req, ex, log=d.log)   # §65.3 self_improve 卡：gh 核验
    # mirror the natural executing->review transition's review fields
    # (reconcile_executing §2/§11): done flag + review_at, so the 待验收 card
    # renders (dashboard reads execution.review_at) and a later purge is
    # never mistaken for a crash needing auto-resume.
    ex["done"] = True
    ex["review_at"] = d.iso_now()
    req.execution = ex
    append_note(req, "[stopped by user] 手动停止，已收下成果待验收")
    req.set_status(State.REVIEW)
    d.save(req)
    d.log(f"inbox: {req.id} stop_to_review ({prev_status}) -> review")
    return "running"


# --------------------------------------------------------------------------- #
# backlog / archive: defer / archive / unarchive
# --------------------------------------------------------------------------- #
def _defer(d: Daemon, req: Requirement, inp: _Input) -> str:
    # v0.18 存备选：card_sent -> detected（退回备选）。Deliberately NOT
    # trash: a deferred card keeps its expanded summary/plan/sources/
    # repeated_mentions and stays in merge_or_new matching (restatements
    # merge in; radar act-now re-promotes) — trashed cards are excluded
    # and would re-card from scratch. Only card_sent is allowed (raising
    # finishes its expansion and becomes card_sent first); anything else
    # is the v0.10.2 idempotent no-op. Undo = the backlog lane's raise.
    if str(req.status) != State.CARD_SENT.value:
        d.log(f"inbox: {req.id} defer ignored (status={req.status}) — no-op")
        return "noop"
    append_note(req, "[deferred] 暂缓，入库")
    req.set_status(State.DETECTED)
    d.save(req)
    d.log(f"inbox: {req.id} defer -> detected (backlog)")
    return "running"


def _archive(d: Daemon, req: Requirement, inp: _Input) -> str:
    # v0.20.0 封存线程 (§3.7): archive is reachable ONLY from 已验收
    # (delivered) or 备选 (detected) per Q2; anything else is the v0.10.2
    # idempotent no-op. registry.archive relocates the card to archive/ and
    # stamps prev_status/archived_at/archive_reason.
    if str(req.status) not in (State.DELIVERED.value, State.DETECTED.value):
        d.log(f"inbox: {req.id} archive ignored (status={req.status}) — no-op")
        return "noop"
    prev = str(req.status)
    registry.archive(req, reason="user")
    d.log(f"inbox: {req.id} archived (from {prev})")
    return "running"


def _unarchive(d: Daemon, req: Requirement, inp: _Input) -> str:
    # v0.20.0 取消归档 (§3.7): archived -> prev_status, file back to active dir.
    if str(req.status) != State.ARCHIVED.value:
        d.log(f"inbox: {req.id} unarchive ignored (status={req.status}) — no-op")
        return "noop"
    registry.unarchive(req)
    d.log(f"inbox: {req.id} unarchived -> {req.status}")
    return "running"


_VERBS = {
    "approve": _approve,
    "reject": _reject,
    "comment": _comment,
    "raise": _raise,
    "trash": _trash,
    "restore": _restore,
    "pin": _pin,
    "accept": _accept,
    "rework": _rework,
    "done_external": _done_external,
    "abort_execution": _abort_execution,
    "stop_to_review": _stop_to_review,
    "revert_review": _revert_review,
    "defer": _defer,
    "archive": _archive,
    "unarchive": _unarchive,
}
