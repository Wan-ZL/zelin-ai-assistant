"""§64 self_improve lane 的资格闸（policy.may_auto_dispatch 第二条 lane；§51）。

钉死的判据锚点：**写死的 sources channel + target_repo 的 realpath**。LLM 可写
字段（type / target_repo 单独 / cost / plan 文字）任何组合都开不了这条 lane
（§50 M1.d 教训）；混合来源两个方向都失格；lane 专属天花板（开关 / 暂停 /
needs_mcp / 仓库不符）逐 token；其余天花板（t2_confirm / outbound / repo:*）
对两条 lane 一视同仁；hand lane 行为逐字节不变（cost:unknown 仍在）。

纯函数测试：realpath / path_exists 全注入，不碰真文件系统（symlink 判例用
沙箱 TMP_HOME 里的真 symlink，只为证明默认 realpath 真能解开它）。
"""
import os
import tempfile
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, policy
from act.lib.config import Config
from act.lib.registry import Requirement, State

_SI = [{"who": "loop", "channel": "self_improve", "date": "2026-09-02",
        "ref": "proposal:abc", "quote": "让 doctor 多一行"}]
_HAND = [{"who": "zelin", "channel": "quick_capture", "date": "2026-09-02",
          "quote": "手打"}]
_SLACK = [{"who": "boss", "channel": "slack", "date": "2026-09-02", "quote": "x"}]

def _EXISTS(_p):
    return True


def _cfg(si=None, **attrs):
    raw = {"self_improve": si} if si is not None else {}
    cfg = Config(raw=raw)
    for k, v in attrs.items():
        setattr(cfg, k, v)
    return cfg


def _card(sources=_SI, **over):
    base = dict(id="P-7", title="lane 测试", type="self-improvement", tier="T1",
                status=State.CARD_SENT.value, sources=list(sources),
                target_repo=str(config.HOME), target_kind="existing",
                delivery_mode="repo")
    base.update(over)
    return Requirement(**base)


def _may(card, cfg=None, **kw):
    return policy.may_auto_dispatch(card, cfg or _cfg(), path_exists=_EXISTS, **kw)


class AdmissionKeyTestCase(unittest.TestCase):
    """开门的钥匙只有一把：channel=self_improve ∧ realpath(target_repo)=本仓库。"""

    def test_self_improve_card_targeting_this_repo_is_admitted_without_cost(self):
        ok, reason = _may(_card(cost_estimate_usd=None))
        self.assertEqual((ok, reason), (True, "ok:self_improve"))

    def test_type_self_improvement_alone_never_opens_the_lane(self):
        # analytics 渠道（digest 进化建议）+ type=self-improvement + 本仓库：
        # 这正是 LLM 可写字段的组合——照旧 origin:proposed 人批
        card = _card(sources=[{"channel": "analytics", "date": "2026-09-02"}],
                     type="self-improvement", cost_estimate_usd=1.0)
        self.assertEqual(_may(card), (False, "origin:proposed"))

    def test_target_repo_alone_never_opens_the_lane(self):
        card = _card(sources=_SLACK, target_repo=str(config.HOME), cost_estimate_usd=1.0)
        self.assertEqual(_may(card), (False, "origin:external"))

    def test_self_improve_channel_but_other_repo_is_repo_mismatch(self):
        card = _card(target_repo="/somewhere/else/repo")
        self.assertEqual(_may(card), (False, "self_improve:repo_mismatch"))

    def test_missing_target_repo_is_repo_mismatch_not_default_workbench(self):
        # hand lane 会回落 cfg.default_target_repo；lane 卡没有 target_repo =
        # 不知道落点 = 不放行（fail-closed），绝不借默认 workbench 搭便车
        card = _card(target_repo=None)
        cfg = _cfg(default_target_repo=str(config.HOME))
        self.assertEqual(_may(card, cfg), (False, "self_improve:repo_mismatch"))

    def test_configured_repo_path_wins_over_install_root(self):
        cfg = _cfg(si={"repo_path": "/opt/other-checkout"})
        self.assertEqual(_may(_card(), cfg), (False, "self_improve:repo_mismatch"))
        self.assertEqual(_may(_card(target_repo="/opt/other-checkout"), cfg),
                         (True, "ok:self_improve"))

    def test_realpath_resolves_symlinked_target(self):
        # ~/Projects/zelin-ai-assistant 是指向外置卷的 symlink（v0.48.2 事故）
        link = os.path.join(tempfile.mkdtemp(prefix="lane-link-"), "repo-link")
        os.symlink(str(config.HOME), link)
        self.addCleanup(lambda: os.unlink(link))
        self.assertEqual(_may(_card(target_repo=link)), (True, "ok:self_improve"))

    def test_injected_realpath_seam_is_honoured(self):
        rp = {"/a": "/same", "/b": "/same"}.get
        card = _card(target_repo="/a")
        cfg = _cfg(si={"repo_path": "/b"})
        self.assertEqual(_may(card, cfg, realpath=lambda p: rp(p, p)),
                         (True, "ok:self_improve"))

    def test_channel_match_is_case_and_space_insensitive_but_exact(self):
        ok, _ = _may(_card(sources=[{"channel": " Self_Improve ", "date": "d"}]))
        self.assertTrue(ok)
        self.assertEqual(_may(_card(sources=[{"channel": "self_improve_x", "date": "d"}])),
                         (False, "origin:external"))     # 未知渠道 fail-closed


