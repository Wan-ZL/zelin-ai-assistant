"""executor 这个搭档降级成 None 时，reconcile 的两个探针原地站住（CONTRACT §58.3
的 `Daemon` 快照 + 宪法第 11 条；§37.1 追记的改名探针 / §71.3 的睡眠重试）。

`act/actd.py` 用 try/except 导入 `act.executor`（headless claude 那一层），导不进来
就是 `None`——`Daemon` 快照如实带着那个 None，下游每一处都自己判。这条路在真机上
出现过：装机脚本还没落地、或 `claude` CLI 不在 PATH 上的那台机器照样在跑 actd。

判例钉两个**新加的** None 判：
  * 活着的会话（working / idle）不去读 transcript 改名——连那 120 s 的节流台账
    都不碰（没探过就不该记「刚探过」，否则 executor 回来后第一次改名要白等两分钟）；
  * 被睡眠打断的受阻会话**不消费**那一次重试额度（`sleep_retry_used` 不落卡），
    照 #119 收割进待验收；executor 回来的那天这张卡还能用上它那一次原地重试。
两条都绝不抛：探针坏了不许崩 pass。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import config, registry
from act.lib.actd import reconcile
from act.lib.registry import Requirement, State

LIVE_AGENT = {"session_id": "sid-nx", "state": "working"}
BLOCKED_AGENT = {"session_id": "sid-nx", "state": "waiting_for_input"}


def _card(req_id: str, **execution) -> Requirement:
    ex = {"session_id": "sid-nx", "dispatched_at": "2026-09-15T04:10:00Z"}
    ex.update(execution)
    req = Requirement(id=req_id, title=f"{req_id} 没有 executor 的一轮",
                      status=State.EXECUTING.value, execution=ex)
    registry.save(req)
    return req


class ReconcileWithoutExecutorTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for path in config.REGISTRY_DIR.glob("*.yaml"):
            path.unlink()
        for ledger in (actd._HARVEST_PROBE_AT, actd._TITLE_PROBE_AT):
            ledger.clear()
            self.addCleanup(ledger.clear)

    def _pass(self, req: Requirement, agent: dict) -> Requirement:
        """一轮 reconcile，roster 里只有这一条会话，且 executor 没导进来。"""
        with mock.patch.object(actd, "executor", None), \
                mock.patch.object(reconcile.notify, "notify", return_value=True), \
                mock.patch.object(actd, "_run_claude_agents", return_value=[dict(agent)]):
            actd.reconcile_executing(config.Config(), set())
        return registry.load(req.id)

    def test_a_live_session_is_not_renamed_and_the_throttle_stays_untouched(self):
        req = _card("R-8400")
        saved = self._pass(req, LIVE_AGENT)
        self.assertEqual(saved.status, State.EXECUTING.value)
        self.assertFalse(saved.display_title)
        self.assertEqual(dict(actd._TITLE_PROBE_AT), {})   # 没探过 = 不记「刚探过」

    def test_a_sleep_interrupted_session_keeps_its_one_retry_and_is_harvested(self):
        req = _card("R-8401", sleep_interrupted=True)
        saved = self._pass(req, BLOCKED_AGENT)
        self.assertEqual(saved.status, State.REVIEW.value)
        self.assertNotIn("sleep_retry_used", saved.execution)   # 额度一分没花
        self.assertEqual(saved.execution.get("interrupted_reason"), "blocked")
        self.assertIn("[会话受阻]", saved.notes or "")


if __name__ == "__main__":
    unittest.main()
