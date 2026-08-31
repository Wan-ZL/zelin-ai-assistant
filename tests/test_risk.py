"""W17 effective tier + W18 remote direct-run gate (act/lib/risk.py).

法源:docs/design/vnext-amendments.md §W17/§W18。纯函数,无需起 server;
dashboard 投影的 add-only ``effective_tier`` 字段也在这里钉住。
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act.* import

from act.lib import config, dashboard, risk
from act.lib.registry import Requirement, State


class EffectiveTierTestCase(unittest.TestCase):
    def test_external_dict_forces_t2_and_expand(self):
        et = risk.effective_tier({"tier": "T0", "origin_trust": "external"})
        self.assertEqual(et.tier, "T2")
        self.assertTrue(et.forced_expand)
        self.assertEqual(et.reason, "origin_trust=external")

    def test_external_already_t2_stays_t2_but_still_forced(self):
        et = risk.effective_tier({"tier": "T2", "origin_trust": "external"})
        self.assertEqual(et.tier, "T2")
        self.assertTrue(et.forced_expand)

    def test_hand_keeps_declared_tier(self):
        et = risk.effective_tier({"tier": "T0", "origin_trust": "hand"})
        self.assertEqual(et.tier, "T0")
        self.assertFalse(et.forced_expand)
        self.assertIsNone(et.reason)

    def test_missing_origin_trust_keeps_declared_tier(self):
        # 无 sources 的缺章卡:现算为 proposed → 保持声明档(v0.48.1 起缺章
        # 卡从 sources 现算,抬档的只有真判 external 的——见 §50 修订)。
        et = risk.effective_tier({"tier": "T1"})
        self.assertEqual(et.tier, "T1")
        self.assertFalse(et.forced_expand)

    def test_stampless_external_sources_force_t2(self):
        # F2(v0.48.1 §50 修订):缺章但 sources 是 slack → 出身现算 external,
        # 强制 T2 + expansion——手改/存量 YAML 抹掉章也洗不回声明档
        et = risk.effective_tier({"tier": "T1", "sources": [
            {"who": "boss", "channel": "slack", "date": "2026-08-30",
             "quote": "外部请求"}]})
        self.assertEqual(et.tier, "T2")
        self.assertTrue(et.forced_expand)
        self.assertEqual(et.reason, "sources=external")

    def test_hand_stamp_cannot_launder_external_sources(self):
        # 章说 hand、sources 说 gmail → 取最不信任(与调度侧同纪律)
        et = risk.effective_tier({"tier": "T1", "origin_trust": "hand",
                                  "sources": [{"channel": "gmail"}]})
        self.assertEqual(et.tier, "T2")
        self.assertTrue(et.forced_expand)

    def test_stampless_hand_sources_keep_declared(self):
        # 手打出身的缺章卡不被追溯抬档——「历史卡一夜全 T2」不会发生
        et = risk.effective_tier({"tier": "T0", "sources": [
            {"channel": "quick_capture"}]})
        self.assertEqual(et.tier, "T0")
        self.assertFalse(et.forced_expand)

    def test_missing_tier_defaults_t1_and_external_still_forces(self):
        self.assertEqual(risk.effective_tier({}).tier, "T1")
        self.assertEqual(
            risk.effective_tier({"origin_trust": "external"}).tier, "T2")

    def test_trust_value_normalized_case_and_whitespace(self):
        et = risk.effective_tier({"tier": "T1", "origin_trust": "  External "})
        self.assertEqual(et.tier, "T2")
        self.assertTrue(et.forced_expand)

    def test_requirement_object_without_field_is_declared_tier(self):
        req = Requirement(id="R-900", title="x", tier="T2",
                          status=State.CARD_SENT.value)
        et = risk.effective_tier(req)
        self.assertEqual(et.tier, "T2")
        self.assertFalse(et.forced_expand)

    def test_junk_card_never_raises(self):
        for junk in (None, 42, "card", [], object()):
            et = risk.effective_tier(junk)
            self.assertEqual(et.tier, "T1")
            self.assertFalse(et.forced_expand)


class RemoteDirectRunAllowedTestCase(unittest.TestCase):
    def tearDown(self):
        if config.CONFIG_PATH.exists():
            config.CONFIG_PATH.unlink()

    def test_default_is_off(self):
        # sandbox HOME 里没有 config.yaml → 默认 Config → 闸门关。
        self.assertFalse(risk.remote_direct_run_allowed())
        self.assertFalse(risk.remote_direct_run_allowed(config.Config()))

    def test_config_opt_in(self):
        config.CONFIG_PATH.write_text(
            "remote:\n  allow_direct_run: true\n", encoding="utf-8")
        self.assertTrue(risk.remote_direct_run_allowed())

    def test_explicit_false_stays_off(self):
        config.CONFIG_PATH.write_text(
            "remote:\n  allow_direct_run: false\n", encoding="utf-8")
        self.assertFalse(risk.remote_direct_run_allowed())

    def test_cfg_without_field_fails_closed(self):
        class Bare:  # 老 Config / 任意对象:缺字段 = 闸门关
            pass
        self.assertFalse(risk.remote_direct_run_allowed(Bare()))


class DashboardEffectiveTierTestCase(unittest.TestCase):
    def test_needs_approval_carries_effective_tier(self):
        # v0.10.3 Requirement 无 origin_trust => effective_tier 恒等于 tier
        # (add-only 字段,W17 接线后由 origin_trust 驱动)。
        reqs = [
            Requirement(id="R-901", title="卡一", tier="T1",
                        status=State.CARD_SENT.value),
            Requirement(id="R-902", title="卡二", tier="T2",
                        status=State.RAISING.value),
        ]
        dash = dashboard.build_dashboard(reqs=reqs, agents=[],
                                         cfg=config.Config())
        items = {i["id"]: i for i in dash["needs_approval"]}
        self.assertEqual(items["R-901"]["effective_tier"], "T1")
        self.assertEqual(items["R-901"]["effective_tier"], items["R-901"]["tier"])
        self.assertEqual(items["R-902"]["effective_tier"], "T2")


if __name__ == "__main__":
    unittest.main()
