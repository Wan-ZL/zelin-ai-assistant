"""test-code skill · checks.py 变异体判例（区段 A：plan/result 形状、ctx 访问器、
python 测试 argv、post hooks；checks.py:28–338）。钉死的契约：_cmd 的 tool 回落取
argv 首词、_js_ready 缺 node_modules 时理由说人话、_is_test_file 三个条件缺一不可、
_py_tests_matching 无 tests 目录返回空列表（不是 None）、_py_test_argv 的 discover
目录来自 layout 且回落 "tests"、子集 plan / 触发器加挂层默认只跑一遍、_test_count 取
最后一次 "Ran N tests"（重跑会串起来）、_tests_verdict 默认 runs=1、coverage no-drop
基线写盘前 round 到一位小数、_tally_survivors 对缺字段的 module 记 0。

法典：docs/CONTRACT.md §57（存活变异体 = 补测试提案；变异 runner scripts/qa/mutate.py）
/ §58（skill 只读项目阈值）；卡片 R-217（nightly 2026-09-15：run=839 killed=435
survived=404 —— 本文件负责其中 plan/argv/post 一段）。设计 = vnext2-plan R2.8 / D14。
每个测试的 docstring 写明它杀的是哪一行的哪个变异（checks.py:<line> <op>）。零子进程。
"""
import os
import tempfile
import unittest

from tests import skill_test_code_testkit as kit

import checks  # noqa: E402

lc = kit.lc

PY_FILES = ["pkg/__init__.py", "pkg/m.py", "tests/__init__.py", "tests/test_m.py"]
FIRED_STATE = [{"id": "persisted_state", "evidence": [], "hits": 1}]


def _ctx(repo, files, out=None, sel=None, init=False, **det_over):
    det = kit.fake_det(files, **det_over)
    return checks.make_ctx(repo, det, sel=sel, out=out, init_baselines=init)


def _plans(files, ids, repo="/repo", out="/out", sel=None, **det_over):
    ctx = _ctx(repo, files, out=out, sel=sel, **det_over)
    return checks.build_plans(ctx, ids)


def _argv(plan, step=0):
    return plan["steps"][step]["argv"]


class CmdPlanShapeTestCase(unittest.TestCase):
    """plan 形状的出生点 _cmd：tool 回落、单步、post 透传。"""

    ARGV = ["ruff", "check", "."]

    def test_tool_falls_back_to_first_argv_word(self):
        """checks._cmd: `tool or argv[0]` —— 没显式 tool 就取命令首词，有就原样留着
        （checks.py:69 bool_or：or → and 会让默认情形的 tool 变成 None、显式情形变成 "ruff"）。"""
        plan = checks._cmd(self.ARGV, "/r")
        self.assertEqual(plan["tool"], "ruff")
        self.assertEqual(checks._cmd(self.ARGV, "/r", tool="python-tests")["tool"], "python-tests")

    def test_tool_is_the_program_not_a_flag(self):
        """checks._cmd: `argv[0]` —— 回落取的是**程序名**，不是子命令也不是末位参数
        （checks.py:69 int_plus1/int_minus1：0 → 1 给 "check"，0 → -1 给 "."）。"""
        self.assertEqual(checks._cmd(self.ARGV, "/r")["tool"], "ruff")
        self.assertEqual(checks._cmd(["npx", "--no-install", "tsc"], "/w")["tool"], "npx")

    def test_single_step_carries_cwd_env_and_post(self):
        """checks._cmd → checks._steps/_step: 一条命令 = 一个 step（argv/cwd/env 三键齐全），
        kind 固定 "cmd"，post 回调原样透传给 runner（checks.py:69 整行的形状契约）。"""
        def post(ctx, plan, runs):
            return {"summary": "x"}

        plan = checks._cmd(self.ARGV, "/r", post=post, env={"CI": "1"})
        self.assertEqual(plan["kind"], "cmd")
        self.assertEqual(plan["steps"], [{"argv": self.ARGV, "cwd": "/r", "env": {"CI": "1"}}])
        self.assertIs(plan["post"], post)
        self.assertIsNone(plan["note"])
        self.assertIsNone(checks._cmd(self.ARGV, "/r")["steps"][0]["env"])


