"""test-code skill · builder 可用性闸门判例：`_b_<check>(ctx)` 系列构造器在「配置在 AND 工具在」
上必须真的 AND（少一个就得给 na / unavailable，绝不替不存在的工具拼命令），在 `X or <默认>`
回落上必须真的 OR（配了用配的、没配用默认），每条早退分支都得真返回 plan 而不是 None。

2026-09-16 对 skills/test-code/scripts/checks.py 跑变异测试后补：逐个钉死当时活下来的 45 个
builder 闸门变异体（bool_and / bool_or / cmp_in / return_none / insert 位置 / 时间预算常数）。
每个测试的 docstring 写明它杀的是哪个函数的哪一行变异。零子进程（builders 只拼 argv，不执行）。

法典：docs/CONTRACT.md §57（存活变异体 = 补测试提案）/ §58（项目有门就用项目的门）。
命令形状 truth = skills/test-code/references/adapters.md；lights-up 条件与通过线 truth =
skills/test-code/references/catalog.md；档位与预算 truth = skills/test-code/references/tiers.md。
"""
import datetime
import os
import tempfile
import unittest

from tests import skill_test_code_testkit as kit

import checks  # noqa: E402

# 有源根 / 有 tests 目录的 python 项目 vs 平铺一个模块（py_src_roots 为空 → 走 `.` 回落）
PY_FILES = ["pkg/__init__.py", "pkg/m.py", "tests/__init__.py", "tests/test_m.py"]
FLAT_FILES = ["m.py"]


def _ctx(repo="/repo", files=None, out="/out", sel=None, layout=None, **det_over):
    """builder 上下文：files 定栈与源根，layout=... 逐键覆盖 detection 的 layout，其余覆盖顶层。"""
    det = kit.fake_det(PY_FILES if files is None else files, **det_over)
    det["layout"].update(layout or {})
    return checks.make_ctx(repo, det, sel=sel, out=out)


def _build(cid, ctx):
    return checks.BY_ID[cid]["build"](ctx)


def _argv(plan, step=0):
    return plan["steps"][step]["argv"]


def _argvs(plan):
    return [s["argv"] for s in plan["steps"]]


class SecurityScanStepsTestCase(unittest.TestCase):
    def test_bandit_scans_configured_roots_else_the_repo_root(self):
        """checks._security_steps: `_layout().get("py_src_roots") or ["."]` — 配了源根扫源根，
        没配才退回 `.`（or → and 会把配好的项目也扫成 `.`、把没配的扫成「无路径」）。
        命令形状 truth = references/adapters.md 的 `bandit -q -r <src>` 行。"""
        rooted = checks._security_steps(_ctx(files=PY_FILES, tools={"bandit": "/b"}))
        self.assertEqual([s["argv"] for s in rooted], [["bandit", "-q", "-r", "pkg"]])
        flat = checks._security_steps(_ctx(files=FLAT_FILES, tools={"bandit": "/b"}))
        self.assertEqual([s["argv"] for s in flat], [["bandit", "-q", "-r", "."]])

    def test_pip_audit_needs_both_a_requirements_file_and_the_binary(self):
        """checks._pip_audit_steps: `not reqs or not _tool(ctx, "pip-audit")` — 两个条件缺一就交白卷
        （or → and 会替不在 PATH 上的 pip-audit 拼命令）；`reqs = ... or []` 同理
        （or → and 会把配好的 requirements 抹成空、于是同样交白卷）。"""
        both = _ctx(layout={"requirements": ["requirements.txt"]}, tools={"pip-audit": "/p"})
        self.assertEqual(len(checks._pip_audit_steps(both)), 1)
        self.assertEqual(checks._pip_audit_steps(_ctx(layout={"requirements": ["requirements.txt"]})), [])
        self.assertEqual(checks._pip_audit_steps(_ctx(tools={"pip-audit": "/p"})), [])

    def test_pip_audit_takes_the_first_declared_requirements_file(self):
        """checks._pip_audit_steps: `reqs[0]` — 审的是检测到的第一个 requirements
        （0 → 1 / 0 → -1 都会换成 dev 那份）；`return [_step(...)]` 也必须真返回那一步
        （return_none 会让 _security_steps 的列表相加当场炸）。
        命令形状 truth = references/adapters.md 的 `pip-audit -r <req>` 行。"""
        ctx = _ctx(layout={"requirements": ["requirements.txt", "requirements-dev.txt"]},
                   tools={"pip-audit": "/p"})
        steps = checks._pip_audit_steps(ctx)
        self.assertEqual([s["argv"] for s in steps], [["pip-audit", "-r", "requirements.txt"]])
        self.assertEqual(steps[0]["cwd"], "/repo")

    def test_native_audit_needs_both_the_stack_and_the_tool(self):
        """checks._audit_steps: `stack in _stacks(ctx) and _tool(ctx, tool)` — 栈在 AND 工具在才加一步
        （and → or 会给纯 python 项目拼 `cargo audit`；in → not in 会反转成「栈不在才跑」）。
        工具集 truth = references/tiers.md 的 `security_scan` 行（… / cargo-audit / govulncheck）；
        这两条 argv 的字面形状 references 未成文（只在 checks._NATIVE_AUDITS 里），钉的是闸门不是形状。"""
        tools = {"cargo-audit": "/c", "govulncheck": "/g"}
        both = checks._audit_steps(_ctx(stacks=["rust", "go"], tools=tools))
        self.assertEqual([s["argv"] for s in both], [["cargo", "audit"], ["govulncheck", "./..."]])
        self.assertEqual(checks._audit_steps(_ctx(stacks=["python"], tools=tools)), [])
        self.assertEqual(checks._audit_steps(_ctx(stacks=["rust", "go"])), [])


