"""install.sh flags and the scheduler-less run (CONTRACT §69 / §23, v2.0.0).

The CI acceptance run and scripts/bootstrap.sh dry runs pass
`--non-interactive --no-launchd`: no LaunchAgent is loaded or booted out, the
crontab is neither read nor written. The 1.x one-pass daemon proof
(`actd --once`, step `actd_once`) retired with actd (full text at tag full).
Pinned here by sourcing the real install.sh (main runs only when executed):

  - flags combine; an unknown flag and the retired `--reinstall-agent` are a
    usage error (exit 2) before any side effect; `--pkg-postinstall` refuses
    with exit 3 (§74 v2.0.0 追记: no .pkg ships);
  - `install_server_agent` under --no-launchd records `launchd=skipped` with
    the literal `--no-launchd` at the start of the detail (the marker
    act/lib/fresh_install.py reads) and never calls launchctl;
  - `--from-upgrade` implies `--non-interactive` (§79);
  - `failed_deploy_steps` counts every `fail` line and nothing else.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from act.lib import fresh_install

REPO = Path(__file__).resolve().parents[1]
_WIN = sys.platform.startswith("win")


@unittest.skipIf(_WIN, "install.sh is POSIX-only")
class FlagParsingTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-flags-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _run(self, *flags):
        return subprocess.run(["bash", str(REPO / "install.sh"), *flags],
                              capture_output=True, text=True, timeout=60,
                              env={"HOME": str(self.tmp), "PATH": "/usr/bin:/bin"})

    def test_unknown_flag_is_a_usage_error_before_any_side_effect(self):
        proc = self._run("--non-interactive", "--bogus")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("unknown flag '--bogus'", proc.stderr)
        for flag in ("--non-interactive", "--no-launchd", "--from-upgrade", "--check"):
            self.assertIn(flag, proc.stderr)
        self.assertEqual(proc.stdout, "", "nothing ran before the usage error")
        self.assertEqual(list(self.tmp.iterdir()), [], "no file was written under $HOME")

    def test_retired_flags_say_so(self):
        proc = self._run("--reinstall-agent", "com.zelin.aiassistant.server")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("retired in v2.0.0", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_pkg_postinstall_always_refuses_with_exit_3(self):
        """§74 v2.0.0 追记: no .pkg ships; the flag refuses before any side effect."""
        proc = self._run("--pkg-postinstall")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("CONTRACT §74", proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(list(self.tmp.iterdir()), [], "no file under $HOME, no §23 report")


@unittest.skipIf(_WIN, "install.sh is POSIX-only")
class NoLaunchdTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-nolaunchd-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.calls = self.tmp / "calls.log"
        for name in ("launchctl", "crontab"):
            p = self.bin / name
            p.write_text('#!/bin/bash\necho "%s $*" >> "$CALLS"\n' % name, encoding="utf-8")
            p.chmod(0o755)

    def _sourced(self, body):
        script = 'source "$INSTALL_SH"\nREPO_ROOT="$TMPREPO"\n' + body
        proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
                              env={"PATH": "%s:/usr/bin:/bin" % self.bin, "HOME": str(self.tmp),
                                   "INSTALL_SH": str(REPO / "install.sh"),
                                   "TMPREPO": str(self.tmp / "repo"), "CALLS": str(self.calls)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_server_agent_is_skipped_with_the_doctor_marker(self):
        out = self._sourced('NO_LAUNCHD=1\nNON_INTERACTIVE=1\ninstall_server_agent\n'
                            'printf "@@%s" "$REPORT_STEPS"\n')
        line = out.split("@@", 1)[1].strip()
        self.assertEqual(line, "launchd=skipped:--no-launchd: no agent loaded")
        self.assertTrue(line.split(":", 1)[1].startswith(fresh_install.NO_LAUNCHD_MARKER))
        self.assertFalse(self.calls.exists(), "no launchctl / crontab call under --no-launchd")

    def test_failed_deploy_steps_counts_fail_lines_only(self):
        out = self._sourced(
            'report_step config ok\nreport_step cron skipped_tcc "TCC"\n'
            'report_step migrate fail "still loaded: x"\nreport_step ui skipped\n'
            'report_step anthropic_key warn "empty"\nfailed_deploy_steps\n')
        self.assertEqual(out.splitlines(), ["migrate=fail:still loaded: x"])

    def test_from_upgrade_implies_non_interactive(self):
        text = (REPO / "install.sh").read_text(encoding="utf-8")
        self.assertIn("--from-upgrade) FROM_UPGRADE=1; NON_INTERACTIVE=1 ;;", text)
        self.assertIn("[ \"$FROM_UPGRADE\" -eq 1 ] && _mode=upgrade", text)


if __name__ == "__main__":
    unittest.main()
