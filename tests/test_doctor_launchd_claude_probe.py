"""§55 第三幕 launchd claude 探针（act/lib/checks/launchd.claude_probe）的判例。

真实探针会 bootstrap 一个一次性 launchd job；套件里 AIASSISTANT_LAUNCHD_PROBE=0
把它关死（tests/__init__.py），所以这个函数此前零覆盖（P3a 审计 CRAP 248）。
这里用假 ``subprocess.run`` 演 launchd：bootstrap 时按 plist 里的 verdict 路径
写下 sh 探针会写的文件，覆盖五种结局（ok / failed+blind / cd_failed / hang /
launchd 拒绝）、坏 verdict、bootout 失败被吞、探针错误、以及探针关闭 /
非 darwin / 无 launchctl 的 unavailable。绝不真起 launchd。
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401

from act.lib.checks import launchd

# the probe builds gui/<uid> domains and POSIX paths — a launchd construct; the
# Windows leg exercises only the platform gate (ProbeGatesTestCase.test_not_darwin)
_POSIX = os.name == "posix"


class _FakeLaunchd:
    """subprocess.run stand-in: records argv; on bootstrap, plays the job."""

    def __init__(self, *, verdict: str = "rc:0", out: str = "2.1.206",
                 stage: bool = True, write_verdict: bool = True,
                 refuse: bool = False, bootout_error: bool = False):
        self.calls = []
        self.verdict = verdict
        self.out = out
        self.stage = stage
        self.write_verdict = write_verdict
        self.refuse = refuse
        self.bootout_error = bootout_error

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[1] == "bootout":
            if self.bootout_error:
                raise OSError("bootout exploded")
            return subprocess.CompletedProcess(argv, 3, stdout="", stderr="not loaded")
        assert argv[1] == "bootstrap", argv
        if self.refuse:
            return subprocess.CompletedProcess(argv, 5, stdout="", stderr="Bootstrap failed: 5: Input/output error")
        with open(argv[3], "rb") as fh:
            plist = plistlib.load(fh)
        verdict = Path(plist["ProgramArguments"][-1])
        if self.stage:
            (verdict.parent / "verdict.stage").write_text("started", encoding="utf-8")
        if self.write_verdict:
            (verdict.parent / "verdict.out").write_text(self.out, encoding="utf-8")
            verdict.write_text(self.verdict + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


@unittest.skipUnless(_POSIX, "launchd probe is a POSIX/darwin construct")
class ClaudeProbeTestCase(unittest.TestCase):
    def _probe(self, fake: _FakeLaunchd, budget_s: float = 0.6) -> dict:
        with mock.patch.dict(os.environ, {"AIASSISTANT_LAUNCHD_PROBE": "1"}), \
                mock.patch("sys.platform", "darwin"), \
                mock.patch.object(launchd.shutil, "which", return_value="/bin/launchctl"), \
                mock.patch.object(subprocess, "run", fake):
            return launchd.claude_probe("/fake/claude", "/tmp/repo", budget_s=budget_s)

    def test_ok_exit(self):
        fake = _FakeLaunchd(verdict="rc:0", out="2.1.206\n")
        res = self._probe(fake)
        self.assertEqual(res, {"state": "ok", "rc": 0, "text": "2.1.206"})
        # bootout before bootstrap, and again in the finally
        self.assertEqual([c[1] for c in fake.calls], ["bootout", "bootstrap", "bootout"])
        self.assertTrue(fake.calls[1][2].startswith("gui/"))

    def test_failed_exit_carries_bun_text(self):
        res = self._probe(_FakeLaunchd(verdict="rc:1",
                                       out="error: possibly due to low max file descriptors"))
        self.assertEqual(res["state"], "failed")
        self.assertEqual(res["rc"], 1)
        self.assertIn("file descriptors", res["text"])

    def test_cd_failed(self):
        res = self._probe(_FakeLaunchd(verdict="cd_failed:1", stage=False))
        self.assertEqual(res["state"], "cd_failed")
        self.assertIn("/tmp/repo", res["text"])

    def test_hang_when_started_but_no_exit(self):
        res = self._probe(_FakeLaunchd(write_verdict=False, stage=True), budget_s=0.3)
        self.assertEqual(res["state"], "hang")
        self.assertIn("no exit", res["text"])

    def test_nothing_observable_is_unavailable(self):
        res = self._probe(_FakeLaunchd(write_verdict=False, stage=False), budget_s=0.3)
        self.assertEqual(res["state"], "unavailable")
        self.assertIn("nothing observable", res["text"])

    def test_launchd_refusal_is_unavailable_with_stderr(self):
        res = self._probe(_FakeLaunchd(refuse=True))
        self.assertEqual(res["state"], "unavailable")
        self.assertIn("refused", res["text"])
        self.assertIn("Input/output error", res["text"])

    def test_unreadable_verdict_is_unavailable(self):
        res = self._probe(_FakeLaunchd(verdict="garbage"))
        self.assertEqual(res["state"], "unavailable")
        self.assertIn("unreadable verdict", res["text"])

    def test_bootout_failure_in_cleanup_is_swallowed(self):
        fake = _FakeLaunchd(verdict="rc:0", bootout_error=True)
        # the pre-bootstrap bootout raises → caught as a probe error, cleanup quiet
        res = self._probe(fake)
        self.assertEqual(res["state"], "unavailable")
        self.assertIn("probe error", res["text"])

    def test_temp_dir_is_removed(self):
        made = []
        real_mkdtemp = launchd.tempfile.mkdtemp

        def spy(*a, **kw):
            d = real_mkdtemp(*a, **kw)
            made.append(d)
            return d
        with mock.patch.object(launchd.tempfile, "mkdtemp", spy):
            self._probe(_FakeLaunchd())
        self.assertEqual(len(made), 1)
        self.assertFalse(os.path.exists(made[0]))


class ProbeGatesTestCase(unittest.TestCase):
    def test_switched_off(self):
        with mock.patch.dict(os.environ, {"AIASSISTANT_LAUNCHD_PROBE": "0"}), \
                mock.patch("sys.platform", "darwin"), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("no spawn")):
            self.assertEqual(launchd.claude_probe("/c", "/d")["state"], "unavailable")

    def test_not_darwin(self):
        with mock.patch.dict(os.environ, {"AIASSISTANT_LAUNCHD_PROBE": "1"}), \
                mock.patch("sys.platform", "linux"):
            self.assertIn("no launchd", launchd.claude_probe("/c", "/d")["text"])

    def test_no_launchctl_binary(self):
        with mock.patch.dict(os.environ, {"AIASSISTANT_LAUNCHD_PROBE": "1"}), \
                mock.patch("sys.platform", "darwin"), \
                mock.patch.object(launchd.shutil, "which", return_value=None):
            self.assertEqual(launchd.claude_probe("/c", "/d")["text"], "launchctl not found")


class ProbeHelpersTestCase(unittest.TestCase):
    def test_parse_rc(self):
        self.assertEqual(launchd._parse_rc("rc:7"), 7)
        self.assertIsNone(launchd._parse_rc("rc"))
        self.assertIsNone(launchd._parse_rc("rc:x"))

    def test_read_optional_missing(self):
        self.assertEqual(launchd._read_optional(Path("/nonexistent/zai/out")), "")

    @unittest.skipUnless(_POSIX, "POSIX path shapes")
    def test_probe_plist_shape(self):
        d = launchd._probe_plist("lbl", "/cwd", "/claude", Path("/v"))
        self.assertEqual(d["Label"], "lbl")
        self.assertEqual(d["ProgramArguments"][-3:], ["/cwd", "/claude", "/v"])
        self.assertTrue(d["RunAtLoad"])
        self.assertFalse(d["AbandonProcessGroup"])

    def test_bootout_quietly_swallows(self):
        with mock.patch.object(subprocess, "run", side_effect=OSError("x")):
            launchd._bootout_quietly("gui/1", "lbl")

    def test_first_line_and_real_bin(self):
        self.assertEqual(launchd._first_line({"text": " a\nb "}), ("a\nb", "a"))
        self.assertEqual(launchd._first_line({}), ("", ""))
        self.assertEqual(launchd._real_bin("/bin/sh"), str(Path("/bin/sh").resolve()))


if __name__ == "__main__":
    unittest.main()
