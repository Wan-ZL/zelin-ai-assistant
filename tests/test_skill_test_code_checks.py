"""test-code skill · checks 判例：自制检查全部 fail closed + 走 shrink-only 账本；
builders 按项目形状选「项目的门 / 工具 / fallback / na / unavailable」；触发器加挂层
（fired 无判例 = missing = fail；waive = na）；post hooks 解读 unittest / pytest /
coverage / mutation 输出。零子进程（builders 只构造 argv，不执行）。

法典：docs/CONTRACT.md §58（项目门优先、阈值只读）/ §57（survivors 逐字转载）；设计
vnext2-plan R2.8 / D14；触发器来源 skills/test-code/references/triggers.md。
每个自制检查都有负控制：已知坏 fixture 必须 fail，读不到必须 fail 而不是 pass。
"""
import os
import sys
import tempfile
import unittest

from tests import skill_test_code_testkit as kit

import checks  # noqa: E402

lc = kit.lc
# 运行时拼出来：本文件永远不含 key 形状的字面量（否则 secret_scan 扫自己就红）
FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
FAKE_SLACK = "xoxb-" + "1234567890-abcdefghij"


def _ctx(repo, files, out=None, sel=None, init=False, **det_over):
    det = kit.fake_det(files, **det_over)
    return checks.make_ctx(repo, det, sel=sel, out=out, init_baselines=init)


def _plans(files, ids, repo="/repo", out="/out", sel=None, init=False, **det_over):
    det = kit.fake_det(files, **det_over)
    ctx = checks.make_ctx(repo, det, sel=sel, out=out, init_baselines=init)
    return checks.build_plans(ctx, ids)


def _argv(plan, step=0):
    return plan["steps"][step]["argv"]


class SecretScanTestCase(unittest.TestCase):
    def test_negative_control_key_shaped_string_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"src/a.py": 'KEY = "%s"\n' % FAKE_AWS, "src/b.py": "x = 1\n"})
            res = checks.check_secret_scan(_ctx(tmp, ["src/a.py", "src/b.py"]))
            self.assertEqual(res["status"], "fail")
            self.assertEqual(len(res["details"]["new"]), 1)
            self.assertTrue(res["details"]["new"][0].startswith("src/a.py::aws-access-key::"))

    def test_clean_tree_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"src/b.py": "x = 1\n"})
            self.assertEqual(checks.check_secret_scan(_ctx(tmp, ["src/b.py"]))["status"], "pass")

    def test_design_tokens_without_digits_are_not_secrets(self):
        """`token: "text-primary-strong-heading"` 是设计 token，不是密钥（首次实跑的 11 条误报）；
        同名但值含数字的赋值仍然命中——负控制两边都钉。"""
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"web/typeScale.ts": 'const a = { token: "text-primary-strong-heading-large" };\n',
                                "src/c.py": 'token = "abcdefghijklmnop1234"\n'})
            res = checks.check_secret_scan(_ctx(tmp, ["web/typeScale.ts", "src/c.py"]))
            self.assertEqual(res["status"], "fail")
            self.assertEqual([k.split("::")[0] for k in res["details"]["new"]], ["src/c.py"])

    def test_unreadable_listed_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = checks.check_secret_scan(_ctx(tmp, ["ghost.py"]))
            self.assertEqual(res["status"], "fail")
            self.assertIn("unreadable", res["summary"])

    def test_init_grandfathers_then_ratchets(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"src/a.py": 'KEY = "%s"\n' % FAKE_AWS})
            res = checks.check_secret_scan(_ctx(tmp, ["src/a.py"], init=True))
            self.assertEqual(res["status"], "pass")
            self.assertIn("grandfathered", res["summary"])
            self.assertTrue(os.path.exists(os.path.join(tmp, ".test-code", "baselines", "secret_scan.txt")))
            res = checks.check_secret_scan(_ctx(tmp, ["src/a.py"]))
            self.assertEqual(res["status"], "pass")
            self.assertIn("pre-existing", res["summary"])
            kit.make_repo(tmp, {"src/c.py": 'TOKEN = "%s"\n' % FAKE_SLACK})
            res = checks.check_secret_scan(_ctx(tmp, ["src/a.py", "src/c.py"]))
            self.assertEqual(res["status"], "fail")
            # 同一行命中 slack-token 与 generic-assignment 两条规则：两条 NEW，都指向 src/c.py
            self.assertEqual(sorted(k.split("::")[1] for k in res["details"]["new"]),
                             ["generic-assignment", "slack-token"])
            self.assertTrue(all(k.startswith("src/c.py::") for k in res["details"]["new"]))


def _wf(uses):
    return "on: push\njobs:\n  b:\n    steps:\n      - uses: %s\n" % uses


