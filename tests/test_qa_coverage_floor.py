"""§58.2 覆盖率地板比较器的判例：低于地板 FAIL；高出触发带给建议新地板。

地板 = qa/coverage_floor.txt 的单个数字（只经 PR 上调）；建议值 =
当前覆盖率 − ratchet_buffer，向下取 1 位小数（永不建议高于实测的地板）。
"""
import os
import sys
import tempfile
import unittest

_QA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "qa")
if _QA_DIR not in sys.path:
    sys.path.insert(0, _QA_DIR)

import coverage_floor  # noqa: E402


class FloorComparatorTestCase(unittest.TestCase):
    def test_below_the_floor_fails(self):
        self.assertEqual(coverage_floor.evaluate_floor(81.9, 82.0, 0.5, 0.3),
                         (False, None))

    def test_exactly_at_the_floor_passes_without_a_suggestion(self):
        self.assertEqual(coverage_floor.evaluate_floor(82.0, 82.0, 0.5, 0.3),
                         (True, None))

    def test_within_the_trigger_band_passes_quietly(self):
        self.assertEqual(coverage_floor.evaluate_floor(82.4, 82.0, 0.5, 0.3),
                         (True, None))

    def test_above_the_trigger_suggests_a_buffered_new_floor(self):
        self.assertEqual(coverage_floor.evaluate_floor(82.5, 82.0, 0.5, 0.3),
                         (True, 82.2))

    def test_suggestion_rounds_down_to_one_decimal(self):
        ok, suggestion = coverage_floor.evaluate_floor(83.15, 82.0, 0.5, 0.3)
        self.assertTrue(ok)
        self.assertEqual(suggestion, 82.8)

    def test_suggestion_never_exceeds_the_measured_coverage(self):
        for percent in (82.5, 83.0, 90.0, 99.9):
            _, suggestion = coverage_floor.evaluate_floor(percent, 82.0, 0.5, 0.3)
            self.assertLessEqual(suggestion, percent)


class FloorFileTestCase(unittest.TestCase):
    def test_read_floor_skips_comments_and_reads_the_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "coverage_floor.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# 地板只许经 PR 上调\n\n82.0\n")
            self.assertEqual(coverage_floor.read_floor(path), 82.0)

    def test_a_floor_file_without_a_number_fails_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "coverage_floor.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# only comments\n")
            with self.assertRaises(ValueError):
                coverage_floor.read_floor(path)


if __name__ == "__main__":
    unittest.main()
