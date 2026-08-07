"""§46 stop 确认重试 + 失败台账（session 生命周期可靠性，2026-08-07）.

生产日志里 stop_session→False 一天出现 4 次而无人跟进——session 可能还活着
继续烧钱/占资源。判例：

executor.stop_session_confirmed(sid) -> (stopped, issued, detail)
  - roster 无活 pid：确认成功 (True, False, "not running")，一次 stop 都不发；
  - 有 pid、stop 后死掉：(True, True, ...)；重试轮之间退避（sleeper 可注入）；
  - 重试打满仍存活：(False, ...)——这是要留痕的失败。
  三个 seam（prober/stopper/sleeper）全部可注入，绝不 spawn 真 claude。

actd._stop_session_tracked（所有 inbox stop 调用点的统一外壳）：
  - stop 确认失败 -> execution.stop_failed_at/stop_failed_error 台账 +
    notes [stop-failed] 标签（notes_text 投影到看板）+ notify.msg_stop_failed
    + analytics `stop_failed`，但绝不阻塞调用方的状态落账；
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


if __name__ == "__main__":
    unittest.main()
