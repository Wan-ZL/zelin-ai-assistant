"""test-code skill · 真子进程判例（住 integration/，防腐 #7）：默认 runner 的三种结局
（正常 / 起不来 rc=-2 / 超时杀进程组 rc=-1）+ 整条梯子在 fixture mini-repo 上真跑
一遍（compileall、unittest、complexity_min fallback、自制检查），绿 → 植入坏测试 +
超线函数 → 红且 fix-first 排序正确。零网络、零 claude（tests/__init__.py 守卫在场）。

法典：docs/CONTRACT.md §58；设计 vnext2-plan R2.8。时间预算 BUDGET_SECONDS 兜底。
"""
import os
import sys
import tempfile
import time
import unittest

from tests import skill_test_code_testkit as kit

import detect  # noqa: E402
import run_ladder as rl  # noqa: E402

lc = kit.lc
BUDGET_SECONDS = 60
FIXTURE = {
    "pkg/__init__.py": "", "pkg/m.py": "def f(a):\n    return a + 1\n",
    "tests/__init__.py": "",
    "tests/test_m.py": "import unittest\n\nfrom pkg import m\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n"
                       "        self.assertEqual(m.f(1), 2)\n",
}
CHECKS = ["py_compile", "py_unit", "complexity", "length_caps", "structure", "secret_scan", "actions_sha_pin",
          "test_smells"]
# 核心圈里故意不跑的层要写理由，否则 verdict = incomplete（AI 只能多做不能少做）
SKIP_REASONS = {"py_coverage": "fixture: coverage run is not under test here",
                "diff_coverage": "fixture: needs coverage.json", "crap": "fixture: needs coverage.json",
                "py_lint": "fixture: lint not under test here", "deps_direction": "fixture declares no rules"}
HOT = "def hot(a):\n" + "".join("    if a == %d:\n        return %d\n" % (i, i) for i in range(12)) + "    return 0\n"


class BudgetedTestCase(unittest.TestCase):
    def setUp(self):
        self._started = time.monotonic()

    def tearDown(self):
        self.assertLess(time.monotonic() - self._started, BUDGET_SECONDS, "integration budget exceeded")


class RunCommandTestCase(BudgetedTestCase):
    def test_normal_exit(self):
        res = lc.run_command([sys.executable, "-c", "import sys; print('hi'); sys.stderr.write('e')"])
        self.assertTrue(res.ok)
        self.assertEqual((res.stdout.strip(), res.stderr), ("hi", "e"))

    def test_missing_binary_cannot_start(self):
        res = lc.run_command(["/nonexistent/test-code-binary-xyz"])
        self.assertEqual(res.rc, -2)
        self.assertFalse(res.ok)
        self.assertIn("No such file", res.stderr)

    def test_timeout_kills_and_reports(self):
        res = lc.run_command([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
        self.assertTrue(res.timed_out)
        self.assertEqual(res.rc, -1)
        self.assertFalse(res.ok)
        self.assertLess(res.duration, 10)


class LadderRealRunTestCase(BudgetedTestCase):
    def _sel(self):
        return {"tier": 2, "checks": CHECKS, "skip_reasons": SKIP_REASONS,
                "ask": {"recommended": 2, "reason": "fixture", "chosen": 2, "chosen_by": "user"}}

    def test_green_then_negative_control_red(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, FIXTURE)
            det = detect.detect(tmp)  # 真 runner：非 git 目录 → os.walk 兜底
            self.assertFalse(det["is_git"])
            self.assertEqual(det["layout"]["py_runner"], "unittest")
            report = rl.run(tmp, det, self._sel(), os.path.join(tmp, ".test-code", "reports", "one"))
            by = {c["id"]: c for c in report["checks"]}
            self.assertEqual((report["verdict"], report["exit_code"]), ("green", 0), by)
            self.assertEqual(by["py_unit"]["summary"], "1 tests, 0 failures")
            self.assertEqual(by["complexity"]["summary"], "0 NEW, 0 WORSE, 0 STALE")
            self.assertEqual(by["actions_sha_pin"]["status"], "na")
            self.assertIn("Python", report["tool_versions"]["python"])

            kit.make_repo(tmp, {"pkg/hot.py": HOT,
                                "tests/test_bad.py": "import unittest\n\n\nclass B(unittest.TestCase):\n"
                                                     "    def test_broken(self):\n        self.assertEqual(1, 2)\n"})
            det = detect.detect(tmp)
            report = rl.run(tmp, det, self._sel(), os.path.join(tmp, ".test-code", "reports", "two"))
            by = {c["id"]: c for c in report["checks"]}
            self.assertEqual((report["verdict"], report["exit_code"]), ("red", 1))
            # unittest discover -s tests 以 tests/ 为 top-level：id 不带 `tests.` 前缀
            self.assertEqual(by["py_unit"]["details"]["new"], ["test_bad.B.test_broken"])
            self.assertEqual(by["complexity"]["details"]["new"], ["cc:pkg/hot.py::hot"])
            self.assertEqual(report["fix_first"][0]["kind"], "failing test")
            self.assertEqual(report["fix_first"][1]["item"], "cc:pkg/hot.py::hot")
            self.assertTrue(os.path.exists(os.path.join(tmp, ".test-code", "reports", "two", "logs", "py_unit.log")))


if __name__ == "__main__":
    unittest.main()
