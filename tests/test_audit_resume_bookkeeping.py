"""Auto-resume bookkeeping on executor.resume's early-return False paths
(audit 2026-07-15).

executor.resume returns False WITHOUT touching resume_attempts/last_resume_at
when the transcript is gone (Claude Code retention cleanup, ~/.claude/projects
wiped). actd used to read attempts=0 forever: the 5-try exhaustion notification
never fired, the card showed 运行中 indefinitely, and a resume + log line +
analytics event burst every 10s pass with zero backoff. actd now records the
failed attempt itself when resume bailed before its own bookkeeping.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as _dt
import os
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import config, registry
from act.lib.registry import Requirement, State

SID = "cccc3333-0000-4000-8000-000000000001"


def _iso_ago(seconds):
    t = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class ResumeBookkeepingBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()
        actd._HARVEST_PROBE_AT.clear()
        p = mock.patch.object(actd.notify, "notify", mock.Mock(return_value=True))
        self.notify = p.start()
        self.addCleanup(p.stop)
        # v0.42 §15: notify copy follows AIASSISTANT_UI_LANG > persisted >
        # system locale — pin zh so the zh-title assertion stays
        # locale-independent on any runner.
        lang = mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "zh"})
        lang.start()
        self.addCleanup(lang.stop)
        # dead-path probe must stay hermetic (no transcript reads)
        p2 = mock.patch.object(actd, "_promote_if_delivered", return_value=False)
        p2.start()
        self.addCleanup(p2.stop)

    def _mk_req(self, execution=None, req_id="R-970"):
        req = Requirement(id=req_id, title="resume 死角测试",
                          status=State.EXECUTING.value,
                          execution=execution or {"session_id": SID})
        registry.save(req)
        return req

    def _reconcile(self, resume, notified=None):
        with mock.patch.object(actd, "_run_claude_agents", return_value=[]), \
             mock.patch.object(actd.executor, "resume", resume):
            return actd.reconcile_executing(
                self.cfg, notified if notified is not None else set())


class EarlyReturnBookkeepingTestCase(ResumeBookkeepingBase):
    def test_bookkeeping_free_failure_still_counts_an_attempt(self):
        self._mk_req()
        # mimics the transcript-gone early return: False, req untouched
        resume = mock.Mock(return_value=False)
        self._reconcile(resume)
        ex = registry.load("R-970").execution or {}
        self.assertEqual(ex.get("resume_attempts"), 1)
        self.assertTrue(ex.get("last_resume_at"))
        self.assertIs(ex.get("last_resume_ok"), False)

    def test_backoff_now_applies_instead_of_10s_spam(self):
        self._mk_req()
        resume = mock.Mock(return_value=False)
        self._reconcile(resume)
        # immediate second pass: attempt 1 -> 60s backoff window still open
        self._reconcile(resume)
        self.assertEqual(resume.call_count, 1)

    def test_exhaustion_notification_finally_fires(self):
        self._mk_req(execution={"session_id": SID, "resume_attempts": 4,
                                "last_resume_at": _iso_ago(9999)})
        resume = mock.Mock(return_value=False)
        self._reconcile(resume)               # 5th failed attempt, recorded
        self.assertEqual(
            (registry.load("R-970").execution or {}).get("resume_attempts"), 5)
        self._reconcile(resume)               # next pass: attempts >= 5 -> give up
        ex = registry.load("R-970").execution or {}
        self.assertTrue(ex.get("resume_exhausted"))
        titles = [c.args[0] for c in self.notify.call_args_list]
        self.assertTrue(any("自动恢复已放弃" in t for t in titles))

    def test_no_double_count_when_resume_did_its_own_bookkeeping(self):
        self._mk_req()

        def failing_launch(req, cfg=None, runner=None):
            # mimics the REAL post-launch failure path: bookkeeping recorded,
            # then False returned
            ex = dict(req.execution or {})
            ex["resume_attempts"] = int(ex.get("resume_attempts", 0)) + 1
            ex["last_resume_at"] = _iso_ago(0)
            ex["last_resume_ok"] = False
            req.execution = ex
            registry.save(req)
            return False

        self._reconcile(mock.Mock(side_effect=failing_launch))
        ex = registry.load("R-970").execution or {}
        self.assertEqual(ex.get("resume_attempts"), 1)  # not 2


if __name__ == "__main__":
    unittest.main()
