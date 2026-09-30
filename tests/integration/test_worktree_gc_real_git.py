"""§75 的判决对着**真 git** 跑一遍（tests/integration/，防腐 #7：真 IO 只许住这里）。

单元层的 `FakeGit` 钉的是判决与顺序，钉不住「我们递给 git 的 argv 与解析它 stdout 的
口径是对的」——`git worktree list --porcelain` 的行文法（含目录没了那条的 `prunable`）、
`branch --merged` 的输出形状、`rev-list --count <sha> --not --remotes` 的语义、
`worktree remove` 拒绝脏树、`worktree prune` 到底注销了谁、`worktree remove` **不**碰分支
引用（删掉目录之后 `git worktree add <path> <branch>` 能原样长回来），这些只有真 git
能作证。本文件因此每条判例建一个真 repo（一个
「远端」裸库 + 一个工作副本）、真的 `git worktree add` 出三条，然后跑 `survey` / `sweep`。

每条判例各起一个 repo：`survey` 会在候选上跑 `git status`，而那一下会刷新 gitdir 的
`index` mtime——共用一个 repo 的话，前一条判例的探测就会把后一条的年龄判据洗掉
（这一点本身不是缺陷，见 `worktrees.touched_at` 的注释）。

零网络（远端是本地目录）、零 claude；预算 ``BUDGET_SECONDS``。
"""
import os
import shutil
import subprocess
import sys
import time
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.scratch_testkit import scratch_dir

from act.lib import worktrees

BUDGET_SECONDS = 90
_T0 = [time.monotonic()]
_WIN = sys.platform.startswith("win")
OLD_DAYS = 40


def setUpModule():
    _T0[0] = time.monotonic()


def tearDownModule():
    elapsed = time.monotonic() - _T0[0]
    if elapsed > BUDGET_SECONDS:
        raise AssertionError("tests/integration/test_worktree_gc_real_git.py took %.0fs > %ds"
                             % (elapsed, BUDGET_SECONDS))


def _git(cwd, *args):
    proc = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=str(cwd), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError("git %s failed: %s" % (" ".join(args), proc.stderr.strip()))
    return proc.stdout


def _write(path, text="x"):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _backdate(path, stamp):
    """把 `touched_at` 会看的每一个戳都往回拨——目录 / 顶层条目 / `.git` / gitdir 里
    的 index·HEAD·logs/HEAD。只拨目录 mtime 是不够的，那正是 §75.1 要修的错判据。"""
    targets = [path, os.path.join(path, ".git")]
    targets += [entry.path for entry in os.scandir(path)]
    gitdir = worktrees._gitdir_of(path)
    if gitdir:
        targets += [os.path.join(gitdir, name) for name in worktrees._GITDIR_STAMPS]
    for target in targets:
        if os.path.exists(target):
            os.utime(target, (stamp, stamp))


