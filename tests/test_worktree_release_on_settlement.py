"""§75 / §65.5 追记（issue #315）：卡结算的那一刻顺手删掉它自己的 worktree 与本地分支。

owner 在 GitHub 上合并 = 验收、关闭 = 拒绝——两条出口以前都只写 registry，
`.claude/worktrees/<name>/` 与 `ai/self-improve/R-xxx` 分支原地留着，一年攒 190 个。
本判例钉：两条出口都调 `worktrees.release`；目标只认「这张卡自己的」（同分支的那条
登记 ∪ transcript 记下的会话 cwd），且必须落在托管根之内；脏的一律不删；清扫失败
绝不许把「PR 已合并 = 验收」这条落账带下水。
"""
import datetime as _dt
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.self_improve_testkit import FakeGh, lane_card, pr_doc
from tests.worktree_testkit import FakeGit, Tree

from act.lib import config, notify, registry, self_improve, worktrees
from act.lib.registry import Requirement, State

BRANCH = "ai/self-improve/R-900"
NOW = _dt.datetime(2026, 9, 15, 12, 0, tzinfo=_dt.timezone.utc)


def _card(branch="ai/self-improve/R-900", sid="aaaa1111"):
    return Requirement(id="P-7", title="lane 卡", type="self-improvement", tier="T1",
                       status="review",
                       execution={"session_id": sid, "self_improve": {"branch": branch}})


class ReleaseOnSettlementTestCase(unittest.TestCase):
    def setUp(self):
        self.tree = Tree()
        self.addCleanup(self.tree.cleanup)
        self.cfg = config.Config(raw={"self_improve": {"repo_path": self.tree.repo}})

    def test_the_cards_own_worktree_and_branch_go_away(self):
        path = self.tree.add("r900")
        git = FakeGit(self.tree.repo, [{"path": path, "branch": "ai/self-improve/R-900",
                                        "head": "sha-r900"}])
        got = worktrees.release(_card(), self.cfg, git=git, resolve=lambda _s: None)
        self.assertEqual([r["path"] for r in got["removed"]], [path])
        self.assertEqual(git.branches_deleted, ["ai/self-improve/R-900"])
        self.assertFalse(any("--force" in c[0] or "-D" in c[0] for c in git.calls))

    def test_the_session_cwd_counts_as_the_cards_worktree_too(self):
        path = self.tree.add("hopped")
        git = FakeGit(self.tree.repo, [], remotes=["origin/main"])
        got = worktrees.release(_card(), self.cfg, git=git, resolve=lambda _s: Path(path))
        self.assertEqual([r["path"] for r in got["removed"]], [path])

    def test_a_session_that_never_left_the_repo_root_is_not_a_target(self):
        git = FakeGit(self.tree.repo, [], remotes=["origin/main"])
        got = worktrees.release(_card(), self.cfg, git=git, resolve=lambda _s: Path(self.tree.repo))
        self.assertEqual(got["removed"], [])
        self.assertEqual(git.removed, [])

    def test_uncommitted_changes_keep_the_worktree_even_at_settlement(self):
        path = self.tree.add("dirty")
        git = FakeGit(self.tree.repo, [{"path": path, "branch": "ai/self-improve/R-900",
                                        "head": "sha-d"}], dirty={path})
        got = worktrees.release(_card(), self.cfg, git=git, resolve=lambda _s: None)
        self.assertEqual(got["removed"], [])
        self.assertEqual(got["skipped"], [{"path": path, "reason": "dirty"}])

    def test_a_card_without_a_lane_branch_only_looks_at_its_session_cwd(self):
        """卡上没有 `self_improve.branch`（手改 / 老卡 / 分支还没建）= 不查登记表。

        分支名是「这张卡自己的」唯一判据；没有它就只剩 transcript 记下的会话 cwd
        这一条线索，而按分支名去 `git worktree list` 里捞是无源之举——多删的风险
        全在这一步（§75 的安全边界：宁可少删一条）。"""
        path = self.tree.add("session-only")
        git = FakeGit(self.tree.repo, [], remotes=["origin/main"])
        card = Requirement(id="P-8", title="没有分支名的卡", type="self-improvement",
                           tier="T1", status="review",
                           execution={"session_id": "aaaa1111"})
        got = worktrees.release(card, self.cfg, git=git, resolve=lambda _s: Path(path))
        self.assertEqual(got["branch"], "")
        self.assertEqual([r["path"] for r in got["removed"]], [path])
        self.assertEqual(git.branches_deleted, [])          # 没有分支名 = 不删分支
        self.assertFalse(any(c[0][:2] == ("worktree", "list") for c in git.calls))

    def test_the_release_is_logged_with_its_counts_and_its_error(self):
        """§65.5 的落账要能在日志里对账：删了几条、跳过几条、出错没有。"""
        path = self.tree.add("logged")
        git = FakeGit(self.tree.repo, [{"path": path, "branch": BRANCH, "head": "sha-l"}])
        lines = []
        worktrees.release(_card(), self.cfg, git=git, log=lines.append,
                          resolve=lambda _s: None)
        self.assertEqual(len(lines), 1)
        self.assertIn("P-7 worktree release removed=1 skipped=0", lines[0])
        self.assertNotIn("error=", lines[0])

        def boom(_args, _cwd):
            raise RuntimeError("git exploded")
        lines = []
        worktrees.release(_card(), self.cfg, git=boom, log=lines.append,
                          resolve=lambda _s: None)
        self.assertIn("error=RuntimeError: git exploded", lines[0])

    def test_a_blowing_up_git_is_recorded_not_raised(self):
        def boom(_args, _cwd):
            raise RuntimeError("git exploded")
        got = worktrees.release(_card(), self.cfg, git=boom, resolve=lambda _s: None)
        self.assertIn("RuntimeError", got["error"])

    def test_the_switch_off_makes_release_a_no_op(self):
        got = worktrees.release(_card(), self.cfg)      # 套件默认关，没注入 git = 零子进程
        self.assertEqual(got["removed"], [])
        self.assertEqual(got["skipped"], [{"path": None, "reason": "disabled"}])


