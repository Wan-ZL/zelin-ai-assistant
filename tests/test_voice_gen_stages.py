"""voice_gen.generate — the run / validate / save stages split out in P3b
(docs/VOICE.md).

Pins: the runner stage's three outcomes (clean, spawn/timeout error, non-zero
exit preferring stderr then stdout), the skeleton failure line, the save stage
appending a trailing newline and naming the backup, and the analytics reason
clip on failures.
"""
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import voice_gen


class RunStageTestCase(unittest.TestCase):
    def setUp(self):
        self.events = []
        patcher = mock.patch.object(voice_gen.analytics, "log_event",
                                    side_effect=lambda ev, **kw: self.events.append(kw))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_clean_exit(self):
        proc = SimpleNamespace(returncode=0, stdout="text")
        self.assertEqual(voice_gen._run(lambda p: proc, "prompt"), (proc, None))
        self.assertEqual(voice_gen._proc_text(proc), "text")
        self.assertEqual(voice_gen._proc_text(SimpleNamespace(stdout=None)), "")

    def test_spawn_error_and_timeout(self):
        def boom(_p):
            raise subprocess.TimeoutExpired("claude", 30)

        proc, failure = voice_gen._run(boom, "prompt")
        self.assertIsNone(proc)
        self.assertFalse(failure[0])
        self.assertTrue("旧档案未改动" in failure[1] or "untouched" in failure[1])
        self.assertTrue(self.events[-1]["reason"].startswith("TimeoutExpired"))

        def missing(_p):
            raise FileNotFoundError("claude")

        self.assertFalse(voice_gen._run(missing, "p")[1][0])

    def test_exit_failure_prefers_stderr(self):
        proc = SimpleNamespace(returncode=2, stderr=" err ", stdout="out")
        ok, msg = voice_gen._exit_failure(proc)
        self.assertFalse(ok)
        self.assertEqual(self.events[-1]["reason"], "exit 2: err")
        proc = SimpleNamespace(returncode=3, stderr="", stdout="only out")
        voice_gen._exit_failure(proc)
        self.assertEqual(self.events[-1]["reason"], "exit 3: only out")
        voice_gen._exit_failure(SimpleNamespace())
        self.assertEqual(self.events[-1]["reason"].rstrip(), "exit ?:")   # analytics.clip strips
        _proc, failure = voice_gen._run(lambda p: SimpleNamespace(returncode=1, stderr="x"), "p")
        self.assertFalse(failure[0])

    def test_skeleton_failure_and_reason_clip(self):
        ok, msg = voice_gen._skeleton_failure("缺少 A/B")
        self.assertFalse(ok)
        self.assertIn("缺少 A/B", msg)
        self.assertEqual(self.events[-1]["reason"], "skeleton: 缺少 A/B")
        voice_gen._fail("r" * 300, "m")
        self.assertLessEqual(len(self.events[-1]["reason"]), 121)


class SaveStageTestCase(unittest.TestCase):
    def test_save_profile_backs_up_and_appends_newline(self):
        dest = voice_gen.profile_path()
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        for bak in dest.parent.glob(dest.name + ".bak-*"):
            bak.unlink()
        with mock.patch.object(voice_gen.analytics, "log_event") as le:
            ok, msg = voice_gen._save_profile("first")
        self.assertTrue(ok)
        self.assertEqual(dest.read_text(encoding="utf-8"), "first\n")
        self.assertNotIn(".bak-", msg)
        le.assert_called_once_with("voice_gen", ok=True, chars=5)
        with mock.patch.object(voice_gen.analytics, "log_event"):
            ok, msg = voice_gen._save_profile("second\n")
        self.assertEqual(dest.read_text(encoding="utf-8"), "second\n")
        self.assertIn(".bak-", msg)
        baks = list(dest.parent.glob(dest.name + ".bak-*"))
        self.assertEqual(len(baks), 1)
        self.assertEqual(baks[0].read_text(encoding="utf-8"), "first\n")
        for p in [dest] + baks:
            p.unlink()


if __name__ == "__main__":
    unittest.main()
