"""§75：worktree 回收里还没有判例把守的那些边界（变异网 train 补测）。

夜报（`qa/mutation_targets.toml` 的靶区）在 `act/lib/worktrees.py` 上留了一批存活体，
它们全落在既有判例**没有说出口**的地方：两个与外界打交道的默认 runner 的关键字后果
（`check` / `capture_output` / `text` 一动，rc 与 stdout 就从「答案」变成「没答案」）、
判决的三条**边界线**（`>= STALE_DAYS`、`< MIN_AGE_DAYS`、时间预算「到点」而不是「超点」）、
守卫里 `bare` 那一半、回执的**形状**（`swept_at` / `ok` / `truncated` / `branch_deleted`
是真 bool 而不是 None）、跨 root 的删除额度结算，以及 CLI 两形的渲染（列宽、`detached`、
JSON 的 `sort_keys` / `ensure_ascii`）。

每条判例说的是 §75.1 / §75.4 的一句法条，不是代码的镜像；照旧零子进程、零网络——
`git` / `du` 走注入缝，`subprocess.run` 只在两个默认 runner 的判例里被换成忠实于
`subprocess` 语义的 boundary stub（`check=True` + 非零 rc 会抛、没 capture 的 stdout
是 None、非 text 的 stdout 是 bytes），这样钉的是那三个参数的**后果**而不是调用形状。

刻意不追的等价体（无可观察差异，理由逐条记在这里，永不硬杀）：
`MAX_REMOVALS` / `GIT_TIMEOUT_S` / `DU_TIMEOUT_S` / `OUTPUT_CAP` / `ROWS_CAP` 的 ±1
——这五个是防腐 #4 要求的「有帽」，法条要的是帽存在，不是某一个数（`STALE_DAYS` /
`MIN_AGE_DAYS` 不同：§75.1 与 owner 原话逐字写死 14 与 2，下面钉住）；
`_count_positive` 里 `split()[0]` → `[-1]`——`git rev-list --count` 的输出只有一个
字段，首末逐字同值；`_judge_one` 末尾 `return False` → `return None`——它唯一的去处是
`truncated = _judge_one(...) or truncated`，`None` 与 `False` 在 `or` 左侧完全同效。
"""
import datetime as _dt
import io
import json
import os
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.worktree_testkit import FakeGit, Tree

from act.lib import config, worktrees
from act.lib.registry import Requirement

BRANCH = "ai/self-improve/R-900"


class _Completed:
    def __init__(self, rc, stdout):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = ""


class _FakeRun:
    """忠实于 `subprocess.run` 语义的 boundary stub：`check=True` 遇非零 rc 抛
    `CalledProcessError`、`capture_output=False` 时 `stdout is None`、`text=False` 时
    stdout 是 bytes。判例据此钉住 `default_git` / `default_du` 那三个关键字的后果。"""

    def __init__(self, rc=0, stdout="out\n"):
        self.rc = rc
        self.stdout = stdout

    def __call__(self, argv, **kwargs):
        out = None
        if kwargs.get("capture_output"):
            out = self.stdout if kwargs.get("text") else self.stdout.encode("utf-8")
        if kwargs.get("check") and self.rc != 0:
            raise subprocess.CalledProcessError(self.rc, argv, out)
        return _Completed(self.rc, out)


class _BareGit(FakeGit):
    """porcelain 的 `bare` 那一行——testkit 只发 `locked`，而守卫的第一条同时拦 bare。"""

    def _worktree_list(self, args, cwd):
        rc, out = FakeGit._worktree_list(self, args, cwd)
        if rc != 0:
            return rc, out
        for entry in self.entries:
            if entry.get("bare"):
                out = out.replace("worktree %s\n" % entry["path"],
                                  "worktree %s\nbare\n" % entry["path"], 1)
        return rc, out


