"""§71.2 诚实耗时：每 pass 量一次挂起时长，记进在跑卡的 `slept_seconds` 并投影。

issue #311：「卡面上的『耗时 4 小时 52 分』数的是电脑睡着的时间。」本判例钉住
测量（wall 前进量 − monotonic 前进量）、门槛（< 5 分钟什么都不写）、落账（只落
EXECUTING 且有会话的卡）、投影（运行中 / 待验收行的 add-only `slept_seconds`），
以及 §48 的单采样者不变式：本模块绝不碰 alerts.WAKE_STATE。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act import

from act import actd
from act.lib import config, dashboard, power, registry
from act.lib.actd import alerts
from act.lib.registry import Requirement, State


def _mk(req_id: str, status: str = State.EXECUTING.value, execution=None) -> Requirement:
    req = Requirement(id=req_id, title=f"{req_id} 睡眠账", status=status,
                      execution=execution if execution is not None else {"session_id": "sid-1"})
    registry.save(req)
    return req


class SuspensionMeasurementTestCase(unittest.TestCase):
    def setUp(self):
        power.SUSPEND_STATE.update({"last_wall": None, "last_mono": None})
        self.addCleanup(power.SUSPEND_STATE.update,
                        {"last_wall": None, "last_mono": None})

    def test_first_sample_has_no_baseline(self):
        self.assertEqual(power.sample_suspension(wall=1000.0, mono=10.0), 0.0)

    def test_long_pass_is_not_sleep(self):
        # 两个时钟同步前进 420 s（一次吃满超时的 claude 调用）= 没睡
        power.sample_suspension(wall=1000.0, mono=10.0)
        self.assertEqual(power.sample_suspension(wall=1420.0, mono=430.0), 0.0)

    def test_real_suspension_is_the_difference(self):
        # 墙上走了 3 小时，monotonic 只走了 12 s —— 睡了 ~3 小时
        power.sample_suspension(wall=1000.0, mono=10.0)
        slept = power.sample_suspension(wall=1000.0 + 10800, mono=22.0)
        self.assertAlmostEqual(slept, 10788.0)

    def test_clock_rollback_never_goes_negative(self):
        power.sample_suspension(wall=1000.0, mono=10.0)
        self.assertEqual(power.sample_suspension(wall=900.0, mono=20.0), 0.0)

    def test_never_touches_the_radar_wake_baseline(self):
        # §48 单采样者不变式：两个采样者各用各的基线
        before = dict(alerts.WAKE_STATE)
        power.sample_suspension(wall=1.0, mono=1.0)
        power.sample_suspension(wall=99999.0, mono=2.0)
        self.assertEqual(alerts.WAKE_STATE, before)


class CreditSleepTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for path in config.REGISTRY_DIR.glob("*.yaml"):
            path.unlink()
        power.SUSPEND_STATE.update({"last_wall": None, "last_mono": None})
        self.addCleanup(power.SUSPEND_STATE.update,
                        {"last_wall": None, "last_mono": None})

    def test_gap_below_threshold_writes_nothing(self):
        _mk("R-8200")
        with mock.patch.object(actd, "save") as saver:
            self.assertEqual(actd._power_sample(), 0)
        saver.assert_not_called()

    def test_gap_lands_on_executing_cards_only(self):
        _mk("R-8201")
        _mk("R-8202", status=State.APPROVED.value, execution={})
        _mk("R-8203", execution={})                 # executing 但没有会话：不是在跑
        power.sample_suspension(wall=1000.0, mono=10.0)
        with mock.patch.object(power.time, "time", return_value=1000.0 + 7200), \
                mock.patch.object(power.time, "monotonic", return_value=20.0):
            self.assertEqual(actd._power_sample(), 1)
        ex = registry.load("R-8201").execution
        self.assertEqual(ex["slept_seconds"], 7190)
        self.assertIs(ex["sleep_interrupted"], True)
        self.assertNotIn("slept_seconds", registry.load("R-8202").execution or {})
        self.assertNotIn("slept_seconds", registry.load("R-8203").execution or {})

    def test_second_sleep_accumulates(self):
        _mk("R-8204", execution={"session_id": "sid-1", "slept_seconds": 600})
        power.credit_sleep(actd._ctx(), 1800.0)
        self.assertEqual(registry.load("R-8204").execution["slept_seconds"], 2400)

    def test_garbage_value_starts_from_zero(self):
        _mk("R-8205", execution={"session_id": "sid-1", "slept_seconds": "banana"})
        power.credit_sleep(actd._ctx(), 900.0)
        self.assertEqual(registry.load("R-8205").execution["slept_seconds"], 900)

    def test_accounting_never_raises(self):
        _mk("R-8206")
        with mock.patch.object(power, "sample_suspension", side_effect=OSError("boom")):
            self.assertEqual(actd._power_sample(), 0)


class SleptProjectionTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for path in config.REGISTRY_DIR.glob("*.yaml"):
            path.unlink()

    def test_review_row_carries_slept_seconds(self):
        _mk("R-8210", status=State.REVIEW.value,
            execution={"session_id": "sid-9", "done": True,
                       "dispatched_at": "2026-09-09T04:10:00Z",
                       "review_at": "2026-09-09T09:02:00Z",
                       "slept_seconds": 17400})
        dash = dashboard.build_dashboard(cfg=config.Config(), agents=[])
        row = [r for r in dash["review"] if r["id"] == "R-8210"][0]
        self.assertEqual(row["slept_seconds"], 17400)

    def test_card_that_never_slept_omits_the_key(self):
        _mk("R-8211", status=State.REVIEW.value,
            execution={"session_id": "sid-9", "done": True,
                       "review_at": "2026-09-09T09:02:00Z"})
        dash = dashboard.build_dashboard(cfg=config.Config(), agents=[])
        row = [r for r in dash["review"] if r["id"] == "R-8211"][0]
        self.assertNotIn("slept_seconds", row)


if __name__ == "__main__":
    unittest.main()
