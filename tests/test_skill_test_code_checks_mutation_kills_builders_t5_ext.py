"""test-code skill · checks.py 第 5 档 + 扩展圈 builder 的变异判例（R-217，nightly 2026-09-15：
run=839 killed=435 survived=404，counts truth = .qa/mutation/report.json）。这里钉死
checks.py:1071–1380 那段活下来的变异体：flaky detect 的 tests-dir 闸与 shuffle 插件双条件、security/pip-audit/原生 audit 的「栈在且工具在」、
qlty 与项目自有架构门的**排序**（项目门先说话）、perf budget 的 python/JS 两条路、vulture 与
knip 的接手顺序、mypy / pylint duplicate-code / interrogate 的源根参数、buf 与 api-extractor /
size / license-checker 的 JS fallback、以及 feedback channel 登记上限 12。每条断言都从 builder
的公开产物（plan 的 kind / tool / steps argv / cwd / reason / note）上读，不快照实现。
零子进程、零网络，只在 tempfile.TemporaryDirectory() 里落 fixture 文件。

法典：docs/CONTRACT.md §57（存活变异体 = 补测试提案，逐字转载）/ §58（项目门优先、阈值只读）；
设计 docs/design/vnext2-plan.md R2.8。
"""
import os
import tempfile
import unittest

from tests import skill_test_code_testkit as kit

import checks  # noqa: E402

PY_FILES = ["pkg/__init__.py", "pkg/m.py", "tests/__init__.py", "tests/test_m.py"]


def _ctx(files, repo="/r", layout=None, sel=None, out=None, **det_over):
    """最小 ctx：files → fake_det，layout 的子键逐个覆盖（fake_det 只吃顶层键）。"""
    det = kit.fake_det(sorted(files), **det_over)
    det["layout"].update(layout or {})
    return checks.make_ctx(repo, det, sel=sel, out=out)


def _pkg(**over):
    """一个 JS package 记录；bins/scripts 默认空 = 「声明了但没装/没配」。"""
    pkg = {"dir": "web", "bins": [], "scripts": {}, "lock": None, "tsconfig": False, "eslint": False}
    pkg.update(over)
    return pkg


def _argvs(plan):
    return [step["argv"] for step in plan["steps"]]


class FlakyDetectTestCase(unittest.TestCase):
    """第 4 档 flaky detect 的两道闸：没有 tests/ 必须 na；洗牌要 runner 与插件同时在场。"""

    def test_no_tests_dir_is_an_na_plan(self):
        """checks._b_flaky_detect: `return _na("no python tests dir …")` — 没有 python tests 目录时
        必须给一个带 reason 的 na plan（checks.py:1081 return_none：返回 None，菜单/报告读
        plan["kind"] 当场崩，「为什么没跑」这条理由也就消失了）。"""
        plan = checks._b_flaky_detect(_ctx(["pkg/m.py"]))
        self.assertEqual(plan["kind"], "na")
        self.assertIn("no python tests dir", plan["reason"])
        self.assertEqual(plan["steps"], [])

    def test_shuffle_needs_pytest_runner_and_the_plugin(self):
        """checks._b_flaky_detect: `py_runner == "pytest" and _pymod(ctx, "pytest_randomly")` —
        两者缺一只能是 substituted 的纯重跑，note 里保留「无 shuffle 插件」的诚实降级
        （checks.py:1082 bool_and and→or：unittest 项目只要装了 pytest_randomly、或 pytest 项目
        没装插件，都会被当成能洗牌 → 报告谎称测到了整套顺序依赖）。"""
        with_plugin = checks._b_flaky_detect(_ctx(PY_FILES, pymods={"pytest_randomly": True}))
        self.assertEqual(with_plugin["kind"], "substituted")
        self.assertIn("no shuffle plugin", with_plugin["note"])
        no_plugin = checks._b_flaky_detect(_ctx(PY_FILES, layout={"py_runner": "pytest"}))
        self.assertEqual(no_plugin["kind"], "substituted")
        both = checks._b_flaky_detect(_ctx(PY_FILES, layout={"py_runner": "pytest"},
                                           pymods={"pytest_randomly": True}))
        self.assertEqual((both["kind"], both["steps"][0]["argv"][-2:]), ("cmd", ["-p", "randomly"]))

    def test_reruns_selection_drives_step_count(self):
        """checks._b_flaky_detect: `[step] * reruns` — sel 里的 reruns 决定步骤条数，note 里的
        计数与实际步骤同源（守住 1081/1082 两条断言所依赖的 substituted 形状）。"""
        plan = checks._b_flaky_detect(_ctx(PY_FILES, sel={"reruns": 5}))
        self.assertEqual(len(plan["steps"]), 5)
        self.assertIn("5 plain reruns", plan["note"])


