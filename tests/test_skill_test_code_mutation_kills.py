"""test-code skill · 第 4 档自测留下的判例：2026-09-02 对 skills/test-code/scripts 跑变异测试
（1,581 个变异体，62% 杀伤；剔除 CATALOG 数据表里的时间估计常数后逻辑杀伤 ≈ 85%），
这里逐个钉死当时活下来的**逻辑**变异体——边界比较、and/or、fail-closed 分支。
每个测试的 docstring 写明它杀的是哪一行的哪个变异（file:line op）。零子进程。
设计 = docs/design/vnext2-plan.md R2.8；CONTRACT §57（存活变异体 = 补测试提案）。
"""

import os
import sys
import unittest

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "skills", "test-code", "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import checks  # noqa: E402
import ladder_common as lc  # noqa: E402
import run_ladder as rl  # noqa: E402
from tests import skill_test_code_testkit as kit  # noqa: E402


class LedgerBoundaryTestCase(unittest.TestCase):
    def test_equal_value_is_not_worse(self):
        """ladder_common._worse: `violations[k] > ledger[k]` — 等值不算 WORSE（> → >= 会误红）。"""
        cmp = lc.compare_ledger({"a": 7.0, "b": 8.0, "c": 5.0}, {"a": 7.0, "b": 7.0, "c": 6.0})
        self.assertEqual(cmp["worse"], ["b"])
        self.assertTrue(cmp["ok"] is False)
        self.assertEqual(lc.compare_ledger({"a": 7.0}, {"a": 7.0})["ok"], True)

    def test_format_value_integer_vs_fraction(self):
        """ladder_common.format_value: 整数不带小数点，分数保留一位。"""
        self.assertEqual(lc.format_value(7.0), "7")
        self.assertEqual(lc.format_value(7.25), "7.2")
        self.assertEqual(lc.format_value(0.0), "0")


class TestVerdictBoundaryTestCase(unittest.TestCase):
    def test_runs_suffix_only_above_one(self):
        """checks._tests_verdict: `runs > 1` 才带 ×N 后缀（> → >= 会给单跑加 ×1）。"""
        one = checks._tests_verdict([], [], set(), True, "Ran 3 tests\nOK\n", runs=1)
        two = checks._tests_verdict([], [], set(), True, "Ran 3 tests\nOK\n", runs=2)
        self.assertNotIn("×", one["summary"])
        self.assertIn("×2 runs", two["summary"])

    def test_no_drop_tolerance_boundary(self):
        """checks._no_drop_verdict: `total < floor - 0.1` — 正好差 0.1 放行，差 0.11 判红。"""
        self.assertEqual(checks._no_drop_verdict(83.4, 83.5)["status"], "pass")
        self.assertEqual(checks._no_drop_verdict(83.39, 83.5)["status"], "fail")
        self.assertEqual(checks._no_drop_verdict(90.0, None)["status"], "substituted")

    def test_new_failures_beat_pre_existing(self):
        """checks._tests_verdict: 有 NEW 失败必红；全部在 known 里则 pass 并计 pre_existing。"""
        res = checks._tests_verdict(["t.a", "t.b"], ["t.b"], {"t.a"}, False, "", runs=1)
        self.assertEqual((res["status"], res["details"]["pre_existing"]), ("fail", ["t.a"]))
        res = checks._tests_verdict(["t.a"], [], {"t.a"}, False, "", runs=1)
        self.assertEqual((res["status"], res["details"]["pre_existing"]), ("pass", ["t.a"]))