class ArchAuditTestCase(unittest.TestCase):
    def test_qlty_needs_both_config_and_binary(self):
        """checks._qlty_steps: `_layout().get("qlty") and _tool(ctx, "qlty")` — 配置在 AND binary 在
        （and → or 会替没装 qlty 的项目拼 `qlty check`）；`return [_step(...)]` 必须真返回那一步
        （return_none 会让 _b_arch_audit 拿 None 去 insert）。
        「配了才跑 `qlty check --all`」truth = references/tiers.md 的 `arch_audit` 行。"""
        ready = _ctx(layout={"qlty": True}, tools={"qlty": "/q"})
        self.assertEqual([s["argv"] for s in checks._qlty_steps(ready)],
                         [["qlty", "check", "--all", "--no-progress"]])
        self.assertEqual(checks._qlty_steps(_ctx(layout={"qlty": True})), [])
        self.assertEqual(checks._qlty_steps(_ctx(tools={"qlty": "/q"})), [])

    def test_project_gates_are_inserted_ahead_of_the_vendor_tool(self):
        """checks._b_arch_audit: `steps.insert(0, ...)` — 项目自己的门插到队首（§58 项目有门就用项目的门；
        references/tiers.md 的 `arch_audit` 行同样写成 "project depgraph + hygiene, `qlty check --all`
        when configured"），steps 又是「顺序、首败即停」序列（模块 docstring），位置一动就换了先报谁的错：
        0 → 1 把两个项目门排到 qlty 之后（破的是 §58 那半边）。
        0 → -1 只把 depgraph / hygiene 对调——这一对的先后 references 没成文，是循环顺序的产物；
        两个项目门同时在时它是 insert 位置唯一可观测的差异，所以这里照今天的行为钉住。"""
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"scripts/qa/hygiene.py": "", "scripts/qa/depgraph.py": ""})
            ctx = _ctx(repo=tmp, layout={"qlty": True}, tools={"qlty": "/q"})
            plan = _build("arch_audit", ctx)
            names = [s["argv"][1] if s["argv"][0] == ctx["py"] else s["argv"][0] for s in plan["steps"]]
            self.assertEqual(names, ["scripts/qa/depgraph.py", "scripts/qa/hygiene.py", "qlty"])
            self.assertEqual(plan["kind"], "cmd")


