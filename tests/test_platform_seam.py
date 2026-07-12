"""act/lib/platform.py — the Linux service-manager + notification seam.

Companion to tests/test_platform.py; this file focuses on the parts a Linux
port hangs off (docs/LINUX.md):

  * service_list_text() on Linux shells `systemctl --user list-units` (the text
    act.doctor's systemd branch parses) — argv shape + robustness pinned here;
  * notify_user() on Linux builds the exact `notify-send` argv, escaping-free,
    and never raises when the binary is missing (headless boxes).

All via an injected fake runner — nothing is ever actually spawned, so this is
green on the macOS / ubuntu / windows CI runners alike; the REAL loading of
units and REAL notify-send firing are friend-tested on a Linux box (see
docs/LINUX.md "needs a real Linux machine").
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


if __name__ == "__main__":
    unittest.main()
