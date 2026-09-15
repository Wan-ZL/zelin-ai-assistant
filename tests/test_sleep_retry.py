"""§71.3 被睡眠打断的会话：收割前原地重试**一次**，第二次才上待验收列。

issue #311：三张卡都以「中断收割 · AI 需要拍板」的姿态堆进待验收，而中断的原因
只是电脑睡着了——那不是「需要人拍板」。判例钉住：
  - 有 §71.2 实测的 `sleep_interrupted` 旗 → 同 sid、同上下文 resume 一次，
    卡留在 executing，notes 留痕，`sleep_retry_used` 上卡；
  - 上限一次：第二次中断照旧按 #119 收割进待验收（interrupted_reason=blocked）；
  - 没有那面旗的受阻会话（agent 真的在问问题）永远不重试——重试要花钱，
    证据必须是测量值不是猜测；
  - briefing / steer 的安全窗口仍排在它前面（既有优先级不变）；
  - executor.resume 抛异常也不许崩 pass。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act import

from act import actd
from act.lib import config, registry
from act.lib.actd import reconcile
from act.lib.registry import Requirement, State

BLOCKED_AGENT = {"session_id": "sid-1", "state": "waiting_for_input"}


def _mk(req_id: str, **execution) -> Requirement:
    ex = {"session_id": "sid-1", "dispatched_at": "2026-09-09T04:10:00Z"}
    ex.update(execution)
    req = Requirement(id=req_id, title=f"{req_id} 睡眠重试", status=State.EXECUTING.value,
                      execution=ex)
    registry.save(req)
    return req


def _reconcile_blocked(req: Requirement, executor):
    """把这张卡交给 reconcile 的 blocked 分支（roster 说它在等输入）。"""
    with mock.patch.object(actd, "executor", executor), \
            mock.patch.object(reconcile.notify, "notify", return_value=True), \
            mock.patch.object(actd, "_run_claude_agents",
                              return_value=[dict(BLOCKED_AGENT)]):
        actd.reconcile_executing(config.Config(), set())
    return registry.load(req.id)


class SleepRetryTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for path in config.REGISTRY_DIR.glob("*.yaml"):
            path.unlink()

    def _executor(self) -> mock.MagicMock:
        ex = mock.MagicMock()
        ex.resume.return_value = True
        ex.harvest_delivery.return_value = {}
        return ex

    def test_sleep_interrupted_card_resumes_once_and_stays_executing(self):
        req = _mk("R-8300", sleep_interrupted=True)
        executor = self._executor()
        saved = _reconcile_blocked(req, executor)
        executor.resume.assert_called_once()
        self.assertEqual(saved.status, State.EXECUTING.value)
        self.assertIs(saved.execution["sleep_retry_used"], True)
        self.assertIn("睡眠打断", saved.notes or "")

    def test_second_interruption_harvests_to_review(self):
        req = _mk("R-8301", sleep_interrupted=True, sleep_retry_used=True)
        executor = self._executor()
        saved = _reconcile_blocked(req, executor)
        executor.resume.assert_not_called()
        self.assertEqual(saved.status, State.REVIEW.value)
        self.assertEqual(saved.execution.get("interrupted_reason"), "blocked")

    def test_plain_blocked_session_is_never_retried(self):
        # 真的在问问题的会话没有实测的睡眠旗 —— 一分钱都不再花
        req = _mk("R-8302")
        executor = self._executor()
        saved = _reconcile_blocked(req, executor)
        executor.resume.assert_not_called()
        self.assertEqual(saved.status, State.REVIEW.value)
        self.assertNotIn("sleep_retry_used", saved.execution)

    def test_briefing_window_still_wins(self):
        # §44.3 注入窗口排在 §71.3 之前：有待注入内容就先注入（既有优先级不变）
        req = _mk("R-8303", sleep_interrupted=True, pending_briefings=["FYI"])
        executor = self._executor()
        saved = _reconcile_blocked(req, executor)
        executor.brief.assert_called_once()
        executor.resume.assert_not_called()
        self.assertEqual(saved.status, State.EXECUTING.value)
        self.assertNotIn("sleep_retry_used", saved.execution)

    def test_resume_failure_never_kills_the_pass(self):
        req = _mk("R-8304", sleep_interrupted=True)
        executor = self._executor()
        executor.resume.side_effect = RuntimeError("claude not found")
        saved = _reconcile_blocked(req, executor)
        self.assertEqual(saved.status, State.EXECUTING.value)
        self.assertIs(saved.execution["sleep_retry_used"], True)  # 上限已消费

    def test_flag_is_persisted_before_the_resume_launch(self):
        # resume 自己会落盘 execution —— 标记必须先在 execution 里就位，否则
        # 崩在中间会让这张卡每个 pass 重试一次（重复烧钱）。
        req = _mk("R-8305", sleep_interrupted=True)
        seen = {}
        executor = self._executor()
        executor.resume.side_effect = lambda r, c: seen.setdefault(
            "used", (registry.load(r.id).execution or {}).get("sleep_retry_used"))
        _reconcile_blocked(req, executor)
        self.assertIs(seen["used"], True)


if __name__ == "__main__":
    unittest.main()
