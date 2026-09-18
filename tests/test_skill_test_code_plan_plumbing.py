"""test-code skill · plan/step 构造 + python/js/swift 就绪管线的判例：把 builders 拼出来的
argv **逐项**钉死（不是子串），把「工具缺席 / 本项目无此面」两种闸门的 plan 钉死交出去而不是
变成 None，把 reruns / runs 这类默认值钉死在 1，把 no-drop 基线的存储精度钉死在一位小数。

为什么逐项断言 argv：这一层的变异体大多是**下标平移**（`argv[0]` → `argv[1]`/`argv[-1]`、
切片 `[2:]` → `[1:]`/`[3:]`）和 `or` → `and` 的回落塌缩——子串断言全都看不见，只有整条
argv 列表能看见。fixture 一律让首项 ≠ 末项，否则下标平移测不出来。

法典指针：docs/CONTRACT.md §57（存活变异体 = 补测试提案，变异 runner scripts/qa/mutate.py +
靶区 qa/mutation_targets.toml）、§58（项目有门就用项目的门；skill 只读项目阈值）。
设计 = docs/design/vnext2-plan.md R2.8 / D14。每个测试的 docstring 写明它杀的是哪一行的
哪个变异（`checks._fn: <表达式> —— ...（<变异> 会...）`）。零子进程：builders 只构造 argv。
"""
import importlib
import json
import os
import sys
import tempfile
import unittest

from tests import skill_test_code_testkit as kit

import checks  # noqa: E402

lc = kit.lc


def _ctx(files, layout=None, tools=None, pymods=None, stacks=None,
         repo="/repo", out="/out", sel=None, init=False):
    """kit.fake_det + 逐键覆盖 layout/tools/pymods/stacks，再走 checks.make_ctx 出生点。"""
    det = kit.fake_det(files)
    if layout:
        det["layout"].update(layout)
    if tools is not None:
        det["tools"] = tools
    if pymods is not None:
        det["pymods"] = pymods
    if stacks is not None:
        det["stacks"] = stacks
    return checks.make_ctx(repo, det, sel=sel, out=out, init_baselines=init)


def _argv(plan, step=0):
    return plan["steps"][step]["argv"]


class CmdPlanLabelTestCase(unittest.TestCase):
    def test_tool_label_prefers_explicit_then_falls_back_to_executable(self):
        """checks._cmd: `tool or argv[0]` —— 显式 tool 优先，缺省时才用可执行文件名当标签
        （or → and 会让缺省档的标签变成 None、显式档变成 argv[0]，两边都错）。"""
        inferred = checks._cmd(["ruff", "check", "."], "/repo")
        explicit = checks._cmd(["ruff", "check", "."], "/repo", tool="python-tests")
        self.assertEqual(inferred["tool"], "ruff")
        self.assertEqual(explicit["tool"], "python-tests")

    def test_inferred_label_is_the_first_argv_entry(self):
        """checks._cmd: `argv[0]` —— 首尾不同的 argv 里标签必须取**首**项
        （0 → 1 会取 "check"、0 → -1 会取 "."）。顺带钉死 step 三键形状。"""
        plan = checks._cmd(["ruff", "check", "."], "/repo")
        self.assertEqual(plan["tool"], "ruff")
        self.assertEqual(plan["steps"], [{"argv": ["ruff", "check", "."], "cwd": "/repo", "env": None}])


class PyTestArgvTestCase(unittest.TestCase):
    def test_discover_dir_is_the_declared_tests_dir_else_the_default(self):
        """checks._py_test_argv: `layout.get("tests_dir") or "tests"` —— 声明了就用声明的目录，
        没声明才回落 "tests"（or → and 会把 "spec" 塌成 "tests"、把未声明塌成 argv 里一个 None）。"""
        ctx = _ctx(["spec/test_a.py"], layout={"tests_dir": "spec"})
        self.assertEqual(checks._py_test_argv(ctx),
                         [ctx["py"], "-m", "unittest", "discover", "-s", "spec"])
        bare = _ctx(["a.py"], layout={"tests_dir": None})
        self.assertEqual(checks._py_test_argv(bare),
                         [bare["py"], "-m", "unittest", "discover", "-s", "tests"])

    def test_no_tests_dir_yields_an_empty_list_not_none(self):
        """checks._py_tests_matching: 无 tests 目录时返回空 **list** —— docstring 声明的返回值
        是「相对路径列表」；现有三个调用点（_trigger_subset / _tier_subset / _b_soak_race）只做
        真值判断所以看不出 None，但返回类型本身是契约，任何新调用点一 len()/拼接就炸
        （return [] → return None 会悄悄换掉返回类型）。正向档同时钉死筛选规则本身：
        只认 tests 目录下 basename 以 test 开头的 .py，源码里的同名文件不算判例。"""
        ctx = _ctx(["a.py"], layout={"tests_dir": None})
        self.assertEqual(checks._py_tests_matching(ctx, "race"), [])
        hit = _ctx(["tests/test_race_a.py", "tests/test_b.py",
                    "tests/helper_race.py", "act/test_race_b.py"])
        self.assertEqual(checks._py_tests_matching(hit, "race"), ["tests/test_race_a.py"])


