"""test-code skill · checks builders 第 1–4 档的变异判例（checks.py:746–1070）：把每个
builder 的**可观察产物**钉死——不是「没抛异常」，而是 plan 的 kind / tool / argv 逐词、
cwd、reason 原文、post hook 身份。覆盖 compile/lint/format 的 roots 与 tool 取值、
js eslint 的 blocked 与 ready 两路、swiftc/swift/xcodebuild 的 unavailable 与首个 scheme、
deps direction 的 import-linter 两路、complexity_min 的 src roots、coverage 的 argv 拼接与
out 回落、pytest 集成、generic mutation 的 and 短路、mutation_full 的 7 天预算。

法典：docs/CONTRACT.md §57（存活变异体 = 补测试提案，逐字转载）；卡 R-217（nightly
2026-09-15：run=839 killed=435 survived=404）。零子进程——builders 只构造 argv 从不执行；
真实 IO 只有 tempfile.TemporaryDirectory() 里的 fixture repo（空 = 走 generic 分支；
落几个空文件 = 走项目自带门分支）。
"""
import os
import tempfile
import unittest

from tests import skill_test_code_testkit as kit

import checks  # noqa: E402

PY_FILES = ["pkg/__init__.py", "pkg/m.py", "tests/__init__.py", "tests/test_m.py"]
# 只有源码、没有 tests/ 目录的 python 项目：用来钉 _no_tests_dir 那一侧的闸门
PY_SRC_ONLY = ["pkg/__init__.py", "pkg/m.py"]
JS_PKG = {"dir": "web", "tsconfig": True, "eslint": False, "test_runner": "vitest", "bins": ["tsc", "vitest"],
          "lock": "package-lock.json", "scripts": {}, "coverage_provider": False, "coverage_thresholds": False,
          "playwright": False, "stryker": False}


def _plan(files, cid, repo="/r", out="/out", **det_over):
    """单个 check 的 plan；det 顶层字段可逐键覆盖（tools / pymods / stacks …）。"""
    det = kit.fake_det(files, **det_over)
    return checks.build_plans(checks.make_ctx(repo, det, out=out), [cid])[cid]


def _plan_det(det, cid, repo="/r", out="/out"):
    """已经改过 layout 的 det → plan（layout 是嵌套字段，overrides 覆盖不到）。"""
    return checks.build_plans(checks.make_ctx(repo, det, out=out), [cid])[cid]


def _argv(plan, step=0):
    return plan["steps"][step]["argv"]


def _js_det(pkg_over=None, tools=None, files=None):
    pkg = dict(JS_PKG, **(pkg_over or {}))
    det = kit.fake_det(files or ["web/src/a.ts", "web/package.json"],
                       tools=tools if tools is not None else {"npx": "/npx"})
    det["stacks"] = ["js"]
    det["layout"]["js_packages"] = [pkg]
    return det


def _swift_det(swift, tools):
    det = kit.fake_det(["App/a.swift"], tools=tools, stacks=["swift"])
    det["layout"]["swift"] = swift
    return det


