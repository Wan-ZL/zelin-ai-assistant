"""§75：`.claude/worktrees/` 的判决与回收——谁删得、谁一律留下、以什么顺序删。

issue #315：生产 checkout 攒了 190+ 个 worktree，没有任何一处代码删过它们。本判例钉
的是那把扫帚的**安全边界**：路径不在托管根之内、锁着的、有未提交改动的、还有在飞的
卡指着的、有只存在于本地的提交的，一条都不许碰；够格删的三个理由（分支已并进远端
默认分支 / 分支在 origin 上已不存在 / 目录 STALE_DAYS 天没动过）另加一层年龄地板，
免得刚建出来还没提交过东西的 worktree 在「分支不在 origin 上」这一条上被误删。
执行顺序：`git worktree prune`（闸只拦「会被注销的登记里有落在托管根**之外**的」，
登记表读不出来则一枪不开）→ `git worktree remove`（永不 `--force`）→ `git branch -d`
（永不 `-D`）。「有只存在于本地的提交」是**延期**不是否决：目录的年龄门槛抬到
`STALE_DAYS`，过了照删，而那条分支永远留着（`kept_branch: "unpushed"`）。
"""
import os
import time
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.worktree_testkit import FakeGit, Tree

from act.lib import config, worktrees
from act.lib.registry import Requirement