class SecurityAuditStepsTestCase(unittest.TestCase):
    """security_scan / dependency_audit 的步骤拼装：源根、requirements、栈×工具双条件。"""

    def test_bandit_scans_the_declared_src_roots(self):
        """checks._security_steps: `_layout(ctx).get("py_src_roots") or ["."]` — bandit 只递归声明的
        源根（checks.py:1114 bool_or or→and：退化成 ["."]，把 tests/ 和 .venv 一起扫，噪声淹没结论
        且时间预算爆掉）。"""
        plan = checks._b_security_scan(_ctx(PY_FILES, tools={"bandit": "/bin/bandit"}))
        self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "security"))
        self.assertEqual(_argvs(plan), [["bandit", "-q", "-r", "pkg"]])

    def test_pip_audit_needs_both_requirements_and_the_tool(self):
        """checks._pip_audit_steps: `reqs = … or []` / `if not reqs or not _tool(…)` / `reqs[0]` —
        两者缺一就一条步骤都不出，出的时候审第一个 requirements 文件（checks.py:1121 bool_or
        or→and：reqs 被抹成 []，装了 pip-audit 也不审；checks.py:1122 bool_or or→and：没装
        pip-audit 却照样排命令（rc=127 误红）、或 reqs 为空时 reqs[0] IndexError；
        checks.py:1124 int_minus1/int_plus1：审的是 requirements-dev.txt 而不是主 requirements；
        return_none：返回 None，调用方 `steps + None` TypeError）。"""
        reqs = ["requirements.txt", "requirements-dev.txt"]
        both = _ctx(PY_FILES, tools={"pip-audit": "/bin/pip-audit"}, layout={"requirements": reqs})
        self.assertEqual([s["argv"] for s in checks._pip_audit_steps(both)],
                         [["pip-audit", "-r", "requirements.txt"]])
        self.assertEqual(checks._pip_audit_steps(_ctx(PY_FILES, layout={"requirements": reqs})), [])
        self.assertEqual(checks._pip_audit_steps(_ctx(PY_FILES, tools={"pip-audit": "/bin/pip-audit"})), [])
        plan = checks._b_dependency_audit(both)
        self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "audit"))
        self.assertEqual(_argvs(plan), [["pip-audit", "-r", "requirements.txt"]])

    def test_native_audits_need_the_stack_and_the_tool(self):
        """checks._audit_steps: `if stack in _stacks(ctx) and _tool(ctx, tool)` — 栈在且工具在才排
        原生审计（checks.py:1139 cmp_in in→not in：rust 项目反而不跑 cargo audit、纯 python
        项目被塞一条；bool_and and→or：只要装了 cargo-audit 就给 python 项目排 cargo audit，
        必然 rc≠0 假红）。"""
        rust = _ctx(["src/main.rs"], stacks=["rust"], tools={"cargo-audit": "/bin/cargo-audit"})
        self.assertEqual([s["argv"] for s in checks._audit_steps(rust)], [["cargo", "audit"]])
        self.assertEqual(checks._audit_steps(_ctx(["src/main.rs"], stacks=["rust"])), [])
        go = _ctx(["main.go"], stacks=["go"], tools={"govulncheck": "/bin/govulncheck"})
        self.assertEqual([s["argv"] for s in checks._audit_steps(go)], [["govulncheck", "./..."]])
        py_only = _ctx(PY_FILES, tools={"cargo-audit": "/bin/c", "govulncheck": "/bin/g"})
        self.assertEqual(checks._audit_steps(py_only), [])
        self.assertEqual(checks._b_dependency_audit(py_only)["kind"], "unavailable")