def _card(sid="aaaa1111", branch=BRANCH):
    return Requirement(id="P-7", title="lane 卡", type="self-improvement", tier="T1",
                       status="review",
                       execution={"session_id": sid, "self_improve": {"branch": branch}})


class TreeCase(unittest.TestCase):
    """一棵假 repo + 一个假 git runner 的共用底座（目录是真的，内容是空的）。"""

    def setUp(self):
        self.tree = Tree()
        self.addCleanup(self.tree.cleanup)
        self.cfg = config.Config(raw={"self_improve": {"repo_path": self.tree.repo}})

    def _entry(self, name, branch="feat/x", age_days=30.0, **over):
        path = self.tree.add(name, age_days=age_days)
        row = {"path": path, "branch": branch, "head": "sha-" + name}
        row.update(over)
        return row

    def _sweep(self, git, **kw):
        return worktrees.sweep(self.cfg, git=git, reqs=[], live=kw.pop("live", set()), **kw)


class DefaultRunnerConsequencesTestCase(unittest.TestCase):
    """§75.1：两个与外界打交道的边**永不抛**，而且「答案」要原样带回来。"""

    def test_a_git_that_exits_non_zero_is_an_answer_not_a_missing_answer(self):
        # rc 非零是判决的输入（`is_dirty` 读不到就算脏）——把它变成 (None, "") 会让
        # 「问过了，答案是坏的」与「根本没问到」混成一件事。
        fake = _FakeRun(rc=1, stdout="?? junk\n")
        with mock.patch.object(subprocess, "run", fake):
            got = worktrees.default_git(["status", "--porcelain"], "/r")
        self.assertEqual(got, (1, "?? junk\n"))

    def test_a_du_that_complains_about_one_subdir_still_reports_the_total(self):
        # 真 du 读不动某个子目录时 rc=1 但总数照印；那份总数是 §75.4 的 `bytes`。
        fake = _FakeRun(rc=1, stdout="2048\t/root\n")
        with mock.patch.object(subprocess, "run", fake):
            self.assertEqual(worktrees.default_du("/root"), 2048 * 1024)


