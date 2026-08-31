"""act/lib/policy 的行为测试 — 信任矩阵 + 自动派发天花板 + 排队原因.

纯函数模块：无 I/O、无 registry 写入。repo 存在性经 path_exists seam 注入，
测试绝不碰真文件系统（CONTRIBUTING 测试纪律）。
"""
import unittest

from act.lib import policy
from act.lib.config import Config
from act.lib.registry import Requirement


def _cfg(auto=None, **attrs):
    cfg = Config(raw={"autodispatch": auto} if auto is not None else {})
    for k, v in attrs.items():
        setattr(cfg, k, v)
    return cfg


def _hand_card(**over):
    """默认能过全部天花板的手打卡；over 用于逐项打破。"""
    fields = dict(
        id="R-900", title="hand card", type="other", tier="T1",
        status="card_sent", cost_estimate_usd=2.0,
        target_repo="~/Projects/exists", target_kind="existing",
        sources=[{"who": "zelin", "channel": "quick", "quote": "do x"}],
    )
    fields.update(over)
    return Requirement(**fields)


class TestClassifyOrigin(unittest.TestCase):
    def test_hand_channels(self):
        for chan in ("quick", "quick_capture", " Quick "):
            self.assertEqual(
                policy.classify_origin([{"channel": chan}]), policy.HAND)

    def test_proposed_channels(self):
        for chan in ("analytics", "claude_code", "split",
                     "radar-diagnostic", "radar-parse-degraded",
                     # digest/weekly-digest 是 AI 自提建议卡的生产端 channel
                     # （act/digest.py / act/weekly_digest.py SOURCE_CHANNEL）——
                     # 必须 PROPOSED，绝不 fail-closed 成 external（否则 W17 从
                     # sources 现算后会把存量 digest 卡错抬 T2+强制扩写，MAJOR-2）
                     "digest", "weekly-digest"):
            self.assertEqual(
                policy.classify_origin([{"channel": chan}]), policy.PROPOSED)

    def test_digest_sources_do_not_force_expansion(self):
        # 端到端钉死 MAJOR-2：digest 出身卡的 effective_tier 保持声明档，
        # 不强制扩写（risk 从 sources 现算，digest 判 proposed 而非 external）
        from act.lib import risk
        for chan in ("digest", "weekly-digest"):
            et = risk.effective_tier(
                {"tier": "T1", "sources": [{"channel": chan}]})
            self.assertEqual(et.tier, "T1", chan)
            self.assertFalse(et.forced_expand, chan)

    def test_meeting_channels(self):
        for chan in ("meeting", "audio"):
            self.assertEqual(
                policy.classify_origin([{"channel": chan}]), policy.MEETING)

    def test_external_channels(self):
        for chan in ("slack", "gmail", "screen"):
            self.assertEqual(
                policy.classify_origin([{"channel": chan}]), policy.EXTERNAL)

    def test_unknown_channel_fails_closed(self):
        # 未知/畸形 channel 一律 external（executor 白名单同款纪律）
        for chan in ("carrier-pigeon", "", None, 42, ["quick"]):
            self.assertEqual(
                policy.classify_origin([{"channel": chan}]), policy.EXTERNAL)

    def test_no_sources_is_proposed(self):
        # 无来源 = AI 自铸卡（digest 建议形态）
        self.assertEqual(policy.classify_origin([]), policy.PROPOSED)
        self.assertEqual(policy.classify_origin(None), policy.PROPOSED)

    def test_malformed_sources_fail_closed(self):
        self.assertEqual(policy.classify_origin("quick"), policy.EXTERNAL)
        self.assertEqual(policy.classify_origin([["quick"]]), policy.EXTERNAL)
        self.assertEqual(policy.classify_origin([{}]), policy.EXTERNAL)

    def test_mixed_sources_least_trust_wins(self):
        # 手打卡被外部渠道 fold 过 -> 按外部处理
        self.assertEqual(
            policy.classify_origin([{"channel": "quick"},
                                    {"channel": "slack"}]),
            policy.EXTERNAL)
        self.assertEqual(
            policy.classify_origin([{"channel": "quick"},
                                    {"channel": "meeting"}]),
            policy.MEETING)
        self.assertEqual(
            policy.classify_origin([{"channel": "quick"},
                                    {"channel": "analytics"}]),
            policy.PROPOSED)

    def test_capture_channel_joins_aggregation(self):
        self.assertEqual(policy.classify_origin([], "quick"), policy.HAND)
        self.assertEqual(
            policy.classify_origin([{"channel": "quick"}], "gmail"),
            policy.EXTERNAL)

    def test_table_totality(self):
        # provenance.py 式完备性：表值都在域内，rank 覆盖全部 class
        for chan, cls in policy.CHANNEL_CLASS.items():
            self.assertIn(cls, policy.ORIGINS, chan)
        self.assertEqual(set(policy._TRUST_RANK), set(policy.ORIGINS))