class WorktreeGcTestCase(unittest.TestCase):
    def setUp(self):
        self.tree = Tree()
        self.addCleanup(self.tree.cleanup)
        # 通道 repo = 假 repo（§65.3 的物理闸就是 worktrees 的扫描根）
        self.cfg = config.Config(raw={"self_improve": {"repo_path": self.tree.repo}})

    def _entry(self, name, branch="feat/x", age_days=30.0, **over):
        path = self.tree.add(name, age_days=age_days)
        row = {"path": path, "branch": branch, "head": "sha-" + name}
        row.update(over)
        return row

    def _sweep(self, git, **kw):
        return worktrees.sweep(self.cfg, git=git, reqs=[], live=kw.pop("live", set()), **kw)

    # -- 够格删的三个理由 ----------------------------------------------------- #
    def test_a_branch_merged_into_the_remote_default_is_removed_with_its_branch(self):
        entry = self._entry("wf-merged", branch="ai/self-improve/R-205", age_days=3.0)
        git = FakeGit(self.tree.repo, [entry], merged={"ai/self-improve/R-205"},
                      remotes=["origin/main", "origin/ai/self-improve/R-205"])
        got = self._sweep(git)
        self.assertEqual([r["reason"] for r in got["removed"]], ["merged"])
        self.assertEqual(git.removed, [entry["path"]])
        self.assertEqual(git.branches_deleted, ["ai/self-improve/R-205"])

    def test_a_branch_deleted_on_origin_is_removed_even_when_the_dir_is_fresh_enough(self):
        # wf_*/agent-* 的形态：PR 合了、远端分支删了、本地什么都没剩下
        entry = self._entry("agent-a40e", branch="fix/gone", age_days=5.0)
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"])
        got = self._sweep(git)
        self.assertEqual([r["reason"] for r in got["removed"]], ["gone"])

    def test_an_untouched_worktree_past_the_stale_window_is_removed(self):
        entry = self._entry("old", branch="feat/live", age_days=worktrees.STALE_DAYS + 1)
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main", "origin/feat/live"])
        self.assertEqual([r["reason"] for r in self._sweep(git)["removed"]], ["stale"])

    # -- 守卫 ---------------------------------------------------------------- #
    def test_uncommitted_changes_locks_liveness_and_unpushed_commits_all_keep_the_worktree(self):
        dirty = self._entry("dirty", branch="a/dirty")
        locked = self._entry("locked", branch="a/locked", locked=True)
        live = self._entry("live", branch="a/live")
        # 有本地独有提交的这条还没过 STALE_DAYS——那期限之内它照旧原地不动
        ahead = self._entry("ahead", branch="a/ahead", age_days=worktrees.STALE_DAYS - 1)
        git = FakeGit(self.tree.repo, [dirty, locked, live, ahead], remotes=["origin/main"],
                      dirty={dirty["path"]}, ahead={"sha-ahead": 3})
        got = self._sweep(git, live={live["path"]})
        self.assertEqual(got["removed"], [])
        self.assertEqual(got["skipped"], {"dirty": 1, "locked": 1, "live": 1, "unpushed": 1})
        self.assertEqual(git.removed, [])

    def test_a_worktree_that_is_not_under_the_managed_root_is_never_touched(self):
        outside = {"path": self.tree.base, "branch": "feat/outside", "head": "sha-out"}
        git = FakeGit(self.tree.repo, [outside], remotes=["origin/main"])
        got = self._sweep(git)
        self.assertEqual(got["removed"], [])
        self.assertEqual(got["worktrees"], 0)      # 托管根之外的不算「我们的 worktree」
        self.assertEqual(git.removed, [])

    def test_a_worktree_younger_than_the_age_floor_is_left_alone(self):
        entry = self._entry("just-born", branch="feat/new", age_days=0.1)
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"])
        got = self._sweep(git)
        self.assertEqual(got["removed"], [])
        self.assertEqual(got["skipped"].get("active"), 1)

    def test_an_unreadable_git_status_counts_as_dirty(self):
        entry = self._entry("unreadable", branch="feat/unreadable")
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"], fail={"status --porcelain"})
        self.assertEqual(self._sweep(git)["skipped"], {"dirty": 1})

    # -- 执行的形状 ----------------------------------------------------------- #
    def test_prune_runs_before_remove_and_never_with_force_or_capital_d(self):
        entry = self._entry("ordered", branch="feat/ordered")
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"])
        self._sweep(git)
        shapes = [c[0] for c in git.calls]
        self.assertLess(shapes.index(("worktree", "prune")),
                        shapes.index(("worktree", "remove", entry["path"])))
        self.assertFalse(any("--force" in s or "-D" in s for s in shapes))

    def test_a_ghost_registration_under_the_managed_root_is_what_prune_is_for(self):
        # 目录被手删、登记还在：这正是 prune 要收的东西，闸不该拦它（否则 prune 只在
        # 无事可做时才跑，issue #315 Expected 第 2 条的 prune 那条腿就是死的）
        entry = self._entry("here", branch="feat/here")
        ghost = {"path": self.tree.root + "/vanished", "branch": "feat/ghost", "head": "sha-ghost"}
        git = FakeGit(self.tree.repo, [entry, ghost], remotes=["origin/main"])
        got = self._sweep(git)
        self.assertEqual(got["pruned"][self.tree.repo], "ok")
        self.assertEqual(git.pruned, 1)
        self.assertEqual(got["skipped"].get("missing"), 1)

    def test_prune_is_skipped_when_a_missing_registration_lies_outside_the_managed_root(self):
        # owner 那 142 个手工 worktree 的形态：登记指向别的目录树，那棵树临时不在
        entry = self._entry("here", branch="feat/here")
        ghost = {"path": self.tree.base + "/elsewhere/gone", "branch": "feat/hand-made",
                 "head": "sha-hand"}
        git = FakeGit(self.tree.repo, [entry, ghost], remotes=["origin/main"])
        got = self._sweep(git)
        self.assertEqual(got["pruned"][self.tree.repo], "skipped:missing_paths")
        self.assertEqual(git.pruned, 0)

    def test_a_listing_that_fails_before_the_execution_leaves_prune_unfired(self):
        # 闸的输入没了就不许开枪：空登记表里「有没有外面的幽灵」恒为否，fail-open 会让
        # 一次仓库全局 prune 在零核对之下跑起来
        entry = self._entry("here", branch="feat/here")
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"], list_fails_after=1)
        got = self._sweep(git)
        self.assertEqual(got["pruned"][self.tree.repo], "skipped:unknown")
        self.assertEqual(git.pruned, 0)

    def test_an_unpushed_branch_keeps_its_branch_but_loses_its_worktree_once_stale(self):
        # 守卫有期限：过了 STALE_DAYS，目录照清（可再生），分支连问都不问（提交不可再生）
        entry = self._entry("ahead-old", branch="a/ahead-old",
                            age_days=worktrees.STALE_DAYS + 1)
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"],
                      ahead={"sha-ahead-old": 2})
        got = self._sweep(git)
        self.assertEqual(git.removed, [entry["path"]])
        self.assertEqual(got["removed"][0]["kept_branch"], "unpushed")
        self.assertFalse(got["removed"][0]["branch_deleted"])
        self.assertEqual(git.branches_deleted, [])

    def test_a_branch_that_git_refuses_to_delete_leaves_the_worktree_removed(self):
        entry = self._entry("keepbranch", branch="feat/keep")
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"],
                      undeletable={"feat/keep"})
        got = self._sweep(git)
        self.assertTrue(got["removed"][0]["removed"])
        self.assertFalse(got["removed"][0]["branch_deleted"])

    def test_dry_run_changes_nothing_but_still_names_the_victims(self):
        entry = self._entry("doomed", branch="feat/doomed")
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"])
        got = self._sweep(git, dry_run=True)
        self.assertEqual([r["path"] for r in got["removed"]], [entry["path"]])
        self.assertEqual(git.removed, [])
        self.assertEqual(git.pruned, 0)

    def test_the_per_run_cap_defers_the_rest_to_the_next_round(self):
        entries = [self._entry("w%d" % i, branch="feat/w%d" % i) for i in range(4)]
        git = FakeGit(self.tree.repo, entries, remotes=["origin/main"])
        got = self._sweep(git, limit=2)
        self.assertEqual(len(got["removed"]), 2)
        self.assertEqual(got["skipped"].get("cap"), 2)

    def test_the_switch_off_makes_both_write_paths_no_ops(self):
        # 进程级总闸（测试套件默认关）：没注入 git runner 时 sweep 一个子进程都不起
        got = worktrees.sweep(self.cfg)
        self.assertEqual(got["removed"], [])
        self.assertEqual(got["skipped"], {"disabled": 1})

    def test_the_inventory_reports_counts_and_size_without_writing_anything(self):
        entry = self._entry("counted", branch="feat/counted")
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"])
        got = worktrees.inventory(self.cfg, git=git, reqs=[], live=set(), du=lambda _p: 4096)
        self.assertEqual((got["worktrees"], got["removable"], got["bytes"]), (1, 1, 4096))
        self.assertFalse(got["bytes_partial"])
        self.assertEqual(git.removed, [])
        self.assertEqual(git.pruned, 0)

    def test_an_unmeasurable_size_is_null_not_zero(self):
        self._entry("unmeasured", branch="feat/unmeasured")
        git = FakeGit(self.tree.repo, [], remotes=["origin/main"])
        git.entries = [{"path": self.tree.root + "/unmeasured", "branch": "feat/unmeasured",
                        "head": "sha-u"}]
        got = worktrees.inventory(self.cfg, git=git, reqs=[], live=set(), du=lambda _p: None)
        self.assertIsNone(got["bytes"])
        self.assertTrue(got["bytes_partial"])

    # -- 判决的输入 ----------------------------------------------------------- #
    def test_only_in_flight_cards_contribute_a_live_path(self):
        flying = Requirement(id="P-1", title="在飞", status="executing",
                             execution={"session_id": "aaaa1111", "cwd": self.tree.root + "/x"})
        done = Requirement(id="P-2", title="已交付", status="delivered",
                           execution={"session_id": "bbbb2222"})
        got = worktrees.live_paths([flying, done],
                                   resolve=lambda sid: Path(self.tree.root + "/" + sid))
        self.assertEqual(got, {os.path.realpath(self.tree.root + "/x"),
                               os.path.realpath(self.tree.root + "/aaaa1111")})

    def test_a_card_whose_transcript_cannot_be_read_is_simply_skipped(self):
        def boom(_sid):
            raise OSError("unreadable")
        card = Requirement(id="P-3", title="坏 transcript", status="review",
                           execution={"session_id": "cccc3333"})
        self.assertEqual(worktrees.live_paths([card], resolve=boom), set())
        self.assertEqual(worktrees.live_paths([object()], resolve=boom), set())

    def test_the_scan_roots_are_the_lane_repo_plus_card_repos_that_have_worktrees(self):
        other = Requirement(id="P-4", title="别的 repo", status="detected",
                            target_repo=self.tree.base)          # 没有 .claude/worktrees
        same = Requirement(id="P-5", title="同一 repo", status="detected",
                           target_repo=self.tree.repo)
        self.assertEqual(worktrees.roots(self.cfg, [other, same, same]), [self.tree.repo])

    def test_touched_at_notices_a_git_command_deep_inside_the_gitdir(self):
        path = self.tree.add("touched", age_days=40.0)
        gitdir = os.path.join(self.tree.repo, ".git", "worktrees", "touched")
        os.makedirs(gitdir)
        open(os.path.join(gitdir, "index"), "w", encoding="utf-8").close()
        self.assertGreater(worktrees.touched_at(path), time.time() - 60)
        self.assertIsNone(worktrees.touched_at(self.tree.root + "/never-existed"))

    def test_the_default_remote_ref_falls_back_through_main_master_dev(self):
        git = FakeGit(self.tree.repo, [], default_ref=None, remotes=["origin/dev"])
        self.assertEqual(worktrees.default_remote_ref(git, self.tree.repo, {"origin/dev"}),
                         "origin/dev")
        self.assertIsNone(worktrees.default_remote_ref(git, self.tree.repo, set()))
        self.assertEqual(worktrees.merged_branches(git, self.tree.repo, None), set())

    def test_an_uncountable_rev_list_is_treated_as_unpushed(self):
        git = FakeGit(self.tree.repo, [], fail={"rev-list --count"})
        self.assertTrue(worktrees.unpushed(git, self.tree.repo, "sha"))
        self.assertTrue(worktrees.unpushed(git, self.tree.repo, ""))
        garbled = FakeGit(self.tree.repo, [])
        garbled._rev_list = lambda _a, _c: (0, "not a number\n")
        self.assertTrue(worktrees.unpushed(garbled, self.tree.repo, "sha"))

    def test_a_git_that_cannot_list_worktrees_reports_the_error_and_removes_nothing(self):
        git = FakeGit(self.tree.repo, [], fail={"worktree list"})
        got = self._sweep(git)
        self.assertFalse(got["ok"])
        self.assertEqual(got["removed"], [])
        self.assertIn("worktree list failed", got["repos"][0]["error"])

    def test_the_scan_budget_stops_the_costly_half_and_says_so(self):
        entry = self._entry("budgeted", branch="feat/budgeted")
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"])
        got = worktrees.survey(self.tree.repo, git=git, live=set(), budget_s=0.0)
        self.assertTrue(got["truncated"])
        self.assertEqual([r["reason"] for r in got["rows"]], ["budget"])
        self.assertEqual(got["removable"], 0)


if __name__ == "__main__":
    unittest.main()
