"""Inverse inbox actions — frozen contract v0.10.2 (CONTRACT §10).

done_external   : card_sent | review -> delivered; execution.accepted_at set;
                  notes get "[done outside] Zelin 在系统外完成"; a live session
                  is left alone (the human finished, the AI session idles out).
abort_execution : approved | executing -> card_sent; live session stopped
                  best-effort via executor.stop_session (a stop failure NEVER
                  blocks the state rollback); execution.session_id archived to
                  aborted_session_id then removed (clean re-dispatch on
                  re-approval); execution.done dropped; aborted_at recorded.
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
# done_external — card_sent | review -> delivered
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
        wrong = (State.DETECTED.value, State.APPROVED.value,
                 State.EXECUTING.value, State.DELIVERED.value,
                 State.TRASHED.value)
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
        wrong = (State.CARD_SENT.value, State.REVIEW.value,
                 State.DELIVERED.value, State.DETECTED.value)
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

        events = [(e.get("event"), e.get("req")) for e in analytics.read_events()]
        self.assertIn(("inbox_done_external", "R-840"), events)
        self.assertIn(("inbox_abort_execution", "R-841"), events)
        self.assertIn(("inbox_revert_review", "R-842"), events)


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
        self.assertEqual(calls, [["claude", "stop", "abc12345"]])
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