class ActionsShaPinTestCase(unittest.TestCase):
    WF = ".github/workflows/ci.yml"

    def _run(self, tmp, uses):
        kit.make_repo(tmp, {self.WF: _wf(uses)})
        return checks.check_actions_sha_pin(_ctx(tmp, [self.WF]))

    def test_negative_control_tag_ref_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = self._run(tmp, "actions/checkout@v4")
            self.assertEqual(res["status"], "fail")
            self.assertEqual(res["details"]["new"], [self.WF + "::actions/checkout@v4"])

    def test_missing_ref_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run(tmp, "actions/checkout")["status"], "fail")

    def test_sha_local_and_docker_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            for uses in ("actions/checkout@" + "a" * 40, "./.github/actions/local", "docker://alpine:3"):
                with self.subTest(uses=uses):
                    self.assertEqual(self._run(tmp, uses)["status"], "pass")

    def test_no_workflows_is_na(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(checks.check_actions_sha_pin(_ctx(tmp, ["a.py"]))["status"], "na")


SMELLY = """import time
import subprocess
import unittest


class T(unittest.TestCase):
    def test_no_assert(self):
        x = 1
        return x

    def test_sleep(self):
        time.sleep(0.01)
        self.assertTrue(True)

    def test_ok(self):
        self.assertEqual(1, 1)

    def helper(self):
        pass


def test_pytest_style():
    import pytest
    with pytest.raises(ValueError):
        int("x")
"""


class TestSmellsTestCase(unittest.TestCase):
    def test_negative_control_flags_three_smells(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = {"tests/test_a.py": SMELLY, "tests/integration/test_b.py": SMELLY, "tests/helper.py": "x = 1\n"}
            kit.make_repo(tmp, files)
            ctx = _ctx(tmp, sorted(files))
            ctx["det"]["layout"]["integration_dir"] = "tests/integration"
            res = checks.check_test_smells(ctx)
            self.assertEqual(res["status"], "fail")
            self.assertEqual(res["details"]["new"], [
                "no-assert:tests/integration/test_b.py::T.test_no_assert",
                "no-assert:tests/test_a.py::T.test_no_assert",
                "real-io:tests/test_a.py::subprocess",
                "sleep:tests/integration/test_b.py::T.test_sleep",
                "sleep:tests/test_a.py::T.test_sleep",
            ])

    def test_syntax_error_fails_closed_and_no_tests_dir_is_na(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"tests/test_bad.py": "def (:\n"})
            res = checks.check_test_smells(_ctx(tmp, ["tests/test_bad.py"]))
            self.assertEqual(res["status"], "fail")
            self.assertIn("unparseable", res["summary"])
            self.assertEqual(checks.check_test_smells(_ctx(tmp, ["src/a.py"]))["status"], "na")


class DocsDriftTestCase(unittest.TestCase):
    def test_only_dangling_repo_paths_are_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = {
                "act/x.py": "", "docs/sub/ok.md": "",
                "docs/a.md": ("See `act/missing.py`, `act/x.py`, `state/foo.json`, `docs/*.md`, `https://x/y.md`, "
                              "`act/`, `docs/sub/ok.md`, `./act/x.py`, `act/missing.py.`, `act/_version.py`, "
                              "`config/runtime.json`, `act/data.txt`"),
                "CHANGELOG.md": "removed `act/gone.py`",
                ".gitignore": "# generated at install\nact/_version.py\n*.pyc\n",
            }
            kit.make_repo(tmp, files)
            res = checks.check_docs_drift(_ctx(tmp, sorted(files)))
            self.assertEqual(res["status"], "fail")
            # .gitignore 字面声明的生成物（act/_version.py）与 .json/.txt 运行时数据都不算悬空
            self.assertEqual(res["details"]["new"], ["docs/a.md::act/missing.py"])

    def test_unreadable_doc_fails_closed_and_no_docs_is_na(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"act/x.py": ""})
            res = checks.check_docs_drift(_ctx(tmp, ["act/x.py", "docs/ghost.md"]))
            self.assertEqual(res["status"], "fail")
            self.assertEqual(checks.check_docs_drift(_ctx(tmp, ["act/x.py"]))["status"], "na")


def _diff(changed=(), added=None, added_text=None, removed=None):
    return {"base": "origin/main", "base_commit": "abc", "changed_files": list(changed), "added": added or {},
            "added_text": added_text or {}, "removed": removed or {}, "untracked": []}


