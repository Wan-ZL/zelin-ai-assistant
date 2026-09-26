"""Inbox state-guard matrix — nightly audit 2026-07-14 regressions.

What broke (all reproduced live against the pre-fix daemon):
- a legal-JSON-but-not-an-object inbox file (null/list/number) raised
  AttributeError OUTSIDE the per-file guard; the poison file survived and,
  processed in mtime order, re-crashed every pass — the whole inbox wedged;
- approve's blacklist let a late/replayed ✅ flip trashed/merged/raising
  cards straight to approved;
- a late 💬 on an executing card ripped it back to the backlog lane while its
  agent kept running (session_id survived into the next dispatch);
- reject/trash binned an executing card WITHOUT stopping its live agent;
- restore replayed on a live card rewrote its status;
- accept teleported never-dispatched cards to delivered;
- any action on an ARCHIVED card rewrote status while the file stayed in
  archive/ (split brain).

§78（issue #447，owner 决策 D80）：提案（``card_sent``）车道退役并入潜在任务
（``detected``）。守卫矩阵的**白名单与 no-op 集合一个都没删**，只是「折回/退回」
的落点改成 detected；退役值仍是合法值，落单卡照旧被同一批守卫接住（下面逐条
留着对照行）。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import json
import unittest
import uuid
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import config, registry
from act.lib.registry import Requirement, State


def _mk_req(req_id="R-810", status=State.DETECTED.value, execution=None):
    req = Requirement(id=req_id, title="守卫矩阵测试", status=status,
                      execution=execution)
    registry.save(req)
    return req


def _drop_raw(content: str):
    config.ensure_state_dirs()
    path = config.INBOX_DIR / f"{uuid.uuid4()}.json"
    path.write_text(content, encoding="utf-8")
    return path


def _drop(action, req_id="R-810", comment=None):
    return _drop_raw(json.dumps(
        {"id": req_id, "action": action, "comment": comment,
         "ts": "2026-07-14T00:00:00Z"}))


class GuardsBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()


class PoisonInboxFileTestCase(GuardsBase):
    def test_legal_json_non_dict_is_discarded_not_looping(self):
        # null / list / number are valid JSON but not decisions — each must be
        # consumed (deleted) without crashing the pass.
        for content in ("null", "[1, 2]", "42", '"just a string"', "true"):
            _drop_raw(content)
        _mk_req()
        _drop("approve")  # a real decision BEHIND the poison files (mtime order)
        actd.process_inbox()
        # every file consumed; the real decision still applied
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])
        self.assertEqual(registry.load("R-810").status, State.APPROVED.value)

    def test_lone_surrogate_in_decision_never_crashes_logging(self):
        # json.loads legally produces "\ud800" (lone UTF-16 surrogate) — the
        # log line about the unknown action must not die encoding it.
        _mk_req()
        _drop_raw(json.dumps({"id": "R-810", "action": "\ud800bogus"}))
        actd.process_inbox()  # must not raise
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])


class ArchivedGateTestCase(GuardsBase):
    def test_every_action_but_unarchive_is_a_noop_on_archived(self):
        for action in ("approve", "comment", "reject", "trash", "accept",
                       "raise", "restore", "pin", "rework"):
            for p in config.REGISTRY_DIR.glob("*.yaml"):
                p.unlink()
            _mk_req(status=State.ARCHIVED.value)
            _drop(action)
            actd.process_inbox()
            self.assertEqual(registry.load("R-810").status,
                             State.ARCHIVED.value, action)


class ApproveWhitelistTestCase(GuardsBase):
    def test_approve_only_lands_on_live_backlog_cards(self):
        # §78：「活着的待批卡」自此 = 潜在任务列上的卡（提案列退役）
        for status in (State.TRASHED.value, State.MERGED.value,
                       State.RAISING.value):
            for p in config.REGISTRY_DIR.glob("*.yaml"):
                p.unlink()
            _mk_req(status=status)
            _drop("approve")
            actd.process_inbox()
            self.assertEqual(registry.load("R-810").status, status, status)

    def test_approve_still_works_on_detected_and_card_sent(self):
        for status in (State.DETECTED.value, State.CARD_SENT.value):
            for p in config.REGISTRY_DIR.glob("*.yaml"):
                p.unlink()
            _mk_req(status=status)
            _drop("approve")
            actd.process_inbox()
            self.assertEqual(registry.load("R-810").status,
                             State.APPROVED.value, status)


class LateCommentTestCase(GuardsBase):
    def test_comment_on_executing_keeps_status_and_session(self):
        _mk_req(status=State.EXECUTING.value,
                execution={"session_id": "abcd1234"})
        _drop("comment", comment="改个方向")
        actd.process_inbox()
        req = registry.load("R-810")
        self.assertEqual(req.status, State.EXECUTING.value)
        self.assertEqual((req.execution or {}).get("session_id"), "abcd1234")
        self.assertIn("改个方向", req.notes or "")

    def test_comment_on_a_backlog_card_still_folds_for_reapproval(self):
        # §78：评论折回的落点是潜在任务（退役前是提案列）——卡留在 owner 的
        # 收件箱里等他重新点「促成运行」，而不是被送进一列没有界面的车道。
        _mk_req(status=State.DETECTED.value)
        _drop("comment", comment="补充上下文")
        actd.process_inbox()
        req = registry.load("R-810")
        self.assertEqual(req.status, State.DETECTED.value)
        self.assertIn("补充上下文", req.notes or "")

    def test_comment_on_a_retired_straggler_folds_into_the_backlog(self):
        # 存量落单卡上的评论把它一并搬进潜在任务（§78 归并语义的第二道兜底）
        _mk_req(status=State.CARD_SENT.value)
        _drop("comment", comment="落单卡的补充")
        actd.process_inbox()
        req = registry.load("R-810")
        self.assertEqual(req.status, State.DETECTED.value)
        self.assertIn("落单卡的补充", req.notes or "")


class DestructiveActionsStopAgentTestCase(GuardsBase):
    def _run_with_stop(self, action, status):
        _mk_req(status=status, execution={"session_id": "abcd1234"})
        stop = mock.Mock(return_value=(True, True, "stopped"))
        with mock.patch.object(actd.executor, "stop_session_confirmed", stop):
            _drop(action)
            actd.process_inbox()
        return stop, registry.load("R-810")

    def test_reject_on_executing_stops_agent_then_trashes(self):
        stop, req = self._run_with_stop("reject", State.EXECUTING.value)
        stop.assert_called_once_with("abcd1234")
        self.assertEqual(req.status, State.TRASHED.value)
        ex = req.execution or {}
        self.assertEqual(ex.get("aborted_session_id"), "abcd1234")
        self.assertNotIn("session_id", ex)

    def test_trash_on_executing_stops_agent_then_trashes(self):
        stop, req = self._run_with_stop("trash", State.EXECUTING.value)
        stop.assert_called_once_with("abcd1234")
        self.assertEqual(req.status, State.TRASHED.value)

    def test_stop_failure_never_blocks_the_trash(self):
        _mk_req(status=State.EXECUTING.value,
                execution={"session_id": "abcd1234"})
        with mock.patch.object(actd.executor, "stop_session_confirmed",
                               mock.Mock(side_effect=RuntimeError("boom"))):
            _drop("reject")
            actd.process_inbox()
        self.assertEqual(registry.load("R-810").status, State.TRASHED.value)

    def test_reject_on_a_backlog_card_does_not_touch_stop(self):
        stop, req = self._run_with_stop("reject", State.DETECTED.value)
        stop.assert_not_called()
        self.assertEqual(req.status, State.TRASHED.value)


class RestoreGuardTestCase(GuardsBase):
    def test_restore_is_trash_lane_only(self):
        _mk_req(status=State.EXECUTING.value,
                execution={"session_id": "abcd1234"})
        _drop("restore")
        actd.process_inbox()
        self.assertEqual(registry.load("R-810").status, State.EXECUTING.value)


class AcceptGuardTestCase(GuardsBase):
    def test_accept_noops_on_never_dispatched_cards(self):
        for status in (State.DETECTED.value, State.CARD_SENT.value,
                       State.RAISING.value, State.APPROVED.value):
            for p in config.REGISTRY_DIR.glob("*.yaml"):
                p.unlink()
            _mk_req(status=status)
            _drop("accept")
            actd.process_inbox()
            self.assertEqual(registry.load("R-810").status, status, status)

    def test_accept_still_delivers_review_and_executing(self):
        for status in (State.REVIEW.value, State.EXECUTING.value):
            for p in config.REGISTRY_DIR.glob("*.yaml"):
                p.unlink()
            _mk_req(status=status)
            _drop("accept")
            actd.process_inbox()
            req = registry.load("R-810")
            self.assertEqual(req.status, State.DELIVERED.value, status)
            self.assertTrue((req.execution or {}).get("accepted_at"))

    def test_accept_double_click_is_idempotent(self):
        _mk_req(status=State.DELIVERED.value,
                execution={"accepted_at": "2026-07-14T00:00:00Z"})
        _drop("accept")
        actd.process_inbox()
        self.assertEqual(registry.load("R-810").status, State.DELIVERED.value)


if __name__ == "__main__":
    unittest.main()
