"""executor.ensure_repo — best-effort repo bootstrap for NEW targets (§7).

Every step tolerates failure and stays local (a missing ``gh`` or a network
error must never block dispatch): the git probes, ``git init`` for a bare
directory, the empty initial commit, and the optional private GitHub origin
(only with ``create_github_repo`` + ``gh`` on PATH + no remote yet). Pinned
through a scripted ``subprocess.run`` — no real git, no real gh — plus the
three probes' failure answers and ``compute_target_kind``'s OSError guard.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import executor
from act.lib import config


def _proc(rc=0, stdout=""):
    return subprocess.CompletedProcess(["git"], rc, stdout=stdout, stderr="")


class _Git:
    """Scripted git: answers per subcommand, records every argv."""

    def __init__(self, is_repo=True, has_head=True, remotes="origin\n",
                 fail=()):
        self.is_repo, self.has_head, self.remotes = is_repo, has_head, remotes
        self.fail = set(fail)          # subcommands that raise OSError
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        sub = argv[1] if argv[0] == "git" else argv[0]
        if sub in self.fail or argv[0] in self.fail:
            raise OSError(f"{sub} unavailable")
        if argv[0] == "gh":
            return _proc(0)
        if argv[1:3] == ["rev-parse", "--is-inside-work-tree"]:
            return _proc(0 if self.is_repo else 128, "true\n" if self.is_repo else "")
        if argv[1:3] == ["rev-parse", "--verify"]:
            return _proc(0 if self.has_head else 128)
        if argv[1] == "remote":
            return _proc(0, self.remotes)
        if argv[1] == "init":
            self.is_repo = True
            return _proc(0)
        if argv[1] == "commit":
            self.has_head = True
            return _proc(0)
        raise AssertionError(f"unexpected git call {argv}")

    def subcommands(self):
        return [c[1] if c[0] == "git" else c[0] for c in self.calls]


class EnsureRepoTestCase(unittest.TestCase):
    def setUp(self):
        self.target = Path(tempfile.mkdtemp(prefix="ensure-repo-")) / "new"
        self.cfg = config.Config()
        self.cfg.create_github_repo = False

    def _run(self, git, which=None):
        with mock.patch.object(executor.subprocess, "run", git), \
                mock.patch.object(executor.shutil, "which", return_value=which):
            executor.ensure_repo(self.target, self.cfg)
        return git

    def test_fresh_directory_gets_init_and_initial_commit(self):
        git = self._run(_Git(is_repo=False, has_head=False))
        self.assertTrue(self.target.is_dir())
        self.assertEqual(git.subcommands(),
                         ["rev-parse", "init", "rev-parse", "commit"])
        self.assertIn("--allow-empty", git.calls[-1])

    def test_existing_repo_with_commits_is_left_alone(self):
        git = self._run(_Git())
        self.assertEqual(git.subcommands(), ["rev-parse", "rev-parse"])

    def test_mkdir_failure_aborts_silently(self):
        blocker = self.target.parent / "file"
        blocker.write_text("x", encoding="utf-8")
        git = _Git()
        with mock.patch.object(executor.subprocess, "run", git):
            executor.ensure_repo(blocker / "child", self.cfg)   # mkdir → NotADirectoryError
        self.assertEqual(git.calls, [])

    def test_git_init_spawn_failure_stops_the_bootstrap(self):
        git = self._run(_Git(is_repo=False, has_head=False, fail={"init"}))
        self.assertEqual(git.subcommands(), ["rev-parse", "init"])   # no commit attempt

    def test_commit_spawn_failure_is_tolerated(self):
        self.cfg.create_github_repo = True
        git = self._run(_Git(has_head=False, remotes="", fail={"commit"}), which="/usr/bin/gh")
        # the commit failed, the remote step still ran
        self.assertEqual(git.subcommands(), ["rev-parse", "rev-parse", "commit",
                                             "rev-parse", "remote", "gh"])

    def test_github_remote_only_when_configured_present_and_missing(self):
        self.cfg.create_github_repo = True
        git = self._run(_Git(remotes=""), which="/usr/bin/gh")
        self.assertEqual(git.subcommands()[-1], "gh")
        self.assertEqual(git.calls[-1][:4], ["gh", "repo", "create", "new"])
        self.assertIn("--private", git.calls[-1])
        # already has a remote → no gh
        git = self._run(_Git(remotes="origin\n"), which="/usr/bin/gh")
        self.assertNotIn("gh", git.subcommands())
        # gh not on PATH → no gh, and no remote probe either
        git = self._run(_Git(remotes=""), which=None)
        self.assertNotIn("gh", git.subcommands())
        self.assertNotIn("remote", git.subcommands())
        # switched off → nothing
        self.cfg.create_github_repo = False
        git = self._run(_Git(remotes=""), which="/usr/bin/gh")
        self.assertNotIn("gh", git.subcommands())

    def test_gh_failure_stays_local(self):
        self.cfg.create_github_repo = True
        git = self._run(_Git(remotes="", fail={"gh"}), which="/usr/bin/gh")
        self.assertEqual(git.subcommands()[-1], "gh")   # attempted, swallowed


class ProbesTestCase(unittest.TestCase):
    def setUp(self):
        self.target = Path(tempfile.mkdtemp(prefix="probes-"))

    def test_missing_directory_is_not_a_repo(self):
        with mock.patch.object(executor.subprocess, "run") as run:
            self.assertFalse(executor._has_git_repo(self.target / "nope"))
            self.assertFalse(executor.has_remote(self.target / "nope"))
        run.assert_not_called()

    def test_probe_spawn_failures_are_false(self):
        with mock.patch.object(executor.subprocess, "run", side_effect=OSError("no git")):
            self.assertFalse(executor._has_git_repo(self.target))
            self.assertFalse(executor._has_commits(self.target))
        git = _Git(fail={"remote"})
        with mock.patch.object(executor.subprocess, "run", git):
            self.assertFalse(executor.has_remote(self.target))

    def test_probe_answers(self):
        with mock.patch.object(executor.subprocess, "run", _Git(is_repo=True, has_head=True)):
            self.assertTrue(executor._has_git_repo(self.target))
            self.assertTrue(executor._has_commits(self.target))
            self.assertTrue(executor.has_remote(self.target))
        with mock.patch.object(executor.subprocess, "run",
                               _Git(is_repo=False, has_head=False, remotes="")):
            self.assertFalse(executor._has_git_repo(self.target))
            self.assertFalse(executor._has_commits(self.target))
            self.assertFalse(executor.has_remote(self.target))
        with mock.patch.object(executor.subprocess, "run", _Git(remotes="  \n")):
            self.assertFalse(executor.has_remote(self.target))


class ComputeTargetKindTestCase(unittest.TestCase):
    def test_kinds(self):
        d = Path(tempfile.mkdtemp(prefix="kind-"))
        self.assertEqual(executor.compute_target_kind(d), "new")          # empty dir
        self.assertEqual(executor.compute_target_kind(d / "missing"), "new")
        (d / "f").write_text("x", encoding="utf-8")
        self.assertEqual(executor.compute_target_kind(d), "existing")
        self.assertEqual(executor.compute_target_kind(d / "f"), "new")    # a file

    def test_unreadable_directory_counts_as_new(self):
        with mock.patch.object(executor.Path, "exists", side_effect=OSError("EACCES")):
            self.assertEqual(executor.compute_target_kind(Path("/x")), "new")


if __name__ == "__main__":
    unittest.main()
