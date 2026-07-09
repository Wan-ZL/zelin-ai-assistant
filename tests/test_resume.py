"""executor.resume — the auto-resume launch path (P1-11, resume 三定律).

Laws pinned here (see HANDOFF §3 "resume 三定律"):
  ② a session id with NO transcript anywhere is NEVER resumed — launching
    would crash-loop minting new ids; fall back to root_session_id, else give
    up WITHOUT touching bookkeeping;
  root_session_id is anchored on first resume (the conversation that exists
    on disk); a NEW sid from the launch output is adopted ONLY on a clean
    (returncode 0) launch, so a failed relaunch cannot orphan the anchor.

_transcript_info is patched throughout — it globs the REAL ~/.claude/projects,
which must never be read by the suite. dispatch() failure paths live in
tests/test_dispatch.py; this file covers resume() only.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
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

FULL_SID = "aaaa1111-0000-4000-8000-000000000001"
ROOT_SID = "bbbb2222-0000-4000-8000-000000000002"


def _proc(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["claude"], returncode,
                                       stdout=stdout, stderr=stderr)


class ResumeTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()
        self.wt = Path(tempfile.mkdtemp(prefix="resume-wt-")) / "worktree"

    def _mk_req(self, execution):
        req = Requirement(id="R-950", title="resume 测试",
                          status=State.EXECUTING.value, execution=execution)
        registry.save(req)
        return req

    def _tinfo(self, mapping):
        """Patch _transcript_info: sid -> (full_sid, cwd) per ``mapping``."""
        return mock.patch.object(
            executor, "_transcript_info",
            side_effect=lambda sid: mapping.get(str(sid)))

    # -- law ②: no transcript -> never launch --------------------------------- #
    def test_no_session_id_returns_false(self):
        req = self._mk_req(execution={})
        runner = mock.Mock()
        self.assertFalse(executor.resume(req, self.cfg, runner=runner))
        runner.assert_not_called()

    def test_no_transcript_anywhere_gives_up_without_launching(self):
        req = self._mk_req(execution={"session_id": "deadbeef"})
        runner = mock.Mock()
        with self._tinfo({}):
            ok = executor.resume(req, self.cfg, runner=runner)
        self.assertFalse(ok)
        runner.assert_not_called()
        # gave up BEFORE bookkeeping — no attempt recorded, nothing persisted
        self.assertNotIn("resume_attempts", req.execution or {})

    def test_root_session_fallback_when_current_sid_has_no_transcript(self):
        req = self._mk_req(execution={"session_id": "aaaa1111",
                                      "root_session_id": ROOT_SID})
        runner = mock.Mock(return_value=_proc(0, stdout="backgrounded · cccc3333"))
        with self._tinfo({ROOT_SID: (ROOT_SID, self.wt)}):
            ok = executor.resume(req, self.cfg, runner=runner)
        self.assertTrue(ok)
        runner.assert_called_once()
        ex = registry.load("R-950").execution or {}
        self.assertEqual(ex.get("session_id"), "cccc3333")  # relaunch mints a new id
        self.assertEqual(ex.get("root_session_id"), ROOT_SID)  # anchor untouched
        self.assertEqual(ex.get("resume_attempts"), 1)
        self.assertTrue(ex.get("last_resume_ok"))
        self.assertTrue(ex.get("last_resume_at"))

    # -- bookkeeping ----------------------------------------------------------- #
    def test_root_anchor_set_on_first_resume(self):
        req = self._mk_req(execution={"session_id": "aaaa1111"})
        runner = mock.Mock(return_value=_proc(0))
        with self._tinfo({"aaaa1111": (FULL_SID, self.wt)}):
            ok = executor.resume(req, self.cfg, runner=runner)
        self.assertTrue(ok)
        ex = registry.load("R-950").execution or {}
        # the transcript's FULL UUID becomes the anchor…
        self.assertEqual(ex.get("root_session_id"), FULL_SID)
        # …and with no new id in the output the current sid stays put
        self.assertEqual(ex.get("session_id"), "aaaa1111")

    def test_failed_launch_records_failure_and_keeps_session(self):
        req = self._mk_req(execution={"session_id": "aaaa1111"})
        # id present in the FAILED output must NOT be adopted (ok gate)
        runner = mock.Mock(return_value=_proc(1, stderr="backgrounded · eeee4444"))
        with self._tinfo({"aaaa1111": (FULL_SID, self.wt)}):
            ok = executor.resume(req, self.cfg, runner=runner)
        self.assertFalse(ok)
        ex = registry.load("R-950").execution or {}
        self.assertEqual(ex.get("session_id"), "aaaa1111")
        self.assertEqual(ex.get("resume_attempts"), 1)
        self.assertFalse(ex.get("last_resume_ok"))

    def test_runner_exception_counts_as_failed_attempt(self):
        req = self._mk_req(execution={"session_id": "aaaa1111",
                                      "resume_attempts": 1})
        runner = mock.Mock(side_effect=OSError("claude not on PATH"))
        with self._tinfo({"aaaa1111": (FULL_SID, self.wt)}):
            ok = executor.resume(req, self.cfg, runner=runner)
        self.assertFalse(ok)  # never raises (contract)
        ex = registry.load("R-950").execution or {}
        self.assertEqual(ex.get("resume_attempts"), 2)
        self.assertFalse(ex.get("last_resume_ok"))

    def test_resume_creates_the_transcript_cwd(self):
        # the worktree may have been cleaned up while the task slept
        req = self._mk_req(execution={"session_id": "aaaa1111"})
        runner = mock.Mock(return_value=_proc(0))
        self.assertFalse(self.wt.exists())
        with self._tinfo({"aaaa1111": (FULL_SID, self.wt)}):
            self.assertTrue(executor.resume(req, self.cfg, runner=runner))
        self.assertTrue(self.wt.is_dir())


if __name__ == "__main__":
    unittest.main()