class Tier1PythonRootsTestCase(unittest.TestCase):
    """第 1 档 python 静态门：roots 从 layout 逐词进 argv，没有 python 栈就是 na。"""

    def test_py_compile_argv_ends_with_declared_roots(self):
        """checks._b_py_compile: `_layout(ctx).get("py_roots") or ["."]` — 声明了 roots 就编译
        roots、只有空 layout 才回落 "."（checks.py:748 bool_or：or→and 会把 ["pkg","tests"]
        换成 ["."]，编译范围被悄悄缩到仓库根）。"""
        plan = _plan(PY_FILES, "py_compile")
        self.assertEqual(plan["kind"], "cmd")
        self.assertEqual(plan["tool"], "python")
        self.assertEqual(_argv(plan)[1:], ["-m", "compileall", "-q", "pkg", "tests"])
        # 反面：layout 没有 roots 才允许 "."
        det = kit.fake_det(PY_FILES)
        det["layout"]["py_roots"] = []
        self.assertEqual(_argv(_plan_det(det, "py_compile"))[1:], ["-m", "compileall", "-q", "."])

    def test_py_lint_without_python_stack_is_na_not_unavailable(self):
        """checks._b_py_lint: `return _need_python(ctx)` — 没有 python 源码时这一层是 na
        （不适用），绝不是 unavailable（缺工具）（checks.py:754 return_none：吞掉 _na 后会
        继续找 ruff/flake8，非 python 仓库被误报成「没装 linter」）。"""
        plan = _plan(["web/src/a.ts"], "py_lint", stacks=["js"])
        self.assertEqual(plan["kind"], "na")
        self.assertEqual(plan["reason"], "no python sources")
        # 同一个非 python 仓库即便 PATH 上有 ruff 也还是 na
        plan = _plan(["web/src/a.ts"], "py_lint", stacks=["js"], tools={"ruff": "/r"})
        self.assertEqual(plan["kind"], "na")

    def test_py_format_names_the_formatter_not_its_flags(self):
        """checks._b_py_format: `_unavailable("%s configured but not on PATH" % argv[0])` 与
        `_cmd(argv, ..., tool=argv[0])` — 报告里出现的是 formatter 名字（argv[0]="black"），
        不是它的开关（checks.py:769/770 int_minus1/int_plus1：0→-1 会说「. configured but
        not on PATH」、0→1 会把 tool 记成 "--check"）。"""
        det = kit.fake_det(PY_FILES)
        det["layout"]["py_format"] = "black"
        plan = _plan_det(det, "py_format")
        self.assertEqual(plan["kind"], "unavailable")
        self.assertEqual(plan["reason"], "black configured but not on PATH")
        det["tools"] = {"black": "/usr/bin/black"}
        plan = _plan_det(det, "py_format")
        self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "black"))
        self.assertEqual(_argv(plan), ["black", "--check", "."])

    def test_py_format_ruff_variant_keeps_tool_name(self):
        """checks._b_py_format: 四词 argv（ruff format --check .）下 tool 仍是 argv[0]
        （checks.py:770 int_plus1：0→1 会记成 "format"）。"""
        det = kit.fake_det(PY_FILES, tools={"ruff": "/r"})
        det["layout"]["py_format"] = "ruff"
        plan = _plan_det(det, "py_format")
        self.assertEqual(plan["tool"], "ruff")
        self.assertEqual(_argv(plan), ["ruff", "format", "--check", "."])
        det["tools"] = {}
        self.assertEqual(_plan_det(det, "py_format")["reason"], "ruff configured but not on PATH")


