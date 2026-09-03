"""§64.3 草稿 PR 物理核验（self_improve.verify_delivery / on_harvest）。

「agent 说做完了不算，工具说 OK 才算」：每条核验路径一个判例——PR 不存在 /
关闭 / head 是 main / base 不是 main / 分支名不对 / 不是 draft / diff 为空 /
（跟进卡）没有新 push / gh 不可用；MERGED 视为已验收。on_harvest 只对
self_improve 卡动手：写 execution.delivery，未通过加 interrupted_reason 并发
精确通知；非 lane 卡 ex 零改动。gh 全部走假 runner。
"""
import os
import subprocess
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.self_improve_testkit import FakeGh, lane_card, pr_doc, unavailable_gh

from act.lib import config, notify, self_improve

BRANCH = "ai/self-improve/R-900"
FOLLOW_SRC = [{"who": "Wan-ZL", "channel": "self_improve", "date": "2026-09-02",
               "ref": "pr:123", "quote": "Where is the test?", "pr_number": 123,
               "pr_url": "https://github.com/o/r/pull/123", "head": "ai/self-improve/R-800",
               "head_sha": "old0000"}]


def _verify(gh, card=None):
    return self_improve.verify_delivery(card or lane_card(), None, gh=gh)


class FreshProposalVerifyTestCase(unittest.TestCase):
    def test_draft_pr_on_expected_branch_is_verified(self):
        gh = FakeGh({123: pr_doc(branch=BRANCH)})
        res = _verify(gh)
        self.assertTrue(res["verified"])
        self.assertIsNone(res["reason"])
        self.assertEqual(res["pr_number"], 123)
        self.assertEqual(res["pr_url"], "https://github.com/o/r/pull/123")
        self.assertTrue(res["pr_draft"])
        self.assertEqual(res["base"], "main")
        self.assertEqual(res["branch"], BRANCH)
        self.assertEqual(res["changed_files"], 1)
        self.assertEqual(res["sensitive_paths"], [])
        self.assertEqual(res["head_sha"], "deadbeef")
        self.assertTrue(res["checked_at"].endswith("Z"))
        # 查找 = 按分支列 PR，再按编号取全字段；cwd = 通道 repo
        self.assertEqual(gh.calls[0][:4], ["pr", "list", "--head", BRANCH])
        self.assertEqual(gh.calls[1][:3], ["pr", "view", "123"])
        self.assertTrue(all(c == str(config.HOME) for c in gh.cwds))

    def test_branch_prefix_uses_display_id(self):
        card = lane_card(work_id=None)             # legacy/无工作编号 → 主键
        self.assertEqual(self_improve.expected_branch(card), "ai/self-improve/P-7")
        self.assertEqual(self_improve.expected_branch(lane_card()), BRANCH)

    def test_missing_pr(self):
        self.assertEqual(_verify(FakeGh({}))["reason"], "pr_missing")

    def test_closed_pr(self):
        res = _verify(FakeGh({123: pr_doc(branch=BRANCH, state="CLOSED")}))
        self.assertEqual(res["reason"], "pr_closed")
        self.assertEqual(res["pr_state"], "CLOSED")

    def test_merged_pr_counts_as_accepted(self):
        res = _verify(FakeGh({123: pr_doc(branch=BRANCH, state="MERGED", draft=False)}))
        self.assertTrue(res["verified"])

    def test_head_main_is_refused(self):
        # 分支名对不上也先报 head 是 main——它是最结构性的越线
        gh = FakeGh({123: pr_doc(branch="main")})
        card = lane_card(sources=FOLLOW_SRC)
        gh.prs[123]["headRefName"] = "main"
        self.assertEqual(_verify(gh, card)["reason"], "pr_head_main")

    def test_base_not_main(self):
        res = _verify(FakeGh({123: pr_doc(branch=BRANCH, base="dev")}))
        self.assertEqual(res["reason"], "pr_base_not_main")

    def test_not_draft(self):
        res = _verify(FakeGh({123: pr_doc(branch=BRANCH, draft=False)}))
        self.assertEqual(res["reason"], "pr_not_draft")
        self.assertFalse(res["pr_draft"])

    def test_empty_diff(self):
        res = _verify(FakeGh({123: pr_doc(branch=BRANCH, files=())}))
        self.assertEqual(res["reason"], "pr_diff_empty")
        self.assertEqual(res["changed_files"], 0)

    def test_branch_mismatch_when_lookup_by_number_disagrees(self):
        # 跟进卡按编号查，PR 的 head 却不是卡记的分支
        card = lane_card(sources=FOLLOW_SRC)
        gh = FakeGh({123: pr_doc(branch="somebody/else", sha="new1111")})
        self.assertEqual(_verify(gh, card)["reason"], "pr_branch_mismatch")

    def test_open_pr_preferred_over_closed_on_same_branch(self):
        gh = FakeGh({120: pr_doc(120, branch=BRANCH, state="CLOSED"),
                     125: pr_doc(125, branch=BRANCH)})
        self.assertEqual(_verify(gh)["pr_number"], 125)
        gh = FakeGh({130: pr_doc(130, branch=BRANCH, state="CLOSED"),
                     121: pr_doc(121, branch=BRANCH, state="CLOSED")})
        self.assertEqual(_verify(gh)["pr_number"], 130)   # 都关了 → 编号最大

    def test_gh_unavailable_is_its_own_reason(self):
        res = _verify(unavailable_gh)
        self.assertEqual(res["reason"], "gh_unavailable")
        self.assertFalse(res["verified"])
        self.assertIsNone(res["pr_number"])

    def test_default_gh_honours_suite_guard(self):
        # tests/__init__.py 设 AIASSISTANT_GH=0 → 默认 runner 永不起子进程
        self.assertEqual(os.environ.get("AIASSISTANT_GH"), "0")
        self.assertEqual(self_improve.default_gh(["pr", "view", "1"], "/tmp"), (None, ""))
        self.assertFalse(self_improve.gh_available())

    def test_default_gh_when_enabled_spawns_gh_with_cwd(self):
        done = subprocess.CompletedProcess(["gh"], 0, stdout="[]", stderr="")
        with mock.patch.dict(os.environ, {"AIASSISTANT_GH": "1"}), \
                mock.patch.object(self_improve.shutil, "which", return_value="/usr/bin/gh"), \
                mock.patch.object(self_improve.subprocess, "run", return_value=done) as run:
            self.assertTrue(self_improve.gh_available())
            self.assertEqual(self_improve.default_gh(["pr", "list"], "/repo"), (0, "[]"))
        self.assertEqual(run.call_args.args[0], ["gh", "pr", "list"])
        self.assertEqual(run.call_args.kwargs["cwd"], "/repo")
        self.assertEqual(run.call_args.kwargs["timeout"], self_improve.GH_TIMEOUT_S)

    def test_default_gh_unavailable_shapes(self):
        # 起不来（OSError / 超时）与没装都报 rc=None——「坏掉的通道 ≠ 没有数据」
        with mock.patch.dict(os.environ, {"AIASSISTANT_GH": "1"}), \
                mock.patch.object(self_improve.shutil, "which", return_value="/usr/bin/gh"), \
                mock.patch.object(self_improve.subprocess, "run",
                                  side_effect=subprocess.TimeoutExpired("gh", 60)):
            self.assertEqual(self_improve.default_gh(["pr", "list"], "/repo"), (None, ""))
        with mock.patch.dict(os.environ, {"AIASSISTANT_GH": "1"}), \
                mock.patch.object(self_improve.shutil, "which", return_value=None):
            self.assertEqual(self_improve.default_gh(["pr", "list"], "/repo"), (None, ""))
        nonzero = subprocess.CompletedProcess(["gh"], 1, stdout=None, stderr="no pull requests")
        with mock.patch.dict(os.environ, {"AIASSISTANT_GH": "1"}), \
                mock.patch.object(self_improve.shutil, "which", return_value="/usr/bin/gh"), \
                mock.patch.object(self_improve.subprocess, "run", return_value=nonzero):
            self.assertEqual(self_improve.default_gh(["pr", "view", "9"], "/repo"), (1, ""))

    def test_gh_garbage_stdout_is_not_a_pr(self):
        def gh(args, cwd):
            return 0, "not json"
        self.assertEqual(_verify(gh)["reason"], "pr_missing")