class SettlementCallsReleaseTestCase(unittest.TestCase):
    """§65.5 的两条出口都要走到 release（这里只钉「调了」，删什么由上面那组钉）。"""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        for name in ("lane.json", "rejected.jsonl"):
            p = self_improve.state_dir() / name
            if p.exists():
                p.unlink()
        mock.patch.object(notify, "notify").start()
        self.released = mock.patch.object(worktrees, "release", return_value={"removed": []}).start()
        self.addCleanup(mock.patch.stopall)
        self.cfg = config.Config(self_improve_enabled=True)
        delivery = {"verified": True, "reason": None, "pr_number": 268,
                    "pr_url": "https://github.com/o/r/pull/268", "branch": BRANCH}
        registry.save(lane_card("P-7", status=State.REVIEW.value,
                                execution={"session_id": "aaaa1111", "done": True,
                                           "delivery": delivery, "self_improve": {"branch": BRANCH}}))

    def _settle(self, state):
        gh = FakeGh({268: pr_doc(268, branch=BRANCH, draft=False, state=state)},
                    closers={268: ["Wan-ZL"]})
        self_improve.tick(self.cfg, gh=gh, now=NOW, force=True)

    def test_owner_merge_releases_the_worktree(self):
        self._settle("MERGED")
        self.assertEqual(registry.load("P-7").status, State.DELIVERED.value)
        self.assertEqual(self.released.call_count, 1)

    def test_owner_close_releases_the_worktree(self):
        self._settle("CLOSED")
        self.assertEqual(self.released.call_count, 1)

    def test_a_failing_release_never_undoes_the_acceptance(self):
        self.released.side_effect = RuntimeError("git exploded")
        self._settle("MERGED")
        self.assertEqual(registry.load("P-7").status, State.DELIVERED.value)


if __name__ == "__main__":
    unittest.main()
