"""ui_scout 巡检报告的判决口径（CONTRACT §79.5 报告 / §79.6 判红边界）。

钉的是「只有确定性判官的 error 判红」：warn 是值得看一眼，info 是驾驶员自己说的话与
行程表登记的已知沙箱事实——两者都不判红，否则这份报告一周内就会被训练成「反正都是黄的」。
只喂字面量与临时目录：不起子进程、不碰网络、不读仓里任何报告。
"""

import contextlib
import io
import json
import os
import sys
import unittest

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first
from tests.scratch_testkit import scratch_dir

_QA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "qa")
if _QA_DIR not in sys.path:
    sys.path.insert(0, _QA_DIR)

import ui_scout  # noqa: E402


def _finding(severity="error", **over):
    finding = {
        "journey": "board_tour", "oracle": "lane_count", "severity": severity,
        "summary": "列头写着 4，数据里是 7 张", "detail": "细节", "step": 3,
        "url": "http://127.0.0.1:1/", "screenshot": "board_tour/step-03.png",
        "fingerprint": "abcd1234",
    }
    finding.update(over)
    return finding


def _report(findings, **over):
    report = {
        "protocol": 1, "pilot": "scripted", "startedAt": "2026-09-28T10:00:00.000Z",
        "durationMs": 31000,
        "journeys": [{"name": "board_tour", "steps": [{"step": 1}, {"step": 2}]}],
        "findings": findings,
        "counts": {},
    }
    report.update(over)
    return report


def _write(case, report):
    """报告落一个临时 run 目录（寿命 = ``case`` 的 cleanup），返回目录路径。"""
    run_dir = scratch_dir(case, prefix="ui-scout-run-", dir=TMP_HOME)
    with open(os.path.join(run_dir, ui_scout.REPORT_NAME), "w", encoding="utf-8") as handle:
        json.dump(report, handle)
    return run_dir


class CountsTestCase(unittest.TestCase):
    """三档从 findings 现算——报告自带的 counts 可能是旧跑者写的，不信它。"""

    def test_counts_are_recomputed_not_trusted(self):
        report = _report([_finding("error"), _finding("warn"), _finding("warn")],
                         counts={"error": 99, "warn": 0, "info": 0})
        self.assertEqual(ui_scout.counts(report), {"error": 1, "warn": 2, "info": 0})

    def test_empty_report_counts_zero(self):
        self.assertEqual(ui_scout.counts(_report([])), {"error": 0, "warn": 0, "info": 0})

    def test_non_dict_findings_are_ignored_not_fatal(self):
        report = _report(["歪掉的一行", None, _finding("info")])
        self.assertEqual(ui_scout.counts(report), {"error": 0, "warn": 0, "info": 1})


class VerdictTestCase(unittest.TestCase):
    """退出码：error 判红，warn / info 不判红。"""

    def test_error_fails(self):
        code, lines = ui_scout.verdict_lines(_report([_finding("error")]), "error")
        self.assertEqual(code, 1)
        self.assertTrue(lines[-1].startswith("ui_scout: FAIL"))

    def test_warn_alone_is_green(self):
        code, lines = ui_scout.verdict_lines(_report([_finding("warn"), _finding("info")]), "error")
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1], "ui_scout: OK")

    def test_fail_on_warn_promotes_warns(self):
        code, _ = ui_scout.verdict_lines(_report([_finding("warn")]), "warn")
        self.assertEqual(code, 1)

    def test_fail_on_never_is_green_even_with_errors(self):
        code, lines = ui_scout.verdict_lines(_report([_finding("error")]), "never")
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1], "ui_scout: OK")

    def test_every_finding_is_printed_whatever_the_threshold(self):
        # 不判红 ≠ 不报告（宪法第 3 条：探到什么说什么）
        _, lines = ui_scout.verdict_lines(_report([_finding("info", summary="驾驶员觉得怪")]), "error")
        self.assertTrue(any("驾驶员觉得怪" in line for line in lines))

    def test_severities_upto(self):
        self.assertEqual(ui_scout.severities_upto("error"), ("error",))
        self.assertEqual(ui_scout.severities_upto("warn"), ("error", "warn"))
        self.assertEqual(ui_scout.severities_upto("info"), ("error", "warn", "info"))
        self.assertEqual(ui_scout.severities_upto("never"), ())