class MutationFallbackTestCase(unittest.TestCase):
    def test_mutmut_needs_both_config_and_binary(self):
        """checks._generic_mutation: `_layout().get("mutmut") and _tool(ctx, "mutmut")` — 配置在 AND
        binary 在（and → or 会替只在 pyproject 写了 [tool.mutmut]、却没装 mutmut 的项目拼 `mutmut run`）。
        一个变异工具都没有时的正解 = unavailable，工具表 truth = references/adapters.md mutation 行。"""
        with tempfile.TemporaryDirectory() as tmp:
            plan = _build("mutation_full", _ctx(repo=tmp, layout={"mutmut": True}))
            self.assertEqual(plan["kind"], "unavailable")
            self.assertEqual(plan["steps"], [])

    def test_stryker_plan_runs_npx_in_each_package(self):
        """checks._generic_mutation: `return _steps("cmd", _js_steps(...), "stryker")` 必须真返回 plan
        （return_none 会让装了 stryker 的 JS 项目静默拿到 None）。
        命令形状 truth = references/adapters.md 的 `npx --no-install stryker run`。"""
        with tempfile.TemporaryDirectory() as tmp:
            pkg = {"dir": "web", "stryker": True, "bins": ["stryker"]}
            plan = _build("mutation_full", _ctx(repo=tmp, layout={"js_packages": [pkg]}))
            self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "stryker"))
            self.assertEqual(_argvs(plan), [["npx", "--no-install", "stryker", "run"]])
            self.assertEqual(plan["steps"][0]["cwd"], os.path.join(tmp, "web"))

    def test_full_run_asks_for_a_week_long_budget(self):
        """checks._b_mutation_full: `7 * 24 * 3600` = 整整一周的 `--time-budget`（第 5 档通宵/通几天，
        通过线 truth = references/tiers.md 的 `mutation_full` 行 "a week-long budget"）。三个因子
        任一 ±1 都会改掉那个秒数；这里用 timedelta 独立算一遍，不抄源码字面量。"""
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"scripts/qa/mutate.py": ""})
            argv = _argv(_build("mutation_full", _ctx(repo=tmp)))
            self.assertIn("--all", argv)
            self.assertEqual(int(argv[argv.index("--time-budget") + 1]),
                             int(datetime.timedelta(days=7).total_seconds()))


class FlakyDetectTestCase(unittest.TestCase):
    def test_without_a_python_tests_dir_it_is_na(self):
        """checks._b_flaky_detect: 没有 python tests 目录时的 `return _na(...)` 必须真把那张 na 传出去
        （return_none 会让 run_ladder 拿到 None，而不是「本项目没这一面」）。"""
        plan = _build("flaky_detect", _ctx(files=FLAT_FILES))
        self.assertEqual(plan["kind"], "na")
        self.assertIn("no python tests dir", plan["reason"])

    def test_shuffle_needs_pytest_and_the_randomly_plugin(self):
        """checks._b_flaky_detect: `py_runner == "pytest" and _pymod(ctx, "pytest_randomly")` — 两个都在
        才算真乱序重跑；缺一个只能是 substituted 的 N 次原样重跑（and → or 会替没装插件的项目拼
        `-p randomly`，把替代物谎报成真检查）。两种形态 truth = references/adapters.md flaky 行。"""
        half = _build("flaky_detect", _ctx(layout={"py_runner": "pytest"}))
        self.assertEqual(half["kind"], "substituted")
        self.assertNotIn("randomly", _argv(half))
        other_half = _build("flaky_detect", _ctx(pymods={"pytest_randomly": True}))
        self.assertEqual(other_half["kind"], "substituted")
        self.assertNotIn("randomly", _argv(other_half))
        ready = _ctx(layout={"py_runner": "pytest"}, pymods={"pytest_randomly": True})
        plan = _build("flaky_detect", ready)
        self.assertEqual((plan["kind"], _argv(plan)[-2:]), ("cmd", ["-p", "randomly"]))