class Tier1JsSwiftDepsTestCase(unittest.TestCase):
    """第 1 档 js / swift / 依赖方向：blocked 必须原样返回，ready 才给命令。"""

    def test_js_lint_blocked_when_eslint_not_installed(self):
        """checks._b_js_lint: `if blocked: return blocked` — 配了 eslint 但 node_modules 里没装
        → unavailable，绝不生成 npx 命令（checks.py:793 return_none：吞掉 blocked 后会输出
        一条注定 npx 失败的命令）。"""
        det = _js_det({"eslint": True, "bins": []})
        plan = _plan_det(det, "js_lint")
        self.assertEqual(plan["kind"], "unavailable")
        self.assertIn("eslint not installed in ['web']", plan["reason"])
        self.assertEqual(plan["steps"], [])

    def test_js_lint_ready_plan_is_npx_eslint_per_package_dir(self):
        """checks._b_js_lint: `return _steps("cmd", _js_steps(...), "eslint")` — 装好了就每个
        package dir 一条 `npx --no-install eslint .`（checks.py:794 return_none：返回 None，
        调用方拿不到 plan）。"""
        det = _js_det({"eslint": True, "bins": ["eslint"]})
        plan = _plan_det(det, "js_lint")
        self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "eslint"))
        self.assertEqual(_argv(plan), ["npx", "--no-install", "eslint", "."])
        self.assertEqual(plan["steps"][0]["cwd"], os.path.join("/r", "web"))

    def test_swift_parse_without_swiftc_is_unavailable(self):
        """checks._b_swift_parse: `return _unavailable("swiftc not on PATH")` — 有 .swift 但
        没编译器 = 缺工具，不是「可以跑」（checks.py:813 return_none：吞掉后会生成一串
        swiftc -parse 命令）。"""
        plan = _plan(["a.swift", "b/c.swift"], "swift_parse")
        self.assertEqual(plan["kind"], "unavailable")
        self.assertEqual(plan["reason"], "swiftc not on PATH")
        self.assertEqual(plan["steps"], [])

    def test_deps_direction_import_linter_both_ways(self):
        """checks._b_deps_direction: import-linter 分支的两条 `return` — 装了 lint-imports 就
        跑它、没装就 unavailable 并点名工具（checks.py:824/825 return_none：任一条返回
        None，deps_direction 这层直接从 plan 里消失）。"""
        with tempfile.TemporaryDirectory() as tmp:  # 空 repo：没有 scripts/qa/depgraph.py
            det = kit.fake_det(PY_FILES, tools={"lint-imports": "/usr/bin/lint-imports"})
            det["layout"]["importlinter"] = True
            plan = _plan_det(det, "deps_direction", repo=tmp)
            self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "lint-imports"))
            self.assertEqual(_argv(plan), ["lint-imports"])
            self.assertEqual(plan["steps"][0]["cwd"], tmp)
            det["tools"] = {}
            plan = _plan_det(det, "deps_direction", repo=tmp)
            self.assertEqual(plan["kind"], "unavailable")
            self.assertEqual(plan["reason"], "import-linter configured but lint-imports not on PATH")

    def test_complexity_min_measures_declared_src_roots(self):
        """checks._complexity_min_plan: `_layout(ctx).get("py_src_roots") or ["."]` — roots 是
        argv 的尾巴，声明了就只量源码目录（checks.py:830 bool_or：or→and 会把 ["pkg"] 换成
        ["."]，连 tests/ 和 node_modules 一起量）。"""
        with tempfile.TemporaryDirectory() as tmp:  # 空 repo：没有项目自带 complexity.py
            argv = _argv(_plan(PY_FILES, "complexity", repo=tmp))
            self.assertEqual(argv[-1:], ["pkg"])
            self.assertEqual(argv[2:6], ["--only", "cc", "--root", tmp])
            det = kit.fake_det(PY_FILES)
            det["layout"]["py_src_roots"] = []
            self.assertEqual(_argv(_plan_det(det, "complexity", repo=tmp))[-1:], ["."])