class JsReadinessTestCase(unittest.TestCase):
    def test_pkg_has_requires_declared_and_installed(self):
        """checks._pkg_has: 声明 AND 安装（and → or 会把「只声明未安装」放行）。"""
        self.assertTrue(checks._pkg_has({"stryker": True, "bins": ["stryker"]}, "stryker"))
        self.assertFalse(checks._pkg_has({"stryker": True, "bins": []}, "stryker"))
        self.assertFalse(checks._pkg_has({"stryker": False, "bins": ["stryker"]}, "stryker"))

    def test_js_ready_needs_npx_and_every_bin(self):
        """checks._js_ready: 缺 npx 或任一 pkg 缺 bin 都是 unavailable（or → and 会放行）。"""
        det = kit.fake_det(["web/package.json"])
        det["tools"] = {"npx": "/bin/npx"}
        ctx = checks.make_ctx("/r", det)
        pkgs = [{"dir": "web", "bins": ["tsc"]}]
        self.assertIsNone(checks._js_ready(ctx, pkgs, ["tsc"]))
        self.assertEqual(checks._js_ready(ctx, pkgs, ["tsc", "eslint"])["kind"], "unavailable")
        det["tools"] = {}
        self.assertEqual(checks._js_ready(ctx, pkgs, ["tsc"])["kind"], "unavailable")


class CoreSkippedBoundaryTestCase(unittest.TestCase):
    def _det(self, kinds):
        det = kit.fake_det(["a.py"])
        det["menu"] = [{"id": cid, "kind": kind} for cid, kind in kinds.items()]
        return det

    def test_tier_boundary_is_inclusive_and_circle_is_exact(self):
        """checks.core_skipped: 恰在选定档的核心层要算（<= → < 漏掉）；扩展圈永不算（== → != 反转）。"""
        det = self._det({"py_unit": "cmd", "type_coverage": "cmd", "py_compile": "cmd", "mutation_full": "cmd"})
        skipped = checks.core_skipped(det, 2, [])
        self.assertIn("py_unit", skipped, "tier 2 core check at chosen tier 2")
        self.assertIn("py_compile", skipped)
        self.assertNotIn("type_coverage", skipped, "extended circle is never a core skip")
        self.assertNotIn("mutation_full", skipped, "tier 5 is above the chosen tier")
        self.assertEqual(checks.core_skipped(det, 2, ["py_unit", "py_compile"]), [])

    def test_only_runnable_kinds_count(self):
        """checks.core_skipped: na / unavailable 的核心层不算跳过（in → not in 会反转）。"""
        det = self._det({"py_unit": "na", "py_compile": "unavailable", "secret_scan": "internal"})
        self.assertEqual(checks.core_skipped(det, 2, []), ["secret_scan"])

    def test_verdict_counts_only_unexplained_core_skips(self):
        """run_ladder.verdict / core_skips: 有理由的跳过不降级，无理由的把绿变 incomplete。"""
        results = [{"status": "pass", "details": {}}]
        self.assertEqual(rl.verdict(results, 0), ("green", rl.EXIT_GREEN))
        self.assertEqual(rl.verdict(results, 1), ("incomplete", rl.EXIT_INCOMPLETE))
        self.assertEqual(rl.verdict([{"status": "fail", "details": {}}], 1), ("red", rl.EXIT_RED))
        det = self._det({"py_unit": "cmd", "crap": "internal"})
        sel = {"tier": 2, "checks": [], "skip_reasons": {"crap": "no coverage here"}}
        skips = rl.core_skips(det, sel)
        self.assertEqual([(s["id"], bool(s["reason"])) for s in skips], [("py_unit", False), ("crap", True)])


class NodeFlagsTestCase(unittest.TestCase):
    def test_assert_and_sleep_flags(self):
        """checks._node_flags: ast.Assert → (True, False)；sleep() 调用 → (False, True)；普通调用 → (False, False)。"""
        import ast
        tree = ast.parse("assert x\nsleep(1)\nfoo()\nself.assertEqual(1, 1)\n")
        stmts = tree.body
        self.assertEqual(checks._node_flags(stmts[0]), (True, False))
        self.assertEqual(checks._node_flags(stmts[1].value), (False, True))
        self.assertEqual(checks._node_flags(stmts[2].value), (False, False))
        self.assertEqual(checks._node_flags(stmts[3].value), (True, False))


if __name__ == "__main__":
    unittest.main()
