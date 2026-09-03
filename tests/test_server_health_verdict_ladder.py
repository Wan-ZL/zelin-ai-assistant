"""server/health.py 判决梯的真值表 + 三个视图小件（CONTRACT §47.4 / §49）。

test_server_health 走文件 + 真 server；这里把 _verdict 的五档按输入组合全部
枚举（含「心跳新鲜但循环连崩」「无心跳 + 无看板」），并钉 _stale_after 的
容错、_loop_health_view 对 bool / 负数 / 非 int 计数器的归零。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401

from server import health, paths


class VerdictLadderTestCase(unittest.TestCase):
    def test_stalled_wins_over_everything(self):
        self.assertEqual(health._verdict({"stale": True}, None, 99), "stalled")
        self.assertEqual(health._verdict({"stale": True}, {"stale": False}, 0), "stalled")

    def test_failing_beats_ok_when_beating(self):
        self.assertEqual(health._verdict({"stale": False}, None, health.LOOP_ALARM_AFTER),
                         "failing")
        self.assertEqual(health._verdict({"stale": False}, None,
                                         health.LOOP_ALARM_AFTER - 1), "ok")

    def test_no_heartbeat_ladder(self):
        self.assertEqual(health._verdict(None, None, health.LOOP_ALARM_AFTER), "failing")
        self.assertEqual(health._verdict(None, None, 0), "stale")
        self.assertEqual(health._verdict(None, {"stale": True}, 0), "stale")
        self.assertEqual(health._verdict(None, {"stale": False}, 0), "unknown")


class StaleAfterTestCase(unittest.TestCase):
    def test_writer_value_wins(self):
        self.assertEqual(health._stale_after({"stale_after_s": 30}), 30)

    def test_zero_absent_and_garbage_fall_back_to_floor(self):
        for body in ({}, {"stale_after_s": 0}, {"stale_after_s": "abc"},
                     {"stale_after_s": [1]}):
            self.assertEqual(health._stale_after(body), health.DASHBOARD_FRESH_SECONDS, body)


class LoopHealthViewTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-health-lh-"))
        (self.home / "state").mkdir()

    def _write(self, doc) -> None:
        paths.loop_health_path(self.home).write_text(json.dumps(doc), encoding="utf-8")

    def test_bad_counters_read_as_zero_without_error(self):
        for bad in (True, -1, "3", None, 2.5):
            self._write({"consecutive_failures": bad, "last_error": "x"})
            view = health._loop_health_view(self.home)
            self.assertEqual(view, {"consecutive_failures": 0, "last_error": None}, bad)

    def test_positive_counter_carries_last_error(self):
        self._write({"consecutive_failures": 2, "last_error": "boom"})
        self.assertEqual(health._loop_health_view(self.home),
                         {"consecutive_failures": 2, "last_error": "boom"})

    def test_missing_file_is_zero(self):
        self.assertEqual(health._loop_health_view(self.home)["consecutive_failures"], 0)


class DashboardViewTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-health-dash-"))
        (self.home / "state").mkdir()

    def test_unparseable_generated_at_is_none(self):
        paths.dashboard_path(self.home).write_text(json.dumps({"generated_at": "yesterday"}),
                                                   encoding="utf-8")
        self.assertIsNone(health._dashboard_view(self.home, 0.0))

    def test_future_timestamp_clamps_age_to_zero(self):
        paths.dashboard_path(self.home).write_text(
            json.dumps({"generated_at": "2100-01-01T00:00:00Z"}), encoding="utf-8")
        view = health._dashboard_view(self.home, 0.0)
        self.assertEqual(view["age_s"], 0.0)
        self.assertFalse(view["stale"])


if __name__ == "__main__":
    unittest.main()