class SweepSwitchTestCase(unittest.TestCase):
    """§70.1：进程级总闸默认开，只有三个否定值关得掉它。"""

    def test_the_switch_is_on_unless_it_is_explicitly_turned_off(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(worktrees.SWEEP_ENV, None)
            self.assertIs(worktrees.sweep_enabled(), True)          # 没设 = 开
        with mock.patch.dict(os.environ, {worktrees.SWEEP_ENV: " 1 "}):
            self.assertIs(worktrees.sweep_enabled(), True)
        for off in ("0", "false", "no"):
            with mock.patch.dict(os.environ, {worktrees.SWEEP_ENV: off}):
                self.assertIs(worktrees.sweep_enabled(), False)


class ThresholdTestCase(unittest.TestCase):
    """§75.1：这两个数字是 owner 原话（issue #315）与法条正文，不是实现细节。"""

    def test_the_two_owner_stated_thresholds_are_the_ones_in_the_law(self):
        self.assertEqual(worktrees.STALE_DAYS, 14)      # 「mtime 超过 14 天且无未提交改动」
        self.assertEqual(worktrees.MIN_AGE_DAYS, 2)     # §75.1 年龄地板
        self.assertLess(worktrees.MIN_AGE_DAYS, worktrees.STALE_DAYS)


class PorcelainTestCase(TreeCase):
    """§75.1：登记表怎么读——第一条是主工作树，分支名逐字保留。"""

    def test_only_the_first_registration_is_the_main_worktree(self):
        git = FakeGit(self.tree.repo, [self._entry("a", branch="feat/a"),
                                       self._entry("b", branch="feat/b")])
        rows, error = worktrees.registered(git, self.tree.repo)
        self.assertIsNone(error)
        self.assertEqual([row["main"] for row in rows], [True, False, False])

    def test_a_branch_name_that_contains_the_ref_prefix_keeps_it_verbatim(self):
        # 只剥**第一个** `refs/heads/`：分支名里剩下的部分是名字的一部分，不许再剥
        rows = worktrees.parse_porcelain(
            "worktree /w\nHEAD " + "a" * 40 + "\nbranch refs/heads/refs/heads/odd\n")
        self.assertEqual([row["branch"] for row in rows], ["refs/heads/odd"])

    def test_only_the_first_line_of_the_dot_git_file_points_at_the_gitdir(self):
        # `.git` 是 `gitdir: <path>` 一行；读末行会把 gitdir 丢掉，于是「刚被 git 碰过」
        # 的 worktree 会显得 40 天没动过——正是 §75.1 那条「只看目录 mtime 是错的」。
        path = self.tree.add("noisy", age_days=40.0)
        gitdir = os.path.join(self.tree.repo, ".git", "worktrees", "noisy")
        os.makedirs(gitdir)
        open(os.path.join(gitdir, "index"), "w", encoding="utf-8").close()
        dotgit = os.path.join(path, ".git")
        with open(dotgit, "w", encoding="utf-8") as fh:
            fh.write("gitdir: %s\n# git 从不写第二行，但读法只认第一行\n" % gitdir)
        old = time.time() - 40 * 86400
        os.utime(dotgit, (old, old))
        os.utime(path, (old, old))
        self.assertGreater(worktrees.touched_at(path), time.time() - 60)


class VerdictBoundaryTestCase(TreeCase):
    """§75.1：三条线本身——过时线含端点、年龄地板不含端点、一个提交就算未推送。"""

    def test_the_stale_line_includes_the_day_it_is_reached(self):
        got = worktrees.candidate_reason("feat/x", float(worktrees.STALE_DAYS),
                                         set(), {"origin/feat/x"})
        self.assertEqual(got, "stale")

    def test_the_age_floor_stops_judging_below_it_and_lets_go_on_it(self):
        self.assertIsNone(worktrees.candidate_reason("feat/x", worktrees.MIN_AGE_DAYS - 0.1,
                                                     set(), set()))
        self.assertEqual(worktrees.candidate_reason("feat/x", float(worktrees.MIN_AGE_DAYS),
                                                    set(), set()), "gone")

    def test_a_single_local_only_commit_already_counts_as_unpushed(self):
        git = FakeGit(self.tree.repo, [], ahead={"sha-one": 1})
        self.assertTrue(worktrees.unpushed(git, self.tree.repo, "sha-one"))
        self.assertFalse(worktrees.unpushed(git, self.tree.repo, "sha-none"))

    def test_a_blank_origin_head_falls_through_to_the_named_remotes(self):
        # `symbolic-ref` rc=0 但一行空白（没有 origin/HEAD 时的形态之一）不是答案
        git = FakeGit(self.tree.repo, [], remotes=["origin/main"])
        git._worktree_list = lambda _a, _c: (0, "")
        git._symbolic_ref = lambda _a, _c: (0, "\n")
        self.assertEqual(worktrees.default_remote_ref(git, self.tree.repo, {"origin/main"}),
                         "origin/main")

    def test_an_origin_head_outside_the_fallback_list_is_still_the_default_branch(self):
        # 回落名单只有 main / master / dev；`origin/HEAD` 说了算这条不许被跳过
        git = FakeGit(self.tree.repo, [], default_ref="origin/trunk", remotes=["origin/trunk"])
        self.assertEqual(worktrees.default_remote_ref(git, self.tree.repo, {"origin/trunk"}),
                         "origin/trunk")


class SurveyShapeTestCase(TreeCase):
    """§75.4：清点一个 repo 的形状——年龄精度、守卫、预算、读不出登记表时的零。"""

    def test_the_age_is_reported_to_one_tenth_of_a_day(self):
        path = self.tree.add("aged", age_days=0.0)
        entry = {"path": path, "branch": "feat/aged", "head": "sha-aged"}
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main", "origin/feat/aged"])
        ref = worktrees.touched_at(path)
        got = worktrees.survey(self.tree.repo, git=git, live=set(), now=ref + 3.46 * 86400)
        self.assertEqual([row["age_days"] for row in got["rows"]], [3.5])

    def test_a_bare_worktree_under_the_managed_root_is_never_a_candidate(self):
        # 守卫第一条同时拦「主工作树」与「bare」——bare 里没有工作树可删
        path = self.tree.add("bare-one", age_days=40.0)
        git = _BareGit(self.tree.repo,
                       [{"path": path, "branch": "", "head": "sha-bare", "bare": True}],
                       remotes=["origin/main"])
        got = worktrees.survey(self.tree.repo, git=git, live=set())
        self.assertEqual((got["managed"], got["removable"]), (0, 0))
        self.assertEqual(got["rows"], [])

    def test_a_scan_that_finished_is_not_truncated(self):
        entry = self._entry("done", branch="feat/done")
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"])
        got = self._sweep(git)
        self.assertIs(got["truncated"], False)
        self.assertEqual(len(got["removed"]), 1)

    def test_the_time_budget_stops_the_moment_it_is_reached_not_after(self):
        # 冻结的钟：`clock() == deadline` 就该停——「超点才停」在一台慢机器上等于没有预算
        entry = self._entry("budgeted", branch="feat/budgeted")
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"])
        got = worktrees.survey(self.tree.repo, git=git, live=set(), budget_s=0.0,
                               clock=lambda: 100.0)
        self.assertEqual([row["reason"] for row in got["rows"]], ["budget"])
        self.assertTrue(got["truncated"])

    def test_a_git_that_cannot_list_reports_zeroes_not_guesses(self):
        git = FakeGit(self.tree.repo, [], fail={"worktree list"})
        got = worktrees.survey(self.tree.repo, git=git, live=set())
        self.assertEqual((got["registered"], got["managed"], got["removable"]), (0, 0, 0))
        self.assertIs(got["truncated"], False)
        self.assertEqual(got["rows"], [])
        self.assertIn("worktree list failed", got["error"])


class ExecutionShapeTestCase(TreeCase):
    """§75.1 / §75.4：执行那一段的顺序、失败行的形状、回执里的真 bool。"""

    def test_an_empty_registration_table_at_execution_time_leaves_prune_unfired(self):
        # 闸的输入没了就不许开枪：空清单里「有没有托管根外面的幽灵」恒为否（fail-closed）
        entry = self._entry("doomed", branch="feat/doomed")
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"])
        listing = git._worktree_list
        seen = []

        def once(args, cwd):
            seen.append(1)
            return listing(args, cwd) if len(seen) == 1 else (0, "")

        git._worktree_list = once
        got = self._sweep(git)
        self.assertEqual(got["pruned"][self.tree.repo], "skipped:unknown")
        self.assertEqual(git.pruned, 0)

    def test_a_worktree_git_refuses_to_remove_is_a_failure_row_in_gits_own_words(self):
        entry = self._entry("stubborn", branch="feat/stubborn")
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"])
        git._worktree_remove = lambda _a, _c: (1, "fatal: contains modified or untracked files")
        got = self._sweep(git)
        self.assertEqual(got["removed"], [])
        (failed,) = got["failed"]
        self.assertIs(failed["removed"], False)
        self.assertIs(failed["branch_deleted"], False)
        self.assertIn("worktree remove rc=1", failed["error"])
        self.assertIn("fatal: contains modified", failed["error"])
        self.assertEqual(git.branches_deleted, [])

    def test_the_error_tail_from_git_is_capped(self):
        entry = self._entry("chatty", branch="feat/chatty")
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"])
        git._worktree_remove = lambda _a, _c: (1, "x" * 5000)
        got = self._sweep(git)
        self.assertEqual(len(got["failed"][0]["error"]), 200)

    def test_a_detached_worktree_is_removed_and_says_no_branch_was_deleted(self):
        entry = self._entry("loose", branch="", age_days=worktrees.STALE_DAYS + 1)
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"])
        got = self._sweep(git)
        (row,) = got["removed"]
        self.assertEqual(row["reason"], "stale")
        self.assertIs(row["branch_deleted"], False)
        self.assertEqual(git.branches_deleted, [])

    def test_the_receipt_says_which_branch_git_actually_deleted(self):
        entry = self._entry("merged", branch=BRANCH, age_days=3.0)
        git = FakeGit(self.tree.repo, [entry], merged={BRANCH},
                      remotes=["origin/main", "origin/" + BRANCH])
        got = self._sweep(git)
        (row,) = got["removed"]
        self.assertIs(row["removed"], True)
        self.assertIs(row["branch_deleted"], True)

    def test_an_unpushed_worktree_goes_the_day_it_reaches_the_stale_line(self):
        # §75.1「unpushed 是延期不是否决」：门槛抬到 STALE_DAYS，到了那天照删
        entry = self._entry("ahead-exact", branch="a/ahead", age_days=worktrees.STALE_DAYS)
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"],
                      ahead={"sha-ahead-exact": 1})
        got = self._sweep(git)
        self.assertEqual(git.removed, [entry["path"]])
        self.assertEqual(got["removed"][0]["kept_branch"], "unpushed")

    def test_the_receipt_is_stamped_with_the_clock_it_was_swept_at(self):
        when = _dt.datetime(2026, 9, 15, 12, 0, tzinfo=_dt.timezone.utc)
        git = FakeGit(self.tree.repo, [], remotes=["origin/main"])
        got = self._sweep(git, now=when.timestamp())
        self.assertEqual(got["swept_at"], "2026-09-15T12:00:00Z")

    def test_a_sweep_with_nothing_to_do_is_ok_and_counts_zero(self):
        git = FakeGit(self.tree.repo, [], remotes=["origin/main"])
        got = self._sweep(git)
        self.assertIs(got["ok"], True)
        self.assertEqual((got["worktrees"], got["removable"]), (0, 0))
        self.assertEqual(got["removed"], [])

    def test_a_truncated_survey_shows_up_as_a_truncated_receipt(self):
        entry = self._entry("budgeted", branch="feat/budgeted")
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"])
        got = self._sweep(git, budget_s=0.0)
        self.assertIs(got["truncated"], True)
        self.assertEqual(got["skipped"].get("budget"), 1)

    def test_a_non_positive_removal_limit_removes_nothing(self):
        # `--limit 0` / 负数是「这一轮什么都别删」，不是「从末尾切一刀」
        entries = [self._entry("w%d" % i, branch="feat/w%d" % i) for i in range(2)]
        git = FakeGit(self.tree.repo, entries, remotes=["origin/main"])
        got = self._sweep(git, limit=-1)
        self.assertEqual(got["removed"], [])
        self.assertEqual(git.removed, [])
        self.assertEqual(got["skipped"].get("cap"), 2)

    def test_a_dry_run_row_says_out_loud_that_nothing_was_removed(self):
        entry = self._entry("doomed", branch="feat/doomed")
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"])
        got = self._sweep(git, dry_run=True)
        (row,) = got["removed"]
        self.assertIs(got["dry_run"], True)
        self.assertIs(row["removed"], False)
        self.assertIs(row["branch_deleted"], False)