class MixedSourcesTestCase(unittest.TestCase):
    """混合来源两个方向都关死：lane 卡被 fold 进别的渠道 / 别的卡 fold 进 lane 渠道。"""

    def test_self_improve_plus_hand_is_neither_lane(self):
        card = _card(sources=_SI + _HAND, cost_estimate_usd=1.0)
        self.assertEqual(_may(card), (False, "origin:proposed"))

    def test_self_improve_plus_slack_is_external(self):
        card = _card(sources=_SI + _SLACK, cost_estimate_usd=1.0)
        self.assertEqual(_may(card), (False, "origin:external"))

    def test_empty_sources_is_plain_proposed(self):
        self.assertEqual(_may(_card(sources=[], cost_estimate_usd=1.0)),
                         (False, "origin:proposed"))

    def test_malformed_sources_fail_closed(self):
        self.assertFalse(policy.is_self_improve_sources("self_improve"))
        self.assertFalse(policy.is_self_improve_sources([{"channel": "self_improve"}, "junk"]))
        self.assertFalse(policy.is_self_improve_sources([]))
        self.assertTrue(policy.is_self_improve_sources(_SI))


class LaneCeilingsTestCase(unittest.TestCase):
    def test_lane_disabled_is_routine_reason(self):
        cfg = _cfg(si={"enabled": False})
        self.assertEqual(_may(_card(), cfg), (False, "self_improve:disabled"))
        self.assertTrue(policy.is_routine_reason("self_improve:disabled"))

    def test_lane_paused_blocks_with_on_card_reason(self):
        self.assertEqual(_may(_card(), lane_paused=True), (False, "self_improve:paused"))
        self.assertFalse(policy.is_routine_reason("self_improve:paused"))

    def test_needs_mcp_only_via_owner_approval(self):
        self.assertEqual(_may(_card(needs_mcp=True)), (False, "self_improve:needs_mcp"))

    def test_shared_ceilings_still_apply_to_lane_cards(self):
        self.assertEqual(_may(_card(type="comms")), (False, "outbound"))
        self.assertEqual(_may(_card(tier="T2")), (False, "t2_confirm"))
        self.assertEqual(_may(_card(green_sign_required=True)), (False, "t2_confirm"))
        self.assertEqual(_may(_card(target_kind="new")), (False, "repo:new"))
        blocked = policy.may_auto_dispatch(_card(), _cfg(), path_exists=lambda _p: False)
        self.assertEqual(blocked, (False, "repo:missing"))

    def test_autodispatch_master_switch_still_wins(self):
        cfg = Config(raw={"autodispatch": {"enabled": False}})
        self.assertEqual(_may(_card(), cfg), (False, "disabled"))

    def test_order_disabled_before_paused_before_needs_mcp_before_repo(self):
        cfg = _cfg(si={"enabled": False})
        card = _card(needs_mcp=True, target_repo="/elsewhere")
        self.assertEqual(_may(card, cfg, lane_paused=True), (False, "self_improve:disabled"))
        self.assertEqual(_may(card, _cfg(), lane_paused=True), (False, "self_improve:paused"))
        self.assertEqual(_may(card, _cfg()), (False, "self_improve:needs_mcp"))


