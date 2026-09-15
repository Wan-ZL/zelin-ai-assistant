"""通道关掉之后，**已经存在**的 self_improve 卡不再被自动推进（CONTRACT §65.1
追记；issue #307 第 4 条「关闭开关时至少不再续派」，owner 决策 D55）。

钉的行为（开关本身的三层配置 / 读取器 / 巡检住 test_self_improve_channel_switch.py）：

* 免批批准（`execution.auto_dispatched`）但还没起跑的 lane 卡：关着时**不派**，
  退回 `card_sent`、清掉那枚痕、notes 记一行；下一 pass 的资格闸报常态
  `self_improve:disabled`（不上卡、不来回摇）；
* **owner 亲手批准的**同款卡照派——开关管的是自动化，显式动作不被静默吞掉；
* 非 self_improve 卡一概不受影响；
* 已在跑但 agent 死了的 lane 卡：关着时**不自动续命**（`executor.resume` 一次都
  不调），卡原地留在运行中；开关打开后下一 pass 照常救活；
* 收割不在这把闸下：agent 跑完的卡关着也照常收进待验收（§65.3 核验照做）。

沙箱 AIASSISTANT_HOME；executor / notify / roster 全 mock，gh 用 FakeGh——
零子进程、零网络。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.self_improve_testkit import FakeGh, lane_card, pr_doc

from act import actd
from act.lib import config, registry, self_improve
from act.lib.registry import State

BRANCH = "ai/self-improve/R-900"


def _cfg(enabled: bool) -> config.Config:
    return config.Config(self_improve_enabled=enabled)


class FrozenBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self_improve.lane_state_path().unlink(missing_ok=True)
        mock.patch.object(actd.notify, "notify", mock.Mock(return_value=True)).start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(lambda: config.CONFIG_PATH.unlink(missing_ok=True))
        self.addCleanup(lambda: config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True))


# --------------------------------------------------------------------------- #
# (a) approved-but-never-dispatched：退回待审批，不再起跑
# --------------------------------------------------------------------------- #
class WithdrawApprovedTestCase(FrozenBase):
    def _approved(self, req_id="P-7", execution=None, **over):
        req = lane_card(req_id, status=State.APPROVED.value,
                        execution=dict(execution if execution is not None
                                       else {"auto_dispatched": True}), **over)
        registry.save(req)
        return req

    def _run(self, cfg):
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock):
            n = actd.dispatch_approved(cfg)
        return n, ex_mock

    def test_auto_approved_lane_card_is_withdrawn_instead_of_dispatched(self):
        self._approved()
        n, ex_mock = self._run(_cfg(False))
        self.assertEqual(n, 0)
        ex_mock.dispatch.assert_not_called()
        req = registry.load("P-7")
        self.assertEqual(req.status, State.CARD_SENT.value)
        self.assertNotIn("auto_dispatched", req.execution or {})
        self.assertIn("通道已关", req.notes)

    def test_withdrawn_card_settles_in_card_sent_without_a_token(self):
        """退回之后不来回摇：资格闸报常态 token，不上卡、不再批。"""
        self._approved()
        self._run(_cfg(False))
        self.assertEqual(actd.auto_dispatch_pass(_cfg(False)), 0)
        req = registry.load("P-7")
        self.assertEqual(req.status, State.CARD_SENT.value)
        self.assertNotIn("auto_dispatch_block", req.execution or {})

    def test_switch_on_dispatches_the_same_card(self):
        self._approved()
        n, ex_mock = self._run(_cfg(True))
        self.assertEqual(n, 1)
        ex_mock.dispatch.assert_called_once()
        self.assertEqual(registry.load("P-7").status, State.APPROVED.value)

    def test_owner_hand_approved_lane_card_still_goes_out(self):
        self._approved(execution={})          # 没有 auto_dispatched 痕 = owner 亲批
        n, ex_mock = self._run(_cfg(False))
        self.assertEqual(n, 1)
        ex_mock.dispatch.assert_called_once()
        self.assertEqual(registry.load("P-7").status, State.APPROVED.value)

    def test_non_self_improve_cards_are_untouched(self):
        self._approved("P-8", sources=[{"channel": "quick_capture", "date": "d",
                                        "quote": "x"}])
        n, ex_mock = self._run(_cfg(False))
        self.assertEqual(n, 1)
        ex_mock.dispatch.assert_called_once()


# --------------------------------------------------------------------------- #
# (b) executing：死掉的会话不自动续命；收割照旧
# --------------------------------------------------------------------------- #
class NoAutoResumeTestCase(FrozenBase):
    def _reconcile(self, cfg, agents, resume=None):
        resume = resume if resume is not None else mock.Mock(return_value=True)
        with mock.patch.object(actd, "_run_claude_agents", return_value=agents), \
                mock.patch.object(actd.executor, "resume", resume):
            n = actd.reconcile_executing(cfg, set())
        return n, resume

    def test_dead_lane_session_is_not_revived_while_the_channel_is_off(self):
        registry.save(lane_card())            # EXECUTING + session_id，roster 里没有
        n, resume = self._reconcile(_cfg(False), [])
        self.assertEqual(n, 0)
        resume.assert_not_called()
        req = registry.load("P-7")
        self.assertEqual(req.status, State.EXECUTING.value)
        self.assertNotIn("resume_attempts", req.execution or {})

    def test_the_same_dead_session_is_revived_once_the_switch_is_on(self):
        registry.save(lane_card())
        n, resume = self._reconcile(_cfg(True), [])
        self.assertEqual(n, 1)
        resume.assert_called_once()

    def test_non_self_improve_card_is_revived_with_the_channel_off(self):
        registry.save(lane_card("P-8", sources=[{"channel": "quick_capture",
                                                 "date": "d", "quote": "x"}]))
        n, resume = self._reconcile(_cfg(False), [])
        self.assertEqual(n, 1)
        resume.assert_called_once()

    def test_a_finished_session_is_still_harvested_with_the_channel_off(self):
        """收割不在闸下：活干完了就收下（§65.3 核验照做）。"""
        registry.save(lane_card())
        mock.patch.object(self_improve, "default_gh",
                          FakeGh({123: pr_doc(branch=BRANCH)})).start()
        agent = {"id": "aaaa1111", "sessionId": "aaaa1111", "state": "done",
                 "cwd": "/tmp/wt", "name": "bg agent",
                 "startedAt": "2026-09-02T00:00:00Z"}
        harvest = mock.Mock(return_value={"delivered_summary": "PR: https://x/pull/123"})
        with mock.patch.object(actd.executor, "harvest_delivery", harvest):
            self._reconcile(_cfg(False), [agent])
        req = registry.load("P-7")
        self.assertEqual(req.status, State.REVIEW.value)
        self.assertTrue(req.execution["delivery"]["verified"])


if __name__ == "__main__":
    unittest.main()
