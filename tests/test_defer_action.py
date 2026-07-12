"""defer inbox action — frozen contract v0.18 (CONTRACT §10).

defer : card_sent -> detected（存备选，提案退回备选）. Everything expanded on
        the card is preserved — summary / plan / sources / repeated_mentions
        — only the status changes; notes get "[deferred] 暂缓，入库".
        Deliberately NOT trash: a deferred card stays in merge_or_new
        matching (restatements merge in; radar act-now re-promotes), while
        trashed cards are excluded and re-card from scratch.

Common rule (v0.10.2): any status other than card_sent (incl. raising) =
idempotent no-op + log (double-click / late-inbox protection). The action
auto-logs the existing ``inbox_defer`` analytics event.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import json
import unittest
import uuid
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import analytics, config, registry
from act.lib.registry import Requirement, State


def _mk_req(req_id="R-900", status=State.CARD_SENT.value, **kw):
    req = Requirement(id=req_id, title="存备选测试", status=status, **kw)
    registry.save(req)
    return req


def _drop_inbox(action, req_id):
    config.ensure_state_dirs()
    path = config.INBOX_DIR / f"{uuid.uuid4()}.json"
    path.write_text(
        json.dumps({"id": req_id, "action": action, "comment": None,
                    "ts": "2026-07-11T00:00:00Z"}),
        encoding="utf-8",
    )
    return path


class DeferActionBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()

    def _run(self, action, req_id="R-900"):
        """Drop one inbox decision, run process_inbox, reload from disk."""
        _drop_inbox(action, req_id)
        n = actd.process_inbox()
        self.assertEqual(n, 1)
        # inbox file must be consumed (read then deleted) even on a no-op
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])
        return registry.load(req_id)


class DeferHappyPathTestCase(DeferActionBase):
    def test_from_card_sent_returns_to_backlog_preserving_fields(self):
        _mk_req(status=State.CARD_SENT.value,
                summary="大白话摘要",
                plan=["step 1", "step 2"],
                sources=[{"channel": "slack", "quote": "原话"}],
                repeated_mentions=3,
                notes="原有备注")
        req = self._run("defer")
        self.assertEqual(req.status, State.DETECTED.value)
        # 保留一切已扩写内容 — only the status moved (CONTRACT §10 defer)
        self.assertEqual(req.summary, "大白话摘要")
        self.assertEqual(req.plan, ["step 1", "step 2"])
        self.assertEqual(req.sources, [{"channel": "slack", "quote": "原话"}])
        self.assertEqual(req.repeated_mentions, 3)
        self.assertTrue(req.notes.startswith("原有备注"))
        self.assertIn("[deferred] 暂缓，入库", req.notes)

    def test_without_notes_gets_bare_deferred_tag(self):
        _mk_req(status=State.CARD_SENT.value)
        req = self._run("defer")
        self.assertEqual(req.status, State.DETECTED.value)
        self.assertEqual(req.notes, "[deferred] 暂缓，入库")


class DeferNoOpTestCase(DeferActionBase):
    def test_wrong_status_is_idempotent_noop(self):
        # only card_sent may defer; raising in particular must wait until its
        # expansion lands it in card_sent (CONTRACT §10 defer)
        wrong = (State.DETECTED.value, State.RAISING.value,
                 State.APPROVED.value, State.EXECUTING.value,
                 State.REVIEW.value, State.DELIVERED.value,
                 State.TRASHED.value)
        for i, st in enumerate(wrong):
            rid = f"R-91{i}"
            _mk_req(req_id=rid, status=st, plan=["keep me"], notes="原有备注")
            req = self._run("defer", req_id=rid)
            self.assertEqual(req.status, st, msg=st)
            self.assertEqual(req.plan, ["keep me"], msg=st)
            self.assertNotIn("[deferred]", req.notes or "", msg=st)

    def test_double_defer_second_is_noop(self):
        _mk_req(status=State.CARD_SENT.value, plan="the plan")
        self._run("defer")
        req = self._run("defer")   # 连点第二下：already detected -> no-op
        self.assertEqual(req.status, State.DETECTED.value)
        self.assertEqual(req.plan, "the plan")
        self.assertEqual(req.notes.count("[deferred]"), 1)


class DeferRoundtripTestCase(DeferActionBase):
    def test_defer_then_raise_promotes_again(self):
        # undo path (CONTRACT §10 defer): the backlog lane's 研究并提议 —
        # defer -> detected, raise -> raising (queued for AI expansion)
        _mk_req(status=State.CARD_SENT.value, plan=["step 1"])
        req = self._run("defer")
        self.assertEqual(req.status, State.DETECTED.value)
        with mock.patch.object(actd, "analyze", mock.Mock()):
            req = self._run("raise")
        self.assertEqual(req.status, State.RAISING.value)
        self.assertEqual(req.plan, ["step 1"])   # 往返无信息损失


class DeferAnalyticsTestCase(DeferActionBase):
    def test_inbox_defer_event_autologged(self):
        _mk_req(req_id="R-940", status=State.CARD_SENT.value)
        self._run("defer", req_id="R-940")
        events = [(e.get("event"), e.get("req")) for e in analytics.read_events()]
        self.assertIn(("inbox_defer", "R-940"), events)


if __name__ == "__main__":
    unittest.main()
