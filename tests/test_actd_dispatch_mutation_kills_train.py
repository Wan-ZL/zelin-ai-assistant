"""actd/dispatch — dev 列车改动行上的变异幸存体判例（CONTRACT §65.1 / §71.1 / §51 /
§78）。

三条契约：

  * **§65.1 通道关掉之后不再续派**：免批批准（`execution.auto_dispatched`）但还没
    起跑的 self_improve 卡本 pass 就退回潜在任务（§78 / issue #447 提案车道退役前
    是退回提案列）——「已处理完」的答案必须真的把这张卡从派发链上摘下去，否则它
    照样被派出去烧执行器与 API 额度（#335 review 复现）。
  * **executor 缺席 = 按住，不是一次派发失败**：没有执行器时这张卡不进 `_dispatch_one`
    ——不写卡、不落 `last_error`、不打 `dispatch_failed` 遥测；下一 pass 照常重试。
  * **§71.1 机器在睡按住整个 pass，且状态只探一次**：懒算的判决全 pass 共用，
    半 pass 睡半 pass 醒会让「为什么这张派了那张没派」无法解释。

等价体（不强杀）：`_withdraw_frozen_lane` 的 `return False`→`return None`。唯一调用点
是 `if _withdraw_frozen_lane(...): continue`，False 与 None 同为假值——没有可观察差异。

沙箱 AIASSISTANT_HOME；executor / power 探针全 mock，绝不 spawn 真 claude。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.self_improve_testkit import lane_card

from act import actd
from act.lib import config, power, registry
from act.lib.registry import Requirement, State


class _Exec:
    """Cooperative executor: dispatch stamps a session like the real one."""

    class DispatchError(Exception):
        pass

    def __init__(self):
        self.dispatched = []

    def dispatch(self, req, cfg):
        self.dispatched.append(req.id)
        ex = dict(req.execution or {})
        ex["session_id"] = f"sid-{req.id}"
        req.execution = ex
        registry.save(req)


def _approved(rid):
    req = Requirement(id=rid, title=f"手打的活 {rid}", status=State.APPROVED.value,
                      sources=[{"who": "zelin", "channel": "quick",
                                "date": "2026-09-10", "quote": "q"}],
                      target_repo=TMP_HOME, target_kind="existing",
                      cost_estimate_usd=1.0)
    registry.save(req)
    return req


class DispatchGateTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.events = []
        mock.patch.object(actd.notify, "notify").start()
        mock.patch.object(actd.analytics, "log_event",
                          side_effect=lambda e, **m: self.events.append(e)).start()
        self.addCleanup(mock.patch.stopall)

    def test_a_frozen_lane_card_is_withdrawn_instead_of_dispatched(self):
        """§65.1：退回潜在任务的卡不许在同一个 pass 里又被派出去（§78 落点）。"""
        registry.save(lane_card("P-7", status=State.APPROVED.value,
                                execution={"auto_dispatched": True}))
        ex = _Exec()
        with mock.patch.object(actd, "executor", ex):
            self.assertEqual(actd.dispatch_approved(config.Config()), 0)
        self.assertEqual(ex.dispatched, [])
        req = registry.load("P-7")
        self.assertEqual(req.status, State.DETECTED.value)   # §78：退回潜在任务
        self.assertNotIn("auto_dispatched", req.execution or {})

    def test_a_missing_executor_holds_the_card_rather_than_failing_it(self):
        _approved("R-91")
        with mock.patch.object(actd, "executor", None):
            self.assertEqual(actd.dispatch_approved(config.Config()), 0)
        req = registry.load("R-91")
        self.assertEqual(req.status, State.APPROVED.value)
        self.assertNotIn("last_error", req.execution or {})
        self.assertNotIn("session_id", req.execution or {})
        self.assertNotIn("dispatch_failed", self.events)

    def test_a_sleeping_machine_holds_the_pass_on_a_single_probe(self):
        """§71.1：判决懒算一次，本 pass 内所有 approved 卡共用。"""
        _approved("R-92")
        _approved("R-93")
        ex = _Exec()
        with mock.patch.object(actd, "executor", ex), \
                mock.patch.object(power, "machine_asleep", return_value=True) as probe:
            self.assertEqual(actd.dispatch_approved(config.Config()), 0)
        self.assertEqual(ex.dispatched, [])
        self.assertEqual(probe.call_count, 1)
        for rid in ("R-92", "R-93"):
            self.assertEqual(registry.load(rid).status, State.APPROVED.value)


if __name__ == "__main__":   # pragma: no cover
    unittest.main()