class ArchAuditTestCase(unittest.TestCase):
    """arch_audit：qlty 需要配置+二进制；项目自有的门排在第三方之前
    （步骤构成 truth = skills/test-code/references/tiers.md 的 arch_audit 行）。"""

    def test_qlty_step_needs_both_config_and_binary(self):
        """checks._qlty_steps: `_layout(ctx).get("qlty") and _tool(ctx, "qlty")` — .qlty 配置与
        qlty 二进制同时在才排（checks.py:1153 bool_and and→or：只装了二进制、或只有配置就排，
        前者 qlty 无配置直接报错、后者 rc=127；checks.py:1154 return_none：返回 None，
        _b_arch_audit 随后 None.insert(...) AttributeError）。"""
        both = _ctx(PY_FILES, tools={"qlty": "/bin/qlty"}, layout={"qlty": True})
        self.assertEqual([s["argv"] for s in checks._qlty_steps(both)],
                         [["qlty", "check", "--all", "--no-progress"]])
        self.assertEqual(checks._qlty_steps(_ctx(PY_FILES, layout={"qlty": True})), [])
        self.assertEqual(checks._qlty_steps(_ctx(PY_FILES, tools={"qlty": "/bin/qlty"})), [])

    def test_project_gates_run_before_qlty_depgraph_first(self):
        """checks._b_arch_audit: `steps.insert(0, …)` — 项目自己的门按 depgraph → hygiene 排在
        第三方 qlty 之前（checks.py:1162 int_plus1 insert(1)：顺序变成 qlty, depgraph, hygiene，
        第三方先说话；int_minus1 insert(-1)：变成 hygiene, depgraph, qlty，依赖方向门被挤到
        行数门之后）。post 是账本裁决：§58 的项目门按 NEW/WORSE/STALE 行判，不看裸 rc。"""
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"pkg/m.py": "", "scripts/qa/hygiene.py": "", "scripts/qa/depgraph.py": ""})
            ctx = _ctx(PY_FILES, repo=tmp, tools={"qlty": "/bin/qlty"}, layout={"qlty": True})
            plan = checks._b_arch_audit(ctx)
            self.assertEqual([s["argv"][1] for s in plan["steps"]],
                             ["scripts/qa/depgraph.py", "scripts/qa/hygiene.py", "check"])
            self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "arch"))
            self.assertIs(plan["post"], checks._post_ledger_verdict)