class DiffCoverageTestCase(unittest.TestCase):
    def _ctx(self, tmp, added, cov):
        out = os.path.join(tmp, "out")
        if cov is not None:
            lc.write_json(os.path.join(out, "coverage.json"), {"files": cov})
        return _ctx(tmp, ["pkg/m.py"], out=out, diff=_diff(list(added), added=added))

    def test_negative_control_uncovered_added_line_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            cov = {"pkg/m.py": {"executed_lines": [1, 2], "missing_lines": [3, 4]}}
            res = checks.check_diff_coverage(self._ctx(tmp, {"pkg/m.py": [2, 3, 5], "other/n.py": [1], "web/a.ts": [1]}, cov))
            self.assertEqual(res["status"], "fail")
            self.assertEqual(res["details"]["uncovered"], ["pkg/m.py:3"])
            self.assertEqual(res["details"]["unmeasured"], ["other/n.py"])
            self.assertEqual(res["details"]["measured"], 2)

    def test_all_covered_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cov = {"pkg/m.py": {"executed_lines": [1, 2], "missing_lines": []}}
            res = checks.check_diff_coverage(self._ctx(tmp, {"pkg/m.py": [1, 2]}, cov))
            self.assertEqual(res["status"], "pass")
            self.assertIn("2/2", res["summary"])

    def test_missing_coverage_is_unavailable_and_no_py_is_na(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(checks.check_diff_coverage(self._ctx(tmp, {"pkg/m.py": [1]}, None))["status"], "unavailable")
            self.assertEqual(checks.check_diff_coverage(self._ctx(tmp, {"web/a.ts": [1]}, {}))["status"], "na")


class FieldAddOnlyTestCase(unittest.TestCase):
    def test_removed_key_in_schema_file_fails_prose_ignored(self):
        diff = _diff(["api/schema.json", "docs/CONTRACT.md", "act/x.py"],
                     removed={"api/schema.json": ['  "old_key": 1,', "  }"], "docs/CONTRACT.md": ["Status: x"],
                              "act/x.py": ["key = 1"]})
        res = checks.check_field_add_only(_ctx("/r", ["api/schema.json"], diff=diff))
        self.assertEqual(res["status"], "fail")
        self.assertEqual(res["details"]["removed_keys"], ["api/schema.json: -old_key"])
        self.assertEqual(res["details"]["files"], ["api/schema.json"])

    def test_no_schema_files_or_no_removed_keys_pass(self):
        self.assertEqual(checks.check_field_add_only(_ctx("/r", [], diff=_diff(["act/x.py"])))["status"], "pass")
        diff = _diff(["wire/types.ts"], removed={"wire/types.ts": ["  // comment"]})
        self.assertEqual(checks.check_field_add_only(_ctx("/r", [], diff=diff))["status"], "pass")


class DiffMinimalityTestCase(unittest.TestCase):
    def test_states(self):
        diff = _diff(["skills/x.py", "act/y.py"])
        res = checks.check_diff_minimality(_ctx("/r", [], diff=diff))
        self.assertEqual(res["status"], "unavailable")
        res = checks.check_diff_minimality(_ctx("/r", [], diff=diff, ), )
        res = checks.check_diff_minimality(_ctx("/r", [], sel={"declared_files": ["skills/*"]}, diff=diff))
        self.assertEqual((res["status"], res["details"]["outside"]), ("fail", ["act/y.py"]))
        res = checks.check_diff_minimality(_ctx("/r", [], sel={"declared_files": ["skills/*", "act/*"]}, diff=diff))
        self.assertEqual(res["status"], "pass")
        # 无 diff（干净 clone / 无 base）= 没东西可约束 → pass，不是 unavailable（跨项目实跑抓到）
        res = checks.check_diff_minimality(_ctx("/r", [], diff=_diff([])))
        self.assertEqual((res["status"], res["details"]["outside"]), ("pass", []))


class DependencyBudgetTestCase(unittest.TestCase):
    def test_states(self):
        self.assertEqual(checks.check_dependency_budget(_ctx("/r", [], diff=_diff(["act/x.py"])))["status"], "pass")
        diff = _diff(["web/package.json"], added_text={"web/package.json": ['    "left-pad": "^1",', ""]})
        self.assertEqual(checks.check_dependency_budget(_ctx("/r", [], diff=diff))["status"], "unavailable")
        res = checks.check_dependency_budget(_ctx("/r", [], sel={"declared_deps": ["left-pad"]}, diff=diff))
        self.assertEqual(res["status"], "pass")
        res = checks.check_dependency_budget(_ctx("/r", [], sel={"declared_deps": []}, diff=diff))
        self.assertEqual(res["status"], "fail")
        self.assertEqual(res["details"]["undeclared"], {"web/package.json": ['    "left-pad": "^1",']})


CC3 = "def f(a, b):\n    if a:\n        return 1\n    if b:\n        return 2\n    return 3\n"


class CrapFallbackTestCase(unittest.TestCase):
    def _run(self, tmp, files, cov, crap_max):
        out = os.path.join(tmp, "out")
        lc.write_json(os.path.join(out, "coverage.json"), {"files": cov})
        kit.make_repo(tmp, files)
        ctx = _ctx(tmp, sorted(files), out=out)
        ctx["det"]["thresholds"]["crap_max"] = crap_max
        return checks.check_crap_fallback(ctx)

    def test_negative_control_uncovered_cc3_is_crap_12(self):
        with tempfile.TemporaryDirectory() as tmp:
            cov = {"pkg/m.py": {"executed_lines": [], "missing_lines": [1, 2, 3, 4, 5, 6]}}
            res = self._run(tmp, {"pkg/m.py": CC3}, cov, 6.0)
            self.assertEqual(res["status"], "fail")
            self.assertEqual(res["details"]["new"], ["pkg/m.py::f"])
            self.assertEqual(checks.crap_score(3, 0.0), 12.0)
            self.assertEqual(checks.crap_score(6, 1.0), 6.0)

    def test_loose_threshold_passes_and_missing_source_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cov = {"pkg/m.py": {"executed_lines": [], "missing_lines": [1, 2, 3, 4, 5, 6]}}
            self.assertEqual(self._run(tmp, {"pkg/m.py": CC3}, cov, 30.0)["status"], "pass")
            res = self._run(tmp, {}, {"pkg/ghost.py": {"executed_lines": [], "missing_lines": [1]}}, 30.0)
            self.assertEqual(res["status"], "fail")
            self.assertIn("unreadable", res["summary"])

    def test_no_coverage_json_is_unavailable(self):
        self.assertEqual(checks.check_crap_fallback(_ctx("/r", [], out="/nonexistent"))["status"], "unavailable")


class ParseOutputsTestCase(unittest.TestCase):
    def test_parse_test_failures_formats(self):
        self.assertEqual(checks.parse_test_failures("FAIL: test_a (tests.t.C)\nERROR: test_b (tests.t.C)\n"),
                         ["tests.t.C.test_a", "tests.t.C.test_b"])
        self.assertEqual(checks.parse_test_failures("FAIL: test_a (tests.t.C.test_a)\n"), ["tests.t.C.test_a"])
        self.assertEqual(checks.parse_test_failures("FAILED tests/test_x.py::test_y - AssertionError\n"),
                         ["tests/test_x.py::test_y"])
        self.assertEqual(checks.parse_test_failures(" ✗ src/a.test.ts > does thing\n FAIL  src/b.test.ts > other\n"),
                         ["src/a.test.ts > does thing", "src/b.test.ts > other"])
        self.assertEqual(checks.parse_test_failures("Ran 3 tests\n\nOK\n"), [])
        # pytest 带色输出（收集错误）：ANSI 码剥掉后 `ERROR tests/x.py` 必须被认出（跨项目实跑抓到）
        colored = "\x1b[31mERROR\x1b[0m tests/test_a.py\n\x1b[31m\x1b[1m5 errors\x1b[0m in 0.1s\n"
        self.assertEqual(checks.parse_test_failures(colored), ["tests/test_a.py"])

    def test_post_ledger_verdict_parses_both_formats(self):
        runs = [lc.RunResult(1, "  NEW: a::b = 7\n  WORSE: c::d = 9 (baseline 8)\n  STALE: e::f = gone\n"
                                "NEW cc:p.py::f = 13\nSTALE(advisory) k — strike it\n")]
        res = checks._post_ledger_verdict({}, {}, runs)
        self.assertEqual(res["details"], {"new": ["a::b", "cc:p.py::f"], "worse": ["c::d"], "stale": ["e::f", "k"]})
        self.assertEqual(res["summary"], "2 NEW, 1 WORSE, 2 STALE")


class PostTestsTestCase(unittest.TestCase):
    FAILING = "FAIL: test_a (tests.t.C)\nRan 3 tests in 0.1s\n\nFAILED (failures=1)\n"

    def test_new_failure_is_red_known_is_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = [lc.RunResult(1, self.FAILING)]
            res = checks._post_tests(_ctx(tmp, []), {}, runs)
            self.assertEqual((res["status"], res["details"]["new"]), ("fail", ["tests.t.C.test_a"]))
            res = checks._post_tests(_ctx(tmp, [], sel={"known_failing": ["tests.t.C.test_a"]}), {}, runs)
            self.assertEqual(res["status"], "pass")
            self.assertIn("0 NEW", res["summary"])
            self.assertEqual(res["details"]["pre_existing"], ["tests.t.C.test_a"])

    def test_nonzero_without_parseable_failure_fails_closed(self):
        res = checks._post_tests(_ctx("/r", []), {}, [lc.RunResult(1, "Traceback: boom")])
        self.assertEqual(res["status"], "fail")
        self.assertIn("fail closed", res["summary"])

    def test_green_run_counts_and_reruns(self):
        res = checks._post_tests(_ctx("/r", []), {}, [lc.RunResult(0, "Ran 3 tests\nOK")] * 2)
        self.assertEqual((res["status"], res["summary"]), ("pass", "3 tests, 0 failures ×2 runs"))

    def test_init_writes_known_failing_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = checks._post_tests(_ctx(tmp, [], init=True), {}, [lc.RunResult(1, self.FAILING)])
            self.assertEqual(res["status"], "pass")
            ledger = lc.load_ledger(os.path.join(tmp, ".test-code", "baselines", "known_failing.txt"))
            self.assertEqual(list(ledger), ["tests.t.C.test_a"])


class PostCoverageTestCase(unittest.TestCase):
    def _ctx(self, tmp, total, init=False):
        out = os.path.join(tmp, "out")
        lc.write_json(os.path.join(out, "coverage.json"), {"totals": {"percent_covered": total}})
        return _ctx(tmp, [], out=out, init=init)

    def test_no_baseline_is_substituted_init_arms_no_drop(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok = [lc.RunResult(0)]
            self.assertEqual(checks._post_coverage_generic(self._ctx(tmp, 80.0), {}, ok)["status"], "substituted")
            self.assertEqual(checks._post_coverage_generic(self._ctx(tmp, 80.0, init=True), {}, ok)["status"], "pass")
            self.assertEqual(checks._post_coverage_generic(self._ctx(tmp, 79.95), {}, ok)["status"], "pass")
            res = checks._post_coverage_generic(self._ctx(tmp, 79.5), {}, ok)
            self.assertEqual(res["status"], "fail")
            self.assertIn("vs baseline 80.0%", res["summary"])

    def test_failed_run_or_missing_json_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(checks._post_coverage_generic(self._ctx(tmp, 80.0), {}, [lc.RunResult(1)])["status"], "fail")
            self.assertEqual(checks._post_coverage_generic(_ctx(tmp, [], out=tmp), {}, [lc.RunResult(0)])["status"], "fail")
            res = checks._post_coverage_project(self._ctx(tmp, 80.0), {}, [lc.RunResult(0)])
            self.assertIn("total 80.0%", res["summary"])


class PostMutationTestCase(unittest.TestCase):
    REPORT = {"complete": True, "modules": {"act/lib/x.py": {
        "killed": 3, "timeout": 1, "executed": 5,
        "survivors": [{"location": "act/lib/x.py:10", "line": 10, "col": 1, "op": "cmp", "detail": "< -> <="}]}}}

    def test_survivor_is_red_equivalent_is_green_missing_json_is_red(self):
        with tempfile.TemporaryDirectory() as tmp:
            lc.write_json(os.path.join(tmp, "mutation.json"), self.REPORT)
            res = checks._post_mutation(_ctx(tmp, [], out=tmp), {}, [lc.RunResult(0)])
            self.assertEqual(res["status"], "fail")
            self.assertEqual(res["summary"], "4/5 killed, 1 surviving (0 classified equivalent)")
            self.assertEqual(res["details"]["survivors"][0]["module"], "act/lib/x.py")
            res = checks._post_mutation(_ctx(tmp, [], out=tmp, sel={"equivalent_mutants": ["act/lib/x.py:10"]}), {}, [])
            self.assertEqual(res["status"], "pass")
            self.assertEqual(checks._post_mutation(_ctx(tmp, [], out=os.path.join(tmp, "no")), {}, [])["status"], "fail")


PY_FILES = ["pkg/__init__.py", "pkg/m.py", "tests/__init__.py", "tests/test_m.py"]
JS_PKG = {"dir": "web", "tsconfig": True, "eslint": False, "test_runner": "vitest", "bins": ["tsc", "vitest"],
          "lock": "package-lock.json", "scripts": {}, "coverage_provider": False, "coverage_thresholds": False,
          "playwright": False, "stryker": False}


def _js_det(pkg_over=None, tools=None):
    pkg = dict(JS_PKG, **(pkg_over or {}))
    det = kit.fake_det(["web/src/a.ts", "web/package.json"], tools=tools if tools is not None else {"npx": "/npx"})
    det["stacks"] = ["js"]
    det["layout"]["js_packages"] = [pkg]
    return det


class BuilderPythonTestCase(unittest.TestCase):
    def test_py_unit_unittest_pytest_and_blocked(self):
        plan = _plans(PY_FILES, ["py_unit"])["py_unit"]
        self.assertEqual(plan["kind"], "cmd")
        self.assertEqual(_argv(plan)[1:], ["-m", "unittest", "discover", "-s", "tests"])
        det = kit.fake_det(PY_FILES, pymods={"pytest": True})
        det["layout"]["py_runner"] = "pytest"
        ctx = checks.make_ctx("/r", det, out="/o")
        self.assertIn("pytest", _argv(checks.build_plans(ctx, ["py_unit"])["py_unit"]))
        det["pymods"]["pytest"] = False
        self.assertEqual(checks.build_plans(ctx, ["py_unit"])["py_unit"]["kind"], "unavailable")
        self.assertEqual(_plans(["pkg/m.py"], ["py_unit"])["py_unit"]["kind"], "na")
        self.assertEqual(_plans(["a.ts"], ["py_unit", "py_compile"])["py_compile"]["kind"], "na")

    def test_lint_format_tools(self):
        self.assertEqual(_argv(_plans(PY_FILES, ["py_lint"], tools={"ruff": "/r"})["py_lint"]), ["ruff", "check", "."])
        self.assertEqual(_argv(_plans(PY_FILES, ["py_lint"], tools={"flake8": "/f"})["py_lint"])[0], "flake8")
        self.assertEqual(_plans(PY_FILES, ["py_lint"])["py_lint"]["kind"], "unavailable")
        self.assertEqual(_plans(PY_FILES, ["py_format"])["py_format"]["kind"], "na")
        det = kit.fake_det(PY_FILES, tools={"ruff": "/r"})
        det["layout"]["py_format"] = "ruff"
        plan = checks.build_plans(checks.make_ctx("/r", det), ["py_format"])["py_format"]
        self.assertEqual(_argv(plan), ["ruff", "format", "--check", "."])
        det["tools"] = {}
        self.assertEqual(checks.build_plans(checks.make_ctx("/r", det), ["py_format"])["py_format"]["kind"], "unavailable")

    def test_shellcheck_and_swift_parse(self):
        self.assertEqual(_plans(PY_FILES, ["shellcheck"])["shellcheck"]["kind"], "na")
        self.assertEqual(_plans(["a.sh"], ["shellcheck"])["shellcheck"]["kind"], "unavailable")
        plan = _plans(["a.sh", "b/c.sh"], ["shellcheck"], tools={"shellcheck": "/s"})["shellcheck"]
        self.assertEqual(_argv(plan), ["shellcheck", "a.sh", "b/c.sh"])
        plan = _plans(["a.swift", "b/c.swift"], ["swift_parse"], tools={"swiftc": "/s"})["swift_parse"]
        self.assertEqual([s["argv"] for s in plan["steps"]], [["swiftc", "-parse", "a.swift"], ["swiftc", "-parse", "b/c.swift"]])

    def test_rulers_prefer_project_gates_else_complexity_min(self):
        with tempfile.TemporaryDirectory() as tmp:
            plans = _plans(PY_FILES, ["complexity", "length_caps", "crap", "deps_direction"], repo=tmp)
            self.assertIn("complexity_min.py", _argv(plans["complexity"])[1])
            self.assertEqual(_argv(plans["complexity"])[2:6], ["--only", "cc", "--root", tmp])
            self.assertIn("--max-cc", _argv(plans["complexity"]))
            self.assertIn("--max-func-lines", _argv(plans["length_caps"]))
            self.assertEqual(plans["crap"]["kind"], "internal")
            self.assertEqual(plans["deps_direction"]["kind"], "na")
            kit.make_repo(tmp, {"scripts/qa/complexity.py": "", "scripts/qa/hygiene.py": "", "scripts/qa/crap.py": "",
                                "scripts/qa/depgraph.py": ""})
            plans = _plans(PY_FILES, ["complexity", "length_caps", "crap", "deps_direction"], repo=tmp)
            self.assertEqual(_argv(plans["complexity"])[1:], ["scripts/qa/complexity.py", "--check"])
            self.assertEqual(_argv(plans["length_caps"])[1], "scripts/qa/hygiene.py")
            self.assertEqual(_argv(plans["crap"])[1:], ["scripts/qa/crap.py", "--check", "--coverage-json", "/out/coverage.json"])
            self.assertIsNotNone(plans["deps_direction"]["post"])

    def test_complexity_min_ledger_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            argv = _argv(_plans(PY_FILES, ["complexity"], repo=tmp, init=True)["complexity"])
            self.assertIn("--write-baseline", argv)
            self.assertNotIn("--baseline", _argv(_plans(PY_FILES, ["complexity"], repo=tmp)["complexity"]))
            lc.write_ledger(os.path.join(tmp, ".test-code", "baselines", "complexity.txt"), {}, "h")
            self.assertIn("--baseline", _argv(_plans(PY_FILES, ["complexity"], repo=tmp)["complexity"]))

    def test_coverage_generic_and_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_plans(PY_FILES, ["py_coverage"], repo=tmp)["py_coverage"]["kind"], "unavailable")
            plan = _plans(PY_FILES, ["py_coverage"], repo=tmp, pymods={"coverage": True})["py_coverage"]
            self.assertEqual(len(plan["steps"]), 2)
            self.assertEqual(plan["steps"][0]["env"]["COVERAGE_FILE"], "/out/.coverage")
            self.assertEqual(_argv(plan)[1:6], ["-m", "coverage", "run", "--source=pkg", "-m"])
            kit.make_repo(tmp, {"scripts/qa/run_coverage.sh": "", "scripts/qa/coverage_floor.py": ""})
            plan = _plans(PY_FILES, ["py_coverage"], repo=tmp, pymods={"coverage": True})["py_coverage"]
            self.assertEqual(_argv(plan)[:2], ["bash", "scripts/qa/run_coverage.sh"])
            self.assertEqual(_argv(plan, 1)[1], "scripts/qa/coverage_floor.py")

    def test_integration_golden_migration_subsets(self):
        files = PY_FILES + ["tests/test_golden_wire.py", "tests/test_store_migration.py", "tests/integration/test_x.py"]
        det = kit.fake_det(files)
        det["layout"]["integration_dir"] = "tests/integration"
        plans = checks.build_plans(checks.make_ctx("/r", det), ["py_integration", "golden_contract", "migration_roundtrip"])
        self.assertEqual(_argv(plans["py_integration"])[1:], ["-m", "unittest", "discover", "-s", "tests/integration", "-t", "/r"])
        self.assertEqual(_argv(plans["golden_contract"])[3:], ["tests/test_golden_wire.py"])
        self.assertEqual(_argv(plans["migration_roundtrip"])[3:], ["tests/test_store_migration.py"])
        self.assertEqual(_plans(PY_FILES, ["golden_contract"])["golden_contract"]["kind"], "na")


class BuilderJsSwiftTestCase(unittest.TestCase):
    def test_js_plans(self):
        det = _js_det()
        plans = checks.build_plans(checks.make_ctx("/r", det, out="/o"),
                                   ["ts_typecheck", "js_lint", "js_unit", "js_coverage", "js_e2e"])
        self.assertEqual(plans["ts_typecheck"]["steps"][0]["cwd"], "/r/web")
        self.assertEqual(_argv(plans["ts_typecheck"])[:3], ["npx", "--no-install", "tsc"])
        self.assertEqual(plans["js_lint"]["kind"], "na")
        self.assertEqual(_argv(plans["js_unit"]), ["npx", "--no-install", "vitest", "run"])
        self.assertEqual(plans["js_coverage"]["kind"], "unavailable")
        self.assertEqual(plans["js_e2e"]["kind"], "na")

    def test_js_coverage_substituted_without_thresholds(self):
        det = _js_det({"coverage_provider": True})
        plan = checks.build_plans(checks.make_ctx("/r", det), ["js_coverage"])["js_coverage"]
        self.assertEqual(plan["kind"], "substituted")
        det = _js_det({"coverage_provider": True, "coverage_thresholds": True})
        self.assertEqual(checks.build_plans(checks.make_ctx("/r", det), ["js_coverage"])["js_coverage"]["kind"], "cmd")

    def test_js_blocked_when_bins_or_npx_missing(self):
        det = _js_det({"bins": []})
        self.assertEqual(checks.build_plans(checks.make_ctx("/r", det), ["ts_typecheck"])["ts_typecheck"]["kind"], "unavailable")
        det = _js_det(tools={})
        self.assertEqual(checks.build_plans(checks.make_ctx("/r", det), ["js_unit"])["js_unit"]["kind"], "unavailable")

    def test_js_e2e_script_and_playwright(self):
        det = _js_det({"scripts": {"test:e2e": "pw"}})
        self.assertEqual(_argv(checks.build_plans(checks.make_ctx("/r", det), ["js_e2e"])["js_e2e"]), ["npm", "run", "test:e2e"])
        det = _js_det({"playwright": True, "bins": ["playwright"]})
        self.assertEqual(_argv(checks.build_plans(checks.make_ctx("/r", det), ["js_e2e"])["js_e2e"])[2], "playwright")

    def test_swift_unit_variants(self):
        self.assertEqual(_plans(PY_FILES, ["swift_unit"])["swift_unit"]["kind"], "na")
        det = kit.fake_det(["App/a.swift"], tools={"swift": "/s", "xcodebuild": "/x"}, stacks=["swift"])
        self.assertEqual(checks.build_plans(checks.make_ctx("/r", det), ["swift_unit"])["swift_unit"]["kind"], "na")
        det["layout"]["swift"] = {"package_dir": "Lib", "schemes": []}
        plan = checks.build_plans(checks.make_ctx("/r", det), ["swift_unit"])["swift_unit"]
        self.assertEqual((_argv(plan), plan["steps"][0]["cwd"]), (["swift", "test"], "/r/Lib"))
        det["layout"]["swift"] = {"package_dir": None, "schemes": [{"scheme": "App", "dir": "ios", "container": "App.xcodeproj"}]}
        plan = checks.build_plans(checks.make_ctx("/r", det), ["swift_unit"])["swift_unit"]
        self.assertEqual(_argv(plan)[:4], ["xcodebuild", "test", "-scheme", "App"])
        det["tools"] = {}
        self.assertEqual(checks.build_plans(checks.make_ctx("/r", det), ["swift_unit"])["swift_unit"]["kind"], "unavailable")


class BuilderTriggerTestCase(unittest.TestCase):
    FIRED = [{"id": "persisted_state", "evidence": [], "hits": 1}]

    def test_fired_without_test_is_missing_with_test_runs_waived_is_na(self):
        self.assertEqual(_plans(PY_FILES, ["crash_recovery"])["crash_recovery"]["kind"], "na")
        plan = _plans(PY_FILES, ["crash_recovery"], triggers=self.FIRED)["crash_recovery"]
        self.assertEqual(plan["kind"], "missing")
        self.assertIn("persisted_state fired", plan["reason"])
        plan = _plans(PY_FILES + ["tests/test_crash_recovery.py"], ["crash_recovery"], triggers=self.FIRED)["crash_recovery"]
        self.assertEqual(_argv(plan)[3:], ["tests/test_crash_recovery.py"])
        plan = _plans(PY_FILES, ["crash_recovery"], triggers=self.FIRED,
                      sel={"triggers_waived": {"persisted_state": "read-only change"}})["crash_recovery"]
        self.assertEqual(plan["kind"], "na")
        self.assertIn("waived", plan["reason"])

    def test_race_stress_reruns(self):
        files = PY_FILES + ["tests/test_race_x.py"]
        self.assertEqual(len(_plans(files, ["race_stress"])["race_stress"]["steps"]), 10)
        self.assertEqual(len(_plans(files, ["race_stress"], sel={"race_reruns": 2})["race_stress"]["steps"]), 2)
        self.assertEqual(len(_plans(files, ["soak_race"])["soak_race"]["steps"]), 20)

    def test_default_checks_by_tier_and_trigger(self):
        det = kit.fake_det(PY_FILES)
        t0 = checks.default_checks(det, 1)
        self.assertIn("py_compile", t0)
        self.assertIn("secret_scan", t0)
        self.assertIn("diff_minimality", t0)
        self.assertNotIn("py_unit", t0)
        self.assertNotIn("crash_recovery", t0)
        det["triggers"] = self.FIRED
        self.assertIn("crash_recovery", checks.default_checks(det, 1))
        t1 = checks.default_checks(det, 2)
        self.assertIn("py_unit", t1)
        self.assertIn("crap", t1)
        self.assertNotIn("mutation_changed", t1)
        self.assertIn("mutation_full", checks.default_checks(det, 5))
        # 顺序 = CATALOG 顺序
        self.assertEqual(t1, [e["id"] for e in checks.CATALOG if e["id"] in set(t1)])


class BuilderT3T4TestCase(unittest.TestCase):
    def test_mutation_plans(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_plans(PY_FILES, ["mutation_changed"], repo=tmp)["mutation_changed"]["kind"], "unavailable")
            det = kit.fake_det(PY_FILES, tools={"mutmut": "/m"})
            det["layout"]["mutmut"] = True
            plan = checks.build_plans(checks.make_ctx(tmp, det), ["mutation_changed"])["mutation_changed"]
            self.assertEqual(_argv(plan), ["mutmut", "run"])
            kit.make_repo(tmp, {"scripts/qa/mutate.py": ""})
            diff = _diff(["act/lib/x.py", "act/other.py"])
            plans = _plans(PY_FILES, ["mutation_changed", "mutation_full"], repo=tmp, out="/o",
                           mutation_targets=["act/lib/x.py"], diff=diff)
            argv = _argv(plans["mutation_changed"])
            self.assertEqual(argv[argv.index("--modules") + 1:], ["act/lib/x.py"])
            self.assertEqual(argv[argv.index("--time-budget") + 1], "1800")
            self.assertIn("--all", _argv(plans["mutation_full"]))
            self.assertEqual(_plans(PY_FILES, ["mutation_changed"], repo=tmp, mutation_targets=["act/lib/x.py"])
                             ["mutation_changed"]["kind"], "na")

    def test_property_and_flaky(self):
        self.assertEqual(_plans(PY_FILES, ["property_tests"])["property_tests"]["kind"], "na")
        det = kit.fake_det(PY_FILES)
        det["layout"]["property_tests"] = ["tests/test_m.py"]
        self.assertEqual(checks.build_plans(checks.make_ctx("/r", det), ["property_tests"])["property_tests"]["kind"], "unavailable")
        det["pymods"]["hypothesis"] = True
        self.assertEqual(_argv(checks.build_plans(checks.make_ctx("/r", det), ["property_tests"])["property_tests"])[3:],
                         ["tests/test_m.py"])
        plan = _plans(PY_FILES, ["flaky_detect"])["flaky_detect"]
        self.assertEqual((plan["kind"], len(plan["steps"])), ("substituted", 3))
        self.assertEqual(len(_plans(PY_FILES, ["flaky_detect"], sel={"reruns": 2})["flaky_detect"]["steps"]), 2)
        det["layout"]["py_runner"] = "pytest"
        det["pymods"]["pytest_randomly"] = True
        plan = checks.build_plans(checks.make_ctx("/r", det), ["flaky_detect"])["flaky_detect"]
        self.assertEqual((plan["kind"], _argv(plan)[-2:]), ("cmd", ["-p", "randomly"]))

    def test_t4_availability(self):
        with tempfile.TemporaryDirectory() as tmp:
            plans = _plans(PY_FILES, ["fuzz", "perf_budget", "dead_code", "security_scan", "arch_audit",
                                      "dependency_audit", "docs_drift", "test_smells"], repo=tmp)
            self.assertEqual([plans[k]["kind"] for k in ("fuzz", "perf_budget", "dead_code", "security_scan", "arch_audit",
                                                          "dependency_audit")],
                             ["na", "na", "unavailable", "unavailable", "na", "unavailable"])
            self.assertEqual(plans["docs_drift"]["kind"], "internal")
            tools = {"vulture": "/v", "bandit": "/b", "npm": "/n"}
            det = kit.fake_det(PY_FILES, tools=tools)
            det["layout"]["fuzz_dir"] = True
            det["layout"]["js_packages"] = [dict(JS_PKG)]
            kit.make_repo(tmp, {"scripts/qa/hygiene.py": ""})
            plans = checks.build_plans(checks.make_ctx(tmp, det), ["fuzz", "dead_code", "security_scan", "arch_audit",
                                                                   "dependency_audit"])
            self.assertEqual(plans["fuzz"]["kind"], "unavailable")
            self.assertEqual(_argv(plans["dead_code"])[0], "vulture")
            self.assertEqual([s["argv"][0] for s in plans["security_scan"]["steps"]], ["bandit", "npm"])
            self.assertEqual(_argv(plans["arch_audit"])[1], "scripts/qa/hygiene.py")
            self.assertEqual(_argv(plans["dependency_audit"])[:2], ["npm", "audit"])


class CatalogTestCase(unittest.TestCase):
    def test_menu_covers_catalog_and_previews(self):
        det = kit.fake_det(PY_FILES)
        menu = checks.build_menu(checks.make_ctx("/r", det))
        self.assertEqual([m["id"] for m in menu], [e["id"] for e in checks.CATALOG])
        by_id = {m["id"]: m for m in menu}
        self.assertEqual(by_id["secret_scan"]["command"], "internal:check_secret_scan")
        self.assertIn("unittest discover", by_id["py_unit"]["command"])
        self.assertEqual(by_id["mutation_full"]["est_seconds"], None)
        self.assertEqual(by_id["diff_minimality"]["trigger"], "always")

    def test_make_ctx_shape_and_timeouts(self):
        ctx = checks.make_ctx("/r", {})
        self.assertEqual(ctx["baselines"], "/r/.test-code/baselines")
        self.assertEqual(ctx["py"], sys.executable)
        self.assertFalse(ctx["init_baselines"])
        self.assertIsNone(checks.TIER_TIMEOUTS[5])
        self.assertEqual(checks.TIER_TIMEOUTS[1], 300)
        self.assertEqual(sorted(checks.TRIGGER_CHECKS["deps_changed"]), ["dependency_audit", "dependency_budget"])
        with self.assertRaises(KeyError):
            checks.build_plans(ctx, ["not_a_check"])


if __name__ == "__main__":
    unittest.main()
