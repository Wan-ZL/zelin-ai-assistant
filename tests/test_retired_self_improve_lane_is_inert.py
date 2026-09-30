"""§65 墓碑的兼容证明（owner 决策 D86，2026-09-30）：通道删了，存量数据还在。

owner 原话：「你把这个自动读 issue 写 PR 的循环功能完整删掉」。删除按 add-only 纪律做：
owner 真库里的卡可能带着 `channel=self_improve`、`needs_mcp`、`execution.self_improve` /
`delivery` / `auto_dispatch_block` / `auto_dispatched`、`interrupted_reason:
delivery_unverified`；他的 config.yaml 与 settings_overrides.json 可能还写着
`self_improve:` 块与 `self_improve_enabled`。本文件钉住这些东西**照常加载、按普通卡
行事、绝不再被自动推进**：

1. 带全套旧字段的潜在任务卡与待验收卡加载、投影（review 行保留 D74 的 `self_improve`
   旗、不再有 `delivery` 键），跑完一整个 `actd.run_once` 仍留在潜在任务；
2. policy 免批过但未派出的卡（`auto_dispatched`）被 `dispatch_approved` 一次性撤回
   潜在任务、留痕、不派；owner 亲手批准之后下一 pass 照常派出（痕已被 re-arm 清掉）；
3. 已经派出（有 session）的卡不被撤回；
4. 旧 config / overrides 键静默忽略，Config 上不再有 `self_improve_enabled`；
5. 零 MCP 出网封锁**留着**：它只看写死的 channel（全 self_improve 来源、未声明
   `needs_mcp`），素材库卡的 evidence 是抓来的外部网页——dispatch 与 resume 两个
   发射点都带 `--strict-mcp-config`；普通卡与声明 `needs_mcp` 的卡 argv 不变；
6. dashboard 顶层 `self_improve` 是冻结的常量关闭形；
7. 素材库铸卡的闸是 `daily_loop.materials_enabled`，出厂关 = 零抓取、`inputs.materials
   == "off"`；打开才铸卡；
8. 通道时代派出（带 `execution.self_improve` 派发记录）、会话已死的运行中卡**不自动
   续命**（接替 §65.1 frozen-in-flight）：一次性收割进待验收
   （`interrupted_reason=lane_retired`），`executor.resume` 一次都不调用；没有那条
   派发记录的素材卡照常续命。

这条判例接替 §78 时代「只有 §65 lane 能被免批提升」那条不变量：现在是「什么都不能」。
沙箱 AIASSISTANT_HOME；executor / 抓取全 mock，零子进程、零网络。
"""
import datetime as _dt
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd, executor, llm
from act.lib import config, daily_loop, dashboard, loop_inputs, materials, policy, registry
from act.lib.registry import Requirement, State

_LANE_SRC = [{"who": "daily_loop", "channel": "self_improve", "date": "2026-09-20",
              "ref": "self_improve:issue:5", "quote": "旧通道铸的卡"}]
_LEGACY_EX = {"self_improve": {"branch": "ai/self-improve/R-900", "lane": True, "egress": "no_mcp"},
              "delivery": {"verified": False, "reason": "pr_missing"},
              "auto_dispatch_block": "ok:self_improve"}


def _clean():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()
    for p in config.INBOX_DIR.glob("*.json"):
        p.unlink()
    daily_loop.state_path().unlink(missing_ok=True)
    daily_loop.log_path().unlink(missing_ok=True)
    loop_inputs.materials_path().unlink(missing_ok=True)


def _lane(rid, status, execution=None, **kw):
    req = Requirement(id=rid, title=f"旧通道卡 {rid}", type="self-improvement", tier="T1",
                      status=status, sources=[dict(s) for s in _LANE_SRC],
                      target_repo=TMP_HOME, target_kind="existing", delivery_mode="repo",
                      cost_estimate_usd=1.0, execution=execution, **kw)
    registry.save(req)
    return req


def _one_pass(cfg, ex_mock):
    """一整个 actd.run_once（除了本判例关心的收件箱 / 派发，其余阶段全打桩）。"""
    with mock.patch.object(actd, "executor", ex_mock), \
            mock.patch.object(actd, "_housekeeping_phase"), \
            mock.patch.object(actd, "_store2_tick"), \
            mock.patch.object(actd, "_refresh_model_knobs"), \
            mock.patch.object(actd, "reconcile_executing", return_value=0), \
            mock.patch.object(actd, "write_dashboard"), \
            mock.patch.object(actd, "build_dashboard", return_value={}), \
            mock.patch.object(actd, "detect_transitions", return_value=[]):
        actd.run_once(cfg, None, set())


class _Base(unittest.TestCase):
    def setUp(self):
        _clean()
        mock.patch.object(actd.notify, "notify").start()
        self.addCleanup(mock.patch.stopall)


