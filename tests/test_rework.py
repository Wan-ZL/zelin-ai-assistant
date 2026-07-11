"""executor.rework — the 打回 verdict dispatch (§10 / §30).

Pins the §30 disambiguation contract: a genuine rework round is RECORDED at
the verdict (execution.rework_count + last_rework_at, add-only §20 keys) and
the SAME call flips the card review -> executing. That synchronous transition
is exactly why a review-status card with a live working agent can only be
user attach / organic session activity — never a rework round — which is what
dashboard projects as review[].session_active (tests/test_dashboard.py).

_transcript_info and _agent_info are patched throughout: the suite must never
glob the real ~/.claude/projects or shell out to `claude agents`. Runs inside
the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import executor
from act.lib import config, registry
from act.lib.registry import Requirement, State

FULL_SID = "feedc0de-0000-4000-8000-000000000001"


def _proc(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["claude"], returncode,
                                       stdout=stdout, stderr=stderr)


class ReworkVerdictMarkerTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()
        self.wt = Path(tempfile.mkdtemp(prefix="rework-wt-")) / "worktree"
        # never query the real roster from tests
        patcher = mock.patch.object(executor, "_agent_info", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _mk_req(self):
        req = Requirement(id="R-960", title="打回测试",
                          status=State.REVIEW.value,
                          execution={"session_id": "feedc0de", "done": True})
        registry.save(req)
        return req

    def _tinfo(self):
        return mock.patch.object(
            executor, "_transcript_info",
            side_effect=lambda sid: (FULL_SID, self.wt)
            if str(sid).startswith("feedc0de") else None)

    def test_verdict_records_marker_and_returns_to_executing(self):
        req = self._mk_req()
        runner = mock.Mock(return_value=_proc(0, stdout="backgrounded · feedc0de"))
        with self._tinfo():
            ok = executor.rework(req, "再补一个测试", self.cfg, runner=runner)
        self.assertTrue(ok)
        saved = registry.load("R-960")
        # the same call that records the verdict flips review -> executing:
        # review+working can therefore never be a rework round (§30)
        self.assertEqual(saved.status, State.EXECUTING.value)
        ex = saved.execution or {}
        self.assertEqual(ex.get("rework_count"), 1)
        self.assertTrue(str(ex.get("last_rework_at", "")).endswith("Z"))
        self.assertNotIn("done", ex)  # it's working again

    def test_failed_launch_stays_review_but_attempt_is_recorded(self):
        req = self._mk_req()
        runner = mock.Mock(return_value=_proc(1, stderr="boom"))
        with self._tinfo():
            ok = executor.rework(req, "反馈", self.cfg, runner=runner)
        self.assertFalse(ok)
        saved = registry.load("R-960")
        # card stays actionable in review; the attempt still leaves its marks
        self.assertEqual(saved.status, State.REVIEW.value)
        ex = saved.execution or {}
        self.assertEqual(ex.get("rework_count"), 1)
        self.assertTrue(ex.get("last_error"))


if __name__ == "__main__":
    unittest.main()