class DeadCodeTestCase(unittest.TestCase):
    def test_vulture_scans_configured_roots_else_the_repo_root(self):
        """checks._vulture_plan: `roots = _layout().get("py_src_roots") or ["."]` — 配了源根扫源根，
        没配才退 `.`（or → and 会把配好的项目扫成 `.`、把没配的扫成「无路径」）。
        命令形状 truth = references/adapters.md 的 `vulture --min-confidence 80 <src>`。"""
        rooted = _build("dead_code", _ctx(files=PY_FILES, tools={"vulture": "/v"}))
        self.assertEqual(_argv(rooted), ["vulture", "--min-confidence", "80", "pkg"])
        flat = _build("dead_code", _ctx(files=FLAT_FILES, tools={"vulture": "/v"}))
        self.assertEqual(_argv(flat), ["vulture", "--min-confidence", "80", "."])

    def test_knip_is_the_js_fallback_and_must_be_installed(self):
        """checks._b_dead_code: `"knip" in p.get("bins", [])` 与 `return _steps(...)` —
        只有真把 knip 装进 node_modules/.bin 的包才拼 `npx --no-install knip`（in → not in 会反转成
        「没装才跑」、return_none 会静默返回 None）；一个都没装时的正解 = unavailable。
        lights-up 条件 truth = references/catalog.md 的 `dead_code` 行。"""
        plan = _build("dead_code", _ctx(layout={"js_packages": [{"dir": "web", "bins": ["knip"]}]}))
        self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "knip"))
        self.assertEqual(_argvs(plan), [["npx", "--no-install", "knip"]])
        bare = _build("dead_code", _ctx(layout={"js_packages": [{"dir": "web", "bins": []}]}))
        self.assertEqual(bare["kind"], "unavailable")


class TypeAndDocCoverageTestCase(unittest.TestCase):
    def test_type_coverage_on_a_non_python_project_is_na(self):
        """checks._b_type_coverage: `return _need_python(ctx)` 必须真把那张 na 传出去
        （return_none 会让没有 python 源的项目拿到 None）。"""
        plan = _build("type_coverage", _ctx(files=["web/app.ts"]))
        self.assertEqual(plan["kind"], "na")
        self.assertIn("no python sources", plan["reason"])

    def test_mypy_scans_configured_roots_else_the_repo_root(self):
        """checks._b_type_coverage: `roots = _layout().get("py_src_roots") or ["."]`
        （or → and 会把配好的项目查成 `.`、把没配的查成「无路径」）。
        lights-up 条件与 `--txt-report` truth = references/catalog.md 的 `type_coverage` 行。"""
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"mypy.ini": "[mypy]\n"})
            rooted = _build("type_coverage", _ctx(repo=tmp, files=PY_FILES, tools={"mypy": "/m"}))
            self.assertEqual(_argv(rooted), ["mypy", "--txt-report", "/out/mypy-report", "pkg"])
            flat = _build("type_coverage", _ctx(repo=tmp, files=FLAT_FILES, tools={"mypy": "/m"}))
            self.assertEqual(_argv(flat), ["mypy", "--txt-report", "/out/mypy-report", "."])

    def test_doc_coverage_on_a_non_python_project_is_na(self):
        """checks._b_doc_coverage: `return _need_python(ctx)` 必须真把那张 na 传出去
        （return_none 会让没有 python 源的项目拿到 None）。"""
        plan = _build("doc_coverage", _ctx(files=["web/app.ts"]))
        self.assertEqual(plan["kind"], "na")
        self.assertIn("no python sources", plan["reason"])

    def test_interrogate_scans_configured_roots_else_the_repo_root(self):
        """checks._b_doc_coverage: `roots = _layout().get("py_src_roots") or ["."]`
        （or → and 会把配好的项目查成 `.`、把没配的查成「无路径」）。
        `--fail-under 80` 这条通过线 truth = references/catalog.md 的 `doc_coverage` 行。"""
        rooted = _build("doc_coverage", _ctx(files=PY_FILES, tools={"interrogate": "/i"}))
        self.assertEqual(_argv(rooted), ["interrogate", "-v", "--fail-under", "80", "pkg"])
        flat = _build("doc_coverage", _ctx(files=FLAT_FILES, tools={"interrogate": "/i"}))
        self.assertEqual(_argv(flat), ["interrogate", "-v", "--fail-under", "80", "."])

    def test_pylint_duplicate_code_scans_configured_roots_else_the_repo_root(self):
        """checks._b_duplication: pylint 回落分支的 `roots = _layout().get("py_src_roots") or ["."]`
        （or → and 会把配好的项目查成 `.`、把没配的查成「无路径」）。命令形状 truth =
        references/adapters.md 的 `pylint --disable=all --enable=duplicate-code <src>`。"""
        rooted = _build("duplication", _ctx(files=PY_FILES, tools={"pylint": "/p"}))
        self.assertEqual(_argv(rooted), ["pylint", "--disable=all", "--enable=duplicate-code", "pkg"])
        flat = _build("duplication", _ctx(files=FLAT_FILES, tools={"pylint": "/p"}))
        self.assertEqual(_argv(flat), ["pylint", "--disable=all", "--enable=duplicate-code", "."])


