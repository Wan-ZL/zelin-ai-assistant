"""§75.4 清点的占用数字：量到就加总，量不到就说不知道——**永不虚报 0**（§0 第 3 条）。

`inventory()` 是 `GET /api/worktrees` 与 CLI 裸形的真源，而 `du -sk` 是这条路上唯一
一条可能没有答案的边：`--no-bytes` 明确不量、托管根压根不在、du 起不来或输出读不出
数字。三种「没有答案」都必须落成 `bytes: null` + `bytes_partial: true`（这个 root 下
真有 worktree 的话），因为页面上一个 0 GB 会被当成「已经清干净了」。

零子进程：`git` 与 `du` 都是注入的假 runner（仓规：unit 层禁真 subprocess）。
"""
import os
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.worktree_testkit import FakeGit, Tree

from act.lib import config, worktrees


class InventoryBytesTestCase(unittest.TestCase):
    def setUp(self):
        self.tree = Tree()
        self.addCleanup(self.tree.cleanup)
        self.cfg = config.Config(raw={"self_improve": {"repo_path": self.tree.repo}})
        path = self.tree.add("wf-a", age_days=1.0)
        self.git = FakeGit(self.tree.repo, [{"path": path, "branch": "feat/a",
                                             "head": "sha-a"}],
                           remotes=["origin/main", "origin/feat/a"])

    def _inventory(self, **kw):
        return worktrees.inventory(self.cfg, git=self.git, **kw)

    def test_a_measured_root_reports_the_bytes_it_measured(self):
        got = self._inventory(du=lambda _root: 4096)
        self.assertEqual((got["bytes"], got["bytes_partial"]), (4096, False))
        self.assertEqual(got["worktrees"], 1)

    def test_no_bytes_asks_for_no_measurement_and_says_so(self):
        got = self._inventory(measure=False, du=lambda _root: 4096)
        self.assertIsNone(got["bytes"])
        self.assertTrue(got["bytes_partial"])       # 这个 root 下真有 worktree
        self.assertEqual(got["worktrees"], 1)       # 条数照旧数得出来

    def test_a_du_without_an_answer_is_partial_not_zero(self):
        got = self._inventory(du=lambda _root: None)
        self.assertIsNone(got["bytes"])
        self.assertTrue(got["bytes_partial"])

    def test_a_managed_root_that_is_not_there_is_never_measured(self):
        os.rename(self.tree.root, self.tree.root + "-moved")   # 托管目录整棵不在了
        asked = []
        got = self._inventory(du=asked.append)
        self.assertIsNone(got["bytes"])
        self.assertEqual(asked, [])                 # 目录不在 = 一次 du 都不起


if __name__ == "__main__":
    unittest.main()