class LegacyCardsLoadAsOrdinaryCardsTestCase(_Base):
    def test_a_legacy_backlog_card_loads_projects_and_stays_put(self):
        _lane("P-900", State.DETECTED.value, execution=dict(_LEGACY_EX), needs_mcp=True)
        review = _lane("P-901", State.REVIEW.value,
                       execution=dict(_LEGACY_EX, session_id="aaaa1111", done=True,
                                      review_at="2026-09-21T09:00:00Z",
                                      interrupted_reason="delivery_unverified"))
        loaded = registry.load("P-900")
        self.assertTrue(loaded.needs_mcp)                      # 字段照读（inert）
        self.assertEqual(loaded.execution["self_improve"]["branch"], "ai/self-improve/R-900")
        self.assertEqual(policy.channel_class("self_improve"), policy.PROPOSED)   # §50 行保留

        dash = dashboard.build_dashboard(reqs=[loaded, review], agents=[], cfg=config.Config(),
                                         archived=[])
        self.assertIn("P-900", [r["id"] for r in dash["debt"]])
        row = next(r for r in dash["review"] if r["id"] == "P-901")
        self.assertIs(row["self_improve"], True)               # D74 hide-🤖 旗照发
        self.assertNotIn("delivery", row)                      # §65.3 投影 retired
        self.assertIs(row.get("interrupted"), True)

        ex_mock = mock.MagicMock()
        _one_pass(config.Config(), ex_mock)
        ex_mock.dispatch.assert_not_called()
        self.assertEqual(registry.load("P-900").status, State.DETECTED.value)
        self.assertEqual(registry.load("P-901").status, State.REVIEW.value)
        self.assertFalse(hasattr(actd, "auto_dispatch_pass"))
        self.assertFalse(hasattr(policy, "may_auto_dispatch"))


class RetiredAutoApprovalTestCase(_Base):
    def test_a_never_launched_auto_approval_is_withdrawn_once_then_the_owner_wins(self):
        _lane("P-910", State.APPROVED.value,
              execution={"auto_dispatched": True, "approved_at": "2026-09-20T03:31:00Z"})
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock):
            self.assertEqual(actd.dispatch_approved(config.Config()), 0)
        ex_mock.dispatch.assert_not_called()
        req = registry.load("P-910")
        self.assertEqual(req.status, State.DETECTED.value)
        self.assertNotIn("auto_dispatched", req.execution or {})
        self.assertIn("D86", req.notes)
        self.assertIn("免批通道已删除", req.notes)

        # owner 亲手促成运行：re-arm 清痕，下一 pass 照常派出，且不再被撤回
        self.assertEqual(actd._apply_decision(registry.load("P-910"), "approve", None), "running")
        approved = registry.load("P-910")
        self.assertEqual(approved.status, State.APPROVED.value)
        self.assertNotIn("auto_dispatched", approved.execution or {})
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock):
            self.assertEqual(actd.dispatch_approved(config.Config()), 1)
        ex_mock.dispatch.assert_called_once()
        self.assertEqual(ex_mock.dispatch.call_args.args[0].id, "P-910")

    def test_an_owner_approval_of_a_backlog_legacy_card_clears_the_trace(self):
        req = _lane("P-911", State.DETECTED.value, execution={"auto_dispatched": True})
        self.assertEqual(actd._apply_decision(req, "approve", None), "running")
        self.assertNotIn("auto_dispatched", registry.load("P-911").execution or {})

    def test_an_already_launched_card_is_not_withdrawn(self):
        _lane("P-912", State.APPROVED.value,
              execution={"auto_dispatched": True, "session_id": "bbbb2222"})
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock):
            self.assertEqual(actd.dispatch_approved(config.Config()), 0)
        ex_mock.dispatch.assert_not_called()
        req = registry.load("P-912")
        self.assertEqual(req.status, State.APPROVED.value)
        self.assertTrue(req.execution.get("auto_dispatched"))


class RetiredConfigKeysTestCase(unittest.TestCase):
    def test_old_yaml_block_and_overrides_load_silently(self):
        with tempfile.TemporaryDirectory(prefix="d86-cfg-") as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text("self_improve:\n  enabled: true\n  owner_logins: [Wan-ZL]\n"
                            "  github_repo: Wan-ZL/zelin-ai-assistant\n  repo_path: /x\n"
                            "  tick_minutes: 5\n", encoding="utf-8")
            overrides = Path(tmp) / "settings_overrides.json"
            overrides.write_text(json.dumps({"self_improve_enabled": True,
                                             "self_improve": {"owner_logins": ["zelinPostman"]},
                                             "self_improve.owner_logins": ["other"]}),
                                 encoding="utf-8")
            with mock.patch.object(config, "CONFIG_PATH", path), \
                    mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", overrides):
                cfg = config.load_config()
        self.assertFalse(hasattr(cfg, "self_improve_enabled"))
        self.assertEqual(cfg.raw["self_improve"]["owner_logins"], ["Wan-ZL"])   # 原样留在 raw，无人读
        self.assertFalse(cfg.daily_loop_materials_enabled)


