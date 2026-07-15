"""Re-raised rounds must actually dispatch (§3.5 re-raise → approve → launch).

The trap: a DELIVERED card keeps the finished round's ``execution.session_id``
(dispatch set it; the review/delivered promotions only add keys). The §3.5
re-raise flips the card back to card_sent but used to leave that stale id in
place, and ``dispatch_approved`` skips any card with a ``session_id`` as
"already dispatched" — so after the user approved the re-raised round it sat
queued forever, with no agent behind it and no error anywhere.

Fix under test: ``registry.reraise_or_followup``'s in-place re-raise branch
archives the finished round's id as ``reraised_session_id`` (and drops the
stale ``execution.done``) at the flip — the ONE seam every re-raise caller
shares (merge_or_new deterministic, apply_triage / quick-capture LLM paths).

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py); the
dispatch executor is stubbed (test_dispatch.py pattern), no LLM is invoked.
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, executor
from act.lib import config, registry
from act.lib.quick_capture import apply_triage
from act.lib.registry import Requirement, State


def _src(channel="meeting"):
    return {"who": "boss", "channel": channel, "date": "2026-07-10",
            "quote": "ship the quarterly report"}


class ReraiseDispatchTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        if config.REGISTRY_DIR.exists():
            for p in config.REGISTRY_DIR.glob("*.yaml"):
                p.unlink()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()

    def _seed_delivered(self, req_id="R-100"):
        req = Requirement(
            id=req_id, title="Ship the quarterly report",
            status=State.DELIVERED.value,
            execution={"session_id": "oldround1", "done": True,
                       "accepted_at": "2026-07-10T00:00:00Z",
                       "delivered_summary": "shipped"},
            sources=[_src()])
        registry.save(req)
        return req

    def _dispatch_pass(self) -> int:
        fake = mock.Mock()
        fake.DispatchError = executor.DispatchError

        def _dispatch(req, cfg):
            req.set_status(State.EXECUTING)
            req.execution = {"session_id": "newround2"}
            registry.save(req)
            return req

        fake.dispatch.side_effect = _dispatch
        with mock.patch.object(actd, "executor", fake):
            return actd.dispatch_approved(config.Config())

    def test_deterministic_reraise_then_approve_actually_dispatches(self):
        self._seed_delivered("R-100")
        # normal deterministic re-raise (merge_or_new increment path): a new
        # actionable mention with an earlier deadline flips the original card.
        got = registry.merge_or_new(
            Requirement(id="", title="Ship the quarterly report",
                        summary="need it a week earlier", deadline="2026-07-15",
                        sources=[_src()]))
        self.assertEqual(got.id, "R-100")
        self.assertEqual(got.status, State.CARD_SENT.value)
        ex = got.execution or {}
        self.assertTrue(ex.get("reraised_at"))
        # the flip must archive the FINISHED round's session id — this is what
        # dispatch_approved keys "already dispatched" on.
        self.assertEqual(ex.get("reraised_session_id"), "oldround1")
        self.assertNotIn("session_id", ex)
        self.assertNotIn("done", ex)

        # user approves the new round (normal inbox approve)
        result = actd._apply_decision(registry.load("R-100"), "approve", None)
        self.assertEqual(result, "running")
        self.assertEqual(registry.load("R-100").status, State.APPROVED.value)

        # the dispatch pass must launch it — pre-fix it was skipped forever
        self.assertEqual(self._dispatch_pass(), 1)
        after = registry.load("R-100")
        self.assertEqual(after.status, State.EXECUTING.value)
        self.assertEqual((after.execution or {}).get("session_id"), "newround2")

    def test_llm_reraise_path_archives_sid_too(self):
        self._seed_delivered("R-019")
        cand = Requirement(id=registry.next_id(),
                           title="Ship the quarterly report",
                           summary="manager wants it now",
                           status=State.CARD_SENT.value, sources=[_src()])
        kind, saved = apply_triage(
            {"action": "relates_to", "req": "R-019",
             "note": "manager escalated", "needs_action": True},
            cand, config.Config())
        self.assertEqual(kind, "reraised")
        self.assertEqual(saved.id, "R-019")
        ex = saved.execution or {}
        self.assertEqual(ex.get("reraised_session_id"), "oldround1")
        self.assertNotIn("session_id", ex)
        self.assertNotIn("done", ex)
        # non-session bookkeeping of the finished round is history — keep it
        self.assertEqual(ex.get("delivered_summary"), "shipped")

    def test_reraise_without_prior_session_stays_clean(self):
        # a delivered card that never carried a session (e.g. done_external)
        # re-raises without inventing archive keys.
        req = Requirement(id="R-200", title="Ship the quarterly report",
                          status=State.DELIVERED.value,
                          execution={"accepted_at": "2026-07-10T00:00:00Z"},
                          sources=[_src()])
        registry.save(req)
        got = registry.merge_or_new(
            Requirement(id="", title="Ship the quarterly report",
                        summary="again, sooner", deadline="2026-07-15",
                        sources=[_src()]))
        self.assertEqual(got.status, State.CARD_SENT.value)
        ex = got.execution or {}
        self.assertNotIn("reraised_session_id", ex)
        self.assertNotIn("session_id", ex)
        result = actd._apply_decision(registry.load("R-200"), "approve", None)
        self.assertEqual(result, "running")
        self.assertEqual(self._dispatch_pass(), 1)


if __name__ == "__main__":
    unittest.main()
