"""§28 追记（issue #29）通知偏好：安静时段 + 分类开关。

抑制在**写方**（act/lib/notify.notify），不在 §28 的 drain——队列只留 10 分钟
（STALE_AFTER_S），「压到早上再弹」在那之下是谎话。写方不入队 = 队列的 stale /
burst / 消费即删语义一个字不动，这一条由 QueueUntouchedTestCase 钉住。

钉的行为：
  - 出厂值 = 本改动前的行为（安静时段关、三类全开）——新装机一条通知都不少；
  - 分类开关关掉 → 该类任何时候都不入队；
  - 安静时段盖住当前时刻 → 除失败类外一律不入队（跨午夜算两段）；
  - 失败类穿透安静时段，只有自己的开关能静音它；
  - 坏值 / 零长窗 / 读配置爆炸 → fail-open（照发，宁可多一条也不静音）；
  - 被偏好吞掉返回 True（不是失败——False 只表示「没能交给消费方」）。
"""
import time
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config, notify


def _cfg(**kw) -> config.Config:
    cfg = config.Config()
    for key, value in kw.items():
        setattr(cfg, key, value)
    return cfg


def _at(hour: int, minute: int = 0) -> time.struct_time:
    """本地时间注入缝：只有 tm_hour / tm_min 被读。"""
    return time.struct_time((2026, 9, 12, hour, minute, 0, 5, 255, -1))


class QuietWindowTestCase(unittest.TestCase):
    """in_quiet_hours 是纯函数——判例直接钉它的每一格。"""

    def test_overnight_window_wraps_past_midnight(self):
        for hour in (22, 23, 0, 3, 7):
            with self.subTest(hour=hour):
                self.assertTrue(notify.in_quiet_hours("22:00", "08:00", hour * 60))
        for hour in (8, 12, 21):
            with self.subTest(hour=hour):
                self.assertFalse(notify.in_quiet_hours("22:00", "08:00", hour * 60))

    def test_same_day_window(self):
        self.assertTrue(notify.in_quiet_hours("13:00", "14:00", 13 * 60 + 30))
        self.assertFalse(notify.in_quiet_hours("13:00", "14:00", 12 * 60 + 59))

    def test_half_open_interval(self):
        # 开始的那一分钟算在内，结束的那一分钟不算——两端都不许重叠/漏
        self.assertTrue(notify.in_quiet_hours("22:00", "08:00", 22 * 60))
        self.assertFalse(notify.in_quiet_hours("22:00", "08:00", 8 * 60))

    def test_zero_length_window_is_off(self):
        self.assertFalse(notify.in_quiet_hours("08:00", "08:00", 8 * 60))

    def test_garbage_endpoints_fail_open(self):
        for start, end in (("nope", "08:00"), ("22:00", ""), (None, None), ("25:00", "08:00")):
            with self.subTest(start=start, end=end):
                self.assertFalse(notify.in_quiet_hours(start, end, 3 * 60))


