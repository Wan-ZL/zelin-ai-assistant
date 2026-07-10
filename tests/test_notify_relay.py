"""act/lib/notify.py §28 app relay queue — queue write format + atomicity,
the 20 s osascript-fallback verdict (injectable runner/clock), and the 1h
stale guard.

No osascript / pgrep ever actually runs: runners are injected and
sys.platform is pinned (same discipline as tests/test_platform.py), so the
suite behaves identically on the macOS and ubuntu CI jobs.
"""
import json
import subprocess
import time
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import config, notify


class _Runner:
    """Injectable subprocess runner: returncode per argv[0], records calls."""

    def __init__(self, rc_by_cmd=None):
        self.calls = []
        self.rc_by_cmd = rc_by_cmd or {}

    def __call__(self, argv, timeout) -> subprocess.CompletedProcess:
        self.calls.append((list(argv), timeout))
        return subprocess.CompletedProcess(
            args=argv, returncode=self.rc_by_cmd.get(argv[0], 0),
            stdout="", stderr="")

    @property
    def cmds(self):
        return [argv[0] for argv, _ in self.calls]


def _clear_queue():
    qdir = config.NOTIFY_QUEUE_DIR
    if qdir.exists():
        for f in qdir.iterdir():
            f.unlink()


class QueueWriteTestCase(unittest.TestCase):
    def setUp(self):
        _clear_queue()

    def test_format(self):
        path = notify._queue_write("标题", "正文", "sub")
        self.assertIsNotNone(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["title"], "标题")
        self.assertEqual(data["body"], "正文")
        self.assertEqual(data["subtitle"], "sub")
        # filename is derived from the id — the app posts with identifier=id
        self.assertEqual(data["id"] + ".json", path.name)
        self.assertIsInstance(data["created_at"], int)
        self.assertAlmostEqual(data["created_at"], time.time(), delta=5)

    def test_subtitle_key_absent_when_not_given(self):
        path = notify._queue_write("t", "b")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("subtitle", data)

    def test_atomicity_no_tmp_leftover(self):
        notify._queue_write("t", "b")
        names = [p.name for p in config.NOTIFY_QUEUE_DIR.iterdir()]
        self.assertEqual(len(names), 1)
        self.assertFalse(any(n.endswith(".tmp") for n in names))

    def test_write_failure_returns_none_and_leaves_no_corpse(self):
        with mock.patch("act.lib.notify.os.replace", side_effect=OSError("boom")):
            self.assertIsNone(notify._queue_write("t", "b"))
        self.assertEqual(list(config.NOTIFY_QUEUE_DIR.iterdir()), [])


class NativeNotifyRoutingTestCase(unittest.TestCase):
    def setUp(self):
        _clear_queue()

    def test_darwin_queues_and_arms_fallback(self):
        with mock.patch("sys.platform", "darwin"), \
             mock.patch("act.lib.notify.threading.Timer") as timer, \
             mock.patch("act.lib.platform.notify_user") as nu:
            self.assertTrue(notify._native_notify("t", "b", "s"))
        nu.assert_not_called()   # relay replaces the direct osascript call
        files = list(config.NOTIFY_QUEUE_DIR.glob("*.json"))
        self.assertEqual(len(files), 1)
        timer.assert_called_once()
        args, kwargs = timer.call_args
        self.assertEqual(args[0], notify.FALLBACK_AFTER_S)
        self.assertEqual(args[1], notify._fallback_check)
        self.assertEqual(kwargs["args"], (files[0], "t", "b", "s"))
        timer.return_value.start.assert_called_once()

    def test_queue_failure_falls_back_to_os_seam(self):
        with mock.patch("sys.platform", "darwin"), \
             mock.patch("act.lib.notify._queue_write", return_value=None), \
             mock.patch("act.lib.platform.notify_user", return_value=True) as nu:
            self.assertTrue(notify._native_notify("t", "b"))
        nu.assert_called_once_with("t", "b", None)

    def test_non_darwin_skips_the_queue(self):
        with mock.patch("sys.platform", "linux"), \
             mock.patch("act.lib.platform.notify_user", return_value=True) as nu:
            self.assertTrue(notify._native_notify("t", "b"))
        nu.assert_called_once_with("t", "b", None)
        self.assertEqual(list(config.NOTIFY_QUEUE_DIR.glob("*.json")), [])

    def test_notify_routes_through_relay(self):
        with mock.patch("act.lib.notify._native_notify", return_value=True) as nn, \
             mock.patch("act.lib.notify._phone_mirror"):
            self.assertTrue(notify.notify("t", "b", subtitle="s"))
        nn.assert_called_once_with("t", "b", "s")


class FallbackCheckTestCase(unittest.TestCase):
    def setUp(self):
        _clear_queue()
        patcher = mock.patch("sys.platform", "darwin")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_consumed_file_means_no_fallback(self):
        path = notify._queue_write("t", "b")
        path.unlink()   # the app consumed it within the grace period
        runner = _Runner()
        self.assertFalse(notify._fallback_check(path, "t", "b", runner=runner))
        self.assertEqual(runner.calls, [])   # not even a pgrep

    def test_app_running_leaves_the_file_alone(self):
        path = notify._queue_write("t", "b")
        runner = _Runner(rc_by_cmd={"pgrep": 0})   # app process found
        self.assertFalse(notify._fallback_check(path, "t", "b", runner=runner))
        self.assertTrue(path.exists())             # still the app's to consume
        self.assertEqual(runner.cmds, ["pgrep"])   # osascript never fired

    def test_no_app_falls_back_once_and_deletes(self):
        path = notify._queue_write("t", "b")
        runner = _Runner(rc_by_cmd={"pgrep": 1})   # no app process
        self.assertTrue(
            notify._fallback_check(path, "t", "b", "s", runner=runner))
        self.assertFalse(path.exists())
        self.assertEqual(runner.cmds, ["pgrep", "osascript"])
        script = runner.calls[1][0][2]   # the old display-notification shape
        self.assertIn('display notification "b" with title "t"', script)
        self.assertIn('subtitle "s"', script)

    def test_stale_file_deleted_without_posting(self):
        path = notify._queue_write("t", "b")
        runner = _Runner(rc_by_cmd={"pgrep": 1})
        stale_now = time.time() + notify.STALE_AFTER_S + 60   # injectable clock
        self.assertFalse(notify._fallback_check(
            path, "t", "b", runner=runner, now=stale_now))
        self.assertFalse(path.exists())            # corpse cleaned up
        self.assertEqual(runner.cmds, ["pgrep"])   # but never posted

    def test_pgrep_targets_the_frozen_app_executable(self):
        runner = _Runner(rc_by_cmd={"pgrep": 0})
        self.assertTrue(notify._app_running(runner=runner))
        self.assertEqual(runner.calls[0][0], ["pgrep", "-x", "ZelinAIEngineer"])

    def test_app_running_never_raises(self):
        def exploding_runner(argv, timeout):
            raise OSError("no pgrep here")
        self.assertFalse(notify._app_running(runner=exploding_runner))


if __name__ == "__main__":
    unittest.main()
