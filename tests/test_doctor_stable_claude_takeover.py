"""doctor `stable claude` row — the version-gap WARN says exactly what to do
(CONTRACT §55 第五幕 追记 2026-09-06; §25 `stable claude` row).

Between a Claude Code update and the next merge-to-main nothing runs install.sh,
so the stable daemon copy legitimately lags the login shell's claude (live
09-05T04:43Z → 06:59Z: 2.1.259 vs 2.1.261; 09-06T03:05Z → 09-07T04:38Z: 2.1.261
vs 2.1.263 — every deploy after an update DID refresh it, auto-deploy.log lines
`stable daemon claude: refreshed …`). In that window the worker and the board's
takeover command run the copy while a bare `claude --resume` typed in the shell
runs the newer client — the 09-04 "exit 143 / exit 1 before init" takeover. The
WARN therefore (a) names both versions and warns off the bare command, (b) asks
`lsof` how many processes run the copy (install.sh `stable_claude_in_use` asks
the same before swapping) and (c) prescribes ONE action: wait for / stop those
sessions then `bash install.sh`, or — with none — run it now. Pure row builder
(act/lib/claude_bin.py), fake `run`, no subprocess.
"""
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import claude_bin, failures

REPO = Path(__file__).resolve().parents[1]
SHELL = "/fake/bin/claude"


class _Run:
    """probes.run stand-in: versions per binary, an lsof answer, a call log."""

    def __init__(self, stable, stable_ver="2.1.259", shell_ver="2.1.261", lsof=(1, "")):
        self.stable, self.stable_ver, self.shell_ver, self.lsof = str(stable), stable_ver, shell_ver, lsof
        self.calls = []

    def __call__(self, cmd, env=None, timeout=None):
        self.calls.append(list(cmd))
        if "--version" in cmd:
            if cmd[0] == self.stable:
                return (0, "%s (Claude Code)" % self.stable_ver)
            return (0, "%s (Claude Code)" % self.shell_ver)
        if os.path.basename(cmd[0]) == "lsof":
            return self.lsof
        return (0, "")


class StableClaudeVersionGapTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="doctor-stable-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.stable = self.tmp / "Application Support" / "bin" / "claude"
        self.stable.parent.mkdir(parents=True)
        self.stable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.stable.chmod(0o755)
        lang = mock.patch.object(failures, "ui_lang", return_value="en")
        lang.start()
        self.addCleanup(lang.stop)

    def _row(self, run):
        return claude_bin.stable_claude_row(run, lambda: SHELL, lambda _n: SHELL, "install.sh",
                                            stable=self.stable)

    def test_gap_with_live_sessions_names_count_and_the_wait_then_install_action(self):
        run = _Run(self.stable, lsof=(0, "58493\n62253\n24910\n"))
        row = self._row(run)
        self.assertEqual(row["status"], "warn")
        self.assertEqual(row["failure_id"], "")
        self.assertIn("2.1.259", row["detail"])
        self.assertIn("2.1.261", row["detail"])
        self.assertIn("takeover command", row["detail"])
        self.assertIn("bare `claude --resume`", row["detail"], "warns off the shell's own claude")
        self.assertIn("3 process(es) run the copy", row["fix"])
        self.assertIn("will not swap the file under them", row["fix"])
        self.assertIn("bash install.sh", row["fix"])
        self.assertIn("same path", row["fix"])
        self.assertIn("board's command", row["fix"])

    def test_gap_with_no_live_session_prescribes_install_now(self):
        row = self._row(_Run(self.stable, lsof=(1, "")))
        self.assertEqual(row["status"], "warn")
        self.assertIn("nothing runs the copy right now", row["fix"])
        self.assertIn("bash install.sh", row["fix"])
        self.assertIn("same path", row["fix"])
        self.assertNotIn("process(es)", row["fix"])

    def test_lsof_is_asked_with_the_same_flags_install_sh_uses(self):
        run = _Run(self.stable, lsof=(0, "1\n"))
        self._row(run)
        lsof_calls = [c for c in run.calls if os.path.basename(c[0]) == "lsof"]
        self.assertEqual(len(lsof_calls), 1)
        self.assertEqual(lsof_calls[0][1:], ["-t", "-w", "--", str(self.stable)])
        sh = (REPO / "install.sh").read_text(encoding="utf-8")
        self.assertRegex(sh, re.escape('-t -w -- "$STABLE_CLAUDE_BIN"'),
                         "install.sh stable_claude_in_use asks lsof the same question")

    def test_same_version_is_ok_and_never_asks_lsof(self):
        run = _Run(self.stable, stable_ver="2.1.263", shell_ver="2.1.263", lsof=(0, "1\n"))
        row = self._row(run)
        self.assertEqual(row["status"], "ok")
        self.assertIn("same version", row["detail"])
        self.assertEqual([c for c in run.calls if os.path.basename(c[0]) == "lsof"], [])


class LiveUsersTestCase(unittest.TestCase):
    def test_counts_only_pid_lines(self):
        run = _Run("/s", lsof=(0, "58493\n\n  62253 \nnot-a-pid\n"))
        self.assertEqual(claude_bin.live_users(run, Path("/s")), 2)

    def test_no_match_or_failure_reads_as_zero(self):
        self.assertEqual(claude_bin.live_users(_Run("/s", lsof=(1, "")), Path("/s")), 0)
        self.assertEqual(claude_bin.live_users(_Run("/s", lsof=(2, "lsof: WARNING")), Path("/s")), 0)

    def test_missing_lsof_reads_as_zero_not_in_use(self):
        def run(cmd, env=None, timeout=None):
            raise OSError("no lsof")
        self.assertEqual(claude_bin.live_users(run, Path("/s")), 0)

    def test_falls_back_to_usr_sbin_when_lsof_is_not_on_path(self):
        run = _Run("/s", lsof=(1, ""))
        with mock.patch.object(claude_bin.shutil, "which", return_value=None):
            claude_bin.live_users(run, Path("/s"))
        self.assertEqual(run.calls[0][0], "/usr/sbin/lsof")


if __name__ == "__main__":
    unittest.main()
