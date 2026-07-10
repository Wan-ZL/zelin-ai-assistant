"""act/lib/platform.py — the OS seam (docs/PORTING.md).

Darwin implementations are smoke-tested via injected runners (no osascript /
open / launchctl ever actually runs), and dispatch is pinned by patching
sys.platform — so this file behaves identically on the macOS and ubuntu CI
jobs. Contract under test:

(a) darwin argv shapes: osascript `display notification` (with escaping),
    `open <path>`, `launchctl list`;
(b) linux argv shapes: notify-send, xdg-open; service listing is honestly "";
(c) unsupported OSes degrade to False/"" — never a crash;
(d) NOTHING raises: a runner throwing OSError/TimeoutExpired means False/"".
"""
import subprocess
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import platform


class _FakeRunner:
    """Injectable subprocess runner: records (argv, timeout)."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "",
                 raises: Exception = None):
        self.calls: list = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.raises = raises

    def __call__(self, argv, timeout) -> subprocess.CompletedProcess:
        self.calls.append((list(argv), timeout))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(args=argv, returncode=self.returncode,
                                           stdout=self.stdout, stderr=self.stderr)


class DarwinSeamTestCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("sys.platform", "darwin")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_is_darwin(self):
        self.assertTrue(platform.is_darwin())

    def test_notify_user_builds_osascript(self):
        runner = _FakeRunner()
        self.assertTrue(platform.notify_user("标题", "正文", runner=runner))
        (argv, timeout), = runner.calls
        self.assertEqual(argv[:2], ["osascript", "-e"])
        self.assertEqual(timeout, 10)
        self.assertIn('display notification "正文" with title "标题"', argv[2])
        self.assertNotIn("subtitle", argv[2])

    def test_notify_user_subtitle_and_escaping(self):
        runner = _FakeRunner()
        platform.notify_user('say "hi"', "a\\b", subtitle="sub", runner=runner)
        script = runner.calls[0][0][2]
        self.assertIn('with title "say \\"hi\\""', script)
        self.assertIn('notification "a\\\\b"', script)
        self.assertIn('subtitle "sub"', script)

    def test_notify_user_failure_and_exception_return_false(self):
        self.assertFalse(platform.notify_user("t", "b", runner=_FakeRunner(returncode=1)))
        self.assertFalse(platform.notify_user(
            "t", "b", runner=_FakeRunner(raises=OSError("no osascript"))))
        self.assertFalse(platform.notify_user(
            "t", "b", runner=_FakeRunner(
                raises=subprocess.TimeoutExpired(cmd="osascript", timeout=10))))

    def test_open_path_uses_open(self):
        runner = _FakeRunner()
        self.assertTrue(platform.open_path("/tmp/some file.command", runner=runner))
        (argv, timeout), = runner.calls
        self.assertEqual(argv, ["open", "/tmp/some file.command"])
        self.assertEqual(timeout, 15)
        self.assertFalse(platform.open_path("/x", runner=_FakeRunner(returncode=1)))
        self.assertFalse(platform.open_path("/x", runner=_FakeRunner(raises=OSError())))

    def test_service_list_text_wraps_launchctl(self):
        runner = _FakeRunner(stdout="123\t0\tcom.zelin.aiassistant.actd\n",
                             stderr="warn\n")
        out = platform.service_list_text(runner=runner)
        self.assertEqual(runner.calls[0][0], ["launchctl", "list"])
        self.assertIn("com.zelin.aiassistant.actd", out)
        self.assertIn("warn", out)   # combined streams, doctor parses lines
        self.assertEqual(platform.service_list_text(
            runner=_FakeRunner(raises=OSError())), "")


class LinuxSeamTestCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("sys.platform", "linux")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_is_darwin_false(self):
        self.assertFalse(platform.is_darwin())

    def test_notify_user_uses_notify_send(self):
        runner = _FakeRunner()
        self.assertTrue(platform.notify_user("t", "b", subtitle="s", runner=runner))
        (argv, _), = runner.calls
        self.assertEqual(argv, ["notify-send", "t", "s\nb"])
        # headless box: notify-send missing -> False, never a crash
        self.assertFalse(platform.notify_user(
            "t", "b", runner=_FakeRunner(raises=FileNotFoundError())))

    def test_open_path_uses_xdg_open(self):
        runner = _FakeRunner()
        self.assertTrue(platform.open_path("/tmp/x", runner=runner))
        self.assertEqual(runner.calls[0][0], ["xdg-open", "/tmp/x"])

    def test_service_list_text_is_empty_and_runs_nothing(self):
        runner = _FakeRunner(stdout="should never be used")
        self.assertEqual(platform.service_list_text(runner=runner), "")
        self.assertEqual(runner.calls, [])   # no launchctl equivalent yet


class UnsupportedOSTestCase(unittest.TestCase):
    def test_notify_user_degrades_to_false(self):
        runner = _FakeRunner()
        with mock.patch("sys.platform", "sunos5"):
            self.assertFalse(platform.notify_user("t", "b", runner=runner))
        self.assertEqual(runner.calls, [])


class NotifyDelegationTestCase(unittest.TestCase):
    def test_notify_routes_through_the_native_path(self):
        """act/lib/notify.notify must have no OS calls of its own left.

        Since §28 the native path is the app relay (_native_notify, no
        fallback) — its queue contract lives in tests/test_notify_relay.py;
        here we pin that notify() delegates to it, and that off-darwin (no
        app, no relay) the OS seam is still the one direct route.
        """
        from act.lib import notify
        with mock.patch.object(notify, "_native_notify",
                               return_value=True) as native:
            self.assertTrue(notify.notify("标题", "正文", subtitle="sub"))
        native.assert_called_once_with("标题", "正文", "sub")

    def test_native_path_reaches_the_seam_off_darwin(self):
        from act.lib import notify
        with mock.patch("sys.platform", "linux"), \
             mock.patch.object(platform, "notify_user",
                               return_value=True) as seam:
            self.assertTrue(notify._native_notify("标题", "正文", "sub"))
        seam.assert_called_once_with("标题", "正文", "sub")


if __name__ == "__main__":
    unittest.main()
