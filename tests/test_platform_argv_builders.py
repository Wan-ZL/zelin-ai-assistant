"""platform — the per-OS argv builders behind the two seam functions.

Pins the P3b split of notify_user / service_list_text: each builder's exact
argv on every OS, the unsupported-OS None, and that a None argv short-circuits
before any runner is touched (open_path retired in v2.0.0).
"""
import subprocess
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import platform


class NotifyArgvTestCase(unittest.TestCase):
    def test_darwin_builder_escapes_and_adds_subtitle(self):
        argv = platform._darwin_notify_argv('t"x', "b\\y", "s")
        self.assertEqual(argv[:2], ["osascript", "-e"])
        self.assertEqual(argv[2],
                         'display notification "b\\\\y" with title "t\\"x" subtitle "s"')
        self.assertNotIn("subtitle", platform._darwin_notify_argv("t", "b", None)[2])

    def test_windows_builder_folds_subtitle_and_doubles_quotes(self):
        argv = platform._windows_notify_argv("it's", "body", "sub")
        self.assertEqual(argv[:4], ["powershell", "-NoProfile", "-NonInteractive", "-Command"])
        self.assertIn("CreateTextNode('it''s')", argv[4])
        self.assertIn("CreateTextNode('sub\nbody')", argv[4])
        self.assertIn("CreateTextNode('body')", platform._windows_notify_argv("t", "body", None)[4])

    def test_linux_builder(self):
        self.assertEqual(platform._linux_notify_argv("t", "b", None), ["notify-send", "t", "b"])
        self.assertEqual(platform._linux_notify_argv("t", "b", "s"), ["notify-send", "t", "s\nb"])

    def test_dispatch_per_platform(self):
        with mock.patch("sys.platform", "darwin"):
            self.assertEqual(platform._notify_argv("t", "b", None)[0], "osascript")
        with mock.patch("sys.platform", "win32"):
            self.assertEqual(platform._notify_argv("t", "b", None)[0], "powershell")
        with mock.patch("sys.platform", "linux"):
            self.assertEqual(platform._notify_argv("t", "b", None)[0], "notify-send")
        with mock.patch("sys.platform", "freebsd13"):
            self.assertIsNone(platform._notify_argv("t", "b", None))

    def test_unsupported_os_never_calls_runner(self):
        runner = mock.Mock()
        with mock.patch("sys.platform", "freebsd13"):
            self.assertFalse(platform.notify_user("t", "b", runner=runner))
            self.assertEqual(platform.service_list_text(runner=runner), "")
        runner.assert_not_called()


class ServiceListArgvTestCase(unittest.TestCase):
    def test_argv_per_platform(self):
        with mock.patch("sys.platform", "darwin"):
            self.assertEqual(platform._service_list_argv(), ["launchctl", "list"])
        with mock.patch("sys.platform", "win32"):
            self.assertEqual(platform._service_list_argv()[:2], ["schtasks", "/query"])
        with mock.patch("sys.platform", "linux"):
            self.assertEqual(platform._service_list_argv()[:3], ["systemctl", "--user", "list-units"])
        with mock.patch("sys.platform", "sunos5"):
            self.assertIsNone(platform._service_list_argv())

    def test_combined_output_tolerates_none_streams(self):
        proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=None, stderr="e")
        self.assertEqual(platform._combined_output(proc), "e")
        proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="o", stderr=None)
        self.assertEqual(platform._combined_output(proc), "o")


if __name__ == "__main__":
    unittest.main()
