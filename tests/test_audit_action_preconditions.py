"""CONTRACT §32.2 inherent action preconditions + accept session reaping
(audit 2026-07-15).

- comment: a late 💬 on a terminal card (trashed/merged) used to fall through
  to the card_sent write — resurrecting a rejected card as a live proposal
  with its trash/merge bookkeeping still attached, and re-approving it would
  re-dispatch discarded work.
- raise: a late/replayed raise from a stale board flipped approved→raising,
  silently cancelling the approval (dispatch never picks raising up). Backlog/
  proposal cards only; card_sent deliberately stays allowed (the local board
  offers 研究并提议 there — see test_actd_sync).
- accept: a chat-mode delivery promoted from blocked keeps a live bg session
  that never exits on its own; accept now reaps it like done_external does.

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


def _mk_req(req_id="R-860", status=State.CARD_SENT.value, execution=None,
            notes="", **kw):
    req = Requirement(id=req_id, title="precondition test", status=status,
                      execution=execution, notes=notes, **kw)
    registry.save(req)
    return req


def _drop(action, req_id="R-860", comment=None):
    config.ensure_state_dirs()
    path = config.INBOX_DIR / f"{uuid.uuid4()}.json"
    path.write_text(json.dumps({"id": req_id, "action": action,
                                "comment": comment}), encoding="utf-8")
    return path


class PreconditionBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()


class CommentTerminalGuardTestCase(PreconditionBase):
    def test_comment_never_resurrects_terminal_cards(self):
        for status in (State.TRASHED.value, State.MERGED.value,
                       State.REJECTED.value):
            for p in config.REGISTRY_DIR.glob("*.yaml"):
                p.unlink()
            req = _mk_req(status=status)
            ret = actd._apply_decision(req, "comment", "把它捞回来", None, None)
            self.assertEqual(ret, "noop", status)
            reloaded = registry.load("R-860")
            self.assertEqual(reloaded.status, status, status)
            self.assertNotIn("把它捞回来", reloaded.notes or "", status)

    def test_comment_still_folds_on_live_proposals(self):
        req = _mk_req(status=State.CARD_SENT.value)
        ret = actd._apply_decision(req, "comment", "补充上下文", None, None)
        self.assertEqual(ret, "running")
        self.assertIn("补充上下文", registry.load("R-860").notes)


class RaiseGuardTestCase(PreconditionBase):
    def test_raise_never_rips_cards_past_approval_or_terminal(self):
        for status in (State.APPROVED.value, State.EXECUTING.value,
                       State.REVIEW.value, State.DELIVERED.value,
                       State.TRASHED.value, State.MERGED.value):
            for p in config.REGISTRY_DIR.glob("*.yaml"):
                p.unlink()
            req = _mk_req(status=status,
                          execution={"session_id": "sess-x"}
                          if status == State.EXECUTING.value else None)
            ret = actd._apply_decision(req, "raise", None, None, None)
            self.assertEqual(ret, "noop", status)
            self.assertEqual(registry.load("R-860").status, status, status)

    def test_raise_on_raising_is_idempotent(self):
        req = _mk_req(status=State.RAISING.value)
        ret = actd._apply_decision(req, "raise", None, None, None)
        self.assertEqual(ret, "noop")
        self.assertEqual(registry.load("R-860").status, State.RAISING.value)

    def test_raise_still_works_from_backlog_and_proposal(self):
        for status in (State.DETECTED.value, State.CARD_SENT.value):
            for p in config.REGISTRY_DIR.glob("*.yaml"):
                p.unlink()
            req = _mk_req(status=status)
            ret = actd._apply_decision(req, "raise", None, None, None)
            self.assertEqual(ret, "running", status)
            self.assertEqual(registry.load("R-860").status,
                             State.RAISING.value, status)


class AcceptReapsSessionTestCase(PreconditionBase):
    def test_accept_stops_live_session_before_delivering(self):
        _mk_req(status=State.REVIEW.value,
                execution={"session_id": "sess-52", "done": True,
                           "final_draft": "成稿全文"})
        stop = mock.Mock(return_value=True)
        with mock.patch.object(actd.executor, "stop_session", stop):
            _drop("accept")
            actd.process_inbox()
        stop.assert_called_once_with("sess-52")
        req = registry.load("R-860")
        self.assertEqual(req.status, State.DELIVERED.value)
        ex = req.execution or {}
        self.assertTrue(ex.get("accepted_at"))
        # mirror done_external: the sid is kept for the record, only the
        # process is reaped
        self.assertEqual(ex.get("session_id"), "sess-52")

    def test_stop_failure_never_blocks_the_delivery(self):
        _mk_req(status=State.REVIEW.value,
                execution={"session_id": "sess-52"})
        with mock.patch.object(actd.executor, "stop_session",
                               mock.Mock(side_effect=RuntimeError("boom"))):
            _drop("accept")
            actd.process_inbox()
        self.assertEqual(registry.load("R-860").status, State.DELIVERED.value)

    def test_accept_without_session_never_touches_stop(self):
        _mk_req(status=State.REVIEW.value)
        stop = mock.Mock()
        with mock.patch.object(actd.executor, "stop_session", stop):
            _drop("accept")
            actd.process_inbox()
        stop.assert_not_called()
        self.assertEqual(registry.load("R-860").status, State.DELIVERED.value)


if __name__ == "__main__":
    unittest.main()
