"""act/lib/platform.py — the Linux + Windows service-manager + notification seam.

Companion to tests/test_platform.py; this file focuses on the parts a Linux /
Windows port hangs off (docs/LINUX.md, docs/WINDOWS.md):

  * service_list_text() shells `systemctl --user list-units` (Linux) /
    `schtasks /query /fo LIST /v` (Windows) — the text act.doctor's systemd /
    schtasks branch parses; argv shape + robustness pinned here;
  * notify_user() builds the exact `notify-send` argv (Linux) / a
    `powershell` WinRT-toast argv (Windows), and never raises when the binary
    is missing / the runner fails.

All via an injected fake runner — nothing is ever actually spawned, so this is
green on the macOS / ubuntu / windows CI runners alike; the REAL loading of
units/tasks and REAL notify-send/toast firing are friend-tested on a real box
(see the "needs a real machine" sections in docs/LINUX.md, docs/WINDOWS.md).
"""
import subprocess
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import platform


class _FakeRunner:
    """Injectable subprocess runner: records (argv, timeout)."""

    def __init__(self, returncode=0, stdout="", stderr="", raises=None):
        self.calls = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.raises = raises

    def __call__(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(args=argv, returncode=self.returncode,
                                           stdout=self.stdout, stderr=self.stderr)


class LinuxServiceListTestCase(unittest.TestCase):
    def setUp(self):
        p = mock.patch("sys.platform", "linux")
        p.start()
        self.addCleanup(p.stop)

    def test_argv_is_systemctl_user_list_units(self):
        runner = _FakeRunner(stdout="unit line\n")
        platform.service_list_text(runner=runner)
        (argv, timeout), = runner.calls
        self.assertEqual(argv, [
            "systemctl", "--user", "list-units", "--type=service,timer",
            "--all", "--no-legend", "--no-pager"])
        self.assertEqual(timeout, 10)

    def test_combines_stdout_and_stderr(self):
        # doctor parses line by line, so both streams must survive
        runner = _FakeRunner(stdout="zelin-actd.service ...\n",
                             stderr="Failed to connect to bus\n")
        out = platform.service_list_text(runner=runner)
        self.assertIn("zelin-actd.service", out)
        self.assertIn("Failed to connect to bus", out)

    def test_missing_systemctl_returns_empty_never_raises(self):
        self.assertEqual(platform.service_list_text(
            runner=_FakeRunner(raises=FileNotFoundError())), "")
        self.assertEqual(platform.service_list_text(
            runner=_FakeRunner(raises=subprocess.TimeoutExpired(
                cmd="systemctl", timeout=10))), "")


class LinuxNotifyTestCase(unittest.TestCase):
    def setUp(self):
        p = mock.patch("sys.platform", "linux")
        p.start()
        self.addCleanup(p.stop)

    def test_notify_send_argv_without_subtitle(self):
        runner = _FakeRunner()
        self.assertTrue(platform.notify_user("标题", "正文", runner=runner))
        (argv, timeout), = runner.calls
        self.assertEqual(argv, ["notify-send", "标题", "正文"])
        self.assertEqual(timeout, 10)

    def test_notify_send_argv_folds_subtitle_into_body(self):
        runner = _FakeRunner()
        platform.notify_user("t", "b", subtitle="s", runner=runner)
        (argv, _), = runner.calls
        self.assertEqual(argv, ["notify-send", "t", "s\nb"])

    def test_missing_notify_send_returns_false_never_raises(self):
        self.assertFalse(platform.notify_user(
            "t", "b", runner=_FakeRunner(raises=FileNotFoundError())))
        self.assertFalse(platform.notify_user(
            "t", "b", runner=_FakeRunner(returncode=1)))


class WindowsServiceListTestCase(unittest.TestCase):
    def setUp(self):
        p = mock.patch("sys.platform", "win32")
        p.start()
        self.addCleanup(p.stop)

    def test_is_windows_true(self):
        self.assertTrue(platform.is_windows())
        self.assertFalse(platform.is_darwin())

    def test_argv_is_schtasks_query_list_verbose(self):
        runner = _FakeRunner(stdout="TaskName: \\ZelinAIAssistant\\actd\n")
        platform.service_list_text(runner=runner)
        (argv, timeout), = runner.calls
        self.assertEqual(argv, ["schtasks", "/query", "/fo", "LIST", "/v"])
        self.assertEqual(timeout, 10)

    def test_combines_stdout_and_stderr(self):
        runner = _FakeRunner(stdout="TaskName: \\ZelinAIAssistant\\actd\n",
                             stderr="WARNING: something\n")
        out = platform.service_list_text(runner=runner)
        self.assertIn("ZelinAIAssistant", out)
        self.assertIn("WARNING", out)

    def test_missing_schtasks_returns_empty_never_raises(self):
        self.assertEqual(platform.service_list_text(
            runner=_FakeRunner(raises=FileNotFoundError())), "")
        self.assertEqual(platform.service_list_text(
            runner=_FakeRunner(raises=subprocess.TimeoutExpired(
                cmd="schtasks", timeout=10))), "")


class WindowsNotifyTestCase(unittest.TestCase):
    def setUp(self):
        p = mock.patch("sys.platform", "win32")
        p.start()
        self.addCleanup(p.stop)

    def test_powershell_toast_argv_and_payload(self):
        runner = _FakeRunner()
        self.assertTrue(platform.notify_user("标题", "正文", runner=runner))
        (argv, timeout), = runner.calls
        self.assertEqual(argv[:4],
                         ["powershell", "-NoProfile", "-NonInteractive", "-Command"])
        script = argv[4]
        # the WinRT toast API is present, and title/body were injected
        self.assertIn("ToastNotificationManager", script)
        self.assertIn("标题", script)
        self.assertIn("正文", script)
        self.assertEqual(timeout, 10)

    def test_subtitle_folded_into_body(self):
        runner = _FakeRunner()
        platform.notify_user("t", "b", subtitle="s", runner=runner)
        (argv, _), = runner.calls
        self.assertIn("s\nb", argv[4])

    def test_single_quotes_are_escaped_for_powershell(self):
        # a title with a quote must not break out of the PS single-quoted string
        runner = _FakeRunner()
        platform.notify_user("it's", "o'clock", runner=runner)
        (argv, _), = runner.calls
        self.assertIn("it''s", argv[4])
        self.assertIn("o''clock", argv[4])

    def test_toast_failure_returns_false_never_raises(self):
        self.assertFalse(platform.notify_user(
            "t", "b", runner=_FakeRunner(raises=FileNotFoundError())))
        self.assertFalse(platform.notify_user(
            "t", "b", runner=_FakeRunner(returncode=1)))


if __name__ == "__main__":
    unittest.main()
