"""Inverse inbox actions — frozen contract v0.10.2 (CONTRACT §10).

done_external   : card_sent | review | approved | executing -> delivered
                  (v0.12 widened from card_sent | review); execution.accepted_at
                  set; notes get "[done outside] Zelin 在系统外完成".
                  card_sent | review : a live session is left alone (the human
                  finished, the AI session idles out).
                  executing + session: best-effort executor.harvest_delivery
                  first (non-empty results only are written to
                  delivered_summary/final_draft, a failure is just logged),
                  then best-effort executor.stop_session (clears a hanging
                  blocked agent; failure NEVER blocks the delivery).
                  approved (queued, undispatched): straight to delivered,
                  no harvest/stop.
abort_execution : approved | executing | review -> card_sent; live session
                  stopped best-effort via executor.stop_session (a stop failure
                  NEVER blocks the state rollback); execution.session_id archived
                  to aborted_session_id then removed (clean re-dispatch on
                  re-approval); execution.done dropped; aborted_at recorded.
                  (v0.28.1 §30: review added — a 待验收 card routed into 运行中
                  by an attach-reactivated session can be discarded from there.)
revert_review   : delivered -> review; execution.accepted_at dropped;
                  reverted_at recorded.

Common rule: any status outside the allowed set = idempotent no-op + log
(double-click / late-inbox protection). Every action auto-logs the existing
``inbox_{action}`` analytics event.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import json
import subprocess
import unittest
import uuid
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, executor
from act.lib import analytics, config, registry
from act.lib.registry import Requirement, State


def _mk_req(req_id="R-800", status=State.EXECUTING.value, execution=None, notes=""):
    req = Requirement(id=req_id, title="逆向动作测试", status=status,
                      execution=execution, notes=notes)
    registry.save(req)
    return req


def _drop_inbox(action, req_id):
    config.ensure_state_dirs()
    path = config.INBOX_DIR / f"{uuid.uuid4()}.json"
    path.write_text(
        json.dumps({"id": req_id, "action": action, "comment": None,
                    "ts": "2026-07-08T00:00:00Z"}),
        encoding="utf-8",
    )
    return path


class InverseActionsBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()

    def _run(self, action, req_id="R-800"):
        """Drop one inbox decision, run process_inbox, reload from disk."""
        _drop_inbox(action, req_id)
        n = actd.process_inbox()
        self.assertEqual(n, 1)
        # inbox file must be consumed (read then deleted) even on a no-op
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])
        return registry.load(req_id)


# --------------------------------------------------------------------------- #
# done_external — original paths: card_sent | review -> delivered
# --------------------------------------------------------------------------- #
class DoneExternalTestCase(InverseActionsBase):
    def test_from_card_sent_delivers_and_stamps(self):
        _mk_req(status=State.CARD_SENT.value)
        req = self._run("done_external")
        self.assertEqual(req.status, State.DELIVERED.value)
        self.assertTrue((req.execution or {}).get("accepted_at"))
        self.assertIn("[done outside] Zelin 在系统外完成", req.notes)

    def test_from_review_leaves_live_session_alone(self):
        _mk_req(status=State.REVIEW.value,
                execution={"session_id": "sess-9", "done": True})
        stub = mock.Mock()
        with mock.patch.object(actd.executor, "stop_session", stub):
            req = self._run("done_external")
        stub.assert_not_called()  # 有活 session 不动它
        self.assertEqual(req.status, State.DELIVERED.value)
        ex = req.execution or {}
        self.assertEqual(ex.get("session_id"), "sess-9")
        self.assertTrue(ex.get("done"))
        self.assertTrue(ex.get("accepted_at"))

    def test_preserves_existing_notes(self):
        _mk_req(status=State.CARD_SENT.value, notes="原有备注")
        req = self._run("done_external")
        self.assertTrue(req.notes.startswith("原有备注"))
        self.assertIn("[done outside]", req.notes)

    def test_wrong_status_is_idempotent_noop(self):
        # v0.12: approved|executing are now ALLOWED — the no-op set shrinks.
        wrong = (State.DETECTED.value, State.RAISING.value,
                 State.DELIVERED.value, State.TRASHED.value)
        for i, st in enumerate(wrong):
            rid = f"R-81{i}"
            _mk_req(req_id=rid, status=st)
            req = self._run("done_external", req_id=rid)
            self.assertEqual(req.status, st, msg=st)
            self.assertFalse((req.execution or {}).get("accepted_at"), msg=st)
            self.assertNotIn("[done outside]", req.notes or "", msg=st)

    def test_replay_does_not_overwrite_accepted_at(self):
        _mk_req(status=State.CARD_SENT.value)
        req = self._run("done_external")
        first = (req.execution or {}).get("accepted_at")
        self.assertTrue(first)
        # replay: already delivered -> no-op; a sentinel clock proves no rewrite
        with mock.patch.object(actd, "_iso_now",
                               return_value="9999-01-01T00:00:00Z"):
            req = self._run("done_external")
        self.assertEqual(req.status, State.DELIVERED.value)
        self.assertEqual((req.execution or {}).get("accepted_at"), first)


# --------------------------------------------------------------------------- #
# done_external — v0.12 extension: approved | executing -> delivered
# （agent 停在 blocked 等输入、但 Zelin 已在 attach 会话里拿到交付的完成出口）
# --------------------------------------------------------------------------- #
class DoneExternalExtendedTestCase(InverseActionsBase):
    def _run_with_executor(self, harvest, stop, req_id="R-800"):
        with mock.patch.object(actd.executor, "harvest_delivery", harvest), \
             mock.patch.object(actd.executor, "stop_session", stop):
            return self._run("done_external", req_id=req_id)

    def test_from_executing_harvests_stops_and_delivers(self):
        _mk_req(status=State.EXECUTING.value,
                execution={"session_id": "sess-11", "log": "/tmp/x.log"})
        harvest = mock.Mock(return_value={"delivered_summary": "交付小结",
                                          "final_draft": "FINAL 全文草稿"})
        stop = mock.Mock(return_value=True)
        req = self._run_with_executor(harvest, stop)
        harvest.assert_called_once_with("sess-11")   # 先收割……
        stop.assert_called_once_with("sess-11")      # ……再清掉挂着的 agent
        self.assertEqual(req.status, State.DELIVERED.value)
        ex = req.execution or {}
        self.assertEqual(ex.get("delivered_summary"), "交付小结")
        self.assertEqual(ex.get("final_draft"), "FINAL 全文草稿")
        self.assertTrue(ex.get("accepted_at"))
        self.assertEqual(ex.get("session_id"), "sess-11")  # 不归档不删除
        self.assertEqual(ex.get("log"), "/tmp/x.log")      # 其余字段保留
        self.assertIn("[done outside] Zelin 在系统外完成", req.notes)

    def test_empty_harvest_never_overwrites_existing_fields(self):
        _mk_req(status=State.EXECUTING.value,
                execution={"session_id": "sess-12",
                           "delivered_summary": "旧小结",
                           "final_draft": "旧草稿"})
        harvest = mock.Mock(return_value={"delivered_summary": None,
                                          "final_draft": None})
        req = self._run_with_executor(harvest, mock.Mock(return_value=True))
        self.assertEqual(req.status, State.DELIVERED.value)
        ex = req.execution or {}
        self.assertEqual(ex.get("delivered_summary"), "旧小结")  # 非空才写
        self.assertEqual(ex.get("final_draft"), "旧草稿")
        self.assertTrue(ex.get("accepted_at"))

    def test_harvest_failure_only_logged_stop_still_runs(self):
        _mk_req(status=State.EXECUTING.value,
                execution={"session_id": "sess-13"})
        harvest = mock.Mock(side_effect=RuntimeError("transcript exploded"))
        stop = mock.Mock(return_value=True)
        req = self._run_with_executor(harvest, stop)
        harvest.assert_called_once_with("sess-13")   # harvest 炸了……
        stop.assert_called_once_with("sess-13")      # ……stop 照跑
        self.assertEqual(req.status, State.DELIVERED.value)
        ex = req.execution or {}
        self.assertNotIn("delivered_summary", ex)
        self.assertNotIn("final_draft", ex)
        self.assertTrue(ex.get("accepted_at"))
        self.assertIn("[done outside]", req.notes)

    def test_stop_failure_never_blocks_delivery(self):
        _mk_req(status=State.EXECUTING.value,
                execution={"session_id": "sess-14"})
        harvest = mock.Mock(return_value={"delivered_summary": "小结",
                                          "final_draft": None})
        stop = mock.Mock(side_effect=RuntimeError("claude stop exploded"))
        req = self._run_with_executor(harvest, stop)
        stop.assert_called_once_with("sess-14")      # stop 被调且失败……
        self.assertEqual(req.status, State.DELIVERED.value)  # ……不阻塞落账
        ex = req.execution or {}
        self.assertEqual(ex.get("delivered_summary"), "小结")
        self.assertTrue(ex.get("accepted_at"))
        self.assertIn("[done outside]", req.notes)

    def test_executing_without_session_skips_harvest_and_stop(self):
        _mk_req(status=State.EXECUTING.value, execution=None)
        harvest, stop = mock.Mock(), mock.Mock()
        req = self._run_with_executor(harvest, stop)
        harvest.assert_not_called()
        stop.assert_not_called()
        self.assertEqual(req.status, State.DELIVERED.value)
        self.assertTrue((req.execution or {}).get("accepted_at"))
        self.assertIn("[done outside]", req.notes)

    def test_from_approved_delivers_without_harvest_or_stop(self):
        # 排队未派发：直接落账；即便残留 session_id 也不 harvest/stop（按状态分流）
        _mk_req(status=State.APPROVED.value,
                execution={"session_id": "sess-15"})
        harvest, stop = mock.Mock(), mock.Mock()
        req = self._run_with_executor(harvest, stop)
        harvest.assert_not_called()
        stop.assert_not_called()
        self.assertEqual(req.status, State.DELIVERED.value)
        ex = req.execution or {}
        self.assertTrue(ex.get("accepted_at"))
        self.assertEqual(ex.get("session_id"), "sess-15")
        self.assertIn("[done outside] Zelin 在系统外完成", req.notes)


# --------------------------------------------------------------------------- #
# abort_execution — approved | executing -> card_sent
# --------------------------------------------------------------------------- #
class AbortExecutionTestCase(InverseActionsBase):
    def test_from_executing_archives_session_and_returns_to_card_sent(self):
        _mk_req(status=State.EXECUTING.value,
                execution={"session_id": "sess-1", "done": True,
                           "log": "/tmp/x.log"})
        stub = mock.Mock(return_value=True)
        with mock.patch.object(actd.executor, "stop_session", stub):
            req = self._run("abort_execution")
        stub.assert_called_once_with("sess-1")
        self.assertEqual(req.status, State.CARD_SENT.value)
        ex = req.execution or {}
        self.assertNotIn("session_id", ex)        # 干净重派发
        self.assertEqual(ex.get("aborted_session_id"), "sess-1")
        self.assertNotIn("done", ex)
        self.assertTrue(ex.get("aborted_at"))
        self.assertEqual(ex.get("log"), "/tmp/x.log")  # 其余字段保留

    def test_stop_failure_never_blocks_rollback(self):
        _mk_req(status=State.EXECUTING.value,
                execution={"session_id": "sess-2"})
        stub = mock.Mock(side_effect=RuntimeError("claude stop exploded"))
        with mock.patch.object(actd.executor, "stop_session", stub):
            req = self._run("abort_execution")
        stub.assert_called_once_with("sess-2")    # stop 被调……
        self.assertEqual(req.status, State.CARD_SENT.value)  # ……失败不阻塞回退
        ex = req.execution or {}
        self.assertEqual(ex.get("aborted_session_id"), "sess-2")
        self.assertNotIn("session_id", ex)
        self.assertTrue(ex.get("aborted_at"))

    def test_from_approved_without_session_skips_stop(self):
        _mk_req(status=State.APPROVED.value, execution=None)
        stub = mock.Mock()
        with mock.patch.object(actd.executor, "stop_session", stub):
            req = self._run("abort_execution")
        stub.assert_not_called()                  # 还没派发，无 session 可停
        self.assertEqual(req.status, State.CARD_SENT.value)
        ex = req.execution or {}
        self.assertTrue(ex.get("aborted_at"))
        self.assertNotIn("aborted_session_id", ex)

    def test_wrong_status_is_idempotent_noop(self):
        # v0.28.1 §30: review DROPPED from the no-op set — 「退回提案」 must apply
        # to a review card routed into 运行中 by attach-reactivated session
        # activity (covered by test_from_review_discards_and_returns_to_card_sent).
        wrong = (State.CARD_SENT.value, State.DELIVERED.value,
                 State.DETECTED.value)
        for i, st in enumerate(wrong):
            rid = f"R-82{i}"
            _mk_req(req_id=rid, status=st, execution={"session_id": "keep-me"})
            stub = mock.Mock()
            with mock.patch.object(actd.executor, "stop_session", stub):
                req = self._run("abort_execution", req_id=rid)
            stub.assert_not_called()
            self.assertEqual(req.status, st, msg=st)
            ex = req.execution or {}
            self.assertEqual(ex.get("session_id"), "keep-me", msg=st)
            self.assertNotIn("aborted_session_id", ex, msg=st)
            self.assertNotIn("aborted_at", ex, msg=st)

    def test_from_review_discards_and_returns_to_card_sent(self):
        # v0.28.1 §30: 待验收 card routed to 运行中 by a reactivated session —
        # 「退回提案」 stops the live session and kicks the card back to card_sent
        # for a fresh decision (the reattached run is discarded).
        _mk_req(status=State.REVIEW.value,
                execution={"session_id": "sess-rv2", "done": True})
        stub = mock.Mock(return_value=True)
        with mock.patch.object(actd.executor, "stop_session", stub):
            req = self._run("abort_execution")
        stub.assert_called_once_with("sess-rv2")
        self.assertEqual(req.status, State.CARD_SENT.value)
        ex = req.execution or {}
        self.assertNotIn("session_id", ex)                 # 干净重派发
        self.assertEqual(ex.get("aborted_session_id"), "sess-rv2")
        self.assertTrue(ex.get("aborted_at"))

    def test_double_abort_second_is_noop(self):
        _mk_req(status=State.EXECUTING.value,
                execution={"session_id": "sess-3"})
        stub = mock.Mock(return_value=True)
        with mock.patch.object(actd.executor, "stop_session", stub):
            self._run("abort_execution")
            req = self._run("abort_execution")   # 连点第二下
        self.assertEqual(stub.call_count, 1)
        self.assertEqual(req.status, State.CARD_SENT.value)
        self.assertEqual((req.execution or {}).get("aborted_session_id"), "sess-3")


# --------------------------------------------------------------------------- #
# stop_to_review — executing | approved | review -> review（去待验收：停 agent
# + 收下成果；v0.28.1 §30 review：attach 回流被路由进运行中的卡也能「去待验收」）
# --------------------------------------------------------------------------- #
class StopToReviewTestCase(InverseActionsBase):
    def _run_with_executor(self, harvest, stop, req_id="R-800"):
        with mock.patch.object(actd.executor, "harvest_delivery", harvest), \
             mock.patch.object(actd.executor, "stop_session", stop):
            return self._run("stop_to_review", req_id=req_id)

    def test_from_executing_harvests_stops_and_lands_in_review(self):
        _mk_req(status=State.EXECUTING.value,
                execution={"session_id": "sess-70", "log": "/tmp/x.log"})
        harvest = mock.Mock(return_value={"delivered_summary": "阶段成果",
                                          "final_draft": "半成品草稿"})
        stop = mock.Mock(return_value=True)
        req = self._run_with_executor(harvest, stop)
        harvest.assert_called_once_with("sess-70")   # 先收下成果……
        stop.assert_called_once_with("sess-70")      # ……再停掉跑着的 agent
        self.assertEqual(req.status, State.REVIEW.value)
        ex = req.execution or {}
        self.assertEqual(ex.get("delivered_summary"), "阶段成果")
        self.assertEqual(ex.get("final_draft"), "半成品草稿")
        self.assertTrue(ex.get("review_at"))         # 镜像自然 executing->review
        self.assertTrue(ex.get("done"))
        self.assertEqual(ex.get("session_id"), "sess-70")  # 不归档不删除
        self.assertEqual(ex.get("log"), "/tmp/x.log")      # 其余字段保留
        self.assertIn("[stopped by user] 手动停止，已收下成果待验收", req.notes)

    def test_from_approved_without_session_lands_in_review_no_crash(self):
        # 排队未派发、无 session：harvest 为空，直接落待验收（空交付物，不崩）
        _mk_req(status=State.APPROVED.value, execution=None)
        harvest, stop = mock.Mock(), mock.Mock()
        req = self._run_with_executor(harvest, stop)
        harvest.assert_not_called()
        stop.assert_not_called()
        self.assertEqual(req.status, State.REVIEW.value)
        ex = req.execution or {}
        self.assertTrue(ex.get("review_at"))
        self.assertTrue(ex.get("done"))
        self.assertIsNone(ex.get("delivered_summary"))
        self.assertIn("[stopped by user]", req.notes)

    def test_empty_harvest_never_overwrites_existing_fields(self):
        _mk_req(status=State.EXECUTING.value,
                execution={"session_id": "sess-71",
                           "delivered_summary": "旧小结",
                           "final_draft": "旧草稿"})
        harvest = mock.Mock(return_value={"delivered_summary": None,
                                          "final_draft": None})
        req = self._run_with_executor(harvest, mock.Mock(return_value=True))
        self.assertEqual(req.status, State.REVIEW.value)
        ex = req.execution or {}
        self.assertEqual(ex.get("delivered_summary"), "旧小结")  # 非空才写
        self.assertEqual(ex.get("final_draft"), "旧草稿")

    def test_harvest_failure_only_logged_stop_still_runs(self):
        _mk_req(status=State.EXECUTING.value,
                execution={"session_id": "sess-72"})
        harvest = mock.Mock(side_effect=RuntimeError("transcript exploded"))
        stop = mock.Mock(return_value=True)
        req = self._run_with_executor(harvest, stop)
        harvest.assert_called_once_with("sess-72")   # harvest 炸了……
        stop.assert_called_once_with("sess-72")      # ……stop 照跑
        self.assertEqual(req.status, State.REVIEW.value)
        self.assertTrue((req.execution or {}).get("review_at"))

    def test_stop_failure_never_blocks_review_write(self):
        _mk_req(status=State.EXECUTING.value,
                execution={"session_id": "sess-73"})
        harvest = mock.Mock(return_value={"delivered_summary": "小结",
                                          "final_draft": None})
        stop = mock.Mock(side_effect=RuntimeError("claude stop exploded"))
        req = self._run_with_executor(harvest, stop)
        stop.assert_called_once_with("sess-73")      # stop 被调且失败……
        self.assertEqual(req.status, State.REVIEW.value)  # ……不阻塞落 review
        ex = req.execution or {}
        self.assertEqual(ex.get("delivered_summary"), "小结")
        self.assertTrue(ex.get("review_at"))

    def test_wrong_status_is_idempotent_noop(self):
        # v0.28.1 §30: review DROPPED from the no-op set — a review card with an
        # attach-reactivated session is routed into 运行中 and 「去待验收」 must
        # apply there (covered by test_from_review_reharvests_and_stays_review).
        wrong = (State.CARD_SENT.value, State.DELIVERED.value,
                 State.DETECTED.value)
        for i, st in enumerate(wrong):
            rid = f"R-85{i}"
            _mk_req(req_id=rid, status=st, execution={"session_id": "keep-me"})
            harvest, stop = mock.Mock(), mock.Mock()
            req = self._run_with_executor(harvest, stop, req_id=rid)
            harvest.assert_not_called()
            stop.assert_not_called()
            self.assertEqual(req.status, st, msg=st)
            ex = req.execution or {}
            self.assertEqual(ex.get("session_id"), "keep-me", msg=st)
            self.assertNotIn("review_at", ex, msg=st)
            self.assertNotIn("[stopped by user]", req.notes or "", msg=st)

    def test_from_review_reharvests_and_stays_review(self):
        # v0.28.1 §30: 待验收 card whose session was reactivated via attach and
        # got routed to 运行中 — 「去待验收」 stops the live session, re-harvests
        # (refreshing the draft with anything the reattached run produced) and
        # stays in review. Registry status was never flipped, so this is
        # idempotent-safe and the ✓/↩︎ verdict remains available.
        _mk_req(status=State.REVIEW.value,
                execution={"session_id": "sess-rv", "delivered_summary": "旧稿",
                           "review_at": "2026-07-08T01:00:00Z"})
        harvest = mock.Mock(return_value={"delivered_summary": "重跑后的新稿",
                                          "final_draft": "新草稿全文"})
        stop = mock.Mock(return_value=True)
        req = self._run_with_executor(harvest, stop)
        harvest.assert_called_once_with("sess-rv")   # 活 session -> 收割
        stop.assert_called_once_with("sess-rv")      # ……并停掉 attach 回流会话
        self.assertEqual(req.status, State.REVIEW.value)   # 仍留待验收
        ex = req.execution or {}
        self.assertEqual(ex.get("delivered_summary"), "重跑后的新稿")  # 刷新
        self.assertEqual(ex.get("final_draft"), "新草稿全文")
        self.assertTrue(ex.get("done"))
        self.assertIn("[stopped by user]", req.notes)

    def test_analytics_autologged(self):
        _mk_req(req_id="R-859", status=State.EXECUTING.value,
                execution={"session_id": "s"})
        self._run_with_executor(mock.Mock(return_value={}),
                                mock.Mock(return_value=True), req_id="R-859")
        events = [(e.get("event"), e.get("req")) for e in analytics.read_events()]
        self.assertIn(("inbox_stop_to_review", "R-859"), events)


# --------------------------------------------------------------------------- #
# revert_review — delivered -> review
# --------------------------------------------------------------------------- #
class RevertReviewTestCase(InverseActionsBase):
    def test_from_delivered_returns_to_review(self):
        _mk_req(status=State.DELIVERED.value,
                execution={"session_id": "sess-5",
                           "accepted_at": "2026-07-08T00:00:00Z"})
        req = self._run("revert_review")
        self.assertEqual(req.status, State.REVIEW.value)
        ex = req.execution or {}
        self.assertNotIn("accepted_at", ex)
        self.assertTrue(ex.get("reverted_at"))
        self.assertEqual(ex.get("session_id"), "sess-5")  # 其余字段保留

    def test_wrong_status_is_idempotent_noop(self):
        wrong = (State.CARD_SENT.value, State.APPROVED.value,
                 State.EXECUTING.value, State.REVIEW.value)
        for i, st in enumerate(wrong):
            rid = f"R-83{i}"
            _mk_req(req_id=rid, status=st,
                    execution={"accepted_at": "2026-07-08T00:00:00Z"})
            req = self._run("revert_review", req_id=rid)
            self.assertEqual(req.status, st, msg=st)
            ex = req.execution or {}
            self.assertEqual(ex.get("accepted_at"), "2026-07-08T00:00:00Z", msg=st)
            self.assertNotIn("reverted_at", ex, msg=st)

    def test_revert_then_accept_roundtrip(self):
        # delivered -> review (revert) -> delivered (accept) 全链路
        _mk_req(status=State.DELIVERED.value,
                execution={"accepted_at": "2026-07-08T00:00:00Z"})
        req = self._run("revert_review")
        self.assertEqual(req.status, State.REVIEW.value)
        req = self._run("accept")
        self.assertEqual(req.status, State.DELIVERED.value)
        ex = req.execution or {}
        self.assertTrue(ex.get("accepted_at"))
        self.assertTrue(ex.get("reverted_at"))   # 历史留痕不清除


# --------------------------------------------------------------------------- #
# analytics — every action auto-logs inbox_{action} (existing generic hook)
# --------------------------------------------------------------------------- #
class AnalyticsCoverageTestCase(InverseActionsBase):
    def test_inbox_events_autologged_for_each_action(self):
        _mk_req(req_id="R-840", status=State.CARD_SENT.value)
        self._run("done_external", req_id="R-840")

        _mk_req(req_id="R-841", status=State.EXECUTING.value,
                execution={"session_id": "s"})
        with mock.patch.object(actd.executor, "stop_session",
                               mock.Mock(return_value=True)):
            self._run("abort_execution", req_id="R-841")

        _mk_req(req_id="R-842", status=State.DELIVERED.value)
        self._run("revert_review", req_id="R-842")

        # v0.12：executing 上的 done_external 走同一 inbox_done_external 打点
        _mk_req(req_id="R-843", status=State.EXECUTING.value,
                execution={"session_id": "s2"})
        with mock.patch.object(actd.executor, "harvest_delivery",
                               mock.Mock(return_value={})), \
             mock.patch.object(actd.executor, "stop_session",
                               mock.Mock(return_value=True)):
            self._run("done_external", req_id="R-843")

        events = [(e.get("event"), e.get("req")) for e in analytics.read_events()]
        self.assertIn(("inbox_done_external", "R-840"), events)
        self.assertIn(("inbox_abort_execution", "R-841"), events)
        self.assertIn(("inbox_revert_review", "R-842"), events)
        self.assertIn(("inbox_done_external", "R-843"), events)


# --------------------------------------------------------------------------- #
# executor.stop_session — the extracted rework stop path
# --------------------------------------------------------------------------- #
class StopSessionHelperTestCase(unittest.TestCase):
    def test_stops_live_session_with_short_id_and_sleeps(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        with mock.patch.object(executor.subprocess, "run", fake_run), \
             mock.patch.object(executor.time, "sleep") as slept:
            ok = executor.stop_session(
                "abc12345-6789-4abc-8def-0123456789ab", info={"pid": 4242})
        self.assertTrue(ok)
        # argv[0] is the RESOLVED claude (config.resolve_claude_bin), not the
        # bare name — the daemon PATH once ranked an outdated copy first.
        self.assertEqual(len(calls), 1)
        self.assertEqual(Path(calls[0][0]).name, "claude")
        self.assertEqual(calls[0][1:], ["stop", "abc12345"])
        slept.assert_called_once_with(2)  # rework 原路径的 2s 等待不变

    def test_no_live_pid_is_a_noop(self):
        with mock.patch.object(executor.subprocess, "run") as run:
            ok = executor.stop_session("abc12345", info={})
        self.assertFalse(ok)
        run.assert_not_called()

    def test_queries_roster_when_info_omitted(self):
        with mock.patch.object(executor, "_agent_info", return_value={}) as ai, \
             mock.patch.object(executor.subprocess, "run") as run:
            ok = executor.stop_session("abc12345")
        self.assertFalse(ok)
        ai.assert_called_once_with("abc12345")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
