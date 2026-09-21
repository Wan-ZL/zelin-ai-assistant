"""§75 / §70.1 追记：worktree 回收的心跳打在**工作单元**上，不是每个 root 一下。

现实里 root 只有一个（§65.3 的通道 repo），所以「每个 root beat 一次」= 整段回收只有
开头那一下心跳。而这一段之后还要逐条 `git status` / `rev-list`（时间预算
`SCAN_BUDGET_S`）、再逐条 `git worktree remove`（每条是一份带 `web/node_modules` 的完整
checkout），一台攒了 190 个 worktree 的机器上是分钟级——`heartbeat.stale_after_seconds`
只给 max(3×interval, 90) 秒，`GET /api/health` 与 `act/doctor.py` 会在第一次真扫时把
actd 判成 `actd_stalled`。本判例因此按「判了几条 + 删了几条」数心跳，而不是按 root 数。
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.worktree_testkit import FakeGit, Tree

from act.lib import config, worktrees


class WorktreeSweepHeartbeatTestCase(unittest.TestCase):
    def setUp(self):
        self.tree = Tree()
        self.addCleanup(self.tree.cleanup)
        self.cfg = config.Config(raw={"self_improve": {"repo_path": self.tree.repo}})
        self.beats = []

    def _entries(self, n):
        return [{"path": self.tree.add("w%d" % i, age_days=30.0), "branch": "feat/w%d" % i,
                 "head": "sha-w%d" % i} for i in range(n)]

    def _sweep(self, git, **kw):
        return worktrees.sweep(self.cfg, git=git, reqs=[], live=set(),
                               beat=lambda: self.beats.append(1), **kw)

    def test_the_sweep_beats_once_per_judged_row_and_once_per_removal(self):
        entries = self._entries(3)
        git = FakeGit(self.tree.repo, entries, remotes=["origin/main"])
        got = self._sweep(git)
        self.assertEqual(len(got["removed"]), 3)
        # 1 个 root + 4 条登记（主工作树也走判决这一圈）+ 3 次删除
        self.assertEqual(len(self.beats), 1 + 4 + 3)

    def test_more_work_means_more_heartbeats_not_the_same_one_per_root(self):
        few = FakeGit(self.tree.repo, self._entries(1), remotes=["origin/main"])
        self._sweep(few)
        small = len(self.beats)
        self.beats = []
        self.tree.cleanup()
        self.tree = Tree()
        self.addCleanup(self.tree.cleanup)
        self.cfg = config.Config(raw={"self_improve": {"repo_path": self.tree.repo}})
        many = FakeGit(self.tree.repo, self._entries(6), remotes=["origin/main"])
        self._sweep(many)
        self.assertGreater(len(self.beats), small)

    def test_a_sweep_without_a_beat_hook_is_still_a_sweep(self):
        git = FakeGit(self.tree.repo, self._entries(1), remotes=["origin/main"])
        got = worktrees.sweep(self.cfg, git=git, reqs=[], live=set())
        self.assertEqual(len(got["removed"]), 1)
        self.assertEqual(self.beats, [])


if __name__ == "__main__":
    unittest.main()