class PerfBudgetDeadCodeTestCase(unittest.TestCase):
    """perf_budget 的 python/JS 两条路，dead_code 的 vulture → knip 接手顺序与源根参数。"""

    def test_python_benchmarks_need_the_dir_and_the_plugin(self):
        """checks._b_perf_budget: `benchmarks and _pymod(ctx, "pytest_benchmark")` — benchmarks/
        目录与 pytest-benchmark 同时在才排 --benchmark-only（checks.py:1169 bool_and and→or：
        缺插件也排，pytest 当场 unrecognized arguments 假红；checks.py:1170 return_none：
        返回 None，plan 没有 kind）。"""
        both = _ctx(PY_FILES, layout={"benchmarks": True}, pymods={"pytest_benchmark": True})
        plan = checks._b_perf_budget(both)
        self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "python-tests"))
        self.assertEqual(plan["steps"][0]["argv"][-2:], ["benchmarks", "--benchmark-only"])
        self.assertEqual(checks._b_perf_budget(_ctx(PY_FILES, layout={"benchmarks": True}))["kind"], "na")
        self.assertEqual(checks._b_perf_budget(_ctx(PY_FILES, pymods={"pytest_benchmark": True}))["kind"], "na")

    def test_npm_bench_script_is_the_js_path(self):
        """checks._b_perf_budget: `if "bench" in pkg.get("scripts", {})` — 声明了 npm script bench
        才排，cwd 落在该 package 目录（checks.py:1173 cmp_in in→not in：有 bench 的包被跳过→na、
        没 bench 的包被误排 `npm run bench`；checks.py:1174 return_none：返回 None）。"""
        pkg = _pkg(scripts={"bench": "vitest bench"})
        plan = checks._b_perf_budget(_ctx(["web/package.json"], stacks=["js"],
                                          layout={"js_packages": [pkg]}))
        self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "npm"))
        self.assertEqual(_argvs(plan), [["npm", "run", "bench"]])
        self.assertEqual(plan["steps"][0]["cwd"], os.path.join("/r", "web"))
        bare = checks._b_perf_budget(_ctx(["web/package.json"], stacks=["js"],
                                          layout={"js_packages": [_pkg()]}))
        self.assertEqual(bare["kind"], "na")
        self.assertIn("npm run bench", bare["reason"])

    def test_vulture_roots_then_knip_takes_over(self):
        """checks._vulture_plan / _b_dead_code: vulture 扫声明的源根、装了 knip 的 JS 包接手
        （checks.py:1185 bool_or or→and：vulture 退化成扫 "."，node_modules/.venv 一起进死代码
        报告；checks.py:1193 cmp_in in→not in：装了 knip 反而 unavailable、没装的被误排；
        checks.py:1195 return_none：返回 None）。"""
        plan = checks._b_dead_code(_ctx(PY_FILES, tools={"vulture": "/bin/vulture"}))
        self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "vulture"))
        self.assertEqual(_argvs(plan), [["vulture", "--min-confidence", "80", "pkg"]])
        knip = checks._b_dead_code(_ctx(["web/package.json"], stacks=["js"],
                                        layout={"js_packages": [_pkg(bins=["knip"])]}))
        self.assertEqual((knip["kind"], knip["tool"]), ("cmd", "knip"))
        self.assertEqual(_argvs(knip), [["npx", "--no-install", "knip"]])
        bare = checks._b_dead_code(_ctx(["web/package.json"], stacks=["js"],
                                        layout={"js_packages": [_pkg()]}))
        self.assertEqual(bare["kind"], "unavailable")


