"""scripts/upgrade.sh — the lite upgrader (CONTRACT §79), real bash + real git.

A fixture origin (bare repo) carries main tagged v2.0.0, then v2.0.1; the
"installed" checkout is a clone of it whose install.sh is a stub that logs its
argv. pgrep / pkill / defaults / open are fakes on PATH, so no app is quit or
opened and the screenpipe engine is never reachable. Pinned:

  - success = `git fetch --tags`, fast-forward to the tagged origin/main,
    `bash install.sh --non-interactive --from-upgrade`, receipt status=ok with
    from / target; a running shell gets the engineHandoff key BEFORE its SIGTERM
    and is reopened with `open -b com.zelin.ai-board`; state/ is untouched by the
    checkout;
  - already at the tag = up_to_date, install.sh not run;
  - origin/main that is not exactly a v* tag = refused (exit 2), nothing moves;
  - local commits (diverged), local edits (dirty) or another branch = status
    diverged (exit 4) with the manual commands; the script never resets, stashes
    or rewrites: the commit and the edit are still there afterwards;
  - an untracked file the release would overwrite (`--untracked-files=no` lets it
    past the dirty check) = `git merge --ff-only` fails = status diverged / reason
    ff_failed (exit 4): HEAD and the file stay as they were;
  - `git fetch` failing (offline, origin gone) = status failed / reason fetch
    (exit 1), install.sh not run;
  - install.sh failing = status failed with install_rc (exit 1), no relaunch, no
    rollback;
  - a second upgrade while one holds the lock = exit 3, the receipt untouched.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_WIN = sys.platform.startswith("win")

GIT = ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
       "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", "-c", "init.defaultBranch=main"]
STUB_INSTALL = ('#!/bin/bash\necho "install.sh $*" >> "$CALLS"\n'
                'exit "${FAKE_INSTALL_RC:-0}"\n')
FAKE = '#!/bin/bash\necho "$(basename "$0") $*" >> "$CALLS"\n'
FAKE_PGREP = ('#!/bin/bash\necho "pgrep $*" >> "$CALLS"\n[ -f "$RUNNING_FLAG" ]\n')
FAKE_PKILL = ('#!/bin/bash\necho "pkill $*" >> "$CALLS"\nrm -f "$RUNNING_FLAG"\n')


@unittest.skipIf(_WIN or shutil.which("git") is None, "needs bash + git")
class UpgradeScriptTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zai-upgrade-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = {"PATH": "%s:/usr/bin:/bin" % (self.tmp / "fakebin"),
                    "HOME": str(self.tmp / "home"), "GIT_CONFIG_NOSYSTEM": "1",
                    "CALLS": str(self.tmp / "calls.log"),
                    "RUNNING_FLAG": str(self.tmp / "shell.running")}
        (self.tmp / "home").mkdir()
        fakebin = self.tmp / "fakebin"
        fakebin.mkdir()
        for name, text in (("pgrep", FAKE_PGREP), ("pkill", FAKE_PKILL),
                           ("defaults", FAKE), ("open", FAKE)):
            (fakebin / name).write_text(text, encoding="utf-8")
            (fakebin / name).chmod(0o755)
        self.origin = self.tmp / "origin.git"
        self.seed = self.tmp / "seed"
        self.local = self.tmp / "local"
        self._git(self.tmp, "init", "-q", "--bare", str(self.origin))
        self._git(self.tmp, "init", "-q", str(self.seed))
        (self.seed / "scripts").mkdir()
        shutil.copy(REPO / "scripts" / "upgrade.sh", self.seed / "scripts" / "upgrade.sh")
        (self.seed / "install.sh").write_text(STUB_INSTALL, encoding="utf-8")
        (self.seed / ".gitignore").write_text("state/\n", encoding="utf-8")
        (self.seed / "VERSION").write_text("2.0.0\n", encoding="utf-8")
        self._commit_and_tag("v2.0.0")
        self._git(self.seed, "remote", "add", "origin", str(self.origin))
        self._git(self.seed, "push", "-q", "origin", "main", "--tags")
        self._git(self.tmp, "clone", "-q", str(self.origin), str(self.local))
        (self.local / "state").mkdir()
        (self.local / "state" / "keep.txt").write_text("recaps live here\n", encoding="utf-8")
        self.env["AIASSISTANT_HOME"] = str(self.local)

    # -- helpers ------------------------------------------------------------ #
    def _git(self, cwd, *args):
        proc = subprocess.run(GIT + list(args), cwd=cwd, capture_output=True, text=True,
                              timeout=60, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def _commit_and_tag(self, tag=None, text=None):
        if text:
            (self.seed / "VERSION").write_text(text, encoding="utf-8")
        self._git(self.seed, "add", "-A")
        self._git(self.seed, "commit", "-q", "-m", "release %s" % (tag or "untagged"))
        if tag:
            self._git(self.seed, "tag", "-a", tag, "-m", tag)

    def _release(self, tag="v2.0.1"):
        self._commit_and_tag(tag, text=tag + "\n")
        self._git(self.seed, "push", "-q", "origin", "main", "--tags")

    def _upgrade(self, extra_env=None):
        env = dict(self.env, **(extra_env or {}))
        return subprocess.run(["bash", str(self.local / "scripts" / "upgrade.sh")],
                              cwd=self.local, capture_output=True, text=True, timeout=120,
                              env=env, stdin=subprocess.DEVNULL)

    def _state(self):
        return json.loads((self.local / "state" / "upgrade_state.json").read_text(encoding="utf-8"))

    def _calls(self):
        p = Path(self.env["CALLS"])
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    def _head(self):
        return self._git(self.local, "rev-parse", "HEAD")

    # -- success ------------------------------------------------------------ #
    def test_fast_forwards_to_the_tag_installs_and_relaunches_a_running_shell(self):
        before = self._git(self.local, "rev-parse", "--short", "HEAD")
        self._release()
        Path(self.env["RUNNING_FLAG"]).write_text("", encoding="utf-8")
        proc = self._upgrade()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._git(self.local, "describe", "--tags", "--exact-match"), "v2.0.1")
        self.assertEqual(self._git(self.local, "symbolic-ref", "--short", "HEAD"), "main")
        state = self._state()
        self.assertEqual((state["status"], state["from"], state["target"], state["install_rc"],
                          state["relaunched"]), ("ok", before, "v2.0.1", "0", "yes"))
        calls = self._calls()
        self.assertIn("install.sh --non-interactive --from-upgrade", calls)
        handoff = next(i for i, c in enumerate(calls)
                       if c.startswith("defaults write com.zelin.ai-board engineHandoff -string "))
        term = calls.index("pkill -TERM -x ZelinAIBoard")
        opened = calls.index("open -b com.zelin.ai-board")
        self.assertLess(calls.index("install.sh --non-interactive --from-upgrade"), handoff)
        self.assertLess(handoff, term)
        self.assertLess(term, opened)
        self.assertFalse(any("screenpipe" in c for c in calls), "the engine is never signalled")
        self.assertEqual((self.local / "state" / "keep.txt").read_text(encoding="utf-8"),
                         "recaps live here\n")
        self.assertFalse((self.local / "state" / "upgrade.lock").exists())

    def test_no_running_shell_means_no_relaunch(self):
        self._release()
        proc = self._upgrade()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._state()["relaunched"], "no")
        self.assertFalse(any(c.startswith(("open", "defaults", "pkill")) for c in self._calls()))

    def test_already_at_the_tag_is_up_to_date(self):
        proc = self._upgrade()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._state()["status"], "up_to_date")
        self.assertEqual(self._state()["target"], "v2.0.0")
        self.assertEqual(self._calls(), [])

    # -- refusals ----------------------------------------------------------- #
    def test_untagged_origin_main_is_refused(self):
        head = self._head()
        self._commit_and_tag(None, text="work in progress\n")
        self._git(self.seed, "push", "-q", "origin", "main")
        proc = self._upgrade()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual((self._state()["status"], self._state()["reason"]), ("refused", "untagged"))
        self.assertEqual(self._head(), head)
        self.assertEqual(self._calls(), [])

    def test_diverged_main_writes_the_state_file_and_never_resets(self):
        self._release()
        (self.local / "LOCAL.md").write_text("a local ledger commit\n", encoding="utf-8")
        self._git(self.local, "add", "LOCAL.md")
        self._git(self.local, "commit", "-q", "-m", "chore: local ledger")
        local_head = self._head()
        proc = self._upgrade()
        self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
        state = self._state()
        self.assertEqual((state["status"], state["reason"], state["target"]),
                         ("diverged", "diverged", "v2.0.1"))
        self.assertTrue(any("reset --hard v2.0.1" in c for c in state["commands"]), state)
        self.assertTrue(any("branch keep/local-" in c for c in state["commands"]), state)
        self.assertEqual(self._head(), local_head, "never reset")
        self.assertTrue((self.local / "LOCAL.md").exists())
        self.assertNotIn("install.sh --non-interactive --from-upgrade", self._calls())

    def test_dirty_tree_is_refused_and_the_edit_survives(self):
        self._release()
        (self.local / "VERSION").write_text("my local edit\n", encoding="utf-8")
        head = self._head()
        proc = self._upgrade()
        self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
        state = self._state()
        self.assertEqual((state["status"], state["reason"]), ("diverged", "dirty"))
        self.assertTrue(any("stash" in c for c in state["commands"]), state)
        self.assertEqual((self.local / "VERSION").read_text(encoding="utf-8"), "my local edit\n")
        self.assertEqual(self._head(), head)
        self.assertEqual(self._git(self.local, "stash", "list"), "")

    def test_another_branch_is_refused(self):
        self._release()
        self._git(self.local, "checkout", "-q", "-b", "experiment")
        proc = self._upgrade()
        self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
        self.assertEqual(self._state()["reason"], "not_on_main")
        self.assertEqual(self._git(self.local, "symbolic-ref", "--short", "HEAD"), "experiment")

    def test_an_untracked_collision_is_ff_failed_and_nothing_moves(self):
        (self.seed / "NEWFILE.txt").write_text("from the release\n", encoding="utf-8")
        self._release()
        (self.local / "NEWFILE.txt").write_text("mine, untracked\n", encoding="utf-8")
        head = self._head()
        proc = self._upgrade()
        self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
        state = self._state()
        self.assertEqual((state["status"], state["reason"], state["target"]),
                         ("diverged", "ff_failed", "v2.0.1"))
        self.assertTrue(state["commands"], state)
        self.assertEqual(self._head(), head, "nothing moves")
        self.assertEqual((self.local / "NEWFILE.txt").read_text(encoding="utf-8"),
                         "mine, untracked\n")
        self.assertNotIn("install.sh --non-interactive --from-upgrade", self._calls())
        self.assertFalse((self.local / "state" / "upgrade.lock").exists())

    # -- failures ----------------------------------------------------------- #
    def test_fetch_failure_is_a_failed_receipt(self):
        self._release()
        head = self._head()
        self._git(self.local, "remote", "set-url", "origin", str(self.tmp / "no-such-origin.git"))
        proc = self._upgrade()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        state = self._state()
        self.assertEqual((state["status"], state["reason"]), ("failed", "fetch"))
        self.assertEqual(self._head(), head)
        self.assertEqual(self._calls(), [], "no install.sh, no relaunch")
        self.assertFalse((self.local / "state" / "upgrade.lock").exists())

    def test_install_failure_is_a_receipt_without_relaunch_or_rollback(self):
        self._release()
        Path(self.env["RUNNING_FLAG"]).write_text("", encoding="utf-8")
        proc = self._upgrade({"FAKE_INSTALL_RC": "2"})
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        state = self._state()
        self.assertEqual((state["status"], state["reason"], state["install_rc"]), ("failed", "install", "2"))
        self.assertEqual(self._git(self.local, "describe", "--tags", "--exact-match"), "v2.0.1",
                         "no rollback: the code stays on the tag")
        self.assertFalse(any(c.startswith(("open", "pkill", "defaults")) for c in self._calls()))

    def test_a_stale_lock_is_cleared(self):
        lock = self.local / "state" / "upgrade.lock"
        lock.mkdir()
        old = 1_000_000_000
        os.utime(lock, (old, old))
        proc = self._upgrade()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("clearing a stale lock", proc.stdout)
        self.assertEqual(self._state()["status"], "up_to_date")
        self.assertFalse(lock.exists())

    def test_the_stale_cutoff_matches_the_server(self):
        from server import about
        text = (REPO / "scripts" / "upgrade.sh").read_text(encoding="utf-8")
        self.assertIn("LOCK_STALE_MIN=%d " % (about.UPGRADE_LOCK_STALE_S // 60), text)

    def test_a_held_lock_is_exit_3_and_leaves_the_receipt(self):
        (self.local / "state" / "upgrade.lock").mkdir()
        (self.local / "state" / "upgrade_state.json").write_text('{"status": "running"}\n', encoding="utf-8")
        proc = self._upgrade()
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual(self._state(), {"status": "running"})
        self.assertTrue((self.local / "state" / "upgrade.lock").is_dir())


if __name__ == "__main__":
    unittest.main()
