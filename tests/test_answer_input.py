"""§39 answer_input — executor.answer delivery + actd boundary validation.

executor.answer reuses the rework stop-idle-then-resume plumbing with an
``OWNER ANSWER:`` prefix and its OWN bookkeeping (answer_count, never
rework_count); a clean launch resets the auto-resume machinery
(resume_attempts=0, resume_exhausted dropped) WITHOUT counting a resume
attempt. actd._apply_answer_input is the fail-closed boundary: text 1..4000,
unknown card, stale expected_status, non-executing status — and every ack is
honest (§5.4): "running" only when the answer genuinely reached the session,
failures are noop + notes tag + notification (never silent).

_transcript_info and _agent_info are patched throughout (tests/test_rework.py
discipline): never glob real transcripts or shell out to `claude agents`.
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, executor
from act.lib import config, registry
from act.lib.registry import Requirement, State

FULL_SID = "beefcafe-0000-4000-8000-000000000002"


def _proc(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["claude"], returncode,
                                       stdout=stdout, stderr=stderr)


class AnswerExecutorTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()
        self.wt = Path(tempfile.mkdtemp(prefix="answer-wt-")) / "worktree"
        patcher = mock.patch.object(executor, "_agent_info", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _mk_req(self, execution=None):
        req = Requirement(id="R-970", title="回答测试",
                          status=State.EXECUTING.value,
                          execution=execution if execution is not None
                          else {"session_id": "beefcafe"})
        registry.save(req)
        return req

    def _tinfo(self):
        return mock.patch.object(
            executor, "_transcript_info",
            side_effect=lambda sid: (FULL_SID, self.wt)
            if str(sid).startswith("beefcafe") else None)

    def test_clean_launch_records_answer_bookkeeping(self):
        req = self._mk_req({"session_id": "beefcafe",
                            "resume_attempts": 4, "resume_exhausted": True,
                            "last_error": "old", "last_error_at": "x"})
        runner = mock.Mock(return_value=_proc(0, stdout="backgrounded · deadbeef"))
        with self._tinfo():
            ok = executor.answer(req, "用 A 方案", self.cfg, runner=runner)
        self.assertTrue(ok)
        # the prompt is the minimally-prefixed owner answer, not a 打回 preamble
        self.assertEqual(runner.call_args[0][0], "OWNER ANSWER:\n用 A 方案")
        saved = registry.load("R-970")
        self.assertEqual(saved.status, State.EXECUTING.value)  # state machine untouched
        ex = saved.execution or {}
        self.assertEqual(ex.get("answer_count"), 1)
        self.assertTrue(str(ex.get("last_answer_at", "")).endswith("Z"))
        self.assertNotIn("rework_count", ex)          # never mixed with 打回 rounds
        self.assertEqual(ex.get("session_id"), "deadbeef")  # resume mints a new id
        # §39: the owner revived the session by hand — auto-resume starts fresh
        self.assertEqual(ex.get("resume_attempts"), 0)
        self.assertNotIn("resume_exhausted", ex)
        self.assertNotIn("last_error", ex)            # clean relaunch clears stale errors

    def test_failed_launch_records_error_and_keeps_resume_state(self):
        req = self._mk_req({"session_id": "beefcafe", "resume_attempts": 4,
                            "resume_exhausted": True})
        runner = mock.Mock(return_value=_proc(1, stderr="boom"))
        with self._tinfo():
            ok = executor.answer(req, "用 A", self.cfg, runner=runner)
        self.assertFalse(ok)
        ex = registry.load("R-970").execution or {}
        self.assertEqual(ex.get("answer_count"), 1)
        self.assertIn("boom", ex.get("last_error", ""))
        # a FAILED answer must not touch the auto-resume machinery
        self.assertEqual(ex.get("resume_attempts"), 4)
        self.assertTrue(ex.get("resume_exhausted"))

    def test_missing_transcript_aborts_without_launching(self):
        req = self._mk_req()
        runner = mock.Mock()
        with mock.patch.object(executor, "_transcript_info", return_value=None):
            ok = executor.answer(req, "用 A", self.cfg, runner=runner)
        self.assertFalse(ok)
        runner.assert_not_called()  # a launch would crash-loop minting new ids
        ex = registry.load("R-970").execution or {}
        self.assertIn("transcript missing", ex.get("last_error", ""))

    def test_no_session_id_aborts(self):
        req = self._mk_req({})
        ok = executor.answer(req, "用 A", self.cfg, runner=mock.Mock())
        self.assertFalse(ok)
        ex = registry.load("R-970").execution or {}
        self.assertIn("no session", ex.get("last_error", ""))

    def test_empty_text_is_a_pure_noop(self):
        req = self._mk_req()
        ok = executor.answer(req, "   ", self.cfg, runner=mock.Mock())
        self.assertFalse(ok)
        ex = registry.load("R-970").execution or {}
        self.assertNotIn("answer_count", ex)  # nothing was attempted


class ApplyAnswerInputTestCase(unittest.TestCase):
    """actd._apply_answer_input — boundary validation + honest acks (§5.4)."""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        # never fire a real (queued) notification from the failure path
        patcher = mock.patch.object(actd.notify, "notify", return_value=True)
        self.notify = patcher.start()
        self.addCleanup(patcher.stop)
        # §39.2 pre-delivery roster probe: never shell out to `claude agents`
        # from tests. Default = empty roster (dead/absent session — delivery
        # proceeds via the existing resume-a-dead-session path); individual
        # tests override with self._roster(...).
        roster = mock.patch.object(actd, "_run_claude_agents", return_value=[])
        self.roster = roster.start()
        self.addCleanup(roster.stop)

    def _roster(self, state, pid=4242):
        self.roster.return_value = [{
            "id": "beefcafe", "sessionId": FULL_SID, "state": state, "pid": pid,
        }]

    def _mk_req(self, status=State.EXECUTING.value):
        req = Requirement(id="R-971", title="回答测试", status=status,
                          execution={"session_id": "beefcafe"})
        registry.save(req)
        return req

    def _decision(self, **over):
        d = {"action": "answer_input", "id": "R-971", "text": "用 A 方案"}
        d.update(over)
        return d

    def test_happy_path_acks_running_with_notes_tag(self):
        self._mk_req()
        with mock.patch.object(actd.executor, "answer", return_value=True) as ans:
            result = actd._apply_answer_input(self._decision())
        self.assertEqual(result, "running")
        self.assertEqual(ans.call_args[0][1], "用 A 方案")
        self.assertIn("回答已送达", registry.load("R-971").notes or "")
        self.notify.assert_not_called()

    def test_delivery_failure_is_noop_and_visible(self):
        self._mk_req()
        def fail(r, t, *a, **kw):
            ex = dict(r.execution or {})
            ex["last_error"] = "answer failed: transcript missing"
            r.execution = ex
            registry.save(r)
            return False
        with mock.patch.object(actd.executor, "answer", side_effect=fail):
            result = actd._apply_answer_input(self._decision())
        self.assertEqual(result, "noop")
        saved = registry.load("R-971")
        self.assertIn("回答送达失败", saved.notes or "")
        self.assertIn("transcript missing", saved.notes or "")
        self.notify.assert_called_once()  # never a silent drop

    def test_non_string_text_dropped(self):
        self._mk_req()
        with mock.patch.object(actd.executor, "answer") as ans:
            self.assertEqual(
                actd._apply_answer_input(self._decision(text=42)), "noop")
            self.assertEqual(
                actd._apply_answer_input(self._decision(text=None)), "noop")
            self.assertEqual(
                actd._apply_answer_input(self._decision(text=["a"])), "noop")
        ans.assert_not_called()

    def test_length_boundaries(self):
        self._mk_req()
        with mock.patch.object(actd.executor, "answer", return_value=True) as ans:
            self.assertEqual(
                actd._apply_answer_input(self._decision(text="  ")), "noop")
            self.assertEqual(
                actd._apply_answer_input(self._decision(text="答" * 4001)), "noop")
            ans.assert_not_called()
            self.assertEqual(
                actd._apply_answer_input(self._decision(text="答" * 4000)), "running")
            ans.assert_called_once()

    def test_unknown_card_acks_unknown(self):
        self.assertEqual(
            actd._apply_answer_input(self._decision(id="R-404")), "unknown")
        self.assertEqual(
            actd._apply_answer_input(self._decision(id=None)), "unknown")

    def test_promoted_to_review_archives_text_and_notifies(self):
        # §39.2 promotion race: a blocked chat session with FINAL DRAFT gets
        # promoted executing→review between the board render and the inbox
        # pass — the owner's answer must not vanish while both UIs show
        # success: noop + notes carry the text + notification.
        self._mk_req(status=State.REVIEW.value)
        with mock.patch.object(actd.executor, "answer") as ans:
            self.assertEqual(actd._apply_answer_input(self._decision()), "noop")
        ans.assert_not_called()
        notes = registry.load("R-971").notes or ""
        self.assertIn("回答未投递", notes)
        self.assertIn("用 A 方案", notes)   # the typed text is archived, not lost
        self.assertIn("待验收", notes)
        self.notify.assert_called_once()

    def test_working_session_with_live_pid_is_never_stopped(self):
        # §39.2 roster probe: on-disk EXECUTING covers roster working too —
        # a stale second device's answer must NOT stop+redirect a session
        # that is actively running (it may already have been answered).
        self._mk_req()
        self._roster("working")
        with mock.patch.object(actd.executor, "answer") as ans:
            self.assertEqual(actd._apply_answer_input(self._decision()), "noop")
        ans.assert_not_called()   # no stop, no resume — the session runs on
        notes = registry.load("R-971").notes or ""
        self.assertIn("回答未投递", notes)
        self.assertIn("正在工作", notes)
        self.assertIn("用 A 方案", notes)
        self.notify.assert_called_once()

    def test_genuinely_blocked_session_receives_delivery(self):
        self._mk_req()
        self._roster("blocked")
        with mock.patch.object(actd.executor, "answer", return_value=True) as ans:
            self.assertEqual(actd._apply_answer_input(self._decision()), "running")
        ans.assert_called_once()
        self.notify.assert_not_called()

    def test_dead_or_absent_session_still_goes_through_answer(self):
        # roster empty (default) = session vanished: delivery proceeds via the
        # existing resume-a-dead-session path (executor.answer handles it)
        self._mk_req()
        with mock.patch.object(actd.executor, "answer", return_value=True) as ans:
            self.assertEqual(actd._apply_answer_input(self._decision()), "running")
        ans.assert_called_once()
        # alive-but-not-on-pid (roster row without pid) is dead too
        self._roster("working", pid=None)
        with mock.patch.object(actd.executor, "answer", return_value=True) as ans:
            self.assertEqual(actd._apply_answer_input(self._decision()), "running")
        ans.assert_called_once()

    def test_stale_expected_status_is_noop(self):
        self._mk_req()
        with mock.patch.object(actd.executor, "answer") as ans:
            self.assertEqual(
                actd._apply_answer_input(
                    self._decision(expected_status="review")), "noop")
            ans.assert_not_called()
        # the stale pin archives the text too (§39.2: stale ≠ silent)
        self.assertIn("回答未投递", registry.load("R-971").notes or "")
        # the phone's pin matches the projected lane → applies
        with mock.patch.object(actd.executor, "answer", return_value=True):
            self.assertEqual(
                actd._apply_answer_input(
                    self._decision(expected_status="executing")), "running")

    def test_process_inbox_consumes_answer_file(self):
        self._mk_req()
        config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
        path = config.INBOX_DIR / "answer-test.json"
        path.write_text(json.dumps(self._decision(), ensure_ascii=False),
                        encoding="utf-8")
        with mock.patch.object(actd.executor, "answer", return_value=True):
            n = actd.process_inbox()
        self.assertGreaterEqual(n, 1)
        self.assertFalse(path.exists())  # consumed + deleted like every action
        self.assertIn("回答已送达", registry.load("R-971").notes or "")


if __name__ == "__main__":
    unittest.main()