class JsReadyReasonTestCase(unittest.TestCase):
    """_js_ready 的 unavailable 理由要指得出「差在哪」——人照着能修。"""

    PKGS = [{"dir": "web", "bins": ["tsc"]}]

    def _reason(self, tools, pkgs, needed):
        det = kit.fake_det(["web/package.json"], tools=tools)
        return checks._js_ready(checks.make_ctx("/r", det), pkgs, needed)["reason"]

    def test_missing_npx_blames_node_modules_not_empty_list(self):
        """checks._js_ready: `missing or ["node_modules"]` —— 每个 pkg 的 bin 都在、只缺 npx 时，
        理由要落到 node_modules 上（checks.py:161 bool_or：or → and 会渲染出空列表 "[]"，
        等于告诉人「装的地方是 []」）。"""
        reason = self._reason({}, self.PKGS, ["tsc"])
        self.assertIn("node_modules", reason)
        self.assertNotIn("[]", reason)
        self.assertIn("tsc", reason)

    def test_missing_bins_name_the_offending_package_dirs(self):
        """checks._js_ready: missing 非空时理由列的是缺 bin 的 pkg 目录，不是回落词
        （checks.py:161 同一处 bool_or 的另一半：and 会把 ['web'] 换成 ['node_modules']）。"""
        reason = self._reason({"npx": "/bin/npx"}, self.PKGS, ["tsc", "eslint"])
        self.assertIn("web", reason)
        self.assertNotIn("node_modules", reason)
        self.assertIn("tsc/eslint", reason)


class PyTestFileSelectionTestCase(unittest.TestCase):
    """哪些文件算 python 判例、以及「没有 tests 目录」的返回形状。"""

    def test_test_file_needs_all_three_conditions(self):
        """checks._is_test_file: 在 tests 目录下 AND 文件名 test 开头 AND .py 结尾，三者缺一不可
        （checks.py:175 bool_and：and → or 会把 src/test_x.py、tests/helper.py、
        tests/test_x.txt 全认成判例）。"""
        self.assertTrue(checks._is_test_file("tests/test_x.py", "tests"))
        self.assertFalse(checks._is_test_file("src/foo.py", "tests"))
        self.assertFalse(checks._is_test_file("src/test_x.py", "tests"), "别的目录里的 test_x.py 不算")
        self.assertFalse(checks._is_test_file("tests/helper.py", "tests"))
        self.assertFalse(checks._is_test_file("tests/test_x.txt", "tests"))
        self.assertFalse(checks._is_test_file("testsuite/test_x.py", "tests"), "前缀撞名不算子目录")

    def test_no_tests_dir_yields_empty_list(self):
        """checks._py_tests_matching: 项目没有 tests 目录 → 返回**空列表**而不是 None
        （checks.py:182 return_none：return [] → return None。今天三个调用方
        _tier_subset / _trigger_subset / _b_soak_race 一律 `if (not) files` 真值判断，None
        走同一支，plan 与 na 理由逐字不变——所以这里直接钉 helper 的返回**类型**契约：
        docstring 承诺的是「相对路径，排序」的 list，将来哪个调用方 len()/ 拼接不会静默炸）。"""
        ctx = _ctx("/r", ["pkg/m.py"])
        self.assertIsNone(checks._layout(ctx).get("tests_dir"))
        got = checks._py_tests_matching(ctx, r"golden")
        self.assertEqual(got, [])
        self.assertIsInstance(got, list)

    def test_matching_is_scoped_to_the_tests_dir(self):
        """checks._py_tests_matching: 只认 tests 目录下的判例文件，给的是相对路径
        （配合 checks.py:175 的三条件：pkg/test_golden.py、tests/golden_helper.py 都不该混进来）。"""
        files = PY_FILES + ["tests/test_golden_wire.py", "tests/golden_helper.py", "pkg/test_golden.py"]
        self.assertEqual(checks._py_tests_matching(_ctx("/r", files), r"golden"),
                         ["tests/test_golden_wire.py"])


