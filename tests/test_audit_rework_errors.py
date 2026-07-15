"""Audit 2026-07 — rework() early-abort paths must surface the failure.

Confirmed bug: a 打回 that could not even launch (no session_id on the card,
transcript purged from ~/.claude/projects, cwd unrecreatable) returned False
WITHOUT recording anything — the card silently stayed 待验收 and Zelin's typed
feedback was dropped with zero surface. Every early False path now persists
execution.last_error/last_error_at (same shape as the launch-failed path) so
the dashboard/card can show why.

Same fixture style as tests/test_rework.py: _transcript_info/_agent_info are
patched — the suite never globs the real ~/.claude/projects nor shells out.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import executor
from act.lib import config, registry
from act.lib.registry import Requirement, State

FULL_SID = "feedc0de-0000-4000-8000-000000000001"


class ReworkAbortSurfacingTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()
        patcher = mock.patch.object(executor, "_agent_info", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _mk_req(self, execution: dict) -> Requirement:
        req = Requirement(id="R-970", title="打回失败要可见",
                          status=State.REVIEW.value, execution=execution)
        registry.save(req)
        return req

    def test_no_session_id_records_last_error(self):
        # deterministic in-product path: APPROVED card stop_to_review'd with
        # no session — 打回 has nothing to resume.
        req = self._mk_req(execution={"done": True})
        ok = executor.rework(req, "补充要求", self.cfg)
        self.assertFalse(ok)
        saved = registry.load("R-970")
        ex = saved.execution or {}
        self.assertIn("no session", ex.get("last_error", ""))
        self.assertTrue(str(ex.get("last_error_at", "")).endswith("Z"))
        # never launched: not a rework round, and the card stays actionable
        self.assertNotIn("rework_count", ex)
        self.assertEqual(saved.status, State.REVIEW.value)

    def test_transcript_missing_records_last_error(self):
        req = self._mk_req(execution={"session_id": "feedc0de", "done": True})
        with mock.patch.object(executor, "_transcript_info", return_value=None):
            ok = executor.rework(req, "补充要求", self.cfg)
        self.assertFalse(ok)
        ex = (registry.load("R-970").execution or {})
        self.assertIn("transcript missing", ex.get("last_error", ""))
        self.assertTrue(str(ex.get("last_error_at", "")).endswith("Z"))

    def test_uncreatable_cwd_records_last_error(self):
        # target under a regular FILE -> mkdir raises (OSError subclass)
        blocker = Path(tempfile.mkdtemp(prefix="rework-cwd-")) / "afile"
        blocker.write_text("x", encoding="utf-8")
        req = self._mk_req(execution={"session_id": "feedc0de", "done": True})
        with mock.patch.object(executor, "_transcript_info",
                               return_value=(FULL_SID, blocker / "sub")):
            ok = executor.rework(req, "补充要求", self.cfg)
        self.assertFalse(ok)
        ex = (registry.load("R-970").execution or {})
        self.assertIn("cwd", ex.get("last_error", ""))

    def test_blank_feedback_is_a_noop_without_error(self):
        # nothing typed = nothing lost; actd already acks noop — the executor
        # must not fabricate an error on the card.
        req = self._mk_req(execution={"session_id": "feedc0de", "done": True})
        ok = executor.rework(req, "   ", self.cfg)
        self.assertFalse(ok)
        ex = (registry.load("R-970").execution or {})
        self.assertNotIn("last_error", ex)


if __name__ == "__main__":
    unittest.main()