class MultipleRootsTestCase(TreeCase):
    """§75.1：扫描根不止一个时，`MAX_REMOVALS` 是**全轮**的额度而不是每个 root 一份。"""

    def setUp(self):
        super().setUp()
        self.other = Tree()
        self.addCleanup(self.other.cleanup)

    def _two_root_git(self, mine, theirs):
        g1 = FakeGit(self.tree.repo, [mine], remotes=["origin/main"])
        g2 = FakeGit(self.other.repo, [theirs], remotes=["origin/main"])
        self.fakes = (g1, g2)

        def git(args, cwd):
            return g2(args, cwd) if str(cwd) == self.other.repo else g1(args, cwd)

        return git

    def test_the_removal_budget_is_shared_across_every_scan_root(self):
        mine = self._entry("mine", branch="feat/mine")
        theirs = {"path": self.other.add("theirs", age_days=30.0), "branch": "feat/theirs",
                  "head": "sha-theirs"}
        card = Requirement(id="P-9", title="别的 repo", status="detected",
                           target_repo=self.other.repo)
        got = worktrees.sweep(self.cfg, git=self._two_root_git(mine, theirs), reqs=[card],
                              live=set(), limit=1)
        self.assertEqual(got["removable"], 2)       # 两个 root 各有一个够格的
        self.assertEqual(len(got["removed"]), 1)    # 额度只有一个
        self.assertEqual(got["skipped"].get("cap"), 1)


