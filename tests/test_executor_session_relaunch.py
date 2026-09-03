"""The stop-idle-then-resume plumbing shared by resume / rework / brief (§4,
§11, §44.3) — default runners and the session-resolution fallbacks.

Pinned per launch site, with ``subprocess.run`` patched so the DEFAULT runner's
argv is observed instead of a real claude:
- resume: ``<dispatch argv> --name <session name> --resume <full sid>`` and,
  with ``prompt=``, the SCRUBBED prompt as the trailing argument (empty /
  blank prompt → no trailing argument); cwd = the transcript's last cwd,
  120s timeout, ``llm.runner_env``;
- rework / brief: the idle process is stopped first (``stop_session`` with the
  roster info already fetched), then the same ``--resume`` argv with the
  scrubbed prompt;
- root_session_id fallback when the current sid has no transcript (rework and
  brief — resume's is in tests/test_resume.py); an unrecreatable cwd is an
  abort (rework: persisted; brief: give-up note); a runner that raises is a
  failed launch, never an exception; a fresh id in the launch output is
  adopted by brief; ``cfg=None`` loads the sandbox config.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import executor, llm
from act.lib import config, registry
from act.lib.registry import Requirement, State

FULL_SID = "feedc0de-0000-4000-8000-000000000001"
ROOT_SID = "0000root-0000-4000-8000-000000000002"
SECRET = "sk-ant-api03-" + "a" * 40


def _proc(rc=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["claude"], rc, stdout=stdout, stderr=stderr)


class _Base(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()
        self.wt = Path(tempfile.mkdtemp(prefix="relaunch-wt-")) / "worktree"
        self.calls = []
        for patcher in (
            mock.patch.object(executor, "_agent_info", return_value={"pid": None}),
            mock.patch.object(executor, "_briefing_window_open", return_value=True),
            mock.patch.object(executor.subprocess, "run", self._run),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, argv, **kw):
        self.calls.append((list(argv), kw))
        return _proc(0, stdout="backgrounded · 0a0a0a0a")

    def _req(self, status, execution, **kw):
        req = Requirement(id="R-777", title="重启 测试", status=status,
                          execution=execution, **kw)
        registry.save(req)
        return req

    def _tinfo(self, mapping):
        return mock.patch.object(executor, "_transcript_info",
                                 side_effect=lambda sid: mapping.get(str(sid)))

    def _assert_resume_argv(self, argv, kw, sid, trailing):
        base = llm.dispatch_argv(self.cfg)
        self.assertEqual(argv[:len(base)], base)
        rest = argv[len(base):]
        self.assertEqual(rest[:4], ["--name", "R-777 · 重启 测试", "--resume", sid])
        self.assertEqual(rest[4:], trailing)
        self.assertEqual(kw["cwd"], str(self.wt))
        self.assertEqual(kw["timeout"], 120)
        self.assertTrue(kw["capture_output"] and kw["text"])
        self.assertIn("PATH", kw["env"])


class ResumeDefaultRunnerTestCase(_Base):
    def test_plain_resume_argv(self):
        req = self._req(State.EXECUTING.value, {"session_id": "feedc0de"})
        with self._tinfo({"feedc0de": (FULL_SID, self.wt)}):
            self.assertTrue(executor.resume(req, self.cfg))
        argv, kw = self.calls[-1]
        self._assert_resume_argv(argv, kw, FULL_SID, [])
        self.assertEqual(registry.load("R-777").execution["session_id"], "0a0a0a0a")

    def test_prompt_rides_scrubbed_as_the_last_argument(self):
        req = self._req(State.EXECUTING.value, {"session_id": "feedc0de"})
        with self._tinfo({"feedc0de": (FULL_SID, self.wt)}):
            self.assertTrue(executor.resume(req, self.cfg, prompt=f"OWNER UPDATE key={SECRET}"))
        argv, kw = self.calls[-1]
        self.assertEqual(len(argv), len(llm.dispatch_argv(self.cfg)) + 5)
        self.assertNotIn(SECRET, argv[-1])
        self.assertTrue(argv[-1].startswith("OWNER UPDATE key="))

    def test_blank_prompt_adds_no_argument(self):
        req = self._req(State.EXECUTING.value, {"session_id": "feedc0de"})
        with self._tinfo({"feedc0de": (FULL_SID, self.wt)}):
            self.assertTrue(executor.resume(req, self.cfg, prompt="   "))
        argv, kw = self.calls[-1]
        self._assert_resume_argv(argv, kw, FULL_SID, [])

    def test_cfg_none_loads_config_and_unrecreatable_cwd_is_false(self):
        req = self._req(State.EXECUTING.value, {"session_id": "feedc0de"})
        blocker = self.wt.parent / "blocker"
        blocker.parent.mkdir(parents=True, exist_ok=True)
        blocker.write_text("x", encoding="utf-8")
        with self._tinfo({"feedc0de": (FULL_SID, blocker / "child")}):
            self.assertFalse(executor.resume(req))
        self.assertEqual(self.calls, [])
        self.assertNotIn("resume_attempts", registry.load("R-777").execution)


class ReworkDefaultRunnerTestCase(_Base):
    def test_stop_then_resume_with_feedback_prompt(self):
        req = self._req(State.REVIEW.value, {"session_id": "feedc0de", "done": True})
        with self._tinfo({"feedc0de": (FULL_SID, self.wt)}), \
                mock.patch.object(executor, "stop_session", return_value=True) as stop:
            self.assertTrue(executor.rework(req, f"再补测试 {SECRET}", self.cfg))
        stop.assert_called_once_with(FULL_SID, info={"pid": None})
        argv, kw = self.calls[-1]
        self.assertNotIn(SECRET, argv[-1])
        self.assertIn("再补测试", argv[-1])
        self._assert_resume_argv(argv, kw, FULL_SID, [argv[-1]])
        saved = registry.load("R-777")
        self.assertEqual(saved.status, State.EXECUTING.value)
        self.assertEqual(saved.execution["session_id"], "0a0a0a0a")

    def test_root_session_fallback(self):
        req = self._req(State.REVIEW.value,
                        {"session_id": "aaaa1111", "root_session_id": ROOT_SID})
        with self._tinfo({ROOT_SID: (ROOT_SID, self.wt)}), \
                mock.patch.object(executor, "stop_session", return_value=False):
            self.assertTrue(executor.rework(req, "反馈", self.cfg))
        argv, _kw = self.calls[-1]
        self.assertIn(ROOT_SID, argv)
        self.assertEqual(registry.load("R-777").execution["root_session_id"], ROOT_SID)

    def test_unrecreatable_cwd_is_a_persisted_abort(self):
        req = self._req(State.REVIEW.value, {"session_id": "feedc0de"})
        blocker = self.wt.parent / "blocker"
        blocker.parent.mkdir(parents=True, exist_ok=True)
        blocker.write_text("x", encoding="utf-8")
        with self._tinfo({"feedc0de": (FULL_SID, blocker / "child")}):
            self.assertFalse(executor.rework(req, "反馈", self.cfg))
        ex = registry.load("R-777").execution
        self.assertIn("cannot recreate", ex["last_error"])
        self.assertEqual(self.calls, [])

    def test_runner_exception_is_a_failed_launch(self):
        req = self._req(State.REVIEW.value, {"session_id": "feedc0de"})
        boom = mock.Mock(side_effect=subprocess.TimeoutExpired("claude", 120))
        with self._tinfo({"feedc0de": (FULL_SID, self.wt)}):
            self.assertFalse(executor.rework(req, "反馈", self.cfg, runner=boom))
        saved = registry.load("R-777")
        self.assertEqual(saved.status, State.REVIEW.value)
        self.assertEqual(saved.execution["rework_count"], 1)
        self.assertEqual(saved.execution["last_error"], "rework launch failed (no output)")

    def test_cfg_none_loads_config(self):
        req = self._req(State.REVIEW.value, {"session_id": "feedc0de"})
        with self._tinfo({"feedc0de": (FULL_SID, self.wt)}), \
                mock.patch.object(executor, "stop_session", return_value=False):
            self.assertTrue(executor.rework(req, "反馈"))


class BriefDefaultRunnerTestCase(_Base):
    def _executing(self, execution):
        execution.setdefault("pending_briefings", ["第一条"])
        return self._req(State.EXECUTING.value, execution)

    def test_stop_then_resume_with_fenced_prompt(self):
        req = self._executing({"session_id": "feedc0de"})
        with self._tinfo({"feedc0de": (FULL_SID, self.wt)}), \
                mock.patch.object(executor, "stop_session", return_value=True) as stop:
            self.assertTrue(executor.brief(req, self.cfg))
        stop.assert_called_once_with(FULL_SID, info={"pid": None})
        argv, kw = self.calls[-1]
        self.assertTrue(argv[-1].startswith("BACKGROUND INFO (no action needed):"))
        self._assert_resume_argv(argv, kw, FULL_SID, [argv[-1]])
        ex = registry.load("R-777").execution
        self.assertEqual(ex["session_id"], "0a0a0a0a")        # fresh id adopted
        self.assertEqual(ex["root_session_id"], FULL_SID)
        self.assertEqual(ex["resume_attempts"], 0)

    def test_root_session_fallback(self):
        req = self._executing({"session_id": "aaaa1111", "root_session_id": ROOT_SID})
        with self._tinfo({ROOT_SID: (ROOT_SID, self.wt)}), \
                mock.patch.object(executor, "stop_session", return_value=False):
            self.assertTrue(executor.brief(req, self.cfg))
        self.assertIn(ROOT_SID, self.calls[-1][0])

    def test_no_session_id_gives_up_with_a_note(self):
        req = self._executing({})
        self.assertFalse(executor.brief(req, self.cfg))
        saved = registry.load("R-777")
        self.assertNotIn("pending_briefings", saved.execution)
        self.assertIn("无会话", saved.notes)

    def test_missing_transcript_gives_up_with_a_note(self):
        req = self._executing({"session_id": "feedc0de"})
        with self._tinfo({}):
            self.assertFalse(executor.brief(req, self.cfg))
        self.assertIn("transcript 缺失", registry.load("R-777").notes)

    def test_unrecreatable_cwd_gives_up_with_a_note(self):
        req = self._executing({"session_id": "feedc0de"})
        blocker = self.wt.parent / "blocker"
        blocker.parent.mkdir(parents=True, exist_ok=True)
        blocker.write_text("x", encoding="utf-8")
        with self._tinfo({"feedc0de": (FULL_SID, blocker / "child")}):
            self.assertFalse(executor.brief(req, self.cfg))
        self.assertIn("会话目录不可用", registry.load("R-777").notes)
        self.assertEqual(self.calls, [])

    def test_runner_exception_burns_one_attempt_and_keeps_the_queue(self):
        req = self._executing({"session_id": "feedc0de"})
        boom = mock.Mock(side_effect=OSError("no claude"))
        with self._tinfo({"feedc0de": (FULL_SID, self.wt)}):
            self.assertFalse(executor.brief(req, self.cfg, runner=boom))
        ex = registry.load("R-777").execution
        self.assertEqual(ex["pending_briefings"], ["第一条"])
        self.assertEqual(ex["briefing_attempts"], 1)

    def test_cfg_none_loads_config(self):
        req = self._executing({"session_id": "feedc0de"})
        with self._tinfo({"feedc0de": (FULL_SID, self.wt)}), \
                mock.patch.object(executor, "stop_session", return_value=False):
            self.assertTrue(executor.brief(req))


if __name__ == "__main__":
    unittest.main()