class PyTestArgvTestCase(unittest.TestCase):
    """项目的 python 测试命令：discover 目录来自 layout，回落 "tests"。"""

    def _argv_for(self, tests_dir, files=None, runner="unittest"):
        det = kit.fake_det(PY_FILES)
        det["layout"]["tests_dir"] = tests_dir
        det["layout"]["py_runner"] = runner
        return checks._py_test_argv(checks.make_ctx("/r", det), files)

    def test_discover_dir_comes_from_layout(self):
        """checks._py_test_argv: `layout.get("tests_dir") or "tests"` —— 项目把判例放在 spec/
        就 discover spec/（checks.py:195 bool_or：or → and 会永远给 "tests"，
        在真项目上 discover 到一个不存在的目录）。"""
        self.assertEqual(self._argv_for("spec")[-2:], ["-s", "spec"])
        self.assertEqual(self._argv_for("tests")[1:4], ["-m", "unittest", "discover"])

    def test_discover_dir_falls_back_to_tests(self):
        """checks._py_test_argv: tests_dir 为 None 时回落字面量 "tests"，argv 里绝不出现 None
        （checks.py:195 同一处 bool_or：and 会把末位参数变成 None，argv 拼给 subprocess 即崩）。"""
        argv = self._argv_for(None)
        self.assertEqual(argv[-1], "tests")
        self.assertTrue(all(isinstance(a, str) for a in argv))

    def test_file_subset_and_pytest_never_discover(self):
        """checks._py_test_argv: 给了 files 就点名跑（不 discover）；pytest 项目走 -m pytest -q
        —— discover 目录的回落只影响 unittest 全量那一支（checks.py:195 的作用域）。"""
        self.assertEqual(self._argv_for("spec", files=["tests/test_m.py"])[1:],
                         ["-m", "unittest", "tests/test_m.py"])
        self.assertEqual(self._argv_for("spec", runner="pytest")[1:], ["-m", "pytest", "-q"])


class SubsetRerunDefaultTestCase(unittest.TestCase):
    """子集 plan 与触发器加挂层：没人要求重跑就只跑一遍（重跑是选项，不是默认）。"""

    def test_subset_plan_runs_once_by_default(self):
        """checks._subset_plan: `reruns=1` —— 默认一遍；note 为空时 kind 是 "cmd" 不是 substituted
        （checks.py:198 int_plus1：1 → 2 会让每个子集 plan 白跑一倍，第 2 档预算直接翻番）。"""
        ctx = _ctx("/r", PY_FILES)
        plan = checks._subset_plan(ctx, ["tests/test_m.py"])
        self.assertEqual(len(plan["steps"]), 1)
        self.assertEqual((plan["kind"], plan["note"]), ("cmd", None))
        self.assertEqual(plan["tool"], "python-tests")
        self.assertIs(plan["post"], checks._post_tests)
        self.assertEqual(len(checks._subset_plan(ctx, ["tests/test_m.py"], 3)["steps"]), 3)

    def test_tier_subset_builders_run_once(self):
        """checks._subset_plan 经 _tier_subset / _b_property_tests：golden_contract 与
        property_tests 各自只排一个 step（checks.py:198 int_plus1 的对外可见面）。"""
        files = PY_FILES + ["tests/test_golden_wire.py"]
        self.assertEqual(len(_plans(files, ["golden_contract"])["golden_contract"]["steps"]), 1)
        det = kit.fake_det(PY_FILES, pymods={"hypothesis": True})
        det["layout"]["property_tests"] = ["tests/test_m.py"]
        plan = checks.build_plans(checks.make_ctx("/r", det), ["property_tests"])["property_tests"]
        self.assertEqual(len(plan["steps"]), 1)

    def test_trigger_subset_runs_once_unless_asked(self):
        """checks._trigger_subset: `reruns=1` —— 触发器加挂的判例默认跑一遍；只有点名要重跑的
        （race_stress 的 race_reruns）才排多步（checks.py:204 int_plus1：1 → 2 会让
        crash_recovery / fault_injection / corpus_regression 全部双跑）。"""
        files = PY_FILES + ["tests/test_crash_recovery.py"]
        plan = _plans(files, ["crash_recovery"], triggers=FIRED_STATE)["crash_recovery"]
        self.assertEqual(len(plan["steps"]), 1)
        self.assertEqual(_argv(plan)[3:], ["tests/test_crash_recovery.py"])
        ctx = _ctx("/r", files)
        self.assertEqual(len(checks._trigger_subset(ctx, "persisted_state", r"crash")["steps"]), 1)
        self.assertEqual(len(checks._trigger_subset(ctx, "persisted_state", r"crash", reruns=4)["steps"]), 4)