class Tier2SwiftCoverageTestCase(unittest.TestCase):
    """第 2 档单元 + 尺子：swift 两种工程形态、coverage 的 argv 拼接与 out 回落。"""

    def test_swift_package_without_toolchain_is_unavailable(self):
        """checks._b_swift_package: `return _unavailable("swift toolchain not on PATH")` —
        有 Package.swift 但没装 swift = 缺工具（checks.py:895 return_none：吞掉后会输出
        `swift test`，一条注定 not found 的命令）。"""
        det = _swift_det({"package_dir": "Lib", "schemes": []}, tools={})
        plan = _plan_det(det, "swift_unit")
        self.assertEqual(plan["kind"], "unavailable")
        self.assertEqual(plan["reason"], "swift toolchain not on PATH")
        det["tools"] = {"swift": "/usr/bin/swift"}
        plan = _plan_det(det, "swift_unit")
        self.assertEqual((_argv(plan), plan["steps"][0]["cwd"]), (["swift", "test"], os.path.join("/r", "Lib")))

    def test_swift_xcode_takes_the_first_scheme(self):
        """checks._b_swift_xcode: `first = schemes[0]` — 多 scheme 时取 detect 给出的**第一个**
        （scheme 名和它的 container 目录成对出现）（checks.py:904 int_minus1：0→-1 会拿最后
        一个 scheme，argv 里是 App2 却在 ios1 目录下跑）。"""
        schemes = [{"scheme": "App1", "dir": "ios1", "container": "App1.xcodeproj"},
                   {"scheme": "App2", "dir": "ios2", "container": "App2.xcodeproj"}]
        det = _swift_det({"package_dir": None, "schemes": schemes}, tools={"xcodebuild": "/x"})
        plan = _plan_det(det, "swift_unit")
        self.assertEqual(_argv(plan), ["xcodebuild", "test", "-scheme", "App1",
                                       "-destination", "platform=macOS"])
        self.assertEqual(plan["steps"][0]["cwd"], os.path.join("/r", "ios1"))

    def test_py_coverage_gate_reports_the_first_blocker(self):
        """checks._b_py_coverage: `blocked = _need_python(ctx) or _no_tests_dir(ctx)` 与
        `return blocked` — 两道闸门任一拦下就照原因 na；两道都拦时报告**第一道**
        （checks.py:910 bool_or：or→and 会在只缺 tests/ 时放行、生成 coverage 命令，
        两道都拦时又把原因换成第二道；checks.py:912 return_none：plan 直接变 None）。"""
        plan = _plan(PY_SRC_ONLY, "py_coverage", pymods={"coverage": True})
        self.assertEqual(plan["kind"], "na")
        self.assertEqual(plan["reason"], "no python tests dir")
        # 两道闸门同时拦：报告 _need_python 的原因（or 的左值）
        plan = _plan(["web/src/a.ts"], "py_coverage", stacks=["js"], pymods={"coverage": True})
        self.assertEqual((plan["kind"], plan["reason"]), ("na", "no python sources"))

    def test_generic_coverage_argv_is_coverage_run_plus_test_argv_tail(self):
        """checks._generic_coverage_plan: `... + _py_test_argv(ctx)[2:]` — coverage run 自己带
        `-m`，所以项目测试命令要削掉 `python -m` 两词再接上（checks.py:923 int_minus1
        2→1 会多出一个 `-m`，int_plus1 2→3 会丢掉 `unittest`）。"""
        with tempfile.TemporaryDirectory() as tmp:  # 空 repo：没有 scripts/qa/run_coverage.sh
            det = kit.fake_det(PY_FILES, pymods={"coverage": True})
            plan = _plan_det(det, "py_coverage", repo=tmp, out="/out")
            self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "coverage"))
            self.assertEqual(_argv(plan)[1:], ["-m", "coverage", "run", "--source=pkg", "-m",
                                               "unittest", "discover", "-s", "tests"])
            self.assertEqual(_argv(plan, 1)[1:], ["-m", "coverage", "json", "-o",
                                                  os.path.join("/out", "coverage.json")])

    def test_project_coverage_passes_the_run_out_dir(self):
        """checks._project_coverage_plan: `ctx["out"] or ".qa-report"` — 本次运行的 out 目录
        逐字传给项目脚本，只有 out 缺席才回落 .qa-report（checks.py:930 bool_or：or→and 在
        有 out 时也写 .qa-report，报告会去错目录找 coverage.json）。
        注：.qa-report 这个回落值是 checks.py 本地默认，references/*.md 里没有对应行，
        所以本测试是它唯一的成文处。"""
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"scripts/qa/run_coverage.sh": "", "scripts/qa/coverage_floor.py": ""})
            det = kit.fake_det(PY_FILES, pymods={"coverage": True})
            plan = _plan_det(det, "py_coverage", repo=tmp, out="/out")
            self.assertEqual(_argv(plan), ["bash", "scripts/qa/run_coverage.sh", "/out"])
            plan = _plan_det(det, "py_coverage", repo=tmp, out=None)
            self.assertEqual(_argv(plan), ["bash", "scripts/qa/run_coverage.sh", ".qa-report"])

    def test_js_coverage_blocked_when_vitest_not_installed(self):
        """checks._b_js_coverage: `if blocked: return blocked` — vitest 没装（或缺
        @vitest/coverage-*）→ unavailable 且不给命令（checks.py:940 return_none：吞掉 blocked
        后会照样输出 `npx --no-install vitest run --coverage`）。"""
        det = _js_det({"bins": []})
        plan = _plan_det(det, "js_coverage")
        self.assertEqual(plan["kind"], "unavailable")
        # 两条 unavailable 分支的 kind/steps 完全一样，只有 reason 能分辨——所以钉全文
        self.assertEqual(plan["reason"], "vitest not installed in ['web'] (npm ci) or npx missing")
        self.assertEqual(plan["steps"], [])
        # 装了 vitest 但没装 coverage provider：同样 unavailable，原因换成 provider
        plan = _plan_det(_js_det(), "js_coverage")
        self.assertEqual(plan["kind"], "unavailable")
        self.assertEqual(plan["reason"], "@vitest/coverage-* not installed in ['web']")


