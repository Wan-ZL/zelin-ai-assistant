"""§58.4 shrink-only 账本语义的判例（五道门共用的唯一实现）。

FAIL 三态：新违例（new）、账上恶化（worse）、已修好仍挂账（stale——账本
只许缩）；tolerance 是 coverage 派生分数的抖动缓冲（limbo 带提示不判死）。
"""
import os
import sys
import tempfile
import unittest

_QA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "qa")
if _QA_DIR not in sys.path:
    sys.path.insert(0, _QA_DIR)

import qa_common  # noqa: E402


class ShrinkOnlyVerdictTestCase(unittest.TestCase):
    def _compare(self, scores, ledger, tolerance=0.0, threshold=6.0):
        return qa_common.compare_with_ledger(scores, ledger, threshold, tolerance)

    def test_clean_scan_against_empty_ledger_passes(self):
        result = self._compare({"a": 3.0}, {})
        self.assertTrue(result["ok"])

    def test_a_new_violation_fails(self):
        result = self._compare({"a": 8.0}, {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["new"], ["a"])

    def test_a_listed_violation_that_got_worse_fails(self):
        result = self._compare({"a": 9.0}, {"a": 8.0})
        self.assertFalse(result["ok"])
        self.assertEqual(result["worse"], ["a"])

    def test_a_listed_violation_holding_its_score_passes(self):
        result = self._compare({"a": 8.0}, {"a": 8.0})
        self.assertTrue(result["ok"])

    def test_an_improved_but_still_over_entry_passes_with_a_ratchet_hint(self):
        result = self._compare({"a": 7.0}, {"a": 9.0})
        self.assertTrue(result["ok"])
        self.assertEqual(result["better"], ["a"])

    def test_a_now_clean_entry_still_listed_fails(self):
        result = self._compare({"a": 5.0}, {"a": 8.0})
        self.assertFalse(result["ok"])
        self.assertEqual(result["stale"], ["a"])

    def test_a_vanished_entry_still_listed_fails(self):
        result = self._compare({}, {"a": 8.0})
        self.assertFalse(result["ok"])
        self.assertEqual(result["stale"], ["a"])

    def test_worse_within_tolerance_is_absorbed(self):
        result = self._compare({"a": 8.4}, {"a": 8.0}, tolerance=0.5)
        self.assertTrue(result["ok"])
        result = self._compare({"a": 8.6}, {"a": 8.0}, tolerance=0.5)
        self.assertFalse(result["ok"])
        self.assertEqual(result["worse"], ["a"])

    def test_just_under_the_threshold_is_limbo_not_stale_with_tolerance(self):
        # (threshold − tolerance, threshold] = 抖动缓冲带：提示、不判死
        result = self._compare({"a": 5.8}, {"a": 8.0}, tolerance=0.5)
        self.assertTrue(result["ok"])
        self.assertEqual(result["limbo"], ["a"])
        # 低出缓冲带就是真达标 → 必须划账
        result = self._compare({"a": 5.4}, {"a": 8.0}, tolerance=0.5)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stale"], ["a"])

    def test_unscored_ledgers_use_score_one_and_exact_semantics(self):
        # deps/hygiene 形：违例即 1.0、threshold 0、tolerance 0
        result = qa_common.compare_with_ledger({"edge-a": 1.0}, {"edge-a": 1.0}, 0.0)
        self.assertTrue(result["ok"])
        result = qa_common.compare_with_ledger(
            {"edge-a": 1.0, "edge-b": 1.0}, {"edge-a": 1.0}, 0.0)
        self.assertEqual(result["new"], ["edge-b"])
        result = qa_common.compare_with_ledger({}, {"edge-a": 1.0}, 0.0)
        self.assertEqual(result["stale"], ["edge-a"])


class NonFiniteScoreRejectedTestCase(unittest.TestCase):
    """nan/inf 拒收判例：`float()` 认 non-finite，而 nan 与任何数比较都是
    False——一个登记分或地板写成 nan 就把 worse/stale 判决与 ledger_diff
    的抬分/下调检测同时 fail-open（单 token 永久豁免）。两个 parser 必须
    与 gates.toml 的 _parse_scalar 同哲学 fail-loud。"""

    def test_ledger_score_nan_or_inf_fails_loud(self):
        for bad in ("nan", "NaN", "-nan", "inf", "-inf", "Infinity"):
            with self.assertRaises(ValueError, msg=bad):
                qa_common.parse_ledger_text("act/x.py::f %s\n" % bad)

    def test_floor_nan_or_inf_fails_loud(self):
        for bad in ("nan", "NaN", "-nan", "inf", "-inf", "Infinity"):
            with self.assertRaises(ValueError, msg=bad):
                qa_common.parse_floor_text("# note\n%s\n" % bad)

    def test_nan_would_defeat_the_three_state_verdict(self):
        # 钉住动机本身：nan 一旦进了账，烂到 120 也不算 worse、也永不 stale。
        result = qa_common.compare_with_ledger(
            {"act/x.py::f": 120.0}, {"act/x.py::f": float("nan")}, 6.0)
        self.assertTrue(result["ok"])  # 这就是 parser 必须拒收的原因

    def test_finite_scores_still_parse(self):
        self.assertEqual(qa_common.parse_ledger_text("a 8.5\nb 2\n"),
                         {"a": 8.5, "b": 2.0})
        self.assertEqual(qa_common.parse_floor_text("83.2\n"), 83.2)


class LedgerFileRoundTripTestCase(unittest.TestCase):
    def test_write_then_load_preserves_entries_and_ignores_comments(self):
        entries = {"act/x.py::f": 8.0, "deps:a->b": 1.0, "act/y.py::g": 22.5}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ledger.txt")
            qa_common.write_ledger(path, entries, "complexity")
            with open(path, encoding="utf-8") as fh:
                self.assertTrue(fh.readline().startswith("#"))  # 自述头
            self.assertEqual(qa_common.load_ledger(path), entries)

    def test_missing_ledger_file_means_an_empty_ledger(self):
        self.assertEqual(qa_common.load_ledger("/nonexistent/ledger.txt"), {})

    def test_bare_keys_default_to_score_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ledger.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# comment\n\nedge-a\nedge-b 2\n")
            self.assertEqual(qa_common.load_ledger(path),
                             {"edge-a": 1.0, "edge-b": 2.0})


if __name__ == "__main__":
    unittest.main()