class TestCountAndRunsTestCase(unittest.TestCase):
    """跑完之后的读数：重跑串起来时取最后一段，runs 默认 1。"""

    def test_count_takes_the_last_ran_line(self):
        """checks._test_count: `hits[-1]` —— 多次重跑的输出是拼起来的，报的必须是最后一遍的条数
        （checks.py:259 int_minus1：1 → 0 让 hits[-0] 等价于 hits[0]，串起来的输出会报
        第一遍的条数 "3 tests" 而不是最后一遍的 "5 tests"）。"""
        self.assertEqual(checks._test_count("Ran 3 tests in 0.1s\nOK\nRan 5 tests in 0.2s\nOK\n"), "5 tests")
        self.assertEqual(checks._test_count("Ran 7 tests in 0.1s\nOK\n"), "7 tests")
        self.assertEqual(checks._test_count("nothing here"), "test count unknown")

    def test_last_count_surfaces_in_the_post_hook(self):
        """checks._test_count 经 checks._post_tests: 三遍重跑的 summary 报最后一遍的条数
        （checks.py:259 int_minus1 的对外可见面；同时钉 ×3 runs 后缀）。"""
        runs = [lc.RunResult(0, "Ran 3 tests\nOK"), lc.RunResult(0, "Ran 3 tests\nOK"),
                lc.RunResult(0, "Ran 9 tests\nOK")]
        res = checks._post_tests(_ctx("/r", []), {}, runs)
        self.assertEqual(res["summary"], "9 tests, 0 failures ×3 runs")

    def test_runs_defaults_to_one(self):
        """checks._tests_verdict: `runs=1` 默认值 —— 不传 runs 就是跑了一遍，summary 不带 ×N，
        details["runs"] 如实记 1（checks.py:267 int_plus1：1 → 2 会凭空多报一遍；
        int_minus1：1 → 0 的 summary 看不出差别，但 details 里会写「跑了 0 遍」）。"""
        res = checks._tests_verdict([], [], set(), True, "Ran 3 tests\nOK\n")
        self.assertEqual(res["details"]["runs"], 1)
        self.assertNotIn("×", res["summary"])
        self.assertEqual(res["summary"], "3 tests, 0 failures")
        self.assertEqual(checks._tests_verdict([], [], set(), True, "Ran 3 tests\n", runs=2)["details"]["runs"], 2)