class ReleaseTestCase(TreeCase):
    """§75.2 / §65.5：结算即释放只认「这张卡自己的」，失败只记账。"""

    def test_a_card_without_a_branch_releases_nothing_and_is_not_an_error(self):
        card = Requirement(id="P-8", title="没有分支的卡", status="review",
                           execution={"session_id": "aaaa1111"})
        git = FakeGit(self.tree.repo, [], remotes=["origin/main"])
        got = worktrees.release(card, self.cfg, git=git, resolve=lambda _s: None)
        self.assertEqual((got["removed"], got["skipped"], got["branch"]), ([], [], ""))
        self.assertIsNone(got["error"])

    def test_release_only_takes_the_worktree_whose_branch_matches_the_card(self):
        mine = self.tree.add("r900")
        theirs = self.tree.add("someone-else")
        git = FakeGit(self.tree.repo,
                      [{"path": mine, "branch": BRANCH, "head": "sha-mine"},
                       {"path": theirs, "branch": "feat/not-mine", "head": "sha-theirs"}])
        got = worktrees.release(_card(), self.cfg, git=git, resolve=lambda _s: None)
        self.assertEqual([row["path"] for row in got["removed"]], [mine])
        self.assertEqual(git.removed, [mine])

    def test_a_session_that_stayed_in_the_cards_own_worktree_is_not_removed_twice(self):
        mine = self.tree.add("r900")
        git = FakeGit(self.tree.repo, [{"path": mine, "branch": BRANCH, "head": "sha-mine"}])
        got = worktrees.release(_card(), self.cfg, git=git, resolve=lambda _s: Path(mine))
        self.assertEqual(len(got["removed"]), 1)
        self.assertEqual(git.removed, [mine])

    def test_a_blowing_up_release_is_capped_in_the_receipt_and_named_in_the_log(self):
        def boom(_args, _cwd):
            raise RuntimeError("x" * 5000)

        lines = []
        got = worktrees.release(_card(), self.cfg, git=boom, log=lines.append,
                                resolve=lambda _s: None)
        self.assertEqual(got["error"], "RuntimeError: " + "x" * 200)
        self.assertIn(" error=RuntimeError", lines[0])


