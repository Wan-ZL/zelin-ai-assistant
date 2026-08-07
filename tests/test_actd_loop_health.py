"""§47.3 loop health — actd 主循环连续崩溃的可见化（写侧判例）。

判例 ④ 的 Python 半边：连续 3 次 pass FAILED → state/loop_health.json 的
consecutive_failures 达到 LOOP_ALARM_AFTER（App 侧 LoopHealth.failing 亮红，
读侧判例在 mac/LogicTests 的 LoopHealthTests.swift）；恢复一次成功 pass →
清零回执落盘 → 红点自动消。全程无 subprocess、无真 claude。
"""
import json
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import config


class LoopHealthTrackerTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self.path = config.STATE_DIR / actd.LOOP_HEALTH_NAME
        if self.path.exists():
            self.path.unlink()
        self.addCleanup(lambda: self.path.unlink(missing_ok=True))

    def _read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_three_consecutive_failures_reach_alarm_threshold(self):
        t = actd.LoopHealthTracker()
        for i in range(actd.LOOP_ALARM_AFTER):
            t.record_failure(f"NameError: boom {i}")
        data = self._read()
        self.assertEqual(data["consecutive_failures"], actd.LOOP_ALARM_AFTER)
        self.assertIn("NameError", data["last_error"])
        self.assertIn("updated_at", data)

    def test_recovery_writes_zero_receipt(self):
        t = actd.LoopHealthTracker()
        for _ in range(actd.LOOP_ALARM_AFTER + 2):
            t.record_failure("RuntimeError: x")
        t.record_success()
        data = self._read()
        self.assertEqual(data["consecutive_failures"], 0)   # 红点消
        self.assertIsNone(data["last_error"])

    def test_steady_state_success_never_touches_disk(self):
        # 稳态（从未失败）不写盘：10s 心跳不许平白多一次磁盘写
        t = actd.LoopHealthTracker()
        t.record_success()
        t.record_success()
        self.assertFalse(self.path.exists())

    def test_failure_after_recovery_counts_from_one(self):
        t = actd.LoopHealthTracker()
        for _ in range(actd.LOOP_ALARM_AFTER):
            t.record_failure("A")
        t.record_success()
        t.record_failure("B: fresh streak")
        data = self._read()
        self.assertEqual(data["consecutive_failures"], 1)   # 不是 4——连续才算
        self.assertEqual(data["last_error"], "B: fresh streak")

    def test_restart_inherits_disk_count_and_first_success_clears(self):
        # v0.47 review 判例：重启恰是连崩的标准恢复路径。新进程 init 必须继承
        # 盘上计数，否则首个成功 pass 撞稳态 early-return，盘上 ≥3 永不清零、
        # 红横幅永久挂着。
        t = actd.LoopHealthTracker()
        for _ in range(actd.LOOP_ALARM_AFTER):
            t.record_failure("NameError: boom")
        t2 = actd.LoopHealthTracker()            # actd 重启
        self.assertEqual(t2.consecutive_failures, actd.LOOP_ALARM_AFTER)
        t2.record_success()                      # 重启后首个成功 pass
        self.assertEqual(self._read()["consecutive_failures"], 0)  # 红点消

    def test_corrupt_or_missing_health_file_starts_from_zero(self):
        # 诊断文件缺失/损坏/非法计数 → init 按 0，绝不拦启动、绝不误报
        self.assertEqual(actd.LoopHealthTracker().consecutive_failures, 0)
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(actd.LoopHealthTracker().consecutive_failures, 0)
        self.path.write_text(json.dumps({"consecutive_failures": "3"}),
                             encoding="utf-8")
        self.assertEqual(actd.LoopHealthTracker().consecutive_failures, 0)

    def test_long_error_is_capped_and_writer_never_raises(self):
        t = actd.LoopHealthTracker()
        t.record_failure("E" * 5000)
        self.assertLessEqual(len(self._read()["last_error"]), 300)
