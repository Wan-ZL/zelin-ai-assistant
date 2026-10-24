"""uninstall.sh safety contract (CONTRACT §78 uninstall list; apps-dir seam).

The uninstaller is the most dangerous script after install.sh, so the tests
pin its safety properties rather than its cosmetics: (a) --dry-run changes
NOTHING while listing every planned action, (b) without a TTY and without
--yes it refuses to act, (c) unknown flags abort, (d) a real `--yes` run removes
exactly the §78 list — both agent prefixes, our cron lines (the owner's own
lines stay), the logs / caches / bin / claude-config dirs — and leaves the
recording engine alone unless `--stop-engine` is passed (the engineHandoff key
is written first so the quitting shell leaves it too), and prints the
tccutil / login item / transcript commands it never runs. The script is copied
into a sandbox dir and run with HOME redirected there and every system tool
it could reach (launchctl, crontab, pkill, pgrep, defaults) faked on PATH, so
the real machine's LaunchAgents/crontab/engine are never at risk.
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

    def _run(self, *args, stdin=subprocess.DEVNULL, env_extra=None):
        env = dict(os.environ, HOME=str(self.home))
        env.update(env_extra or {})
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

    def test_apps_dir_seam_keeps_the_real_applications_folder_out_of_the_plan(self):
        # AIASSISTANT_UI_APPS_DIR (the seam install.sh already honours) must steer the
        # bundle removal too — the QA coverage runner relies on it so a sandboxed
        # uninstall never plans against the owner's real /Applications bundle.
        apps = self.home / "Applications"
        bundle = apps / "Zelin's AI Assistant.app" / "Contents"
        bundle.mkdir(parents=True)
        (bundle / "Info.plist").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict><key>CFBundleIdentifier</key>'
            '<string>com.zelin.ai-board</string></dict></plist>\n', encoding="utf-8")
        proc = self._run("--dry-run", env_extra={"AIASSISTANT_UI_APPS_DIR": str(apps)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # safety property, OS-independent: no plan line removes anything under the real
        # /Applications (the seam steers every app path into the sandbox dir instead).
        self.assertNotIn("remove: /Applications/", proc.stdout)
        # the seam path is what the plan reasons about — "would remove:" on macOS (plutil
        # reads the bundle id), "left alone (unreadable)" on Linux CI (no plutil); either
        # way the seam bundle path appears, and the real /Applications never does.
        self.assertIn(str(apps / "Zelin's AI Assistant.app"), proc.stdout)
        if shutil.which("plutil"):
            self.assertIn("remove: %s" % (apps / "Zelin's AI Assistant.app"), proc.stdout)
        self.assertTrue((bundle / "Info.plist").exists())   # dry-run changed nothing

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


FAKE_TOOL = '#!/bin/bash\necho "$(basename "$0") $*" >> "$CALLS"\n'
FAKE_CRONTAB = ('#!/bin/bash\necho "crontab $*" >> "$CALLS"\n'
                'case "${1:-}" in -l) cat "$CRON" 2>/dev/null ;; -) cat > "$CRON" ;; -r) rm -f "$CRON" ;; esac\n')
OWNER_LINE = "0 9 * * * /Users/x/Projects/podcast/update.sh >> /Users/x/update.log 2>&1"


class UninstallRealRunTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="uninstall-real-")
        self.home = Path(self.tmp.name)
        shutil.copy(SCRIPT, self.home / "uninstall.sh")
        (self.home / "state").mkdir()
        self.la = self.home / "Library" / "LaunchAgents"
        self.la.mkdir(parents=True)
        for label in ("com.zelin.aiassistant.server", "com.zelin.aiassistant.actd",
                      "com.zelin.screenpipe-prune", "com.vendor.keep"):
            (self.la / (label + ".plist")).write_text("<plist/>", encoding="utf-8")
        self.support = self.home / "Library" / "Application Support" / "ZelinAIAssistant"
        for d in ("bin", "claude-config/cwd"):
            (self.support / d).mkdir(parents=True)
        (self.support / "home.txt").write_text(str(self.home) + "\n", encoding="utf-8")
        (self.support / "deploy_state.json").write_text("{}", encoding="utf-8")
        (self.home / "Library" / "Logs" / "zelin-ai-assistant").mkdir(parents=True)
        self.bin = self.home / "fakebin"
        self.bin.mkdir()
        for name in ("launchctl", "pkill", "pgrep", "defaults", "osascript"):
            (self.bin / name).write_text(FAKE_TOOL, encoding="utf-8")
            (self.bin / name).chmod(0o755)
        (self.bin / "crontab").write_text(FAKE_CRONTAB, encoding="utf-8")
        (self.bin / "crontab").chmod(0o755)
        self.calls = self.home / "calls.log"
        self.cron = self.home / "crontab.txt"
        self.cron.write_text(
            "*/30 * * * * cd %s && ./ingest/screenpipe-export.sh >> %s/state/ingest.cron.log 2>&1\n%s\n"
            % (self.home, self.home, OWNER_LINE), encoding="utf-8")
        self.tmpfiles = self.home / "tmpfiles"
        self.tmpfiles.mkdir()
        (self.tmpfiles / "export.lock").write_text("999999999\n", encoding="utf-8")   # a dead pid

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args):
        env = {"HOME": str(self.home), "PATH": "%s:/usr/bin:/bin" % self.bin,
               "CALLS": str(self.calls), "CRON": str(self.cron),
               "AIASSISTANT_UI_APPS_DIR": str(self.home / "Applications"),
               "PROCESS_SCREENPIPE_LOCK": str(self.tmpfiles / "process.lock"),
               "ZAI_SCREENPIPE_EXPORT_LOCK": str(self.tmpfiles / "export.lock"),
               "PROCESS_SCREENPIPE_LOG": str(self.tmpfiles / "auto.log")}
        proc = subprocess.run(["bash", str(self.home / "uninstall.sh"), "--yes", *args],
                              cwd=self.home, env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout, self.calls.read_text(encoding="utf-8")

    def test_removes_the_footprint_and_leaves_the_engine_recording(self):
        out, calls = self._run()
        self.assertEqual(sorted(p.name for p in self.la.iterdir()), ["com.vendor.keep.plist"])
        self.assertEqual(self.cron.read_text(encoding="utf-8").strip(), OWNER_LINE)
        for gone in ("bin", "claude-config", "deploy_state.json"):
            self.assertFalse((self.support / gone).exists(), gone)
        self.assertTrue((self.support / "home.txt").exists(), "only --purge removes the pointer dir")
        self.assertFalse((self.home / "Library" / "Logs" / "zelin-ai-assistant").exists())
        self.assertFalse((self.tmpfiles / "export.lock").exists(), "a dead pid's lock goes")
        self.assertTrue((self.home / "state").is_dir())
        self.assertRegex(calls, r"defaults write com\.zelin\.ai-board engineHandoff -string ")
        self.assertLess(calls.index("defaults write"), calls.index("pkill -TERM -x ZelinAIBoard"))
        self.assertNotRegex(calls, r"pkill -f screenpipe", "the engine keeps recording by default")
        self.assertIn("tccutil reset All com.zelin.ai-board", out)
        self.assertIn("Login Items", out)
        self.assertIn("rm -rf ~/.claude/projects/", out)

    def test_stop_engine_stops_it_without_the_handoff(self):
        _, calls = self._run("--stop-engine")
        self.assertRegex(calls, r"pkill -f screenpipe\.\*\[r\]ecord")
        self.assertNotIn("defaults write", calls)

    def test_a_live_lock_is_kept(self):
        live = self.tmpfiles / "process.lock"
        live.write_text("%d\n" % os.getpid(), encoding="utf-8")
        out, _ = self._run()
        self.assertTrue(live.exists())
        self.assertIn("in use by pid %d" % os.getpid(), out)

    def test_a_two_line_live_lock_is_kept(self):
        # process-screenpipe.sh writes $$, then appends its headless-claude child for
        # the whole claude phase; the two lines must not concatenate into one dead pid.
        live = self.tmpfiles / "process.lock"
        live.write_text("%d\n%d\n" % (os.getppid(), os.getpid()), encoding="utf-8")
        out, _ = self._run()
        self.assertTrue(live.exists())
        self.assertIn("in use by pid %d" % os.getppid(), out)

    def test_a_lock_whose_parent_died_but_claude_lives_is_kept(self):
        # A killed parent leaves the orphaned claude running; its pid on line 2 keeps the lock.
        live = self.tmpfiles / "process.lock"
        live.write_text("999999999\n%d\n" % os.getpid(), encoding="utf-8")
        out, _ = self._run()
        self.assertTrue(live.exists())
        self.assertIn("in use by pid %d" % os.getpid(), out)

    def test_a_two_line_lock_with_every_pid_dead_goes(self):
        dead = self.tmpfiles / "process.lock"
        dead.write_text("999999999\n999999998\n", encoding="utf-8")
        self._run()
        self.assertFalse(dead.exists())


if __name__ == "__main__":
    unittest.main()
