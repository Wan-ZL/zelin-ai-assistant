"""免批通道从潜在任务列起跳，但**只抬 §65 那条 lane**——潜在任务列不是自动
派发的候选池。

契约：CONTRACT **§78** / **§78.3** 的「免批扫描」那一行 / **§78.2** D80.4
（§51 hand lane 免批**退役**，墓碑 §78.9）/ §65（self_improve lane 保留，起跳
状态重锚到 ``detected``，D80.5）/ §51（天花板与 queued 词表本身不变）。

这条法条有两半，缺任何一半都是事故：

1. **起跳状态换列**：扫描判据从 ``card_sent`` 改成 ``detected``——不改的话
   §65 lane 整条哑掉（卡再也不会被自动抬起来），而且没有任何报错；
2. **多一道 lane 守卫**：``policy.is_self_improve_sources`` 在资格闸**之前**。
   §78.3 原话——这一行是整张表里最危险的：潜在任务列里躺着雷达噪音、owner
   随手记的半句话与上百张 legacy 卡，少了守卫就是「整条备选列变成自动开跑的
   候选池」。守卫住在 ``dispatch.py``，**不下沉进 policy**：``may_auto_dispatch``
   仍是纯资格函数，对 hand 卡照旧判「可以」（本文件逐条钉住这个分工）。

沙箱 AIASSISTANT_HOME；notify 全 mock；零子进程、零网络（免批只改状态，派发
在 ``dispatch_approved``，不在这条路上）。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.self_improve_testkit import SI_SRC, lane_card

from act import actd
from act.lib import config, policy, registry, self_improve
from act.lib.registry import Requirement, State

_HAND = [{"who": "zelin", "channel": "quick_capture", "date": "2026-09-26",
          "quote": "手打的一句话"}]
_PROPOSED = [{"who": "loop", "channel": "analytics", "date": "2026-09-26",
              "quote": "AI 自提"}]
_SLACK = [{"who": "boss", "channel": "slack", "date": "2026-09-26", "quote": "外部来的"}]


class AutoDispatchLaneBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self_improve.lane_state_path().unlink(missing_ok=True)
        self.notify = mock.patch.object(actd.notify, "notify",
                                        mock.Mock(return_value=True)).start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(lambda: config.CONFIG_PATH.unlink(missing_ok=True))
        # §65.1 的总开关出厂关（#307 / D57）——本文件钉的是开着时的通道
        self.cfg = config.Config(self_improve_enabled=True)

    def _lane(self, rid="P-7", status=State.DETECTED.value, **over):
        req = lane_card(rid, status=status, execution=None, **over)
        registry.save(req)
        return req

    def _plain(self, rid, sources, status=State.DETECTED.value, **over):
        base = dict(id=rid, title="潜在任务列里的普通卡", tier="T1", status=status,
                    sources=list(sources), target_repo=str(config.HOME),
                    target_kind="existing", delivery_mode="repo",
                    cost_estimate_usd=2.0)
        base.update(over)
        req = Requirement(**base)
        registry.save(req)
        return req


class LanePromotionTestCase(AutoDispatchLaneBase):
    """(1) 起跳状态 = 潜在任务。"""

    def test_a_lane_card_sitting_in_detected_is_promoted(self):
        self._lane()
        self.assertEqual(actd.auto_dispatch_pass(self.cfg), 1)
        req = registry.load("P-7")
        self.assertEqual(req.status, State.APPROVED.value)
        self.assertTrue((req.execution or {}).get("auto_dispatched"))
        self.assertIn("self_improve 通道免批自动派发", req.notes)

    def test_the_retired_lane_is_no_longer_the_launch_pad(self):
        """退役车道上的落单卡不在这个闸的射程内——它先由 §78.5 的一次性归并
        扫描搬进潜在任务（同一 pass 里排在免批之前），再从那里起跳。"""
        self._lane(status=State.CARD_SENT.value)
        self.assertEqual(actd.auto_dispatch_pass(self.cfg), 0)
        self.assertEqual(registry.load("P-7").status, State.CARD_SENT.value)

    def test_the_fold_sweep_then_the_gate_is_the_real_pipeline(self):
        """两段接起来：搬进潜在任务 → 同一 pass 被免批抬走（actd.run_once 里
        两者的先后顺序就是为了不让存量卡多等一整轮）。"""
        self._lane(status=State.CARD_SENT.value)
        # §78.5 的开机闩是进程内的，整个测试进程只「开机」一次——不拨回去，本
        # 判例在全量跑里就会静默退化成「扫描没搬、闸没抬」的假绿（单跑时绿、
        # 合跑时红，正是最难查的那种）。
        actd._reset_fold_latch()
        self.addCleanup(actd._reset_fold_latch)
        actd.fold_retired_lane()
        self.assertEqual(actd.auto_dispatch_pass(self.cfg), 1)
        self.assertEqual(registry.load("P-7").status, State.APPROVED.value)


class NonLaneCardsAreShieldedTestCase(AutoDispatchLaneBase):
    """(2) 潜在任务列**不是**候选池——D80.4 之后只有 §65 卡会被自动抬起来。"""

    def test_a_hand_card_that_policy_would_admit_is_still_not_promoted(self):
        """最硬的一条：资格函数对这张卡说「可以」，卡照样不动。

        证明拦住它的是 ``dispatch.py`` 的 lane 守卫，而不是别的天花板顺手
        帮了忙——把守卫删掉，本条立刻红。"""
        hand = self._plain("P-10", _HAND)
        self.assertEqual(policy.may_auto_dispatch(hand, self.cfg), (True, "ok"))
        self.assertEqual(actd.auto_dispatch_pass(self.cfg), 0)
        req = registry.load("P-10")
        self.assertEqual(req.status, State.DETECTED.value)
        self.assertNotIn("auto_dispatched", req.execution or {})
        self.notify.assert_not_called()

    def test_a_proposed_origin_card_is_not_promoted(self):
        self._plain("P-11", _PROPOSED)
        self.assertEqual(actd.auto_dispatch_pass(self.cfg), 0)
        self.assertEqual(registry.load("P-11").status, State.DETECTED.value)

    def test_a_parked_low_confidence_radar_card_is_not_promoted(self):
        """雷达噪音：安静出生、没人看过的一张外部来源卡。潜在任务列现在**就是**
        它的家——它住在那儿这件事不许变成「自动开跑」的资格。"""
        self._plain("P-12", _SLACK, quiet_birth=True, cost_estimate_usd=None)
        self.assertEqual(actd.auto_dispatch_pass(self.cfg), 0)
        self.assertEqual(registry.load("P-12").status, State.DETECTED.value)

    def test_a_mixed_source_card_is_not_promoted(self):
        """搭便车两个方向都关死：self_improve + 别的渠道 = 不是 lane 卡。"""
        self._plain("P-13", list(SI_SRC) + list(_SLACK))
        self.assertEqual(actd.auto_dispatch_pass(self.cfg), 0)
        self.assertEqual(registry.load("P-13").status, State.DETECTED.value)

    def test_a_noisy_backlog_yields_exactly_one_promotion(self):
        """整条列一起跑一遍：只有 lane 卡被抬走，其余一张不动。"""
        self._plain("P-10", _HAND)
        self._plain("P-11", _PROPOSED)
        self._plain("P-12", _SLACK, quiet_birth=True, cost_estimate_usd=None)
        self._lane("P-20")
        self.assertEqual(actd.auto_dispatch_pass(self.cfg), 1)
        promoted = [r.id for r in registry.load_all()
                    if r.status == State.APPROVED.value]
        self.assertEqual(promoted, ["P-20"])
        self.assertEqual(sorted(r.id for r in registry.load_all()
                                if r.status == State.DETECTED.value),
                         ["P-10", "P-11", "P-12"])

    def test_a_stale_block_token_on_a_non_lane_card_is_cleared(self):
        """hand 卡再也不进资格闸，上一轮留在卡上的 ``auto_dispatch_block``
        就成了永不更新的假话（卡面一直挂着「auto-dispatch 拦下…」的 chip）。"""
        self._plain("P-14", _HAND, execution={"auto_dispatch_block": "cost:unknown"})
        self.assertEqual(actd.auto_dispatch_pass(self.cfg), 0)
        self.assertNotIn("auto_dispatch_block",
                         registry.load("P-14").execution or {})


class PolicyRemainsAPureEligibilityFunctionTestCase(AutoDispatchLaneBase):
    """守卫住在 dispatch.py，**不**下沉进 policy（§78.3 末句）。

    ``may_auto_dispatch`` 的裁决表逐例不变是 §51 那份巨大 golden 的前提；
    退役的是**入口**，不是裁决表。"""

    def test_policy_still_says_yes_to_a_hand_card(self):
        hand = self._plain("P-15", _HAND)
        self.assertEqual(policy.may_auto_dispatch(hand, self.cfg), (True, "ok"))

    def test_policy_still_reports_cost_unknown_for_a_hand_card_without_an_estimate(self):
        hand = self._plain("P-16", _HAND, cost_estimate_usd=None)
        self.assertEqual(policy.may_auto_dispatch(hand, self.cfg), (False, "cost:unknown"))

    def test_the_lane_source_predicate_is_the_gate(self):
        self.assertTrue(policy.is_self_improve_sources(SI_SRC))
        self.assertFalse(policy.is_self_improve_sources(_HAND))
        self.assertFalse(policy.is_self_improve_sources(list(SI_SRC) + list(_SLACK)))
