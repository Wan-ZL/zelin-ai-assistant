"""§28 追记（issue #29）通知偏好：安静时段（v2.0 起分类开关随 Act 卡片类一并退役）。

抑制在**写方**（act/lib/notify.notify），不在 §28 的 drain——队列只留 10 分钟
（STALE_AFTER_S），「压到早上再弹」在那之下是谎话。写方不入队 = 队列的 stale /
burst / 消费即删语义一个字不动，这一条由 QueueUntouchedTestCase 钉住。

钉的行为：
  - 出厂值 = 安静时段关——新装机一条通知都不少；
  - 安静时段盖住当前时刻 → 除失败类外一律不入队（跨午夜算两段）；
  - 失败类穿透安静时段；
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
        for kind in (None, "general", notify.KIND_RECAP_READY, notify.KIND_FAILURE):
            with self.subTest(kind=kind):
                self.assertIsNone(notify.suppression_reason(kind, cfg, now=_at(3)))

    def test_kind_vocabulary_is_the_lite_pair(self):
        """宪法第 10 条（v2.0）：只剩纪要已生成 + 录制 / ingest 坏了两类。"""
        self.assertEqual(notify.KINDS, ("recap_ready", "failure"))
        self.assertEqual(notify.QUIET_HOURS_EXEMPT, frozenset({"failure"}))

    def test_quiet_hours_silence_everything_but_failures(self):
        cfg = _cfg(quiet_hours_enabled=True, quiet_hours_start="22:00", quiet_hours_end="08:00")
        for kind in (None, "general", notify.KIND_RECAP_READY):
            with self.subTest(kind=kind):
                self.assertEqual(notify.suppression_reason(kind, cfg, now=_at(2, 30)), "quiet_hours")
        self.assertIsNone(notify.suppression_reason(notify.KIND_FAILURE, cfg, now=_at(2, 30)))

    def test_outside_the_window_nothing_is_suppressed(self):
        cfg = _cfg(quiet_hours_enabled=True, quiet_hours_start="22:00", quiet_hours_end="08:00")
        self.assertIsNone(notify.suppression_reason(notify.KIND_RECAP_READY, cfg, now=_at(9)))

    def test_window_off_means_the_endpoints_are_ignored(self):
        cfg = _cfg(quiet_hours_enabled=False, quiet_hours_start="00:00", quiet_hours_end="23:59")
        self.assertIsNone(notify.suppression_reason(notify.KIND_RECAP_READY, cfg, now=_at(12)))

    def test_clock_defaults_to_now_when_not_injected(self):
        """``now=None`` 走 time.localtime()——把那把钟钉死，两个方向各判一次
        （「无论本机几点都对」的写法等于没判：它接受任何答案）。"""
        cfg = _cfg(quiet_hours_enabled=True, quiet_hours_start="22:00", quiet_hours_end="08:00")
        with mock.patch.object(notify.time, "localtime", return_value=_at(2, 30)) as clock:
            self.assertEqual(notify.suppression_reason(notify.KIND_RECAP_READY, cfg), "quiet_hours")
        clock.assert_called_once_with()
        with mock.patch.object(notify.time, "localtime", return_value=_at(9, 0)):
            self.assertIsNone(notify.suppression_reason(notify.KIND_RECAP_READY, cfg))


class OverridesTestCase(unittest.TestCase):
    """三把安静时段键都在 overrides 白名单里，坏值回落出厂值（per-entry 跳过）。"""

    def _loaded(self, overrides: dict) -> config.Config:
        with mock.patch.object(config, "_read_overrides", return_value=overrides):
            return config.load_config()

    def test_all_three_keys_land(self):
        cfg = self._loaded({"quiet_hours_enabled": True, "quiet_hours_start": "1:05",
                            "quiet_hours_end": "07:30"})
        self.assertTrue(cfg.quiet_hours_enabled)
        self.assertEqual(cfg.quiet_hours_start, "01:05")   # 归一为两位小时
        self.assertEqual(cfg.quiet_hours_end, "07:30")

    def test_garbage_clock_keeps_the_factory_value(self):
        cfg = self._loaded({"quiet_hours_start": "half past ten", "quiet_hours_end": "99:99"})
        self.assertEqual(cfg.quiet_hours_start, "22:00")
        self.assertEqual(cfg.quiet_hours_end, "08:00")


class QueueUntouchedTestCase(unittest.TestCase):
    """notify() 半边：被吞 = 一个队列文件都不写，且返回 True（不是失败）。"""

    def _notify(self, kind, cfg):
        with mock.patch.object(config, "load_config", return_value=cfg), \
                mock.patch.object(notify.platform, "is_darwin", return_value=True), \
                mock.patch.object(notify, "_queue_write") as queue:
            ok = notify.notify("t", "b", kind=kind)
        return ok, queue

    def test_suppressed_never_reaches_the_queue_and_reports_success(self):
        cfg = _cfg(quiet_hours_enabled=True, quiet_hours_start="00:00", quiet_hours_end="23:59")
        with mock.patch.object(notify.time, "localtime", return_value=_at(12)):
            ok, queue = self._notify(notify.KIND_RECAP_READY, cfg)
        self.assertTrue(ok)             # 偏好吞掉 ≠ 失败
        queue.assert_not_called()       # §28 队列一个字节都没动

    def test_allowed_still_rides_the_relay_with_its_kind(self):
        ok, queue = self._notify(notify.KIND_RECAP_READY, config.Config())
        queue.assert_called_once_with("t", "b", None, kind=notify.KIND_RECAP_READY)
        self.assertTrue(ok)

    def test_a_broken_config_fails_open(self):
        with mock.patch.object(config, "load_config", side_effect=RuntimeError("boom")), \
                mock.patch.object(notify.platform, "is_darwin", return_value=True), \
                mock.patch.object(notify, "_queue_write") as queue:
            notify.notify("t", "b", kind=notify.KIND_RECAP_READY)
        queue.assert_called_once()


if __name__ == "__main__":
    unittest.main()
