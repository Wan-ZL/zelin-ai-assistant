"""§71.3 被睡眠打断的会话：收割前原地重试**一次**，第二次才上待验收列。

issue #311：三张卡都以「中断收割 · AI 需要拍板」的姿态堆进待验收，而中断的原因
只是电脑睡着了——那不是「需要人拍板」。判例钉住：
  - 有 §71.2 实测的 `sleep_interrupted` 旗 → 同 sid、同上下文 resume 一次，
    卡留在 executing，notes 留痕，`sleep_retry_used` 上卡；
  - 上限一次：第二次中断照旧按 #119 收割进待验收（interrupted_reason=blocked）；
  - 没有那面旗的受阻会话（agent 真的在问问题）永远不重试——重试要花钱，
    证据必须是测量值不是猜测；
  - briefing / steer 的安全窗口仍排在它前面（既有优先级不变）；
  - executor.resume 抛异常也不许崩 pass；
  - **两个入口**（2026-09-14 review 修正）：roster 说 blocked 的会话固然要走这道
    门，#311 的真实形态却是 agent 进程被睡眠切断后**直接退出**（roster = done、
    transcript 末尾 `API Error: Your computer went to sleep mid-response`）——
    那条路收割前也要问同一句，且只在这轮收割空手时才重试（真交付过的不动）；
  - 证据要新鲜：睡醒之后还活着的会话，那面旗当场清掉——它证明的是「这台机器
    睡过一觉」，不是「这个会话被切断了」。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act import

from act import actd
from act.lib import config, registry
from act.lib.actd import reconcile
from act.lib.registry import Requirement, State

BLOCKED_AGENT = {"session_id": "sid-1", "state": "waiting_for_input"}
DONE_AGENT = {"session_id": "sid-1", "state": "completed"}
LIVE_AGENT = {"session_id": "sid-1", "state": "working"}


def _mk(req_id: str, **execution) -> Requirement:
    ex = {"session_id": "sid-1", "dispatched_at": "2026-09-09T04:10:00Z"}
    ex.update(execution)
    req = Requirement(id=req_id, title=f"{req_id} 睡眠重试", status=State.EXECUTING.value,
                      execution=ex)
    registry.save(req)
    return req


def _reconcile(req: Requirement, executor, agent: dict):
    """把这张卡交给 reconcile 的某一条 roster 分支。"""
    with mock.patch.object(actd, "executor", executor), \
            mock.patch.object(reconcile.notify, "notify", return_value=True), \
            mock.patch.object(actd, "_run_claude_agents", return_value=[dict(agent)]):
        actd.reconcile_executing(config.Config(), set())
    return registry.load(req.id)


def _reconcile_blocked(req: Requirement, executor):
    """roster 说它在等输入。"""
    return _reconcile(req, executor, BLOCKED_AGENT)


def _reconcile_done(req: Requirement, executor):
    """roster 说它已经退出（睡眠切断 agent 时的真实形态）。"""
    return _reconcile(req, executor, DONE_AGENT)


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


class SleepKilledAgentTestCase(unittest.TestCase):
    """#311 的真实形态：会话不是「在等输入」，是**没了**（roster = done）。"""

    def setUp(self):
        config.ensure_state_dirs()
        for path in config.REGISTRY_DIR.glob("*.yaml"):
            path.unlink()

    def _executor(self, harvest=None) -> mock.MagicMock:
        ex = mock.MagicMock()
        ex.resume.return_value = True
        ex.harvest_delivery.return_value = dict(harvest or {})
        return ex

    def test_exited_session_with_sleep_evidence_is_retried_not_harvested(self):
        req = _mk("R-8310", sleep_interrupted=True)
        executor = self._executor()
        saved = _reconcile_done(req, executor)
        executor.resume.assert_called_once()
        self.assertEqual(saved.status, State.EXECUTING.value)
        self.assertIs(saved.execution["sleep_retry_used"], True)
        self.assertNotIn("done", saved.execution)      # 收割没发生
        self.assertNotIn("review_at", saved.execution)

    def test_a_session_that_actually_delivered_is_never_retried(self):
        # 交付物在 transcript 里 = 活干完了，再 resume 只是白烧一次钱
        req = _mk("R-8311", sleep_interrupted=True)
        executor = self._executor({"final_draft": "FINAL DRAFT\n交付内容"})
        saved = _reconcile_done(req, executor)
        executor.resume.assert_not_called()
        self.assertEqual(saved.status, State.REVIEW.value)

    def test_exited_session_without_evidence_harvests_as_before(self):
        req = _mk("R-8312")
        executor = self._executor()
        saved = _reconcile_done(req, executor)
        executor.resume.assert_not_called()
        self.assertEqual(saved.status, State.REVIEW.value)
        self.assertNotIn("sleep_retry_used", saved.execution)

    def test_the_cap_holds_on_this_path_too(self):
        req = _mk("R-8313", sleep_interrupted=True, sleep_retry_used=True)
        executor = self._executor()
        saved = _reconcile_done(req, executor)
        executor.resume.assert_not_called()
        self.assertEqual(saved.status, State.REVIEW.value)


class StaleEvidenceTestCase(unittest.TestCase):
    """旗是「机器睡过一觉」的证据，不是「这个会话被切断」的证据——会话在睡醒
    之后还活着就当场清掉，否则下午真去提问时会白挨一次重试（review 修正）。"""

    def setUp(self):
        config.ensure_state_dirs()
        for path in config.REGISTRY_DIR.glob("*.yaml"):
            path.unlink()

    def test_a_session_that_survived_the_sleep_loses_the_flag(self):
        req = _mk("R-8320", sleep_interrupted=True, slept_seconds=7200)
        executor = mock.MagicMock()
        saved = _reconcile(req, executor, LIVE_AGENT)
        executor.resume.assert_not_called()
        self.assertEqual(saved.status, State.EXECUTING.value)
        self.assertNotIn("sleep_interrupted", saved.execution)
        self.assertEqual(saved.execution["slept_seconds"], 7200)   # 账还在

    def test_then_a_genuine_question_is_harvested_not_retried(self):
        req = _mk("R-8321", sleep_interrupted=True)
        executor = mock.MagicMock()
        executor.resume.return_value = True
        executor.harvest_delivery.return_value = {}
        _reconcile(req, executor, LIVE_AGENT)          # 中午：睡醒了还在干活
        saved = _reconcile_blocked(registry.load("R-8321"), executor)  # 下午：真提问
        executor.resume.assert_not_called()
        self.assertEqual(saved.status, State.REVIEW.value)


if __name__ == "__main__":
    unittest.main()