class PostCoverageBaselineTestCase(unittest.TestCase):
    """no-drop 基线写盘的精度：一位小数，和 _no_drop_verdict 的 0.1 容差同一把尺。"""

    def _ctx_with_coverage(self, tmp, total, init=True):
        out = os.path.join(tmp, "out")
        lc.write_json(os.path.join(out, "coverage.json"), {"totals": {"percent_covered": total}})
        return _ctx(tmp, [], out=out, init=init)

    def test_baseline_is_rounded_to_one_decimal(self):
        """checks._post_coverage_generic: `round(total, 1)` —— --init-baselines 写进
        baselines/coverage_total.txt 的是一位小数（checks.py:327 int_minus1：1 → 0 写成整数 83，
        把 0.1 的容差放大成 1；int_plus1：1 → 2 留两位 83.45，被 format_value 二次进位成 83.5，
        基线反而比实测高）。"""
        with tempfile.TemporaryDirectory() as tmp:
            res = checks._post_coverage_generic(self._ctx_with_coverage(tmp, 83.449), {}, [lc.RunResult(0)])
            self.assertEqual(res["status"], "pass")
            path = os.path.join(tmp, ".test-code", "baselines", "coverage_total.txt")
            self.assertEqual(lc.load_ledger(path)["total"], 83.4)

    def test_recorded_baseline_lets_the_same_run_pass_again(self):
        """checks._post_coverage_generic → _no_drop_verdict: 刚写下的基线必须让同一个覆盖率
        复跑判绿（checks.py:327 int_plus1 把基线抬到 83.5，83.449 就掉出 0.1 容差判红）。"""
        with tempfile.TemporaryDirectory() as tmp:
            checks._post_coverage_generic(self._ctx_with_coverage(tmp, 83.449), {}, [lc.RunResult(0)])
            again = self._ctx_with_coverage(tmp, 83.449, init=False)
            res = checks._post_coverage_generic(again, {}, [lc.RunResult(0)])
            self.assertEqual(res["status"], "pass")
            self.assertEqual(res["details"]["baseline"], 83.4)


class TallySurvivorsTestCase(unittest.TestCase):
    """mutate.py 报告的合计：缺字段的 module 记 0，timeout 算在 killed 侧。"""

    def test_missing_counter_keys_tally_as_zero(self):
        """checks._tally_survivors: `mod.get("killed"/"timeout"/"executed", 0)` —— 报告里某个
        module 没有计数键（跑到一半 / 老格式）时一律记 0，合计不许凭空多算或倒扣
        （checks.py:337 两处 + :338 一处的 int_plus1/int_minus1 共 6 个变异体：
        默认值变 ±1 会让 killed 报 ±2、executed 报 ±1，杀伤率直接失真）。"""
        report = {"modules": {"a.py": {"survivors": []}}}
        self.assertEqual(checks._tally_survivors(report, set()), ([], 0, 0))

    def test_partial_counters_only_add_what_is_there(self):
        """checks._tally_survivors: 只有 killed 的 module 合计 (2, 0)；只有 executed 的 (0, 4)；
        只有 timeout 的 (3, 0)（checks.py:337/338 三个默认值各自单独可见）。"""
        self.assertEqual(checks._tally_survivors({"modules": {"a.py": {"killed": 2}}}, set()), ([], 2, 0))
        self.assertEqual(checks._tally_survivors({"modules": {"a.py": {"executed": 4}}}, set()), ([], 0, 4))
        self.assertEqual(checks._tally_survivors({"modules": {"a.py": {"timeout": 3}}}, set()), ([], 3, 0))

    def test_zero_tally_reaches_the_post_hook_summary(self):
        """checks._tally_survivors 经 checks._post_mutation: 空计数的报告 summary 写 "0/0 killed"
        （checks.py:337/338 默认值变 ±1 → "2/1 killed" 之类的幻觉数字进报告）。"""
        with tempfile.TemporaryDirectory() as tmp:
            lc.write_json(os.path.join(tmp, "mutation.json"),
                          {"complete": True, "modules": {"a.py": {"survivors": []}}})
            res = checks._post_mutation(_ctx(tmp, [], out=tmp), {}, [lc.RunResult(0)])
            self.assertEqual(res["summary"], "0/0 killed, 0 surviving (0 classified equivalent)")
            self.assertEqual(res["status"], "pass")


if __name__ == "__main__":
    unittest.main()