class SummaryTestCase(unittest.TestCase):
    def test_summary_line_shape(self):
        line = ui_scout.summary_line(_report([_finding("error"), _finding("warn")]))
        self.assertEqual(line, "UI_SCOUT pilot=scripted journeys=1 steps=2 error=1 warn=1 info=0")

    def test_summary_survives_a_report_without_journeys(self):
        self.assertIn("journeys=0", ui_scout.summary_line(_report([], journeys=None)))


class LoadTestCase(unittest.TestCase):
    """读不动的报告要说清楚是哪一份坏了，而不是抛一个裸 KeyError。"""

    def test_round_trip(self):
        run_dir = _write(self, _report([_finding("warn")]))
        self.assertEqual(len(ui_scout.load_report(run_dir)["findings"]), 1)

    def test_missing_file(self):
        with self.assertRaises(ui_scout.ReportError):
            ui_scout.load_report(os.path.join(TMP_HOME, "没有这个目录"))

    def test_bad_json(self):
        run_dir = scratch_dir(self, prefix="ui-scout-bad-", dir=TMP_HOME)
        with open(os.path.join(run_dir, ui_scout.REPORT_NAME), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        with self.assertRaises(ui_scout.ReportError):
            ui_scout.load_report(run_dir)

    def test_json_that_is_not_a_report(self):
        run_dir = scratch_dir(self, prefix="ui-scout-shape-", dir=TMP_HOME)
        with open(os.path.join(run_dir, ui_scout.REPORT_NAME), "w", encoding="utf-8") as handle:
            json.dump({"hello": "world"}, handle)
        with self.assertRaises(ui_scout.ReportError):
            ui_scout.load_report(run_dir)

    def test_latest_run_picks_the_newest_timestamp_dir(self):
        root = scratch_dir(self, prefix="ui-scout-root-", dir=TMP_HOME)
        for name in ("2026-09-01T00-00-00-000Z", "2026-09-28T11-08-34-823Z"):
            os.makedirs(os.path.join(root, name))
            with open(os.path.join(root, name, ui_scout.REPORT_NAME), "w", encoding="utf-8") as handle:
                json.dump(_report([]), handle)
        self.assertTrue(ui_scout.latest_run(root).endswith("2026-09-28T11-08-34-823Z"))

    def test_latest_run_skips_dirs_without_a_report(self):
        root = scratch_dir(self, prefix="ui-scout-part-", dir=TMP_HOME)
        os.makedirs(os.path.join(root, "2026-09-29T00-00-00-000Z"))  # 半路崩掉的一次
        os.makedirs(os.path.join(root, "2026-09-01T00-00-00-000Z"))
        with open(os.path.join(root, "2026-09-01T00-00-00-000Z", ui_scout.REPORT_NAME),
                  "w", encoding="utf-8") as handle:
            json.dump(_report([]), handle)
        self.assertTrue(ui_scout.latest_run(root).endswith("2026-09-01T00-00-00-000Z"))

    def test_latest_run_on_a_machine_that_never_ran_it(self):
        self.assertIsNone(ui_scout.latest_run(os.path.join(TMP_HOME, "从没跑过")))


class CliTestCase(unittest.TestCase):
    """退出码是 CLI 的产品，打印的内容不该淹了套件输出——判例自己把它吞掉。"""

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ui_scout.main(argv)
        return code, out.getvalue() + err.getvalue()

    def test_check_returns_one_on_error(self):
        run_dir = _write(self, _report([_finding("error")]))
        code, text = self._run(["--run", run_dir, "--check"])
        self.assertEqual(code, 1)
        self.assertIn("ui_scout: FAIL", text)

    def test_check_returns_zero_on_warn_only(self):
        run_dir = _write(self, _report([_finding("warn")]))
        self.assertEqual(self._run(["--run", run_dir])[0], 0)

    def test_summary_flag_is_always_zero(self):
        run_dir = _write(self, _report([_finding("error")]))
        code, text = self._run(["--run", run_dir, "--summary"])
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("UI_SCOUT "))

    def test_missing_report_is_exit_two_not_a_traceback(self):
        code, text = self._run(["--run", os.path.join(TMP_HOME, "无")])
        self.assertEqual(code, 2)
        self.assertIn("ui_scout:", text)


if __name__ == "__main__":
    unittest.main()