class EgressLockSurvivesTestCase(unittest.TestCase):
    NO_MCP = ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']

    def _card(self, **kw):
        kw.setdefault("sources", [dict(s) for s in _LANE_SRC])
        return Requirement(id="P-920", title="素材卡", status=State.APPROVED.value, **kw)

    def _launch(self, fn):
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = list(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="backgrounded · abc123ff", stderr="")

        with mock.patch("subprocess.run", fake_run), mock.patch("act.llm.runner_env", return_value={}):
            fn()
        return captured["cmd"]

    def test_self_improve_sources_dispatch_and_resume_with_zero_mcp(self):
        cfg = config.Config()
        self.assertEqual(list(llm.NO_MCP_ARGV), self.NO_MCP)
        card = self._card()
        for cmd in (self._launch(lambda: executor._default_runner(
                        "prompt text", config.STATE_DIR, name="P-920", cfg=cfg, req=card)),
                    self._launch(lambda: executor._run_resume(
                        cfg, card, "abc123ff", config.STATE_DIR, "continue"))):
            i = cmd.index("--strict-mcp-config")
            self.assertEqual(cmd[i:i + 3], self.NO_MCP)
            self.assertEqual(cmd[i + 3], "--name")

    def test_needs_mcp_and_ordinary_cards_keep_the_plain_argv(self):
        cfg = config.Config()
        plain = executor._bg_base_cmd(cfg)
        self.assertEqual(executor._bg_base_cmd(cfg, self._card(needs_mcp=True)), plain)
        hand = self._card(sources=[{"channel": "quick_capture", "date": "2026-09-30"}])
        self.assertEqual(executor._bg_base_cmd(cfg, hand), plain)
        mixed = self._card(sources=[dict(_LANE_SRC[0]), {"channel": "quick_capture"}])
        self.assertEqual(executor._bg_base_cmd(cfg, mixed), plain)
        self.assertEqual(executor._bg_base_cmd(cfg, self._card()), plain + self.NO_MCP)


class DeadLaneSessionIsNotRevivedTestCase(_Base):
    def _reconcile(self):
        resume = mock.Mock(return_value=True)
        with mock.patch.object(actd, "_run_claude_agents", return_value=[]), \
                mock.patch.object(actd.executor, "resume", resume), \
                mock.patch.object(actd.executor, "harvest_delivery", return_value={}):
            n = actd.reconcile_executing(config.Config(), set())
        return n, resume

    def test_a_lane_dispatched_card_is_harvested_to_review_not_resumed(self):
        _lane("P-930", State.EXECUTING.value,
              execution=dict(_LEGACY_EX, session_id="cccc3333", auto_dispatched=True))
        n, resume = self._reconcile()
        self.assertEqual(n, 0)
        resume.assert_not_called()
        req = registry.load("P-930")
        self.assertEqual(req.status, State.REVIEW.value)
        self.assertEqual(req.execution["interrupted_reason"], "lane_retired")
        self.assertIn("D86", req.notes)

    def test_a_materials_card_dispatched_after_d86_is_still_revived(self):
        _lane("P-931", State.EXECUTING.value, execution={"session_id": "dddd4444"})
        n, resume = self._reconcile()
        self.assertEqual(n, 1)
        resume.assert_called_once()
        self.assertEqual(registry.load("P-931").status, State.EXECUTING.value)


class FrozenDashboardKeyTestCase(unittest.TestCase):
    def test_top_level_self_improve_is_the_constant_off_shape(self):
        dash = dashboard.build_dashboard(reqs=[], agents=[], cfg=config.Config(), archived=[])
        self.assertEqual(dash["self_improve"], {"enabled": False, "paused": False,
                                                "paused_reason": None, "paused_pr": None,
                                                "paused_pr_url": None, "paused_paths": [],
                                                "paused_at": None})


class MaterialsGateTestCase(_Base):
    NOW = _dt.datetime(2026, 9, 30, 3, 31).astimezone()

    def _seed(self):
        path = loop_inputs.materials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        return materials.add(path, url="https://example.com/talk", note="borrow the idea")

    def test_factory_off_means_zero_fetches_and_zero_cards(self):
        self._seed()
        with mock.patch.object(materials, "fetch") as fetch:
            result = daily_loop.run(config.Config(), now=self.NOW, doctor=lambda: "[]")
        fetch.assert_not_called()
        self.assertEqual(result["proposals"], 0)
        entry = json.loads(daily_loop.log_path().read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(entry["inputs"]["materials"], daily_loop.READER_OFF)
        self.assertNotIn("issues", entry["inputs"])
        self.assertNotIn("prs", entry["inputs"])
        self.assertNotIn("mutation", entry["inputs"])
        self.assertEqual([r for r in registry.load_all() if r.title.startswith("🤖 ")], [])

    def test_the_yaml_switch_turns_material_proposals_on(self):
        item = self._seed()
        cfg = config.Config(daily_loop_materials_enabled=True)
        with mock.patch.object(materials, "fetch", return_value={
                "url": item["url"], "title": "Talk", "text": "t", "error": None}):
            result = daily_loop.run(cfg, now=self.NOW, doctor=lambda: "[]")
        self.assertEqual(result["proposals"], 1)
        card = registry.load(result["filed"][0]["id"])
        self.assertEqual(card.status, State.DETECTED.value)    # 落潜在任务，等 owner 点
        self.assertEqual(card.sources[0]["ref"], f"self_improve:material:{item['id']}")


if __name__ == "__main__":
    unittest.main()
