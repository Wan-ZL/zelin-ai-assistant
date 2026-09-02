"""§46 stop 确认重试 + 失败台账（session 生命周期可靠性，2026-08-07）.

生产日志里 stop_session→False 一天出现 4 次而无人跟进——session 可能还活着
继续烧钱/占资源。判例：

executor.stop_session_confirmed(sid) -> (stopped, issued, detail)
  - roster 确认无活 pid：成功 (True, False, "not running")，一次 stop 都不发；
  - 有 pid、stop 后死掉：(True, True, ...)；重试轮之间退避（sleeper 可注入）；
  - 重试打满仍存活：(False, ...)——这是要留痕的失败；
  - 探测失败（prober None/抛异常，CLI 超时）≠「不在 roster」：立即判失败、
    不发 stop、不重试——绝不误判成「已停」清台账（PR #97 review P1）；
  - 超总预算 STOP_CONFIRM_BUDGET_S（clock 可注入）：立即判失败——单线程
    actd 主循环不能被一次 stop 挂死 ~218s（PR #97 review P2）。
  seam（prober/stopper/sleeper/budget_s/clock）全部可注入，绝不 spawn 真 claude。

actd._stop_session_tracked（所有 inbox stop 调用点的统一外壳）：
  - stop 确认失败 -> execution.stop_failed_at/stop_failed_error 台账 +
    notes [stop-failed] 标签（notes_text 投影到看板）+ notify.msg_stop_failed
    + analytics `stop_failed`，但绝不阻塞调用方的状态落账；
  - analytics 打点脱敏：error 里会话 UUID 只留前 8 位、PID 脱掉（TELEMETRY
    红线）——全量 detail 只进本机台账；
  - 之后一次确认成功 -> 清掉台账字段。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import json
import os
import unittest
import uuid
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, executor
from act.lib import analytics, config, registry
from act.lib.registry import Requirement, State

SID = "abcd1234-0000-4000-8000-000000000001"


class StopSessionConfirmedTestCase(unittest.TestCase):
    def test_not_running_confirms_without_issuing_stop(self):
        prober = mock.Mock(return_value={})
        stopper = mock.Mock()
        stopped, issued, detail = executor.stop_session_confirmed(
            SID, prober=prober, stopper=stopper, sleeper=mock.Mock())
        self.assertTrue(stopped)
        self.assertFalse(issued)          # 本来就死——一次 stop 都没发
        self.assertEqual(detail, "not running")
        stopper.assert_not_called()

    def test_stop_that_works_first_try_is_confirmed(self):
        # 第一次探测有 pid -> 发 stop；第二次探测没了 -> 确认成功
        prober = mock.Mock(side_effect=[{"pid": 42}, {}])
        stopper = mock.Mock(return_value=True)
        stopped, issued, _ = executor.stop_session_confirmed(
            SID, prober=prober, stopper=stopper, sleeper=mock.Mock())
        self.assertTrue(stopped)
        self.assertTrue(issued)
        stopper.assert_called_once()      # 一次就停住，不多打

    def test_retries_with_backoff_then_succeeds(self):
        # 前两轮 pid 都在（第一发 stop 没停住），第三轮探测才消失
        prober = mock.Mock(side_effect=[{"pid": 42}, {"pid": 42}, {}])
        stopper = mock.Mock(return_value=True)
        sleeper = mock.Mock()
        stopped, issued, _ = executor.stop_session_confirmed(
            SID, retries=2, prober=prober, stopper=stopper, sleeper=sleeper)
        self.assertTrue(stopped)
        self.assertTrue(issued)
        self.assertEqual(stopper.call_count, 2)
        # 退避递增：2s、4s（首轮不睡）
        self.assertEqual([c.args[0] for c in sleeper.call_args_list], [2.0, 4.0])

    def test_still_alive_after_retries_is_a_reported_failure(self):
        prober = mock.Mock(return_value={"pid": 42})
        stopper = mock.Mock(return_value=True)
        stopped, issued, detail = executor.stop_session_confirmed(
            SID, retries=2, prober=prober, stopper=stopper, sleeper=mock.Mock())
        self.assertFalse(stopped)
        self.assertTrue(issued)
        self.assertIn("still alive", detail)
        self.assertEqual(stopper.call_count, 3)   # 1 + 2 次重试

    def test_stopper_oserror_is_swallowed_and_retried(self):
        prober = mock.Mock(side_effect=[{"pid": 42}, {"pid": 42}, {}])
        stopper = mock.Mock(side_effect=[OSError("claude missing"), True])
        stopped, issued, _ = executor.stop_session_confirmed(
            SID, retries=2, prober=prober, stopper=stopper, sleeper=mock.Mock())
        self.assertTrue(stopped)
        self.assertTrue(issued)                   # 第二发成功过

    def test_probe_failure_is_a_failure_not_a_confirmed_stop(self):
        # CLI 超时/崩溃（prober None）≠「不在 roster」：进程可能还活着 ——
        # 判失败留痕，不发 stop、不再重试（重试打的还是同一个挂死的 CLI）
        prober = mock.Mock(return_value=None)
        stopper = mock.Mock()
        stopped, issued, detail = executor.stop_session_confirmed(
            SID, prober=prober, stopper=stopper, sleeper=mock.Mock())
        self.assertFalse(stopped)
        self.assertFalse(issued)
        self.assertIn("roster query failed", detail)
        stopper.assert_not_called()
        prober.assert_called_once()               # 不重试

    def test_probe_exception_is_a_failure_not_a_confirmed_stop(self):
        prober = mock.Mock(side_effect=RuntimeError("roster exploded"))
        stopped, _, detail = executor.stop_session_confirmed(
            SID, prober=prober, stopper=mock.Mock(), sleeper=mock.Mock())
        self.assertFalse(stopped)
        self.assertIn("roster query failed", detail)

    def test_budget_exceeded_is_a_reported_failure(self):
        # 总预算 60s：超时立即判失败返回，单线程 actd 不被一次 stop 挂死
        clock = mock.Mock(side_effect=[0.0, 100.0])   # deadline 计算后即超预算
        prober = mock.Mock()
        stopped, issued, detail = executor.stop_session_confirmed(
            SID, prober=prober, stopper=mock.Mock(), sleeper=mock.Mock(),
            budget_s=60.0, clock=clock)
        self.assertFalse(stopped)
        self.assertFalse(issued)
        self.assertIn("budget", detail)
        prober.assert_not_called()

    def test_budget_cuts_off_between_retry_rounds(self):
        # 首轮在预算内正常探测+发 stop；第二轮开场已超预算 -> 判失败不再打
        clock = mock.Mock(side_effect=[0.0, 1.0, 5.0, 100.0])
        prober = mock.Mock(return_value={"pid": 42})
        stopper = mock.Mock(return_value=True)
        stopped, issued, detail = executor.stop_session_confirmed(
            SID, retries=2, prober=prober, stopper=stopper,
            sleeper=mock.Mock(), budget_s=60.0, clock=clock)
        self.assertFalse(stopped)
        self.assertTrue(issued)                   # 首轮那发算数
        self.assertIn("budget", detail)
        self.assertEqual(stopper.call_count, 1)


def _drop_inbox(action, req_id):
    config.ensure_state_dirs()
    path = config.INBOX_DIR / f"{uuid.uuid4()}.json"
    path.write_text(
        json.dumps({"id": req_id, "action": action, "comment": None,
                    "ts": "2026-08-07T00:00:00Z"}),
        encoding="utf-8",
    )
    return path


class StopFailureLedgerTestCase(unittest.TestCase):
    """accept 路径为样本：stop 确认失败 -> 台账 + 通知 + 打点，交付照常落账。"""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()
        p = mock.patch.object(actd.notify, "notify", mock.Mock(return_value=True))
        self.notify = p.start()
        self.addCleanup(p.stop)
        # §15: 通知文案跟 UI 语言走——钉 zh，zh-标题断言不受 runner locale 影响
        lang = mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "zh"})
        lang.start()
        self.addCleanup(lang.stop)

    def _accept(self, confirmed):
        req = Requirement(id="R-900", title="stop 台账测试",
                          status=State.REVIEW.value,
                          execution={"session_id": SID, "done": True})
        registry.save(req)
        _drop_inbox("accept", "R-900")
        with mock.patch.object(actd.executor, "stop_session_confirmed",
                               confirmed):
            actd.process_inbox()
        return registry.load("R-900")

    def test_confirmed_failure_leaves_ledger_but_never_blocks(self):
        req = self._accept(mock.Mock(
            return_value=(False, True, f"session {SID} still alive (pid 7)")))
        self.assertEqual(req.status, State.DELIVERED.value)   # 绝不阻塞落账
        ex = req.execution or {}
        self.assertTrue(ex.get("stop_failed_at"))
        self.assertIn("still alive", ex.get("stop_failed_error") or "")
        self.assertIn("[stop-failed]", req.notes or "")       # 看板可见可搜
        titles = [c.args[0] for c in self.notify.call_args_list]
        self.assertTrue(any("没停住" in t for t in titles))
        events = [e.get("event") for e in analytics.read_events()]
        self.assertIn("stop_failed", events)

    def test_confirmed_success_clears_old_ledger(self):
        req = Requirement(id="R-901", title="台账清除测试",
                          status=State.REVIEW.value,
                          execution={"session_id": SID, "done": True,
                                     "stop_failed_at": "2026-08-06T00:00:00Z",
                                     "stop_failed_error": "old failure"})
        registry.save(req)
        _drop_inbox("accept", "R-901")
        with mock.patch.object(actd.executor, "stop_session_confirmed",
                               mock.Mock(return_value=(True, True, "stopped"))):
            actd.process_inbox()
        ex = registry.load("R-901").execution or {}
        self.assertNotIn("stop_failed_at", ex)                # 台账描述当前事实
        self.assertNotIn("stop_failed_error", ex)

    def test_confirmed_exception_is_best_effort_and_ledgered(self):
        req = self._accept(mock.Mock(side_effect=RuntimeError("roster exploded")))
        self.assertEqual(req.status, State.DELIVERED.value)
        self.assertIn("[stop-failed]", req.notes or "")

    def test_analytics_detail_carries_no_full_uuid_or_pid(self):
        # TELEMETRY 红线（issue #37 收紧）：analytics 默认上传——stop_failed 事件
        # 不再携带任何原文（此前留 UUID 前 8 位 + 脱 PID 的截断 error），只带 req
        # + 分类 failure_id（无法分类时整键缺席）；全量 detail 只进本机台账
        # （stop_failed_error/notes）
        req = Requirement(id="R-902", title="打点脱敏测试",
                          status=State.REVIEW.value,
                          execution={"session_id": SID, "done": True})
        registry.save(req)
        _drop_inbox("accept", "R-902")
        with mock.patch.object(actd.executor, "stop_session_confirmed",
                               mock.Mock(return_value=(
                                   False, True,
                                   f"session {SID} still alive (pid 7)"))):
            actd.process_inbox()
        req = registry.load("R-902")
        self.assertIn(SID, (req.execution or {}).get("stop_failed_error") or "")
        events = [e for e in analytics.read_events()
                  if e.get("event") == "stop_failed" and e.get("req") == "R-902"]
        self.assertTrue(events)
        for ev in events:
            self.assertNotIn("error", ev)         # 原文一个字节都不出机
            blob = json.dumps(ev)
            self.assertNotIn(SID[:8], blob)       # 连 UUID 前缀也不再上传
            self.assertNotIn("pid", blob)
            # "still alive" 无 §25 分类 → failure_id 整键缺席（诚实未知）
            self.assertNotIn("failure_id", ev)


if __name__ == "__main__":
    unittest.main()
