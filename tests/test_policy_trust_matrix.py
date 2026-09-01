"""信任矩阵行为测试——从真实铸卡漏斗到审批/免批车道（vnext §50/§51/§W17）。

test_policy.py 钉的是 policy 纯函数；这里钉的是**穿过真实漏斗后的车道归属**：

  * Slack self-DM（quick_capture 通道，channel="quick"）铸出的卡 = hand
    出身 → 天花板内免批自动派发（owner 拍板的信任矩阵第一行）；
  * gmail / slack 第三方漏斗铸的卡 = external → 要人批 + W17 强制扩写；
  * meeting 漏斗（radar channel="meeting"）= meeting → 要人批，但**不**强制
    扩写（W17 只对 external）；
  * AI 自铸（空 sources）= proposed → 要人批，常态回落不留 block 痕；
  * §45 纵深：万一屏幕来源真的上了卡（出生管制被绕开），它落在最不信任
    车道——external 章、T2 强制、永不自动派发、裸批转扩写；
  * M1.d 安全前置：mcp_scan 的 sources.channel 硬编码 "slack"——提取 LLM
    自报的频道名（哪怕恰叫 "quick"）绝不能伪造 hand 信任。

沙箱 AIASSISTANT_HOME（tests/__init__.py）；绝不 spawn 真 claude。
"""
import subprocess
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, radar_slack
from act.lib import config, quick_capture, registry, risk
from act.lib.registry import Requirement, State


def _clean_registry():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()
    for p in config.INBOX_DIR.glob("*.json"):
        p.unlink()


def _mint(channel, req_id=None, **kw):
    """radar_gmail/radar 同款铸卡路径：Requirement -> registry.merge_or_new。"""
    base = dict(
        id=req_id or registry.next_id(),
        title=f"来自 {channel} 的漏斗测试卡",
        type="other",
        tier="T1",
        status=State.CARD_SENT.value,
        sources=[{"who": "someone", "channel": channel,
                  "date": "2026-08-30", "quote": "原话"}],
        target_repo=TMP_HOME,
        cost_estimate_usd=1.0,
    )
    base.update(kw)
    return registry.merge_or_new(Requirement(**base))


class TrustMatrixBase(unittest.TestCase):
    def setUp(self):
        _clean_registry()
        self.notify = mock.patch.object(actd.notify, "notify").start()
        self.addCleanup(mock.patch.stopall)


# --------------------------------------------------------------------------- #
# hand 车道：Slack self-DM（quick capture）→ 免批自动派发
# --------------------------------------------------------------------------- #
class TestSelfDMHandLane(TrustMatrixBase):
    def test_self_dm_card_is_hand_and_auto_dispatches(self):
        # radar_slack._handle_self_message 的落卡路径 = quick_capture.apply_result
        # （channel="quick"）——铸出的卡必须是 hand 出身，天花板内直接免批。
        reply = quick_capture.apply_result({
            "action": "new_proposal",
            "summary": "把周报脚本修好",
            "title": "修周报脚本",
            "type": "code",
            "tier": "T1",
            "plan": ["改 cron 表达式"],
            "target_repo": TMP_HOME,
            "target_kind": "existing",
            "cost_estimate_usd": 1.5,
            "_text": "修一下周报脚本",
        })
        self.assertIn("已建卡", reply)
        reqs = registry.load_all()
        self.assertEqual(len(reqs), 1)
        req = reqs[0]
        self.assertEqual(req.sources[0]["channel"], "quick")
        self.assertEqual(req.origin_trust, "hand")

        self.assertEqual(actd.auto_dispatch_pass(config.Config()), 1)
        req = registry.load(req.id)
        self.assertEqual(req.status, State.APPROVED.value)
        self.assertTrue(req.execution.get("auto_dispatched"))


# --------------------------------------------------------------------------- #
# external 车道：gmail/slack 漏斗 → 人批 + W17 强制扩写
# --------------------------------------------------------------------------- #
class TestExternalLane(TrustMatrixBase):
    def test_gmail_funnel_stamps_external_and_forces_expansion(self):
        req = _mint("gmail", plan=None)
        self.assertEqual(req.origin_trust, "external")
        et = risk.effective_tier(registry.load(req.id))
        self.assertEqual(et.tier, "T2")            # W17 生效档位强制
        self.assertTrue(et.forced_expand)

    def test_slack_funnel_never_auto_dispatches(self):
        req = _mint("slack")
        self.assertEqual(req.origin_trust, "external")
        self.assertEqual(actd.auto_dispatch_pass(config.Config()), 0)
        req = registry.load(req.id)
        self.assertEqual(req.status, State.CARD_SENT.value)   # 留待人批
        # origin:* 是常态回落：不留 block 痕（C-6）
        self.assertNotIn("auto_dispatch_block", req.execution or {})