class Tier3Tier4TestCase(unittest.TestCase):
    """第 3/4 档：pytest 集成、generic mutation 的工具二元判断、全量变异的 7 天预算。"""

    def test_py_integration_pytest_branch(self):
        """checks._b_py_integration: pytest 分支的 `return _cmd([... "pytest", "-q", integ] ...)`
        — 项目用 pytest 就直接点集成目录，post 走 _post_tests（checks.py:989 return_none：
        返回 None，py_integration 这层从 plan 里消失）。"""
        det = kit.fake_det(PY_FILES, pymods={"pytest": True})
        det["layout"]["integration_dir"] = "tests/integration"
        det["layout"]["py_runner"] = "pytest"
        plan = _plan_det(det, "py_integration")
        self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "python-tests"))
        self.assertEqual(_argv(plan)[1:], ["-m", "pytest", "-q", "tests/integration"])
        self.assertIs(plan["post"], checks._post_tests)
        # unittest 项目走另一条：discover -s <integ> -t <repo>
        det["layout"]["py_runner"] = "unittest"
        self.assertEqual(_argv(_plan_det(det, "py_integration"))[1:],
                         ["-m", "unittest", "discover", "-s", "tests/integration", "-t", "/r"])

    def test_generic_mutation_needs_mutmut_configured_and_installed(self):
        """checks._generic_mutation: `_layout(ctx).get("mutmut") and _tool(ctx, "mutmut")` —
        配置和安装**两者都要**才跑 mutmut；只满足一个就落到 unavailable
        （checks.py:1043 bool_and：and→or 会在只配置没装、或只装没配置时输出 `mutmut run`）。"""
        with tempfile.TemporaryDirectory() as tmp:  # 空 repo：没有 scripts/qa/mutate.py
            det = kit.fake_det(PY_FILES)
            det["layout"]["mutmut"] = True  # 配了但没装
            plan = _plan_det(det, "mutation_full", repo=tmp)
            self.assertEqual(plan["kind"], "unavailable")
            self.assertIn("no mutation tool", plan["reason"])
            det["layout"]["mutmut"] = False  # 装了但没配
            det["tools"] = {"mutmut": "/usr/bin/mutmut"}
            self.assertEqual(_plan_det(det, "mutation_full", repo=tmp)["kind"], "unavailable")
            det["layout"]["mutmut"] = True  # 两者齐备
            plan = _plan_det(det, "mutation_full", repo=tmp)
            self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "mutmut"))
            self.assertEqual(_argv(plan), ["mutmut", "run"])

    def test_generic_mutation_stryker_fallback(self):
        """checks._generic_mutation: `return _steps("cmd", _js_steps(...), "stryker")` — 没有
        mutmut 但 package.json 声明且装了 stryker → 每个 pkg 一条 `npx --no-install stryker
        run`（checks.py:1047 return_none：返回 None，第 4 档拿不到 plan）。"""
        with tempfile.TemporaryDirectory() as tmp:
            det = _js_det({"stryker": True, "bins": ["vitest", "stryker"]})
            plan = _plan_det(det, "mutation_full", repo=tmp)
            self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "stryker"))
            self.assertEqual(_argv(plan), ["npx", "--no-install", "stryker", "run"])
            self.assertEqual(plan["steps"][0]["cwd"], os.path.join(tmp, "web"))

    def test_mutation_full_budget_is_seven_days(self):
        """checks._b_mutation_full: `_mutate_plan(ctx, None, 7 * 24 * 3600)` — 全量变异的预算
        就是 7 天 604800 秒，且跑 --all（checks.py:1065 int_minus1/int_plus1 各三个：7/24/3600
        任一被改，预算就成 518400 / 579600 / 604632 / 691200 / 630000 / 604968）。"""
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"scripts/qa/mutate.py": ""})
            plan = _plan(PY_FILES, "mutation_full", repo=tmp, out="/out")
            argv = _argv(plan)
            self.assertEqual(argv[argv.index("--time-budget") + 1], "604800")
            self.assertIn("--all", argv)
            self.assertNotIn("--modules", argv)
            self.assertIs(plan["post"], checks._post_mutation)


if __name__ == "__main__":
    unittest.main()