class InventoryTestCase(TreeCase):
    """§75.4：清点的形状——不虚报 0、不假装干净、不假装完整。"""

    def test_skipping_the_measurement_never_starts_a_du(self):
        self._entry("counted", branch="feat/counted")
        git = FakeGit(self.tree.repo, [], remotes=["origin/main"])
        asked = []
        got = worktrees.inventory(self.cfg, git=git, reqs=[], live=set(), measure=False,
                                  du=lambda path: asked.append(path) or 4096)
        self.assertIsNone(got["bytes"])
        self.assertEqual(asked, [])

    def test_a_root_with_no_worktrees_at_all_is_not_a_partial_measurement(self):
        # `bytes_partial` 说的是「这里有东西但我量不到」；一条都没有的 root 不是半份答案
        git = FakeGit(self.tree.repo, [], remotes=["origin/main"])
        got = worktrees.inventory(self.cfg, git=git, reqs=[], live=set(), du=lambda _p: None)
        self.assertIsNone(got["bytes"])
        self.assertIs(got["bytes_partial"], False)

    def test_an_inventory_that_went_fine_says_ok_and_untruncated(self):
        entry = self._entry("fine", branch="feat/fine")
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"])
        got = worktrees.inventory(self.cfg, git=git, reqs=[], live=set(), du=lambda _p: 1024)
        self.assertIs(got["ok"], True)
        self.assertIs(got["truncated"], False)
        self.assertEqual(got["stale_days"], worktrees.STALE_DAYS)

    def test_one_broken_repo_makes_the_whole_inventory_not_ok(self):
        git = FakeGit(self.tree.repo, [], fail={"worktree list"})
        got = worktrees.inventory(self.cfg, git=git, reqs=[], live=set(), du=lambda _p: None)
        self.assertIs(got["ok"], False)

    def test_one_truncated_survey_makes_the_whole_inventory_truncated(self):
        entry = self._entry("budgeted", branch="feat/budgeted")
        git = FakeGit(self.tree.repo, [entry], remotes=["origin/main"])
        got = worktrees.inventory(self.cfg, git=git, reqs=[], live=set(), du=lambda _p: 1024,
                                  budget_s=0.0)
        self.assertIs(got["truncated"], True)


