"""§64.2 出网封锁的 argv 判例 + 派发侧接线（prompt 段 / execution 记录）。

钉死：self_improve 卡的四个发射点（dispatch / resume / rework / brief）argv
都带 ``--strict-mcp-config --mcp-config {"mcpServers":{}}``，位置紧跟模型旗标、
在 ``--name`` 之前；声明 needs_mcp 的卡与非 lane 卡 argv 逐字节不变（add-only
kwarg 默认关）；build_prompt 只对 self_improve 卡多一段 lane 契约（分支名 /
受保护路径 / 只准草稿 PR）；dispatch 成功后 execution.self_improve 记分支与
出网档。runner 全部注入或 mock subprocess.run——绝不 spawn 真 claude。
"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.self_improve_testkit import SI_SRC, lane_card

from act import executor, llm
from act.lib import config, registry, self_improve
from act.lib.registry import Requirement, State

NO_MCP = ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
FULL_SID = "aaaa1111-0000-4000-8000-000000000001"


def _proc(rc=0, stdout="backgrounded · aaaa1111"):
    return subprocess.CompletedProcess(["claude"], rc, stdout=stdout, stderr="")


class LlmBoundaryTestCase(unittest.TestCase):
    def test_no_mcp_argv_shape_and_position(self):
        cfg = config.Config()
        base = llm.dispatch_argv(cfg)
        self.assertEqual(llm.dispatch_argv(cfg, no_mcp=False), base)      # 默认逐字节不变
        self.assertEqual(llm.dispatch_argv(cfg, no_mcp=True), base + NO_MCP)
        self.assertEqual(list(llm.NO_MCP_ARGV), NO_MCP)

    def test_no_mcp_rides_after_the_model_flag(self):
        cfg = config.Config()
        cfg.models_dispatch = "claude-opus-5"
        argv = llm.dispatch_argv(cfg, no_mcp=True)
        self.assertEqual(argv[-5:], ["--model", "claude-opus-5"] + NO_MCP)

    def test_skip_permissions_off_still_appends_no_mcp(self):
        cfg = config.Config()
        cfg.skip_permissions = False
        argv = llm.dispatch_argv(cfg, no_mcp=True)
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertEqual(argv[-3:], NO_MCP)


class EgressLockTestCase(unittest.TestCase):
    def test_lane_card_locks_egress_unless_needs_mcp(self):
        self.assertTrue(self_improve.egress_locked(lane_card()))
        self.assertFalse(self_improve.egress_locked(lane_card(needs_mcp=True)))
        hand = lane_card(sources=[{"channel": "quick_capture", "date": "d"}])
        self.assertFalse(self_improve.egress_locked(hand))
        self.assertFalse(self_improve.egress_locked(None))

    def test_bg_base_cmd_per_card(self):
        cfg = config.Config()
        plain = executor._bg_base_cmd(cfg)
        self.assertEqual(executor._bg_base_cmd(cfg, None), plain)
        self.assertEqual(executor._bg_base_cmd(cfg, lane_card()), plain + NO_MCP)
        self.assertEqual(executor._bg_base_cmd(cfg, lane_card(needs_mcp=True)), plain)
        # 通道开关 / 仓库不符都不影响封锁：封锁只看写死的 channel（更严）
        other = lane_card(target_repo="/elsewhere")
        self.assertEqual(executor._bg_base_cmd(
            config.Config(raw={"self_improve": {"enabled": False}}), other), plain + NO_MCP)


class LaunchSitesTestCase(unittest.TestCase):
    """四个发射点各抓一次真实 argv（subprocess.run mock）。"""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()
        self.cfg.memory_inject = False
        self.wt = Path(tempfile.mkdtemp(prefix="lane-wt-"))
        for patcher in (
            mock.patch.object(executor, "has_remote", return_value=True),
            mock.patch.object(executor.notify, "notify", mock.Mock(return_value=True)),
            mock.patch.object(executor, "_agent_info", return_value={}),
            mock.patch.object(executor, "_agent_info_strict", return_value=None),
            mock.patch.object(executor, "stop_session", return_value=True),
            mock.patch.object(executor, "_transcript_info",
                              side_effect=lambda sid: (FULL_SID, self.wt)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _capture(self, fn):
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = list(cmd)
            return _proc()

        with mock.patch.object(executor.subprocess, "run", fake_run):
            fn()
        return captured["cmd"]

    def _assert_locked_before_name(self, cmd):
        i = cmd.index("--strict-mcp-config")
        self.assertEqual(cmd[i:i + 3], NO_MCP)
        self.assertEqual(cmd[i + 3], "--name")

    def test_dispatch_argv(self):
        req = lane_card(status=State.APPROVED.value, execution=None,
                        target_repo=str(self.wt))
        (self.wt / "keep").write_text("x", encoding="utf-8")
        registry.save(req)
        cmd = self._capture(lambda: executor.dispatch(req, self.cfg))
        self._assert_locked_before_name(cmd)
        self.assertEqual(cmd[-3], "--name")                 # [..., --name, <name>, <prompt>]
        # 派发记录：分支 / 出网档 / lane 归属（target_repo=沙箱 wt ≠ 安装根 → lane False）
        saved = registry.load("P-7")
        self.assertEqual(saved.status, State.EXECUTING.value)
        self.assertEqual(saved.execution["self_improve"],
                         {"branch": "ai/self-improve/R-900", "egress": "none", "lane": False})
        self.assertIn("SELF-IMPROVE LANE", cmd[-1])

    def test_resume_argv(self):
        req = lane_card(execution={"session_id": "aaaa1111"})
        registry.save(req)
        cmd = self._capture(lambda: executor.resume(req, self.cfg))
        self._assert_locked_before_name(cmd)
        self.assertIn("--resume", cmd)

    def test_rework_argv(self):
        req = lane_card(status=State.REVIEW.value,
                        execution={"session_id": "aaaa1111", "done": True})
        registry.save(req)
        cmd = self._capture(lambda: executor.rework(req, "补个测试", self.cfg))
        self._assert_locked_before_name(cmd)

    def test_brief_argv(self):
        req = lane_card(execution={"session_id": "aaaa1111",
                                   "pending_briefings": [{"text": "hi", "ts": "t"}]})
        registry.save(req)
        cmd = self._capture(lambda: executor.brief(req, self.cfg))
        self._assert_locked_before_name(cmd)

    def test_hand_card_argv_has_no_mcp_flags(self):
        req = Requirement(id="R-1", title="手打", status=State.APPROVED.value,
                          sources=[{"channel": "quick_capture", "date": "d"}],
                          target_repo=str(self.wt))
        (self.wt / "keep").write_text("x", encoding="utf-8")
        registry.save(req)
        cmd = self._capture(lambda: executor.dispatch(req, self.cfg))
        self.assertNotIn("--strict-mcp-config", cmd)
        self.assertNotIn("--mcp-config", cmd)
        self.assertNotIn("self_improve", registry.load("R-1").execution)
        self.assertNotIn("SELF-IMPROVE LANE", cmd[-1])

    def test_needs_mcp_card_keeps_mcp(self):
        req = lane_card(execution={"session_id": "aaaa1111"}, needs_mcp=True)
        registry.save(req)
        cmd = self._capture(lambda: executor.resume(req, self.cfg))
        self.assertNotIn("--strict-mcp-config", cmd)


class PromptBlockTestCase(unittest.TestCase):
    def setUp(self):
        self.cfg = config.Config()
        self.cfg.memory_inject = False
        mock.patch.object(executor, "has_remote", return_value=True).start()
        self.addCleanup(mock.patch.stopall)

    def test_lane_block_present_for_lane_card_only(self):
        prompt = executor.build_prompt(lane_card(), self.cfg, target=Path(TMP_HOME))
        self.assertIn("## SELF-IMPROVE LANE", prompt)
        self.assertIn("Branch: create `ai/self-improve/R-900`", prompt)
        self.assertIn("gh pr create --draft --base main", prompt)
        for path in self_improve.SENSITIVE_PATHS:
            self.assertIn(path, prompt)
        self.assertIn("never post PR comments", prompt)
        self.assertIn("Slack/Gmail MCP servers are not available", prompt)
        # 原有质量门句仍在（lane 段是叠加不是替换）
        self.assertIn("Do NOT push to main", prompt)
        hand = lane_card(sources=[{"channel": "quick_capture", "date": "d", "quote": "x"}])
        self.assertNotIn("SELF-IMPROVE LANE", executor.build_prompt(hand, self.cfg,
                                                                   target=Path(TMP_HOME)))

    def test_followup_block_points_at_the_pr_branch(self):
        src = dict(SI_SRC[0], pr_number=123, head="ai/self-improve/R-800", head_sha="s")
        prompt = executor.build_prompt(lane_card(sources=[src]), self.cfg, target=Path(TMP_HOME))
        self.assertIn("Follow-up on PR #123", prompt)
        self.assertIn("`ai/self-improve/R-800`", prompt)
        self.assertIn("Do not open a new PR", prompt)
        self.assertIn("BRANCH: ai/self-improve/R-800", prompt)

    def test_dispatch_record_shapes(self):
        self.assertEqual(self_improve.dispatch_record(
            lane_card(sources=[{"channel": "slack", "date": "d"}]), self.cfg), {})
        rec = self_improve.dispatch_record(lane_card(needs_mcp=True), self.cfg)
        self.assertEqual(rec["self_improve"]["egress"], "mcp")
        self.assertTrue(rec["self_improve"]["lane"])       # target_repo = 安装根


if __name__ == "__main__":
    unittest.main()