class ApiBreakingTestCase(unittest.TestCase):
    def test_either_buf_spelling_lights_up_the_buf_plan(self):
        """checks._b_api_breaking: `_has("buf.yaml") or _has("buf.yml")` — 两种拼法任一即可
        （or → and 会要求两份配置同时在，单文件的 protobuf 项目就掉进 na）；`return _buf_plan(ctx)`
        也必须真返回（return_none 会静默返回 None）。lights-up 条件 truth =
        references/catalog.md 的 `api_breaking` 行。"""
        for name in ("buf.yaml", "buf.yml"):
            with tempfile.TemporaryDirectory() as tmp, self.subTest(config=name):
                kit.make_repo(tmp, {name: "version: v1\n"})
                plan = _build("api_breaking", _ctx(repo=tmp, tools={"buf": "/b"}))
                self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "buf"))

    def test_buf_absent_is_unavailable_and_the_base_branch_comes_from_the_diff(self):
        """checks._buf_plan: `return _unavailable(...)` 与 `return _cmd(...)` 必须真返回 plan
        （return_none 会静默返回 None）；`base = _diff().get("base") or "main"` 必须是 or
        （or → and 会把配好 base 的项目也比到 main，把没 base 的比到 `.git#branch=None`）。
        命令形状 truth = references/adapters.md 的 `buf breaking --against .git#branch=<base>`。"""
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"buf.yaml": "version: v1\n"})
            self.assertEqual(_build("api_breaking", _ctx(repo=tmp))["kind"], "unavailable")
            plan = _build("api_breaking", _ctx(repo=tmp, tools={"buf": "/b"}))
            self.assertEqual(_argv(plan), ["buf", "breaking", "--against", ".git#branch=origin/main"])
            headless = _ctx(repo=tmp, tools={"buf": "/b"}, diff={"base": None, "changed_files": []})
            self.assertEqual(_argv(_build("api_breaking", headless)),
                             ["buf", "breaking", "--against", ".git#branch=main"])

    def test_api_extractor_is_the_js_path_and_must_be_installed(self):
        """checks._b_api_breaking: `"api-extractor" in p.get("bins", [])` 与 `return _steps(...)` —
        装了才拼 `npx --no-install api-extractor run`（in → not in 会反转、return_none 会静默返回 None）；
        没有任何 API 契约物时的正解 = na。命令形状 truth = references/adapters.md 的 `api_breaking` 行。"""
        with tempfile.TemporaryDirectory() as tmp:
            pkgs = [{"dir": "web", "bins": ["api-extractor"]}]
            plan = _build("api_breaking", _ctx(repo=tmp, layout={"js_packages": pkgs}))
            self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "api-extractor"))
            self.assertEqual(_argvs(plan), [["npx", "--no-install", "api-extractor", "run"]])
            bare = _build("api_breaking", _ctx(repo=tmp, layout={"js_packages": [{"dir": "web", "bins": []}]}))
            self.assertEqual(bare["kind"], "na")