class TestNormalizeOrigin(unittest.TestCase):
    def test_known_values_pass_garbage_fails_closed(self):
        for o in policy.ORIGINS:
            self.assertEqual(policy.normalize_origin(o), o)
        self.assertEqual(policy.normalize_origin(" Hand "), policy.HAND)
        for junk in ("banana", None, 3, ["hand"]):
            self.assertEqual(policy.normalize_origin(junk), policy.EXTERNAL)


class TestAutodispatchConfig(unittest.TestCase):
    def test_defaults(self):
        for cfg in (None, Config(raw={}), {}):
            self.assertEqual(policy.autodispatch_config(cfg),
                             policy.AUTODISPATCH_DEFAULTS)

    def test_explicit_values(self):
        got = policy.autodispatch_config(_cfg(auto={
            "enabled": False, "daily_budget_usd": 10,
            "max_concurrent": 1, "notify": False}))
        self.assertEqual(got, {"enabled": False, "daily_budget_usd": 10.0,
                               "max_concurrent": 1, "notify": False})

    def test_garbage_values_fall_back_per_key(self):
        got = policy.autodispatch_config(_cfg(auto={
            "daily_budget_usd": "cheap", "max_concurrent": 0,
            "enabled": 1}))
        self.assertEqual(got["daily_budget_usd"], 5.0)
        self.assertEqual(got["max_concurrent"], 3)
        self.assertTrue(got["enabled"])

    def test_bare_dict_cfg(self):
        got = policy.autodispatch_config(
            {"autodispatch": {"daily_budget_usd": 2}})
        self.assertEqual(got["daily_budget_usd"], 2.0)


class TestMayAutoDispatch(unittest.TestCase):
    def setUp(self):
        self.cfg = _cfg()
        self.exists = lambda p: True

    def _may(self, card, cfg=None, spend=0.0, exists=None):
        return policy.may_auto_dispatch(
            card, cfg if cfg is not None else self.cfg, spend,
            path_exists=exists if exists is not None else self.exists)

    def test_happy_path(self):
        self.assertEqual(self._may(_hand_card()), (True, "ok"))

    def test_disabled(self):
        cfg = _cfg(auto={"enabled": False})
        self.assertEqual(self._may(_hand_card(), cfg=cfg),
                         (False, "disabled"))

    def test_non_hand_origins_denied(self):
        cases = [("slack", "origin:external"), ("gmail", "origin:external"),
                 ("meeting", "origin:meeting"),
                 ("analytics", "origin:proposed")]
        for chan, reason in cases:
            card = _hand_card(sources=[{"channel": chan}])
            self.assertEqual(self._may(card), (False, reason), chan)

    def test_hand_folded_with_external_denied(self):
        card = _hand_card(sources=[{"channel": "quick"},
                                   {"channel": "slack"}])
        self.assertEqual(self._may(card), (False, "origin:external"))

    def test_t2_semantics_survive(self):
        self.assertEqual(self._may(_hand_card(tier="T2")),
                         (False, "t2_confirm"))
        self.assertEqual(self._may(_hand_card(green_sign_required=True)),
                         (False, "t2_confirm"))
        # 估价高过文字确认线（cfg 默认 50）一样按 T2 拦
        cfg = _cfg(require_text_confirm_above_usd=1.0)
        self.assertEqual(self._may(_hand_card(), cfg=cfg),
                         (False, "t2_confirm"))

    def test_outbound_denied(self):
        self.assertEqual(self._may(_hand_card(type="comms")),
                         (False, "outbound"))

    def test_repo_ceilings(self):
        self.assertEqual(self._may(_hand_card(target_kind="new")),
                         (False, "repo:new"))
        cfg = _cfg(default_target_repo="")
        self.assertEqual(self._may(_hand_card(target_repo=None), cfg=cfg),
                         (False, "repo:none"))
        self.assertEqual(self._may(_hand_card(), exists=lambda p: False),
                         (False, "repo:missing"))

    def test_repo_falls_back_to_default_target_repo(self):
        seen = []
        cfg = _cfg(default_target_repo="~/Projects/workbench")
        ok, reason = policy.may_auto_dispatch(
            _hand_card(target_repo=None), cfg, 0.0,
            path_exists=lambda p: seen.append(p) or True)
        self.assertEqual((ok, reason), (True, "ok"))
        self.assertTrue(seen and seen[0].endswith("Projects/workbench"))

    def test_cost_ceilings(self):
        self.assertEqual(self._may(_hand_card(cost_estimate_usd=None)),
                         (False, "cost:unknown"))
        self.assertEqual(self._may(_hand_card(cost_estimate_usd=7.0)),
                         (False, "cost:over_ceiling"))
        # 边界：正好 == 预算 放行
        self.assertEqual(self._may(_hand_card(cost_estimate_usd=5.0)),
                         (True, "ok"))

    def test_budget_ceilings(self):
        self.assertEqual(self._may(_hand_card(), spend=4.0),
                         (False, "budget:exhausted"))
        self.assertEqual(self._may(_hand_card(), spend=3.0), (True, "ok"))
        self.assertEqual(self._may(_hand_card(), spend="garbage"),
                         (False, "budget:unknown"))
        # 负 spend（台账异常）不放大预算
        self.assertEqual(self._may(_hand_card(cost_estimate_usd=5.0),
                                   spend=-100.0), (True, "ok"))

    def test_dict_card_accepted(self):
        card = {"tier": "T1", "type": "other", "cost_estimate_usd": 1,
                "target_repo": "~/x", "target_kind": "existing",
                "sources": [{"channel": "quick"}]}
        self.assertEqual(self._may(card), (True, "ok"))

    def test_reason_tokens_in_vocabulary(self):
        cards = [
            _hand_card(), _hand_card(tier="T2"), _hand_card(type="comms"),
            _hand_card(target_kind="new"), _hand_card(cost_estimate_usd=None),
            _hand_card(cost_estimate_usd=99),
            _hand_card(sources=[{"channel": "slack"}]),
        ]
        for card in cards:
            _ok, reason = self._may(card)
            self.assertIn(reason, policy.MAY_REASONS)