class PythonRootExtendedTestCase(unittest.TestCase):
    """扩展圈里三个吃 py_src_roots 的 builder：mypy / pylint duplicate-code / interrogate。"""

    def test_type_coverage_gates_on_python_then_scans_roots(self):
        """checks._b_type_coverage: `return _need_python(ctx)` 与 `roots = … or ["."]` — 无 python
        栈必须是带 reason 的 na plan，有栈时 mypy argv 以声明的源根收尾（checks.py:1284
        return_none：返回 None，菜单读 kind 崩；checks.py:1289 bool_or or→and：mypy 改扫 "."，
        把 build/.venv 的第三方代码也判进类型覆盖）。"""
        js = checks._b_type_coverage(_ctx(["web/package.json"], stacks=["js"]))
        self.assertEqual(js["kind"], "na")
        self.assertIn("no python sources", js["reason"])
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"pkg/m.py": "", "mypy.ini": "[mypy]\nstrict = True\n"})
            plan = checks._b_type_coverage(_ctx(PY_FILES, repo=tmp, tools={"mypy": "/bin/mypy"}))
            self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "mypy"))
            self.assertEqual(plan["steps"][0]["argv"][-1:], ["pkg"])

    def test_duplication_pylint_fallback_scans_roots(self):
        """checks._b_duplication: pylint duplicate-code 的 roots（checks.py:1298 bool_or or→and：
        改扫 "."，tests/ 里合理的重复也被算进 ≤3% 预算，结论不可用）。"""
        plan = checks._b_duplication(_ctx(PY_FILES, tools={"pylint": "/bin/pylint"}))
        self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "pylint"))
        self.assertEqual(_argvs(plan), [["pylint", "--disable=all", "--enable=duplicate-code", "pkg"]])

    def test_doc_coverage_gates_on_python_then_scans_roots(self):
        """checks._b_doc_coverage: `return _need_python(ctx)` 与 roots（checks.py:1342 return_none：
        无 python 栈返回 None；checks.py:1345 bool_or or→and：interrogate 改扫 "."，docstring
        覆盖率被第三方文件稀释，--fail-under 80 失去意义）。"""
        js = checks._b_doc_coverage(_ctx(["web/package.json"], stacks=["js"]))
        self.assertEqual(js["kind"], "na")
        self.assertIn("no python sources", js["reason"])
        plan = checks._b_doc_coverage(_ctx(PY_FILES, tools={"interrogate": "/bin/interrogate"}))
        self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "interrogate"))
        self.assertEqual(_argvs(plan), [["interrogate", "-v", "--fail-under", "80", "pkg"]])


class ApiBundleLicenseTestCase(unittest.TestCase):
    """扩展圈的 API / bundle / license：buf 两种拼法、diff base、JS bin fallback。"""

    def test_buf_plan_needs_the_tool_and_uses_the_diff_base(self):
        """checks._buf_plan / _b_api_breaking: buf.yaml 在但 buf 不在 PATH → unavailable；在则
        --against 用 detect 记下的 diff base（checks.py:1305/1307/1312 return_none：返回 None，
        「装了没装」这条理由消失、plan 无 kind；checks.py:1306 bool_or or→and：base 被写死成
        "main"，从 origin/main 切出来的分支比对了本地陈旧的 main）。"""
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"buf.yaml": "version: v1\n", "proto/a.proto": ""})
            files = ["buf.yaml", "proto/a.proto"]
            blocked = checks._b_api_breaking(_ctx(files, repo=tmp))
            self.assertEqual(blocked["kind"], "unavailable")
            self.assertIn("buf not on PATH", blocked["reason"])
            plan = checks._b_api_breaking(_ctx(files, repo=tmp, tools={"buf": "/bin/buf"}))
            self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "buf"))
            self.assertEqual(_argvs(plan), [["buf", "breaking", "--against", ".git#branch=origin/main"]])

    def test_buf_yml_spelling_also_counts(self):
        """checks._b_api_breaking: `_has(ctx, "buf.yaml") or _has(ctx, "buf.yml")` — 两种拼法任一
        存在就走 buf（checks.py:1311 bool_or or→and：只有 buf.yml 的仓库被判 na，proto 的
        breaking change 从此无人看）。"""
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"buf.yml": "version: v1\n"})
            plan = checks._b_api_breaking(_ctx(["buf.yml"], repo=tmp, tools={"buf": "/bin/buf"}))
            self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "buf"))
            self.assertEqual(plan["steps"][0]["argv"][:2], ["buf", "breaking"])

    def test_api_extractor_is_the_js_fallback(self):
        """checks._b_api_breaking: `"api-extractor" in p.get("bins", [])`（checks.py:1313 cmp_in
        in→not in：装了 api-extractor 的包被跳过→na、没装的包被误排 npx；checks.py:1315
        return_none：返回 None）。repo 用 tempdir：这个 builder 会 os.path.exists(repo/buf.yaml)，
        不拿真实文件系统当 fixture。"""
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"web/package.json": "{}\n"})
            plan = checks._b_api_breaking(_ctx(["web/package.json"], repo=tmp, stacks=["js"],
                                               layout={"js_packages": [_pkg(bins=["api-extractor"])]}))
            self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "api-extractor"))
            self.assertEqual(_argvs(plan), [["npx", "--no-install", "api-extractor", "run"]])
            bare = checks._b_api_breaking(_ctx(["web/package.json"], repo=tmp, stacks=["js"],
                                               layout={"js_packages": [_pkg()]}))
            self.assertEqual(bare["kind"], "na")

    def test_bundle_size_prefers_the_npm_script(self):
        """checks._b_bundle_size: `if "size" in pkg.get("scripts", {})` — 声明了 size script 就用它，
        cwd 落在 package 目录（checks.py:1322 cmp_in in→not in：有 size script 的包被跳过、没有的
        被误排；checks.py:1323 return_none：返回 None）。"""
        plan = checks._b_bundle_size(_ctx(["web/package.json"], stacks=["js"],
                                          layout={"js_packages": [_pkg(scripts={"size": "size-limit"})]}))
        self.assertEqual((plan["kind"], plan["tool"]), ("cmd", "npm"))
        self.assertEqual(_argvs(plan), [["npm", "run", "size"]])
        self.assertEqual(plan["steps"][0]["cwd"], os.path.join("/r", "web"))
        bare = checks._b_bundle_size(_ctx(["web/package.json"], stacks=["js"],
                                          layout={"js_packages": [_pkg()]}))
        self.assertEqual(bare["kind"], "na")

    def test_license_checker_is_inventory_only(self):
        """checks._b_license_check: `"license-checker" in p.get("bins", [])` — 装了就排，但 kind
        只能是 substituted（没 allowlist 就不是 gate），note 要写怎么变成 gate（checks.py:1333
        cmp_in in→not in：装了反而 unavailable；checks.py:1335 return_none：返回 None）。"""
        plan = checks._b_license_check(_ctx(["web/package.json"], stacks=["js"],
                                            layout={"js_packages": [_pkg(bins=["license-checker"])]}))
        self.assertEqual((plan["kind"], plan["tool"]), ("substituted", "license-checker"))
        self.assertEqual(_argvs(plan), [["npx", "--no-install", "license-checker", "--summary"]])
        self.assertIn("--failOn", plan["note"])


