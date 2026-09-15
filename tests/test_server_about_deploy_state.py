"""「一键更新」不许报假成功（CONTRACT §68.6 追记 2026-09-14 / §56.5 追记；issue #309）。

生产机的 checkout 常驻 `release` 分支，`scripts/auto-deploy.sh` 从 2026-09-05 起每 10 分钟
写一次 `refused_branch`（539 次），而 `POST /api/update/install` 照样 kickstart 完回
`{"ok": true}`——页面于是说「已触发自动部署——几分钟后这里的版本会变」，版本一动不动。

本判例钉三件事：
1. `GET /api/about` add-only 带 `deploy_state`（**请求时现读**，不是看板投影），老键一个不少；
2. 状态属于 `deploy_state.BLOCKING` → `install_now` 抛 409 `deploy_refused`（details 带
   `deploy_status` / `deploy_detail` / `fix`），假 runner 记录到的 `kickstart` 调用数为 0；
3. 其余状态照旧 kickstart，回执带上一轮的 `deploy_status` / `deploy_detail` /
   `deploy_failed_sha`（页面据此决定能不能承诺版本会变；sha 现读自文件，页面一进来拉的
   about 快照可能比它陈——中毒的那一轮可能是页面载入之后才倒下的）；`scripts/auto-deploy.sh`
   的五个 `status=failed` 写点里只有两个记得下 `failed_sha`（ff-only 失败的分叉 checkout
   写的就是没有 sha 的那一种），所以「没有 sha」必须是回执里说得出口的一种形状；reader 炸了
   当没有记录（宪法第 11 条：关于页不许 500）。
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import get_json, start_server

from act.lib import deploy_state
from server import about


def _runner(loaded=True):
    """假 launchctl：`print` 回 0 = agent 已加载；每条 argv 都记账。"""
    seen = []

    def run(argv):
        seen.append(argv)
        return (0 if loaded else 113), ""
    return run, seen


def _kickstarts(seen):
    return [argv for argv in seen if len(argv) > 1 and argv[1] == "kickstart"]


class AboutDeployStateTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-about-ds-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        user_home = Path(self.tmp.name) / "user"
        user_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(user_home), "USERPROFILE": str(user_home)})
        env.start()
        self.addCleanup(env.stop)
        _httpd, self.port = start_server(self, self.home)

    # ----- 1) about 的 add-only 字段 ----- #

    def test_snapshot_carries_the_state_and_keeps_every_old_key(self):
        state = {"status": "refused_branch", "version": "1.0.23+92",
                 "detail": "HEAD is on 'release', not main"}
        obj = about.snapshot(self.home, state_reader=lambda: dict(state))
        self.assertEqual(obj["deploy_state"], state)
        for key in ("version", "home", "repo", "update_available", "update_check", "check_enabled"):
            self.assertIn(key, obj)

    def test_snapshot_without_a_record_is_none_not_a_crash(self):
        self.assertIsNone(about.snapshot(self.home, state_reader=lambda: None)["deploy_state"])
        self.assertIsNone(about.snapshot(self.home, state_reader=lambda: {})["deploy_state"])
        self.assertIsNone(about.snapshot(self.home, state_reader=lambda: "junk")["deploy_state"])

        def boom():
            raise OSError("volume offline")
        self.assertIsNone(about.snapshot(self.home, state_reader=boom)["deploy_state"])

    def test_route_is_wired_and_reads_the_file_not_the_board_projection(self):
        # 看板投影里写着 deployed 也不算数——about 读的是 deploy_state 的 reader
        (self.home / "state" / "dashboard.json").write_text(
            '{"deploy_state": {"status": "deployed", "version": "9.9.9"}}', encoding="utf-8")
        status, obj = get_json(self.port, "/api/about")
        self.assertEqual(status, 200)
        self.assertIn("deploy_state", obj)
        self.assertIsNone(obj["deploy_state"])

    # ----- 2) BLOCKING → 409，零 kickstart ----- #

    def test_blocking_states_refuse_without_kickstarting(self):
        self.assertEqual(deploy_state.BLOCKING, frozenset({"refused_branch", "refused_dirty", "blocked_tcc"}))
        for status in sorted(deploy_state.BLOCKING):
            run, seen = _runner(loaded=True)
            with self.assertRaises(about.ConflictError) as ctx:
                about.install_now({}, runner=run, platform="darwin",
                                  state_reader=lambda s=status: {"status": s, "detail": "refusing: %s" % s})
            details = ctx.exception.details
            self.assertEqual(ctx.exception.status, 409)
            self.assertEqual(details["reason"], "deploy_refused")
            self.assertEqual(details["deploy_status"], status)
            self.assertEqual(details["deploy_detail"], "refusing: %s" % status)
            # 修法与 doctor 的 auto-deploy 行同一句（唯一真源 deploy_state.auto_deploy_fix）
            self.assertEqual(details["fix"], deploy_state.auto_deploy_fix(status))
            self.assertEqual(_kickstarts(seen), [], "%s must not fire a kickstart" % status)

    def test_not_loaded_still_wins_over_the_refusal(self):
        # agent 根本没加载 → 原生非 Sparkle 兜底（打开 release 页）仍是对的答案，不是 deploy_refused
        run, seen = _runner(loaded=False)
        with self.assertRaises(about.ConflictError) as ctx:
            about.install_now({}, runner=run, platform="darwin",
                              state_reader=lambda: {"status": "refused_branch", "detail": "x"})
        self.assertNotIn("reason", ctx.exception.details)
        self.assertEqual(_kickstarts(seen), [])

    # ----- 3) 其余状态照旧 kickstart，回执带上一轮的判决 ----- #

    def test_non_blocking_states_kickstart_and_carry_the_previous_verdict(self):
        # 第三个字段 = 上一轮记下的 failed_sha；中毒家族里它常常是空的（下一例点名为什么）
        rounds = (("deferred", "deploy of v1.0.99 (abc1234) deferred: 2 live sessions", ""),
                  ("ci_failed", "origin/main abc1234 failed CI", "abc1234def5678"),
                  ("failed", "git merge --ff-only abc1234 failed", ""),
                  ("up_to_date", "", ""))
        for status, detail, sha in rounds:
            run, seen = _runner(loaded=True)
            state = {"status": status, "detail": detail}
            if sha:
                state["failed_sha"] = sha
            receipt = about.install_now({}, runner=run, platform="darwin",
                                        state_reader=lambda st=state: dict(st))
            self.assertEqual(len(_kickstarts(seen)), 1, status)
            self.assertNotIn("-k", _kickstarts(seen)[0])
            self.assertTrue(receipt["ok"])
            self.assertEqual(receipt["action"], "kickstart")
            self.assertEqual(receipt["deploy_status"], status)
            self.assertEqual(receipt["deploy_detail"], detail)
            self.assertEqual(receipt["deploy_failed_sha"], sha, status)

    def test_a_poisoned_round_without_a_sha_is_a_shape_the_receipt_can_say(self):
        # `scripts/auto-deploy.sh` 写 `status=failed` 的五处里只有 poison_pairs 那两处附
        # failed_sha；分叉 checkout 的「fast-forward … impossible — refusing」（#309 那台
        # 机器在 AUTODEPLOY_BRANCH=release 下的落点）、卷探针、symbolic-ref 读不动、认不出
        # GitHub 远端这四处都不附。回执必须照实说空串，页面才不会拿「有没有 sha」当能不能
        # 承诺版本会变的判据——POISONED 整族都不许承诺。
        self.assertEqual(deploy_state.POISONED,
                         frozenset({"failed", "rolled_back", "rollback_failed", "ci_failed"}))
        run, _seen = _runner(loaded=True)
        receipt = about.install_now(
            {}, runner=run, platform="darwin",
            state_reader=lambda: {"status": "failed",
                                  "detail": "git merge --ff-only abc1234 failed"})
        self.assertEqual(receipt["deploy_status"], "failed")
        self.assertEqual(receipt["deploy_failed_sha"], "")

    def test_unreadable_state_is_not_a_refusal(self):
        def boom():
            raise ValueError("torn file")
        run, seen = _runner(loaded=True)
        receipt = about.install_now({}, runner=run, platform="darwin", state_reader=boom)
        self.assertEqual(len(_kickstarts(seen)), 1)
        self.assertEqual(receipt["deploy_status"], "")
        self.assertEqual(receipt["deploy_detail"], "")
        self.assertEqual(receipt["deploy_failed_sha"], "")


if __name__ == "__main__":
    unittest.main()
