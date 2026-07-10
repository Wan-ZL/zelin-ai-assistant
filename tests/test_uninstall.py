"""uninstall.sh safety contract.

The uninstaller is the most dangerous script after install.sh, so the tests
pin its safety properties rather than its cosmetics: (a) --dry-run changes
NOTHING while listing every planned action, (b) without a TTY and without
--yes it refuses to act, (c) unknown flags abort. The script is copied into
a sandbox dir and run with HOME redirected there, so the real machine's
LaunchAgents/app/state are never at risk even if a regression slips in.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "uninstall.sh"


class UninstallDryRunTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="uninstall-home-")
        self.home = Path(self.tmp.name)
        # sandbox "repo": the script treats its own directory as REPO_ROOT
        shutil.copy(SCRIPT, self.home / "uninstall.sh")
        (self.home / "state").mkdir()
        self.sentinel = self.home / "state" / "sentinel.json"
        self.sentinel.write_text("{}", encoding="utf-8")
        # fake installed launchd agent under the sandbox HOME
        la_dir = self.home / "Library" / "LaunchAgents"
        la_dir.mkdir(parents=True)
        self.plist = la_dir / "com.zelin.aiassistant.actd.plist"
        self.plist.write_text("<plist/>", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args, stdin=subprocess.DEVNULL):
        env = dict(os.environ, HOME=str(self.home))
        return subprocess.run(
            ["bash", str(self.home / "uninstall.sh"), *args],
            cwd=self.home, env=env, stdin=stdin,
            capture_output=True, text=True, timeout=60,
        )

    def test_dry_run_lists_plan_and_changes_nothing(self):
        proc = self._run("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout
        # every surface of the plan is announced ...
        self.assertIn("dry-run", out)
        self.assertIn("com.zelin.aiassistant.actd", out)
        self.assertIn("crontab", out)
        self.assertIn("Zelin's AI Assistant.app", out)
        self.assertIn("nothing was changed", out)
        # ... and nothing was actually touched
        self.assertTrue(self.sentinel.exists())
        self.assertTrue(self.plist.exists())

    def test_dry_run_keeps_user_data_out_of_the_plan_by_default(self):
        proc = self._run("--dry-run")
        # state/ (task history) must never appear as a default removal target
        self.assertNotIn("would remove: %s" % (self.home / "state"), proc.stdout)
        self.assertIn("Kept", proc.stdout)

    def test_purge_dry_run_plans_state_removal_but_still_changes_nothing(self):
        proc = self._run("--dry-run", "--purge")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(str(self.home / "state"), proc.stdout)
        self.assertTrue(self.sentinel.exists())
        # the vault promise is explicit even in purge mode
        self.assertIn("vault", proc.stdout.lower())

    def test_refuses_without_tty_and_without_yes(self):
        proc = self._run()  # stdin=/dev/null → no TTY
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--yes", proc.stderr)
        self.assertTrue(self.sentinel.exists())
        self.assertTrue(self.plist.exists())

    def test_unknown_flag_aborts(self):
        proc = self._run("--nuke-everything")
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(self.plist.exists())


if __name__ == "__main__":
    unittest.main()