class BundleSizeAndLicenseTestCase(unittest.TestCase):
    def test_npm_size_script_runs_in_the_package_dir(self):
        """checks._b_bundle_size: `"size" in pkg.get("scripts", {})` 与 `return _cmd(...)` —
        声明了 size 脚本才跑它（in → not in 会反转成「没声明才跑」、return_none 会静默返回 None）；
        既没脚本也没 size-limit 时的正解 = na。lights-up 条件 truth = references/catalog.md 的
        `bundle_size` 行。
        注：今天这条分支不查 npm 在不在 PATH（不同于 _npm_audit_steps），所以这里显式把 npm 放进
        tools——钉的是「声明了脚本才跑」，补上 npm 闸门后本判例依旧成立。"""
        with tempfile.TemporaryDirectory() as tmp:
            pkgs = [{"dir": "web", "scripts": {"size": "size-limit"}, "bins": []}]
            npm = {"npm": "/usr/bin/npm"}
            plan = _build("bundle_size", _ctx(repo=tmp, layout={"js_packages": pkgs}, tools=npm))
            self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "npm"))
            self.assertEqual(_argv(plan), ["npm", "run", "size"])
            self.assertEqual(plan["steps"][0]["cwd"], os.path.join(tmp, "web"))
            bare = _build("bundle_size", _ctx(repo=tmp, layout={"js_packages": [{"dir": "web"}]}, tools=npm))
            self.assertEqual(bare["kind"], "na")

    def test_license_checker_plan_is_a_substituted_inventory(self):
        """checks._b_license_check: `"license-checker" in p.get("bins", [])` 与 `return _steps(...)` —
        装了才拼 `npx --no-install license-checker --summary`（in → not in 会反转、return_none 会静默
        返回 None）。kind 必须是 substituted 而不是 cmd：没配 allowlist 就只是清单、永不写 pass，
        这条通过线 truth = references/catalog.md 的 `license_check` 行。"""
        pkgs = [{"dir": "web", "bins": ["license-checker"]}]
        plan = _build("license_check", _ctx(layout={"js_packages": pkgs}))
        self.assertEqual((plan["kind"], plan["tool"]), ("substituted", "license-checker"))
        self.assertEqual(_argvs(plan), [["npx", "--no-install", "license-checker", "--summary"]])
        self.assertIn("inventory", plan["note"])
        bare = _build("license_check", _ctx(layout={"js_packages": [{"dir": "web", "bins": []}]}))
        self.assertEqual(bare["kind"], "unavailable")


class PerfBudgetTestCase(unittest.TestCase):
    def test_pytest_benchmark_needs_both_the_dir_and_the_plugin(self):
        """checks._b_perf_budget: `_layout().get("benchmarks") and _pymod(ctx, "pytest_benchmark")` —
        benchmarks/ 在 AND 插件在才拼命令（and → or 会替没装插件的项目拼 `--benchmark-only`）；
        `return _cmd(...)` 必须真返回（return_none 会静默返回 None）。
        lights-up 条件 truth = references/catalog.md 的 `perf_budget` 行。"""
        self.assertEqual(_build("perf_budget", _ctx(layout={"benchmarks": True}))["kind"], "na")
        ready = _ctx(layout={"benchmarks": True}, pymods={"pytest_benchmark": True})
        plan = _build("perf_budget", ready)
        self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "python-tests"))
        self.assertEqual(_argv(plan)[1:], ["-m", "pytest", "-q", "benchmarks", "--benchmark-only"])

    def test_npm_bench_script_is_the_js_path(self):
        """checks._b_perf_budget: `"bench" in pkg.get("scripts", {})` 与 `return _cmd(...)` —
        声明了 bench 脚本才跑它（in → not in 会反转、return_none 会静默返回 None）；
        没声明时的正解 = na。lights-up 条件 truth = references/catalog.md 的 `perf_budget` 行。
        注：同 _b_bundle_size，这条分支今天不查 npm 在不在 PATH，所以显式把 npm 放进 tools——
        钉的是「声明了脚本才跑」，补上 npm 闸门后本判例依旧成立。"""
        with tempfile.TemporaryDirectory() as tmp:
            pkgs = [{"dir": "web", "scripts": {"bench": "vitest bench"}, "bins": []}]
            npm = {"npm": "/usr/bin/npm"}
            plan = _build("perf_budget", _ctx(repo=tmp, layout={"js_packages": pkgs}, tools=npm))
            self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "npm"))
            self.assertEqual(_argv(plan), ["npm", "run", "bench"])
            self.assertEqual(plan["steps"][0]["cwd"], os.path.join(tmp, "web"))
            bare = _build("perf_budget", _ctx(repo=tmp, layout={"js_packages": [{"dir": "web"}]}, tools=npm))
            self.assertEqual(bare["kind"], "na")


if __name__ == "__main__":
    unittest.main()