class SuppressionReasonTestCase(unittest.TestCase):
    def test_factory_defaults_suppress_nothing(self):
        cfg = config.Config()
        self.assertFalse(cfg.quiet_hours_enabled)
        for kind in (None, "general", notify.KIND_PROPOSAL, notify.KIND_REVIEW_READY,
                     notify.KIND_NEEDS_INPUT, notify.KIND_FAILURE, "recap_ready"):
            with self.subTest(kind=kind):
                self.assertIsNone(notify.suppression_reason(kind, cfg, now=_at(3)))

    def test_category_switch_off_silences_that_class_at_any_hour(self):
        pairs = ((notify.KIND_PROPOSAL, "notify_proposals"),
                 (notify.KIND_NEEDS_INPUT, "notify_needs_input"),
                 (notify.KIND_FAILURE, "notify_failures"))
        for kind, key in pairs:
            with self.subTest(kind=kind):
                cfg = _cfg(**{key: False})
                self.assertEqual(notify.suppression_reason(kind, cfg, now=_at(12)), "category")
                # 其余类不受这把开关影响
                other = notify.KIND_REVIEW_READY
                self.assertIsNone(notify.suppression_reason(other, cfg, now=_at(12)))

    def test_review_ready_has_no_category_switch_here(self):
        """完成提醒的三档 `review_notify` 归消费方（壳）执法，写方不碰。"""
        self.assertNotIn(notify.KIND_REVIEW_READY, notify.CATEGORY_PREFERENCE)
        cfg = _cfg(review_notify="off")
        self.assertIsNone(notify.suppression_reason(notify.KIND_REVIEW_READY, cfg, now=_at(12)))

    def test_quiet_hours_silence_everything_but_failures(self):
        cfg = _cfg(quiet_hours_enabled=True, quiet_hours_start="22:00", quiet_hours_end="08:00")
        for kind in (None, "general", "recap_ready", notify.KIND_PROPOSAL,
                     notify.KIND_REVIEW_READY, notify.KIND_NEEDS_INPUT):
            with self.subTest(kind=kind):
                self.assertEqual(notify.suppression_reason(kind, cfg, now=_at(2, 30)), "quiet_hours")
        self.assertIsNone(notify.suppression_reason(notify.KIND_FAILURE, cfg, now=_at(2, 30)))

    def test_outside_the_window_nothing_is_suppressed(self):
        cfg = _cfg(quiet_hours_enabled=True, quiet_hours_start="22:00", quiet_hours_end="08:00")
        self.assertIsNone(notify.suppression_reason(notify.KIND_PROPOSAL, cfg, now=_at(9)))

    def test_failures_still_obey_their_own_switch_inside_quiet_hours(self):
        cfg = _cfg(quiet_hours_enabled=True, quiet_hours_start="22:00",
                   quiet_hours_end="08:00", notify_failures=False)
        self.assertEqual(notify.suppression_reason(notify.KIND_FAILURE, cfg, now=_at(2)), "category")

    def test_window_off_means_the_endpoints_are_ignored(self):
        cfg = _cfg(quiet_hours_enabled=False, quiet_hours_start="00:00", quiet_hours_end="23:59")
        self.assertIsNone(notify.suppression_reason(notify.KIND_PROPOSAL, cfg, now=_at(12)))

    def test_clock_defaults_to_now_when_not_injected(self):
        cfg = _cfg(quiet_hours_enabled=True, quiet_hours_start="00:00", quiet_hours_end="23:59")
        # 23:59 之外的那一分钟很难撞上；无论本机几点，这个窗覆盖除一分钟外的全天
        reason = notify.suppression_reason(notify.KIND_PROPOSAL, cfg)
        self.assertIn(reason, ("quiet_hours", None))


class OverridesTestCase(unittest.TestCase):
    """六把键都在 overrides 白名单里，坏值回落出厂值（per-entry 跳过）。"""

    def _loaded(self, overrides: dict) -> config.Config:
        with mock.patch.object(config, "_read_overrides", return_value=overrides):
            return config.load_config()

    def test_all_six_keys_land(self):
        cfg = self._loaded({"quiet_hours_enabled": True, "quiet_hours_start": "1:05",
                            "quiet_hours_end": "07:30", "notify_proposals": False,
                            "notify_needs_input": False, "notify_failures": False})
        self.assertTrue(cfg.quiet_hours_enabled)
        self.assertEqual(cfg.quiet_hours_start, "01:05")   # 归一为两位小时
        self.assertEqual(cfg.quiet_hours_end, "07:30")
        self.assertFalse(cfg.notify_proposals)
        self.assertFalse(cfg.notify_needs_input)
        self.assertFalse(cfg.notify_failures)

    def test_garbage_clock_keeps_the_factory_value(self):
        cfg = self._loaded({"quiet_hours_start": "half past ten", "quiet_hours_end": "99:99"})
        self.assertEqual(cfg.quiet_hours_start, "22:00")
        self.assertEqual(cfg.quiet_hours_end, "08:00")


class QueueUntouchedTestCase(unittest.TestCase):
    """notify() 半边：被吞 = 一个队列文件都不写，且返回 True（不是失败）。"""

    def _notify(self, kind, cfg):
        with mock.patch.object(config, "load_config", return_value=cfg), \
                mock.patch.object(notify, "_native_notify") as native:
            ok = notify.notify("t", "b", kind=kind)
        return ok, native

    def test_suppressed_never_reaches_the_queue_and_reports_success(self):
        cfg = _cfg(notify_proposals=False)
        ok, native = self._notify(notify.KIND_PROPOSAL, cfg)
        self.assertTrue(ok)             # 偏好吞掉 ≠ 失败
        native.assert_not_called()      # §28 队列一个字节都没动

    def test_allowed_still_rides_the_relay_with_its_kind(self):
        ok, native = self._notify(notify.KIND_PROPOSAL, config.Config())
        native.assert_called_once_with("t", "b", None, kind=notify.KIND_PROPOSAL)
        self.assertIs(ok, native.return_value)

    def test_a_broken_config_fails_open(self):
        with mock.patch.object(config, "load_config", side_effect=RuntimeError("boom")), \
                mock.patch.object(notify, "_native_notify") as native:
            notify.notify("t", "b", kind=notify.KIND_PROPOSAL)
        native.assert_called_once()


if __name__ == "__main__":
    unittest.main()