class RerunDefaultsTestCase(unittest.TestCase):
    def test_subset_plan_runs_once_by_default(self):
        """checks._subset_plan: `reruns=1` 默认 —— 省略 reruns 的调用者只跑一遍
        （1 → 2 会让每个子集判例默认双跑，时间预算直接翻倍）。"""
        ctx = _ctx(["tests/test_a.py"])
        self.assertEqual(len(checks._subset_plan(ctx, ["tests/test_a.py"])["steps"]), 1)
        self.assertEqual(len(checks._subset_plan(ctx, ["tests/test_a.py"], 3)["steps"]), 3)

    def test_trigger_subset_runs_once_by_default(self):
        """checks._trigger_subset: `reruns=1` 默认 —— 触发器加挂的判例默认单跑
        （1 → 2 会把每个触发器子集悄悄变成双跑）。"""
        ctx = _ctx(["tests/test_fault_injection.py"])
        plan = checks._trigger_subset(ctx, "boundary", r"fault")
        self.assertEqual(plan["kind"], "cmd")
        self.assertEqual(len(plan["steps"]), 1)

    def test_tests_verdict_counts_one_run_by_default(self):
        """checks._tests_verdict: `runs=1` 默认 —— 省略 runs 时摘要不带 ×N 后缀、details.runs 记 1
        （1 → 2 会给单跑贴上 "×2 runs"；1 → 0 会把跑过的一遍记成 0 遍）。"""
        res = checks._tests_verdict([], [], set(), True, "Ran 3 tests\nOK\n")
        self.assertNotIn("×", res["summary"])
        self.assertEqual(res["details"]["runs"], 1)