class CliRenderingTestCase(unittest.TestCase):
    """§75.4：CLI 两形的渲染——人读那形的列宽与 `detached`，机器那形的确定性。"""

    def setUp(self):
        self.buf = io.StringIO()
        patched = mock.patch.object(worktrees.sys, "stdout", self.buf)
        patched.start()
        self.addCleanup(patched.stop)

    def _run(self, argv, doc):
        with mock.patch.multiple(worktrees, inventory=mock.Mock(return_value=doc),
                                 sweep=mock.Mock()):
            worktrees.main(argv)
        return self.buf.getvalue()

    @staticmethod
    def _doc(rows):
        return {"ok": True, "worktrees": len(rows), "removable": 0, "bytes": None,
                "repos": [{"repo": "/r", "managed": len(rows), "error": None, "rows": rows}]}

    @staticmethod
    def _row(name, branch):
        return {"name": name, "branch": branch, "age_days": 1.0,
                "verdict": "keep", "reason": "active"}

    def test_the_branch_column_names_the_branch_and_only_a_detached_one_says_so(self):
        out = self._run([], self._doc([self._row("a", "feat/a"), self._row("b", "")]))
        self.assertIn("feat/a", out)
        self.assertIn("detached", out)

    def test_long_names_and_branches_are_cut_to_the_column_width(self):
        # 列宽是 `%-38s` / `%-30s`；切多一个字符就把整张表的列对齐撞歪
        name, branch = "w" * 50, "feat/" + "b" * 60
        out = self._run([], self._doc([self._row(name, branch)]))
        self.assertIn(name[:38], out)
        self.assertNotIn(name[:39], out)
        self.assertIn(branch[:30], out)
        self.assertNotIn(branch[:31], out)

    def test_a_document_without_counts_still_prints_zeroes(self):
        out = self._run(["--no-bytes"], {"repos": [], "bytes": None})
        self.assertIn("worktrees: 0（可清理 0）", out)

    def test_the_json_form_is_deterministic_and_keeps_its_characters(self):
        # server 的 GET 逐字读这一行；键序排过才 diff 得动，中文不许被转义成 \\uXXXX
        out = self._run(["--json"], {"zzz": "未知", "aaa": 1})
        self.assertIn("未知", out)
        self.assertLess(out.index('"aaa"'), out.index('"zzz"'))
        self.assertEqual(json.loads(out)["zzz"], "未知")


if __name__ == "__main__":
    unittest.main()