class HandLaneUnchangedTestCase(unittest.TestCase):
    def test_hand_card_without_cost_still_cost_unknown(self):
        card = _card(sources=_HAND, cost_estimate_usd=None)
        self.assertEqual(_may(card), (False, "cost:unknown"))

    def test_hand_card_ok_token_is_plain_ok(self):
        card = _card(sources=_HAND, cost_estimate_usd=2.0)
        self.assertEqual(_may(card), (True, "ok"))

    def test_hand_card_ignores_lane_pause(self):
        card = _card(sources=_HAND, cost_estimate_usd=2.0)
        self.assertEqual(_may(card, lane_paused=True), (True, "ok"))

    def test_routine_reasons_truth_table(self):
        for r in ("disabled", "origin:proposed", "origin:meeting", "origin:external",
                  "self_improve:disabled"):
            self.assertTrue(policy.is_routine_reason(r), r)
        for r in ("t2_confirm", "outbound", "repo:new", "cost:unknown",
                  "self_improve:paused", "self_improve:repo_mismatch",
                  "self_improve:needs_mcp", None, 3):
            self.assertFalse(policy.is_routine_reason(r), r)


class VocabularyTestCase(unittest.TestCase):
    def test_new_tokens_are_add_only_in_may_reasons(self):
        for tok in ("ok:self_improve", "self_improve:disabled", "self_improve:paused",
                    "self_improve:needs_mcp", "self_improve:repo_mismatch"):
            self.assertIn(tok, policy.MAY_REASONS)
        # 旧词表原样在前
        self.assertEqual(policy.MAY_REASONS[:11], (
            "ok", "disabled", "origin:proposed", "origin:meeting", "origin:external",
            "t2_confirm", "outbound", "repo:new", "repo:none", "repo:missing",
            "cost:unknown"))

    def test_channel_class_row_is_proposed(self):
        self.assertEqual(policy.CHANNEL_CLASS["self_improve"], policy.PROPOSED)
        self.assertEqual(policy.channel_class("self_improve"), policy.PROPOSED)
        self.assertEqual(policy.classify_origin(_SI), policy.PROPOSED)
        self.assertEqual(policy.SELF_IMPROVE_CHANNEL, "self_improve")

    def test_config_block_dirty_values_fall_back(self):
        si = policy.self_improve_config(Config(raw={"self_improve": {
            "enabled": 0, "repo_path": 42, "tick_minutes": "abc",
            "owner_logins": "not-a-list"}}))
        self.assertEqual(si, {"enabled": False, "repo_path": "", "tick_minutes": 60,
                              "owner_logins": [], "github_repo": ""})
        si = policy.self_improve_config(Config(raw={"self_improve": {
            "tick_minutes": "15", "owner_logins": ["a", " b ", "", 7]}}))
        self.assertEqual(si["tick_minutes"], 15)
        self.assertEqual(si["owner_logins"], ["a", "b", "7"])
        si = policy.self_improve_config(Config(raw={"self_improve": {"github_repo": " Wan-ZL/x "}}))
        self.assertEqual(si["github_repo"], "Wan-ZL/x")
        self.assertEqual(policy.self_improve_config(None), policy.SELF_IMPROVE_DEFAULTS)
        self.assertEqual(policy.self_improve_config({"self_improve": "junk"})["enabled"], True)

    def test_repo_path_defaults_to_install_root(self):
        self.assertEqual(policy.self_improve_repo_path(None), str(config.HOME))
        self.assertEqual(policy.self_improve_repo_path(_cfg(si={"repo_path": " /x "})), "/x")

    def test_same_repo_is_total(self):
        self.assertFalse(policy.same_repo(None, "/x"))
        self.assertFalse(policy.same_repo("/x", ""))
        self.assertFalse(policy.same_repo(3, 3))
        self.assertTrue(policy.same_repo("/x/", "/x", realpath=lambda p: p.rstrip("/")))


if __name__ == "__main__":
    unittest.main()