class TestQueuedReason(unittest.TestCase):
    def test_vocabulary_and_none(self):
        self.assertIsNone(policy.queued_reason(_hand_card(), {}))
        self.assertIsNone(policy.queued_reason(_hand_card(), None))

    def test_dependency(self):
        self.assertEqual(
            policy.queued_reason(_hand_card(), {"blocked_by": ["R-001"]}),
            "dependency")

    def test_budget(self):
        st = {"today_spend": 4.0, "daily_budget_usd": 5.0}
        self.assertEqual(
            policy.queued_reason(_hand_card(cost_estimate_usd=2.0), st),
            "budget")
        # 无估价卡按 0 计——不超预算就不报 budget
        self.assertIsNone(
            policy.queued_reason(_hand_card(cost_estimate_usd=None), st))

    def test_concurrency(self):
        st = {"running": 3, "max_concurrent": 3}
        self.assertEqual(policy.queued_reason(_hand_card(), st),
                         "concurrency")
        self.assertIsNone(policy.queued_reason(
            _hand_card(), {"running": 2, "max_concurrent": 3}))

    def test_precedence_dependency_budget_concurrency(self):
        st = {"blocked_by": "R-001", "today_spend": 9, "daily_budget_usd": 5,
              "running": 3, "max_concurrent": 3}
        self.assertEqual(policy.queued_reason(_hand_card(), st), "dependency")
        st.pop("blocked_by")
        self.assertEqual(policy.queued_reason(_hand_card(), st), "budget")
        st.pop("today_spend")
        self.assertEqual(policy.queued_reason(_hand_card(), st),
                         "concurrency")

    def test_missing_keys_skip_checks(self):
        # 只有一半预算键 / 垃圾值 -> 该检查跳过，绝不 raise
        self.assertIsNone(policy.queued_reason(
            _hand_card(), {"today_spend": 4.0}))
        self.assertIsNone(policy.queued_reason(
            _hand_card(), {"running": "many", "max_concurrent": 3}))
        self.assertIn(
            policy.queued_reason(_hand_card(), {"blocked_by": [],
                                                "running": 5,
                                                "max_concurrent": 3}),
            policy.QUEUED_REASONS)


if __name__ == "__main__":
    unittest.main()