@unittest.skipIf(_WIN, "git worktree 布局在 Windows CI 上另说；判决本身由单元层钉")
@unittest.skipIf(shutil.which("git") is None, "no git on PATH")
class RealGitTestCase(unittest.TestCase):
    """真 repo：`origin` 是本地裸库；main 上一个提交；三条 worktree（干净 / 脏 / 有本地提交）。"""

    def setUp(self):
        self.base = os.path.realpath(scratch_dir(self, prefix="wt-gc-real-"))
        remote = os.path.join(self.base, "remote.git")
        self.repo = os.path.join(self.base, "repo")
        _git(self.base, "init", "--bare", "--initial-branch=main", remote)
        _git(self.base, "clone", remote, self.repo)
        _write(os.path.join(self.repo, "README.md"), "hello\n")
        _git(self.repo, "add", "README.md")
        _git(self.repo, "commit", "-m", "init")
        _git(self.repo, "push", "-u", "origin", "main")
        self.root = os.path.join(self.repo, ".claude", "worktrees")
        os.makedirs(self.root)
        for name in ("gone", "dirty", "ahead"):
            _git(self.repo, "worktree", "add", "-b", "feat/" + name,
                 os.path.join(self.root, name), "main")
        _write(os.path.join(self.root, "dirty", "scratch.txt"))     # 未跟踪文件 = 脏
        _write(os.path.join(self.root, "ahead", "local.txt"))
        _git(os.path.join(self.root, "ahead"), "add", "local.txt")
        _git(os.path.join(self.root, "ahead"), "commit", "-m", "local only")
        self.cfg = {"self_improve": {"repo_path": self.repo}}

    def _age_them_all(self):
        stamp = time.time() - OLD_DAYS * 86400.0
        for name in ("gone", "dirty", "ahead"):
            _backdate(os.path.join(self.root, name), stamp)

    def _sweep(self, **kw):
        return worktrees.sweep(self.cfg, git=worktrees.default_git, reqs=[], live=set(),
                               now=time.time(), **kw)

    @staticmethod
    def _by_name(receipt) -> dict:
        """回执里的删除行按 worktree 名索引（真 git 的登记顺序不保证等于建的顺序）。"""
        return {os.path.basename(row["path"]): row for row in receipt["removed"]}

    def test_porcelain_parsing_and_the_age_floor_agree_with_real_git(self):
        rows = {r["name"]: r for r in
                worktrees.survey(self.repo, now=time.time(), live=set())["rows"]}
        self.assertEqual(set(rows), {"gone", "dirty", "ahead"})
        self.assertEqual(rows["gone"]["branch"], "feat/gone")
        # 全都是刚建出来的 → 年龄地板挡住，一条都不该被判删
        self.assertEqual({r["verdict"] for r in rows.values()}, {"keep"})
        self.assertEqual({r["reason"] for r in rows.values()}, {"active"})

    def test_stale_worktrees_go_dirty_stays_and_an_unpushed_branch_outlives_its_worktree(self):
        self._age_them_all()
        got = self._sweep()
        rows = self._by_name(got)
        self.assertEqual(set(rows), {"gone", "ahead"})
        self.assertTrue(rows["gone"]["branch_deleted"])
        self.assertFalse(os.path.isdir(os.path.join(self.root, "gone")))
        self.assertTrue(os.path.isdir(os.path.join(self.root, "dirty")))
        self.assertFalse(os.path.isdir(os.path.join(self.root, "ahead")))
        self.assertEqual(got["skipped"].get("dirty"), 1)
        self.assertEqual(got["pruned"][self.repo], "ok")
        self.assertNotIn("feat/gone", _git(self.repo, "branch", "--list"))
        # 目录没了，提交没丢：真 git 上分支引用照旧在主 repo 的 .git 里
        self.assertEqual(rows["ahead"]["kept_branch"], "unpushed")
        self.assertFalse(rows["ahead"]["branch_deleted"])
        self.assertIn("feat/ahead", _git(self.repo, "branch", "--list"))
        self.assertIn("local only", _git(self.repo, "log", "-1", "--format=%s", "feat/ahead"))

    def test_a_worktree_removed_by_hand_can_be_added_back_from_its_branch(self):
        self._age_them_all()
        self._sweep()
        back = os.path.join(self.root, "ahead-again")
        _git(self.repo, "worktree", "add", back, "feat/ahead")
        self.assertTrue(os.path.isfile(os.path.join(back, "local.txt")))

    def test_a_dry_run_over_the_same_tree_removes_nothing(self):
        self._age_them_all()
        got = self._sweep(dry_run=True)
        self.assertEqual(set(self._by_name(got)), {"gone", "ahead"})
        self.assertTrue(os.path.isdir(os.path.join(self.root, "gone")))
        self.assertTrue(os.path.isdir(os.path.join(self.root, "ahead")))
        self.assertIn("feat/gone", _git(self.repo, "branch", "--list"))

    def test_a_ghost_registration_under_the_managed_root_is_pruned(self):
        self._age_them_all()
        shutil.rmtree(os.path.join(self.root, "dirty"))       # 手工删掉目录，登记还在
        got = self._sweep()
        self.assertEqual(got["pruned"][self.repo], "ok")
        self.assertEqual(got["skipped"].get("missing"), 1)
        self.assertNotIn("dirty", _git(self.repo, "worktree", "list"))

    def test_a_missing_registration_outside_the_managed_root_disables_prune(self):
        self._age_them_all()
        outside = os.path.join(self.base, "hand-made")        # owner 那 142 个的形态
        _git(self.repo, "worktree", "add", "-b", "feat/outside", outside, "main")
        shutil.rmtree(outside)
        got = self._sweep()
        self.assertEqual(got["pruned"][self.repo], "skipped:missing_paths")
        self.assertIn("hand-made", _git(self.repo, "worktree", "list"))


if __name__ == "__main__":
    unittest.main()