class FollowupVerifyTestCase(unittest.TestCase):
    def test_new_push_on_pr_branch_verifies_even_if_not_draft(self):
        # owner 可能已把 PR 标成 ready；跟进卡不许把它撤回 draft，也不因此判失败
        card = lane_card(sources=FOLLOW_SRC)
        gh = FakeGh({123: pr_doc(branch="ai/self-improve/R-800", sha="new1111", draft=False)})
        res = _verify(gh, card)
        self.assertTrue(res["verified"])
        self.assertEqual(gh.calls[0][:3], ["pr", "view", "123"])   # 直接按编号

    def test_no_push_is_refused(self):
        card = lane_card(sources=FOLLOW_SRC)
        gh = FakeGh({123: pr_doc(branch="ai/self-improve/R-800", sha="old0000")})
        self.assertEqual(_verify(gh, card)["reason"], "pr_no_push")


class OnHarvestTestCase(unittest.TestCase):
    def setUp(self):
        self.notify = mock.patch.object(notify, "notify").start()
        self.addCleanup(mock.patch.stopall)
        lane = self_improve.lane_state_path()
        if lane.exists():
            lane.unlink()

    def test_non_lane_card_is_untouched(self):
        card = lane_card(sources=[{"channel": "quick_capture", "date": "d"}])
        ex = {"session_id": "x"}
        self.assertIsNone(self_improve.on_harvest(card, ex, gh=FakeGh({})))
        self.assertEqual(ex, {"session_id": "x"})
        self.notify.assert_not_called()

    def test_verified_delivery_is_recorded_without_interruption(self):
        ex = {"session_id": "x"}
        logs = []
        res = self_improve.on_harvest(lane_card(), ex, gh=FakeGh({123: pr_doc(branch=BRANCH)}),
                                      log=logs.append)
        self.assertTrue(res["verified"])
        self.assertEqual(ex["delivery"], res)
        self.assertNotIn("interrupted_reason", ex)
        self.notify.assert_not_called()
        self.assertTrue(any("verified=True" in line for line in logs))

    def test_unverified_delivery_marks_interrupted_and_notifies(self):
        ex = {"session_id": "x"}
        res = self_improve.on_harvest(lane_card(), ex, gh=FakeGh({}))
        self.assertFalse(res["verified"])
        self.assertEqual(ex["interrupted_reason"], "delivery_unverified")
        self.assertEqual(ex["delivery"]["reason"], "pr_missing")
        self.notify.assert_called_once()
        title, body = self.notify.call_args.args[:2]
        # 文案随 UI 语言（failures.pick）；两种语言都点名核验失败 + 原因 token
        self.assertTrue("未通过核验" in title or "failed verification" in title, title)
        self.assertIn("pr_missing", body)
        self.assertEqual(self.notify.call_args.kwargs.get("req"), "P-7")

    def test_gh_unavailable_parks_the_card_honestly(self):
        ex = {}
        res = self_improve.on_harvest(lane_card(), ex, gh=unavailable_gh)
        self.assertEqual(res["reason"], "gh_unavailable")
        self.assertEqual(ex["interrupted_reason"], "delivery_unverified")
        self.assertFalse(self_improve.lane_paused())   # 不可用 ≠ 敏感路径，不暂停


if __name__ == "__main__":
    unittest.main()
