"""§70.1 追记 / §75：每日循环的第三个维护阶段——worktree 回收，在过时清扫之后、提案之前。

计数走 add-only 的 `last_result.worktrees`（§2 maintenance 投影同名整数键），审计行留
整份回执。阶段与其它三段一样各自隔离：扫地爆炸只进 `errors`，提案照跑（宪法第 11 条）。
"""
import datetime as _dt
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.worktree_testkit import FakeGit, Tree

from act.lib import config, daily_loop

NOW = _dt.datetime(2026, 9, 15, 4, 0)


def _gh_none(_args, _cwd=None):
    return None, ""


class DailyLoopWorktreePhaseTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        state = daily_loop.state_path()
        if state.exists():
            state.unlink()
        self.tree = Tree()
        self.addCleanup(self.tree.cleanup)
        self.cfg = config.Config(raw={"self_improve": {"repo_path": self.tree.repo}})

    def _run(self, git):
        return daily_loop.run(self.cfg, now=NOW, gh=_gh_none, doctor=lambda: "[]", git=git)

    def test_the_phase_runs_between_stale_sweep_and_proposals_and_counts_what_it_removed(self):
        path = self.tree.add("dead", age_days=40.0)
        git = FakeGit(self.tree.repo, [{"path": path, "branch": "feat/dead", "head": "sha-dead"}],
                      remotes=["origin/main"])
        out = self._run(git)
        self.assertEqual(out["worktrees"], 1)
        self.assertEqual(git.removed, [path])
        self.assertEqual(daily_loop.load_state()["phase"], daily_loop.PHASE_IDLE)
        self.assertEqual(daily_loop.load_state()["last_result"]["worktrees"], 1)

    def test_the_projection_carries_the_count(self):
        git = FakeGit(self.tree.repo, [], remotes=["origin/main"])
        self._run(git)
        m = daily_loop.attach({}, self.cfg)["maintenance"]
        self.assertEqual(m["last_result"]["worktrees"], 0)

    def test_a_blowing_up_sweep_only_lands_in_errors(self):
        def boom(_args, _cwd):
            raise RuntimeError("git exploded")
        out = self._run(boom)
        self.assertEqual(out["worktrees"], 0)
        self.assertTrue(any(e.startswith("worktree_sweep:") for e in out["errors"]))
        self.assertIn("proposals", out)


if __name__ == "__main__":
    unittest.main()