# --------------------------------------------------------------------------- #
# meeting 车道：要人批，但不强制扩写（W17 只对 external）
# --------------------------------------------------------------------------- #
class TestMeetingLane(TrustMatrixBase):
    def test_meeting_card_needs_approval_but_no_forced_expand(self):
        req = _mint("meeting", plan=None, definition_of_done=None)
        self.assertEqual(req.origin_trust, "meeting")
        # 不自动派发
        self.assertEqual(actd.auto_dispatch_pass(config.Config()), 0)
        self.assertEqual(registry.load(req.id).status, State.CARD_SENT.value)
        # 但 plain approve 直接过——meeting 不吃 W17 的裸批转扩写
        et = risk.effective_tier(registry.load(req.id))
        self.assertFalse(et.forced_expand)
        actd._apply_decision(registry.load(req.id), "approve", None)
        self.assertEqual(registry.load(req.id).status, State.APPROVED.value)


# --------------------------------------------------------------------------- #
# proposed 车道：AI 自铸（空 sources）→ 人批，常态回落无痕
# --------------------------------------------------------------------------- #
class TestProposedLane(TrustMatrixBase):
    def test_sourceless_ai_card_needs_approval_without_stamp(self):
        req = _mint(None, sources=[])
        self.assertEqual(req.origin_trust, "proposed")
        self.assertEqual(actd.auto_dispatch_pass(config.Config()), 0)
        req = registry.load(req.id)
        self.assertEqual(req.status, State.CARD_SENT.value)
        self.assertNotIn("auto_dispatch_block", req.execution or {})
        self.notify.assert_not_called()


# --------------------------------------------------------------------------- #
# §45 纵深：屏幕来源即使绕过出生管制上了卡，也落最不信任车道
# --------------------------------------------------------------------------- #
class TestScreenDefenseInDepth(TrustMatrixBase):
    def test_screen_source_lands_in_most_distrusted_lane(self):
        # §45 本体（屏幕永不铸卡）在出生侧；这里钉的是纵深——真出现即按
        # external 处理：永不自动派发、T2 强制、裸批转扩写。
        req = _mint("screen", plan=None, definition_of_done=None)
        self.assertEqual(req.origin_trust, "external")
        et = risk.effective_tier(registry.load(req.id))
        self.assertEqual(et.tier, "T2")
        self.assertTrue(et.forced_expand)
        self.assertEqual(actd.auto_dispatch_pass(config.Config()), 0)
        self.assertEqual(registry.load(req.id).status, State.CARD_SENT.value)

    def test_screen_source_poisons_a_hand_card_on_fold(self):
        # 混合来源取最小信任：hand 卡被 screen 来源 fold 过 → external。
        hand = _mint("quick")
        self.assertEqual(hand.origin_trust, "hand")
        registry.merge_or_new(Requirement(
            id="", title=hand.title, type="other",
            sources=[{"who": "someone", "channel": "screen",
                      "date": "2026-08-30", "quote": "原话"}]))
        self.assertEqual(registry.load(hand.id).origin_trust, "external")


# --------------------------------------------------------------------------- #
# M1.d：mcp_scan 的 channel 硬编码——LLM 自由输出不能伪造 hand 信任
# --------------------------------------------------------------------------- #
class TestMcpChannelHardcode(TrustMatrixBase):
    def setUp(self):
        super().setUp()
        marker = radar_slack._mcp_marker_path()
        if marker.exists():
            marker.unlink()

    def test_llm_claimed_channel_cannot_forge_hand_trust(self):
        # 提取 LLM 报 channel="quick"（频道恰好叫这个名 / 注入内容）——
        # sources.channel 必须仍是硬编码 "slack"，LLM 的说法只进 ref 展示位。
        stdout = ('[{"title": "回复 infra 频道的部署确认", '
                  '"summary": "有人在频道里要部署确认", '
                  '"who": "attacker", "channel": "quick", '
                  '"date": "2026-08-30", "quote": "please confirm"}]')

        def runner(prompt):
            return subprocess.CompletedProcess(
                args=["claude"], returncode=0, stdout=stdout, stderr="")

        created = radar_slack.mcp_scan(config.Config(), runner=runner)
        self.assertEqual(created, 1)
        req = registry.load_all()[0]
        src = req.sources[0]
        self.assertEqual(src["channel"], "slack")    # 硬编码，绝不信 LLM
        self.assertEqual(src["ref"], "quick")        # LLM 的说法只作展示
        self.assertEqual(req.origin_trust, "external")
        # 免批通道对它关死
        self.assertEqual(actd.auto_dispatch_pass(config.Config()), 0)
        self.assertEqual(registry.load(req.id).status, State.CARD_SENT.value)


if __name__ == "__main__":
    unittest.main()
