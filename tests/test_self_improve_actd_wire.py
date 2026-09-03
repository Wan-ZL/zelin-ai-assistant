"""§64 在 actd 里的接线：免批通道（notes 痕 / 观察通知 / 暂停 token 上卡）、四条
收割路径都过 gh 核验（reconcile done 分支 / _promote_if_delivered / 受阻收割 /
stop_to_review）、dashboard review 行带 `delivery`（未通过 = interrupted）、巡检
钩子绝不崩 pass。

沙箱 AIASSISTANT_HOME；executor/notify/roster 全 mock；gh 经
self_improve.default_gh 的 patch 注入 FakeGh——绝不 spawn 真 claude / gh。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.self_improve_testkit import FakeGh, lane_card, pr_doc

from act import actd
from act.lib import config, registry, self_improve
from act.lib.dashboard import build_dashboard
from act.lib.registry import State

BRANCH = "ai/self-improve/R-900"
SID = "aaaa1111-0000-4000-8000-000000000001"


def _agent(state, pid=None):
    a = {"id": "aaaa1111", "sessionId": SID, "state": state, "cwd": "/tmp/wt",
         "name": "bg agent", "startedAt": "2026-09-02T00:00:00Z"}
    if pid is not None:
        a["pid"] = pid
    return a


def _clean():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()
    lane = self_improve.lane_state_path()
    if lane.exists():
        lane.unlink()


class WireBase(unittest.TestCase):
    def setUp(self):
        _clean()
        self.notify = mock.patch.object(actd.notify, "notify").start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(lambda: config.CONFIG_PATH.unlink(missing_ok=True))
        self.cfg = config.Config()

    def _gh(self, prs=None, **kw):
        gh = FakeGh(prs if prs is not None else {123: pr_doc(branch=BRANCH)}, **kw)
        mock.patch.object(self_improve, "default_gh", gh).start()
        return gh

    def _reconcile(self, agents):
        harvest = mock.Mock(return_value={"delivered_summary": "PR: https://x/pull/123"})
        with mock.patch.object(actd, "_run_claude_agents", return_value=agents), \
                mock.patch.object(actd.executor, "harvest_delivery", harvest), \
                mock.patch.object(actd.executor, "resume", mock.Mock(return_value=True)):
            actd.reconcile_executing(self.cfg, set())


class AutoDispatchWireTestCase(WireBase):
    def test_lane_card_is_approved_with_its_own_note_and_notice(self):
        registry.save(lane_card(status=State.CARD_SENT.value, execution=None))
        self.assertEqual(actd.auto_dispatch_pass(self.cfg), 1)
        req = registry.load("P-7")
        self.assertEqual(req.status, State.APPROVED.value)
        self.assertTrue(req.execution["auto_dispatched"])
        self.assertIn("self_improve 通道免批自动派发", req.notes)
        self.assertNotIn("hand 出身", req.notes)
        self.notify.assert_called_once()
        title = self.notify.call_args.args[0]
        self.assertTrue("自我改进通道" in title or "Self-improve lane" in title, title)

    def test_hand_card_note_and_notice_unchanged(self):
        registry.save(lane_card("P-8", status=State.CARD_SENT.value, execution=None,
                                sources=[{"channel": "quick_capture", "date": "d", "quote": "x"}],
                                cost_estimate_usd=2.0))
        self.assertEqual(actd.auto_dispatch_pass(self.cfg), 1)
        req = registry.load("P-8")
        self.assertIn("hand 出身免批自动派发（est $2）", req.notes)
        self.assertEqual(self.notify.call_args.args[0], "观察模式：手打卡已自动派发（免批）")

    def test_lane_disabled_is_routine_and_clears_stale_token(self):
        registry.save(lane_card(status=State.CARD_SENT.value,
                                execution={"auto_dispatch_block": "self_improve:paused"}))
        cfg = config.Config(raw={"self_improve": {"enabled": False}})
        self.assertEqual(actd.auto_dispatch_pass(cfg), 0)
        req = registry.load("P-7")
        self.assertEqual(req.status, State.CARD_SENT.value)
        self.assertNotIn("auto_dispatch_block", req.execution)
        self.assertEqual(req.notes, "")

    def test_repo_mismatch_is_stated_on_the_card(self):
        registry.save(lane_card(status=State.CARD_SENT.value, execution=None,
                                target_repo="/somewhere/else"))
        self.assertEqual(actd.auto_dispatch_pass(self.cfg), 0)
        req = registry.load("P-7")
        self.assertEqual(req.execution["auto_dispatch_block"], "self_improve:repo_mismatch")
        self.assertIn("self_improve:repo_mismatch", req.notes)
        self.notify.assert_not_called()


class HarvestWireTestCase(WireBase):
    def test_done_agent_promotes_with_verified_delivery(self):
        registry.save(lane_card())
        gh = self._gh()
        self._reconcile([_agent("done")])
        req = registry.load("P-7")
        self.assertEqual(req.status, State.REVIEW.value)
        d = req.execution["delivery"]
        self.assertTrue(d["verified"])
        self.assertEqual(d["pr_number"], 123)
        self.assertNotIn("interrupted_reason", req.execution)
        self.assertEqual(gh.argv_with("pr")[0][:2], ["pr", "list"])
        self.assertTrue(gh.pr_calls_all_pinned())
        # dashboard：review 行带 delivery、无 interrupted
        with mock.patch("act.lib.dashboard._run_claude_agents", return_value=[]):
            dash = build_dashboard(cfg=self.cfg)
        row = dash["review"][0]
        self.assertEqual(row["delivery"]["pr_url"], "https://github.com/o/r/pull/123")
        self.assertTrue(row["delivery"]["verified"])
        self.assertNotIn("interrupted", row)

    def test_done_agent_without_pr_lands_in_review_interrupted(self):
        registry.save(lane_card())
        self._gh({})
        self._reconcile([_agent("done")])
        req = registry.load("P-7")
        self.assertEqual(req.status, State.REVIEW.value)
        self.assertEqual(req.execution["interrupted_reason"], "delivery_unverified")
        self.assertEqual(req.execution["delivery"]["reason"], "pr_missing")
        with mock.patch("act.lib.dashboard._run_claude_agents", return_value=[]):
            dash = build_dashboard(cfg=self.cfg)
        row = dash["review"][0]
        self.assertTrue(row["interrupted"])
        self.assertEqual(row["delivery"]["reason"], "pr_missing")
        # detect_transitions 对 interrupted 行不发「AI 已交付草稿」
        prev = {"running": [{"id": "P-7"}], "review": [], "needs_approval": []}
        msgs = actd.detect_transitions(prev, dash)
        self.assertEqual([m for m in msgs if m[2] == "P-7"], [])
        self.notify.assert_called_once()       # 精确文案已由 on_harvest 发

    def test_blocked_agent_harvest_path_verifies_too(self):
        registry.save(lane_card())
        self._gh({})
        self._reconcile([_agent("blocked", pid=42)])
        req = registry.load("P-7")
        self.assertEqual(req.status, State.REVIEW.value)
        # 受阻收割自己的 interrupted_reason 被核验失败覆盖为更具体的原因
        self.assertEqual(req.execution["interrupted_reason"], "delivery_unverified")
        self.assertIn("delivery", req.execution)

    def test_promote_if_delivered_path_verifies(self):
        registry.save(lane_card())
        self._gh()
        harvest = mock.Mock(return_value={"final_draft": "PR: x", "delivered_summary": "s"})
        actd._HARVEST_PROBE_AT.clear()          # 进程内 120s 探针节流，别的判例可能刚探过
        with mock.patch.object(actd.executor, "harvest_delivery", harvest):
            req = registry.load("P-7")
            ex = dict(req.execution)
            self.assertTrue(actd._promote_if_delivered(req, ex, "aaaa1111"))
        self.assertTrue(registry.load("P-7").execution["delivery"]["verified"])

    def test_stop_to_review_verifies(self):
        registry.save(lane_card())
        self._gh({})
        with mock.patch.object(actd.executor, "harvest_delivery", return_value={}), \
                mock.patch.object(actd, "_stop_session_tracked"):
            req = registry.load("P-7")
            result = actd._apply_decision(req, "stop_to_review", None, None, None)
        self.assertEqual(result, "running")
        req = registry.load("P-7")
        self.assertEqual(req.status, State.REVIEW.value)
        self.assertEqual(req.execution["delivery"]["reason"], "pr_missing")

    def test_hand_card_harvest_untouched(self):
        registry.save(lane_card(sources=[{"channel": "quick_capture", "date": "d", "quote": "x"}]))
        gh = self._gh()
        self._reconcile([_agent("done")])
        req = registry.load("P-7")
        self.assertEqual(req.status, State.REVIEW.value)
        self.assertNotIn("delivery", req.execution)
        self.assertEqual(gh.calls, [])

    def test_check_never_breaks_the_harvest(self):
        registry.save(lane_card())
        with mock.patch.object(self_improve, "on_harvest", side_effect=RuntimeError("boom")):
            self._reconcile([_agent("done")])
        self.assertEqual(registry.load("P-7").status, State.REVIEW.value)


class TickHookTestCase(WireBase):
    def test_tick_hook_swallows_and_logs(self):
        logs = []
        with mock.patch.object(self_improve, "tick", side_effect=RuntimeError("boom")):
            self_improve.tick_hook(self.cfg, log=logs.append)
        self.assertIn("boom", logs[0])

    def test_tick_hook_logs_gh_unavailable_once_per_tick(self):
        registry.save(lane_card(status=State.REVIEW.value,
                                execution={"delivery": {"pr_number": 1}}))
        logs = []
        self_improve.tick_hook(self.cfg, log=logs.append)     # AIASSISTANT_GH=0 → 不可用
        self.assertTrue(any("gh_unavailable" in line for line in logs))
        logs.clear()
        self_improve.tick_hook(self.cfg, log=logs.append)     # 节流：不再重试、不再打日志
        self.assertEqual(logs, [])

    def test_run_once_wires_the_tick_hook(self):
        # run_once 里的一行钩子——存在且传 actd 的 _log（巡检日志与 actd.log 同一份）
        with mock.patch.object(self_improve, "tick_hook") as hook, \
                mock.patch.object(actd, "build_dashboard", return_value={"generated_at": "t"}), \
                mock.patch.object(actd, "write_dashboard"), \
                mock.patch.object(actd, "_run_claude_agents", return_value=[]), \
                mock.patch.object(actd, "detect_transitions", return_value=[]), \
                mock.patch.object(actd, "_check_auth_failures", return_value=[]), \
                mock.patch.object(actd, "_check_radar_liveness", return_value=[]):
            actd.run_once(self.cfg, None, set(), set(), set(), interval=10)
        hook.assert_called_once_with(self.cfg, log=actd._log)


if __name__ == "__main__":
    unittest.main()