class FeedbackChannelCapTestCase(unittest.TestCase):
    """信息项 feedback channel：登记上限 12，summary 的计数与账本同源。"""

    def test_found_list_caps_at_twelve(self):
        """checks.check_feedback_channel: `sorted({…})[:12]` — 命中文件只登记前 12 条（排序稳定），
        summary 的「N file(s)」直接数 found（checks.py:1362 int_minus1 → 只留 11 条、int_plus1 →
        留 13 条：报告里的计数与 details 账本对不上，读者无法复核这个盲区判定）。"""
        files = ["docs/feedback_%02d.md" % i for i in range(13)]
        res = checks.check_feedback_channel(_ctx(files))
        self.assertEqual(res["status"], "pass")
        self.assertEqual(len(res["details"]["found"]), 12)
        self.assertEqual(res["details"]["found"][-1], "docs/feedback_11.md")
        self.assertIn("(12 file(s))", res["summary"])
        self.assertNotIn("blind_spots", res["details"])

    def test_no_match_reports_a_blind_spot_and_still_passes(self):
        """checks.check_feedback_channel: 一条都没命中 → found 为空 + blind_spots 有话说，status
        仍是 pass（信息项永不判红）。这是 1362 上限断言的负面另一半：空 found 也得走 pass，
        否则「12 条」那个断言可以靠「任何命中都判红」的实现蒙过去。"""
        res = checks.check_feedback_channel(_ctx(PY_FILES))
        self.assertEqual((res["status"], res["details"]["found"]), ("pass", []))
        self.assertTrue(res["details"]["blind_spots"])


if __name__ == "__main__":
    unittest.main()