class CoverageBaselinePrecisionTestCase(unittest.TestCase):
    def test_no_drop_baseline_is_stored_at_one_decimal(self):
        """checks._post_coverage_generic: `round(total, 1)` —— 基线的存储精度必须和
        _no_drop_verdict 的比较容差（`total < floor - 0.1`）同一档，所以存一位小数：
        83.449 → 83.4。round(·,0) 会存成 83.0，round(·,2) 经 lc.format_value 的 "%.1f"
        反而进位成 83.5——两个方向都把地板挪动 0.1（= 整个容差），实测三值互不相同。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            os.makedirs(out)
            with open(os.path.join(out, "coverage.json"), "w", encoding="utf-8") as fh:
                json.dump({"totals": {"percent_covered": 83.449}}, fh)
            ctx = _ctx(["tests/test_a.py"], repo=tmp, out=out, init=True)
            res = checks._post_coverage_generic(ctx, None, [lc.RunResult(0)])
            self.assertEqual(res["status"], "pass")
            stored = lc.load_ledger(checks._ledger_path(ctx, "coverage_total"))
            self.assertEqual(stored["total"], 83.4)


class PythonBuilderReadinessTestCase(unittest.TestCase):
    def test_compileall_targets_declared_roots_else_the_repo_root(self):
        """checks._b_py_compile: `py_roots or ["."]` —— 声明了包根就只编包根，没声明才编 "."
        （or → and 会把 ["act"] 塌成 ["."]、把 [] 塌成一条没有编译目标的 argv）。"""
        ctx = _ctx(["act/a.py"], layout={"py_roots": ["act"]})
        self.assertEqual(_argv(checks._b_py_compile(ctx)),
                         [ctx["py"], "-m", "compileall", "-q", "act"])
        bare = _ctx(["a.py"], layout={"py_roots": []})
        self.assertEqual(_argv(checks._b_py_compile(bare)),
                         [bare["py"], "-m", "compileall", "-q", "."])

    def test_py_lint_without_python_sources_is_na(self):
        """checks._b_py_lint: 无 python 栈时把 _need_python 的 na plan 交出去
        （return X → return None 会让 run_ladder 拿到 None，而不是「本项目无此面」）。"""
        plan = checks._b_py_lint(_ctx(["web/app.ts"], stacks=[]))
        self.assertEqual(plan["kind"], "na")
        self.assertIn("no python sources", plan["reason"])

    def test_formatter_missing_from_path_names_the_formatter(self):
        """checks._b_py_format: `"%s configured but not on PATH" % argv[0]` —— 点名的是
        格式化器本身（0 → 1 会说 "format configured…"、0 → -1 会说 ". configured…"）。"""
        plan = checks._b_py_format(_ctx(["a.py"], layout={"py_format": "ruff"}, tools={}))
        self.assertEqual(plan["kind"], "unavailable")
        self.assertEqual(plan["reason"].split()[0], "ruff")

    def test_formatter_plan_is_labelled_with_the_formatter(self):
        """checks._b_py_format: `tool=argv[0]` —— plan 的 tool 是 "ruff"，不是 argv 里的
        "format" / "."（0 → 1 / 0 → -1 都会把报告里的工具名换掉）。"""
        plan = checks._b_py_format(_ctx(["a.py"], layout={"py_format": "ruff"},
                                        tools={"ruff": "/bin/ruff"}))
        self.assertEqual(plan["tool"], "ruff")
        self.assertEqual(_argv(plan), ["ruff", "format", "--check", "."])

    def test_py_coverage_blocked_reason_reports_the_first_missing_face(self):
        """checks._b_py_coverage: `blocked = _need_python(ctx) or _no_tests_dir(ctx)` + `return blocked`
        —— 两面都缺时报「无 python 源」（or → and 会改报「无 tests 目录」）；只缺一面时 and 会把
        blocked 塌成 None、整个闸门被漏过去落到 coverage 模块检查上（return X → return None 同样
        让 na plan 变 None）。三种形状各钉一条。"""
        both = _ctx(["web/app.ts"], stacks=[], layout={"tests_dir": None})
        self.assertIn("no python sources", checks._b_py_coverage(both)["reason"])
        no_dir = _ctx(["a.py"], layout={"tests_dir": None})
        self.assertIn("no python tests dir", checks._b_py_coverage(no_dir)["reason"])
        no_py = _ctx(["web/app.ts", "tests/test_a.py"], stacks=[])
        self.assertIn("no python sources", checks._b_py_coverage(no_py)["reason"])

    def test_pytest_integration_plan_targets_the_integration_dir(self):
        """checks._b_py_integration: pytest 项目走 `pytest -q <integration_dir>`，tool 记
        python-tests、post 挂 _post_tests（return X → return None 会让第 3 档集成层静默消失）。"""
        ctx = _ctx(["tests/integration/test_a.py"],
                   layout={"py_runner": "pytest", "integration_dir": "tests/integration"})
        plan = checks._b_py_integration(ctx)
        self.assertEqual(_argv(plan), [ctx["py"], "-m", "pytest", "-q", "tests/integration"])
        self.assertEqual(plan["tool"], "python-tests")
        self.assertIs(plan["post"], checks._post_tests)


class JsReadinessTestCase(unittest.TestCase):
    def test_reason_names_node_modules_when_only_npx_is_missing(self):
        """checks._js_ready: `missing or ["node_modules"]` —— bin 都装好、只缺 npx 时原因里写
        node_modules（or → and 会塌成 `[]`，人看到 "not installed in []" 无从下手）；缺 bin 时
        写的是包目录（and 会反过来把包目录换成 node_modules）。"""
        no_npx = checks._js_ready(_ctx(["web/package.json"], tools={}),
                                  [{"dir": "web", "bins": ["eslint"]}], ["eslint"])
        self.assertIn("node_modules", no_npx["reason"])
        self.assertNotIn("[]", no_npx["reason"])
        no_bin = checks._js_ready(_ctx(["web/package.json"], tools={"npx": "/bin/npx"}),
                                  [{"dir": "web", "bins": []}], ["eslint"])
        self.assertIn("'web'", no_bin["reason"])

    def test_js_lint_blocked_plan_is_handed_over_not_swallowed(self):
        """checks._b_js_lint: `return blocked` —— 配了 eslint 但 node_modules/.bin 空时要把
        unavailable plan 交出去（return X → return None 会让这一层变 None）。原因要同时点名
        缺的 bin 和缺它的包目录，steps 必须是空的——unavailable 档永不带命令。"""
        ctx = _ctx(["web/package.json"], tools={},
                   layout={"js_packages": [{"dir": "web", "eslint": True, "bins": []}]})
        plan = checks._b_js_lint(ctx)
        self.assertEqual(plan["kind"], "unavailable")
        self.assertIn("eslint", plan["reason"])
        self.assertIn("'web'", plan["reason"])
        self.assertEqual(plan["steps"], [])

    def test_js_lint_ready_plan_runs_eslint_in_each_package_dir(self):
        """checks._b_js_lint: 就绪时每个包目录一步 `npx --no-install eslint .`，cwd = repo/包目录
        （return X → return None 会让 eslint 层静默消失）。"""
        ctx = _ctx(["web/package.json"], tools={"npx": "/bin/npx"},
                   layout={"js_packages": [{"dir": "web", "eslint": True, "bins": ["eslint"]}]})
        plan = checks._b_js_lint(ctx)
        self.assertEqual(plan["tool"], "eslint")
        self.assertEqual(_argv(plan), ["npx", "--no-install", "eslint", "."])
        self.assertEqual(plan["steps"][0]["cwd"], os.path.join("/repo", "web"))

    def test_vitest_coverage_blocked_plan_is_handed_over_not_swallowed(self):
        """checks._vitest_cov_ready: `return blocked` —— vitest 本体没装时交出 _js_ready 的
        unavailable plan（return X → return None 会谎报「就绪」，让调用方接着拼 --coverage 命令）。"""
        ctx = _ctx(["web/package.json"], tools={"npx": "/bin/npx"})
        self.assertEqual(checks._vitest_cov_ready(ctx, [{"dir": "web", "bins": []}])["kind"],
                         "unavailable")


class SwiftBuilderReadinessTestCase(unittest.TestCase):
    def test_swift_parse_without_swiftc_is_unavailable(self):
        """checks._b_swift_parse: swiftc 缺席 = unavailable plan
        （return X → return None 会让有 .swift 的项目静默跳过语法门）。"""
        plan = checks._b_swift_parse(_ctx(["mac/Sources/App.swift"], tools={}))
        self.assertEqual(plan["kind"], "unavailable")
        self.assertIn("swiftc", plan["reason"])

    def test_swift_package_without_toolchain_is_unavailable(self):
        """checks._b_swift_package: swift 工具链缺席 = unavailable plan
        （return X → return None 会让 swift test 层变 None）。"""
        plan = checks._b_swift_package(_ctx(["mac/Package.swift"], tools={}), "mac")
        self.assertEqual(plan["kind"], "unavailable")
        self.assertIn("swift toolchain", plan["reason"])

    def test_xcode_plan_uses_the_first_scheme(self):
        """checks._b_swift_xcode: `first = schemes[0]` —— 两个 scheme 时跑**第一**个，cwd 也取
        它的目录（0 → -1 会跑 AssistantTests 并把 cwd 换成 mac/Tests）。"""
        ctx = _ctx(["mac/App.swift"], tools={"xcodebuild": "/usr/bin/xcodebuild"})
        schemes = [{"scheme": "AssistantApp", "dir": "mac"},
                   {"scheme": "AssistantTests", "dir": "mac/Tests"}]
        plan = checks._b_swift_xcode(ctx, schemes)
        self.assertEqual(_argv(plan), ["xcodebuild", "test", "-scheme", "AssistantApp",
                                       "-destination", "platform=macOS"])
        self.assertEqual(plan["steps"][0]["cwd"], os.path.join("/repo", "mac"))


class DepsDirectionPlanTestCase(unittest.TestCase):
    def test_import_linter_plan_when_configured_and_installed(self):
        """checks._b_deps_direction: 项目没有 scripts/qa/depgraph.py 但配了 import-linter 且
        lint-imports 在 PATH → lint-imports plan（return X → return None 会让方向门变 None）。"""
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _ctx(["act/a.py"], repo=tmp, layout={"importlinter": True},
                       tools={"lint-imports": "/bin/lint-imports"})
            plan = checks._b_deps_direction(ctx)
            self.assertEqual(_argv(plan), ["lint-imports"])
            self.assertEqual(plan["tool"], "lint-imports")

    def test_import_linter_configured_but_not_installed_is_unavailable(self):
        """checks._b_deps_direction: 配了 import-linter 但 lint-imports 不在 PATH = unavailable
        （return X → return None 会把「工具缺席」变成 None，方向门从报告里消失）。"""
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _ctx(["act/a.py"], repo=tmp, layout={"importlinter": True}, tools={})
            plan = checks._b_deps_direction(ctx)
            self.assertEqual(plan["kind"], "unavailable")
            self.assertIn("lint-imports", plan["reason"])


class ComplexityMinPlanTestCase(unittest.TestCase):
    def test_roots_come_from_py_src_roots_else_the_repo_root(self):
        """checks._complexity_min_plan: `py_src_roots or ["."]` —— 声明了源根就只扫源根（测试目录
        不进复杂度账本），没声明才扫 "."（or → and 会把 ["act"] 塌成 ["."]、把 [] 塌成一条完全
        没有扫描目标的 argv——末项会变成 --root 的值）。"""
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _ctx(["act/a.py"], repo=tmp, layout={"py_src_roots": ["act"]})
            argv = _argv(checks._complexity_min_plan(ctx, "cc", "complexity", ["--max-cc", "10"]))
            self.assertEqual(argv[:2], [ctx["py"], checks._skill_script(ctx, "complexity_min.py")])
            self.assertEqual(argv[-1], "act")
            bare = _ctx(["a.py"], repo=tmp, layout={"py_src_roots": []})
            self.assertEqual(_argv(checks._complexity_min_plan(bare, "cc", "complexity", []))[-1], ".")


class CoveragePlanArgvTestCase(unittest.TestCase):
    def test_generic_run_drops_the_interpreter_and_dash_m_prefix(self):
        """checks._generic_coverage_plan: `_py_test_argv(ctx)[2:]` —— 切掉 [python, -m] 再接到
        `coverage run … -m` 后面（[1:] 会留下多余的 -m 让 coverage 把 "-m" 当模块名；
        [3:] 会丢掉 "unittest" 只剩 discover）。整条 argv 逐项断言，下标平移才看得见。"""
        ctx = _ctx(["act/a.py", "tests/test_a.py"], layout={"py_src_roots": ["act"]})
        plan = checks._generic_coverage_plan(ctx)
        self.assertEqual(_argv(plan),
                         [ctx["py"], "-m", "coverage", "run", "--source=act", "-m",
                          "unittest", "discover", "-s", "tests"])
        self.assertEqual(_argv(plan, 1),
                         [ctx["py"], "-m", "coverage", "json", "-o",
                          os.path.join("/out", "coverage.json")])

    def test_project_coverage_passes_the_out_dir_else_the_default(self):
        """checks._project_coverage_plan: `ctx["out"] or ".qa-report"` —— 有 --out 就把它传给
        项目的 run_coverage.sh，没有才回落 .qa-report（or → and 会把 /out 塌成 .qa-report、
        把 None 原样塞进 argv）。"""
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _ctx(["a.py"], repo=tmp, out="/out")
            self.assertEqual(_argv(checks._project_coverage_plan(ctx)),
                             ["bash", "scripts/qa/run_coverage.sh", "/out"])
            bare = _ctx(["a.py"], repo=tmp, out=None)
            self.assertEqual(_argv(checks._project_coverage_plan(bare)),
                             ["bash", "scripts/qa/run_coverage.sh", ".qa-report"])


class ModuleImportPrecedenceTestCase(unittest.TestCase):
    def test_skill_scripts_dir_is_prepended_not_inserted_later(self):
        """checks 模块级 `sys.path.insert(0, <本脚本目录>)` —— skill 跑在**任意**用户 repo 里，
        它的同层模块（ladder_common / complexity_min / structure_check）必须赢过目标 repo 上的
        同名文件，所以只能插在 sys.path 最前面。0 → 1 / 0 → -1 都会让先到的路径条目抢走这三个
        名字（0 → 1 让 sys.path[0] 赢、0 → -1 更是退到倒数第二位）。

        观测法：先把一个竞争目录塞到 sys.path[0]，reload 重跑模块级代码，看 skill 目录有没有
        把它顶掉。真源 = tests/skill_test_code_testkit.py 里独立算出的 kit.SKILL_SCRIPTS
        （从 repo 根 + skills/test-code/scripts 拼出，不抄 checks.py 自己的值）。

        坑：importlib.reload 会**重新走 sys.path 找 spec**，不是照着原来的 __file__ 重跑——
        竞争目录里真放一个 checks.py 就会被换掉，而且换掉之后本类之后的每个类（按类名排序
        在 M 之后的 Py* / Rerun* / Swift*）都在测另一个文件。所以 reload 前后把 __file__
        钉住：换文件 = 当场红，而不是悄悄绿。"""
        saved = list(sys.path)
        origin = checks.__file__
        try:
            with tempfile.TemporaryDirectory() as rival:
                sys.path.insert(0, rival)
                importlib.reload(checks)
                self.assertEqual(sys.path[0], kit.SKILL_SCRIPTS)
                self.assertEqual(checks.__file__, origin)
        finally:
            sys.path[:] = saved


if __name__ == "__main__":
    unittest.main()
