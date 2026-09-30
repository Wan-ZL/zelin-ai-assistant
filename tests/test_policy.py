"""act/lib/policy 的行为测试 — 信任矩阵 + autodispatch 配置块 + 排队原因.

（may_auto_dispatch 与全部天花板随 §51 最后一条免批 lane retired D86，判例同删。）

纯函数模块：无 I/O、无 registry 写入。repo 存在性经 path_exists seam 注入，
测试绝不碰真文件系统（CONTRIBUTING 测试纪律）。
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env first
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
            "enabled": False, "max_concurrent": 1, "notify": False}))
        # require_awake（§71.1，add-only）没写就是默认 true——老 config 的三键
        # 语义逐字不变，新键只是多一把默认开着的闸。
        self.assertEqual(got, {"enabled": False, "max_concurrent": 1,
                               "notify": False, "require_awake": True})

    def test_require_awake_knob(self):
        # §71.1：机器状态闸的旋钮，脏值按 bool() 收敛（同 enabled / notify）
        self.assertTrue(policy.autodispatch_config(_cfg(auto={}))["require_awake"])
        self.assertFalse(policy.autodispatch_config(
            _cfg(auto={"require_awake": False}))["require_awake"])
        self.assertTrue(policy.autodispatch_config(
            _cfg(auto={"require_awake": "yes"}))["require_awake"])

    def test_garbage_values_fall_back_per_key(self):
        got = policy.autodispatch_config(_cfg(auto={
            "max_concurrent": 0, "enabled": 1}))
        self.assertEqual(got["max_concurrent"], 3)
        self.assertTrue(got["enabled"])

    def test_bare_dict_cfg(self):
        got = policy.autodispatch_config(
            {"autodispatch": {"max_concurrent": 2}})
        self.assertEqual(got["max_concurrent"], 2)

    def test_legacy_daily_budget_key_is_ignored(self):
        # D9（vnext2-plan）：预算天花板 retired v0.48.7。旧 config 里残留的
        # daily_budget_usd 既不进解析结果、也不影响别的键——tombstone 判例。
        got = policy.autodispatch_config(_cfg(auto={
            "daily_budget_usd": 5, "max_concurrent": 2}))
        self.assertNotIn("daily_budget_usd", got)
        self.assertNotIn("daily_budget_usd", policy.AUTODISPATCH_DEFAULTS)
        self.assertEqual(got["max_concurrent"], 2)


class TestQueuedReason(unittest.TestCase):
    def test_vocabulary_and_none(self):
        self.assertIsNone(policy.queued_reason(_hand_card(), {}))
        self.assertIsNone(policy.queued_reason(_hand_card(), None))

    def test_dependency(self):
        self.assertEqual(
            policy.queued_reason(_hand_card(), {"blocked_by": ["R-001"]}),
            "dependency")

    def test_budget_token_retired_d9(self):
        # 原判例 test_budget 钉 today_spend 4 + cost 2 > 5 → "budget"。D9
        # retired v0.48.7：旧快照里残留的两个预算键被忽略、不 raise、不报 budget，
        # 词表里也不再有这个 token（永不复用）。
        st = {"today_spend": 4.0, "daily_budget_usd": 5.0}
        self.assertIsNone(
            policy.queued_reason(_hand_card(cost_estimate_usd=2.0), st))
        self.assertNotIn("budget", policy.QUEUED_REASONS)
        # 词表 add-only：machine_asleep 由 §71.1 加入（位置即优先级），
        # 退役的 budget 永不复用。
        self.assertEqual(policy.QUEUED_REASONS,
                         ("dependency", "machine_asleep", "concurrency"))

    def test_machine_asleep_between_dependency_and_concurrency(self):
        # §71.1：闸按住整个 pass 时 chip 说「等电脑醒来」；依赖更「粘」排在前面，
        # 并发最快松动排在后面。
        card = _hand_card()
        self.assertEqual(policy.queued_reason(card, {"machine_asleep": True}),
                         "machine_asleep")
        self.assertEqual(policy.queued_reason(
            card, {"machine_asleep": True, "blocked_by": ["R-1"]}), "dependency")
        self.assertEqual(policy.queued_reason(
            card, {"machine_asleep": True, "running": 3, "max_concurrent": 3}),
            "machine_asleep")
        self.assertIsNone(policy.queued_reason(card, {"machine_asleep": False}))

    def test_concurrency(self):
        st = {"running": 3, "max_concurrent": 3}
        self.assertEqual(policy.queued_reason(_hand_card(), st),
                         "concurrency")
        self.assertIsNone(policy.queued_reason(
            _hand_card(), {"running": 2, "max_concurrent": 3}))

    def test_precedence_dependency_concurrency(self):
        # 原为 dependency > budget > concurrency 三级；budget retired v0.48.7（D9）
        st = {"blocked_by": "R-001", "running": 3, "max_concurrent": 3}
        self.assertEqual(policy.queued_reason(_hand_card(), st), "dependency")
        st.pop("blocked_by")
        self.assertEqual(policy.queued_reason(_hand_card(), st),
                         "concurrency")

    def test_missing_keys_skip_checks(self):
        # 只有一半并发键 / 垃圾值 -> 该检查跳过，绝不 raise
        self.assertIsNone(policy.queued_reason(
            _hand_card(), {"running": 4}))
        self.assertIsNone(policy.queued_reason(
            _hand_card(), {"running": "many", "max_concurrent": 3}))
        self.assertIn(
            policy.queued_reason(_hand_card(), {"blocked_by": [],
                                                "running": 5,
                                                "max_concurrent": 3}),
            policy.QUEUED_REASONS)


if __name__ == "__main__":
    unittest.main()
