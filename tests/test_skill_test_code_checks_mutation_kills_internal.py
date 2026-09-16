"""test-code skill · checks.py 内建检查区（secret scan / Actions SHA pin / test smells /
docs drift / diff coverage / CRAP fallback，checks.py:400–745）的变异判例。

来源：卡片 R-217（self-improve lane，docs/CONTRACT.md §57「存活变异体 = 补测试提案」）。
nightly 2026-09-15 对 skills/test-code/scripts/checks.py 跑了 run=839 / killed=435 /
survived=404；本文件逐个钉死落在上述区间的存活体——hash 截断宽度、`rsplit` 的 maxsplit、
workflow 文件过滤的 and/or、AST helper 的 "" 回落、非 test 函数 `continue` 而非 `break`、
.gitignore 字面量解析、diff 覆盖率摘要的加减号、CRAP 公式的指数与 round 位数、span 的
`end + 1`、以及「恰等于阈值不算违例」的 `>`。每个 docstring 写明它杀哪一行的哪个 operator。
零子进程。

判例风格与姐妹文件 tests/test_skill_test_code_mutation_kills.py 一致；断言尽量走公共入口
（check_* 的 status / summary / details），只有当 helper 是唯一可观测面时才直呼私名。
"""
import ast
import os
import re
import tempfile
import unittest

from tests import skill_test_code_testkit as kit

import checks  # noqa: E402
import complexity_min as cm  # noqa: E402

lc = kit.lc

# 运行时拼出来：本文件不留 key 形状的字面量（否则 secret_scan 扫自己就红）
FAKE_AWS = "AKIA" + "QRSTUVWXYZ123456"
SHA40 = "0123456789abcdef0123456789abcdef01234567"


def _ctx(repo, files, out=None, sel=None, init=False, **det_over):
    det = kit.fake_det(files, **det_over)
    return checks.make_ctx(repo, det, sel=sel, out=out, init_baselines=init)


def _diff(changed=(), added=None):
    return {"base": "origin/main", "base_commit": "abc", "changed_files": list(changed),
            "added": added or {}, "added_text": {}, "removed": {}, "untracked": []}


def _wf(uses):
    return "on: push\njobs:\n  b:\n    steps:\n      - uses: %s\n" % uses


class SecretHashShapeTestCase(unittest.TestCase):
    """账本 key 的形状是跨 run 的契约：指纹宽度一变，历史 baseline 全部对不上号。"""

    HEX10 = re.compile(r"^[0-9a-f]{10}$")

    def test_ledger_key_carries_exactly_ten_hex_chars(self):
        """checks._line_hash: `hexdigest()[:10]` — 账本 key = rel::rule::10 位十六进制指纹
        （checks.py:413 int_minus1/int_plus1：9 或 11 位会让既有 baseline 整批变 NEW；
        return_none：key 尾段变成字面 "None"，同一文件多条命中互相覆盖）。"""
        hits = checks._secret_hits("src/a.py", 'KEY = "%s"\n' % FAKE_AWS)
        self.assertEqual(len(hits), 1)
        rel, rule, digest = list(hits)[0].split("::")
        self.assertEqual((rel, rule), ("src/a.py", "aws-access-key"))
        self.assertRegex(digest, self.HEX10)
        self.assertEqual(len(digest), 10)

    def test_hash_ignores_surrounding_whitespace_but_not_content(self):
        """checks._line_hash: `line.strip()` — 缩进变化不移账，内容变化必换 key
        （同时复核 checks.py:413 的宽度：两条不同行的指纹都得是 10 位十六进制）。"""
        a = checks._line_hash('  KEY = "%s"  ' % FAKE_AWS)
        b = checks._line_hash('KEY = "%s"' % FAKE_AWS)
        c = checks._line_hash('OTHER = "%s"' % FAKE_AWS)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        for digest in (a, c):
            self.assertRegex(digest, self.HEX10)

    def test_full_check_details_key_shape(self):
        """checks.check_secret_scan: details["new"] 的 key 逐字是 rel::rule::<10 hex>
        ——公共入口上再钉一遍 checks.py:413 的三个变异体。"""
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"src/a.py": 'KEY = "%s"\n' % FAKE_AWS})
            res = checks.check_secret_scan(_ctx(tmp, ["src/a.py"]))
            self.assertEqual(res["status"], "fail")
            self.assertEqual(len(res["details"]["new"]), 1)
            self.assertRegex(res["details"]["new"][0], r"^src/a\.py::aws-access-key::[0-9a-f]{10}$")


class ActionsPinRefTestCase(unittest.TestCase):
    """`uses:` 的 ref = 最后一个 @ 之后的部分；本地/docker 引用豁免 SHA pin。"""

    WF = ".github/workflows/ci.yml"

    def test_local_and_docker_uses_are_exempt_and_the_predicate_returns_a_bool(self):
        """checks._unpinned: `return False` — `./local` 与 `docker://` 引用豁免 SHA pin
        （skills/test-code/references/adapters.md:45 文档化的是这条**行为**，不是返回类型；
        行为面已由 tests/test_skill_test_code_checks.py 的 test_sha_local_and_docker_pass 钉住）。
        豁免分支的返回值只在 `match and _unpinned(...)` 的真假位置被消费，所以
        checks.py:438 return_none 经任何公共入口都**不可观测**——与 checks.py:572
        `dangling` 闭包那条 return_none 同形（那条判 EQUIVALENT）。这里刻意在私名上多钉一道
        「谓词只返真布尔」的**选定不变量**：_unpinned 与 _path_token 一类判定 helper 的返回值
        将来若进 details/账本，None 与 False 的差别立刻变成可观测的，先钉住便宜。"""
        self.assertIs(checks._unpinned("./.github/actions/local"), False)
        self.assertIs(checks._unpinned("docker://alpine:3"), False)
        self.assertIs(checks._unpinned("actions/checkout@v4"), True)

    def test_ref_is_whatever_follows_the_last_at(self):
        """checks._unpinned: `uses.rsplit("@", 1)[1]` — ref 取**最后**一个 @ 之后的整段，
        所以 `org/act@ion@<40hex>` 算已钉（checks.py:439 int_plus1：maxsplit 2 会把 [1]
        取成中段 "ion"，把这条已钉的 uses 误判成未钉）。"""
        self.assertIs(checks._unpinned("org/act@ion@" + SHA40), False)
        self.assertIs(checks._unpinned("org/act@ion@v4"), True)
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {self.WF: _wf("org/act@ion@" + SHA40)})
            res = checks.check_actions_sha_pin(_ctx(tmp, [self.WF]))
            self.assertEqual(res["status"], "pass")
            self.assertEqual(res["details"]["total"], 0)

    def test_workflow_filter_needs_both_dir_and_extension(self):
        """checks.check_actions_sha_pin: `f.startswith(".github/workflows/") and f.endswith(...)`
        —— 根目录的 ci.yml 与 workflows 下的 notes.md 都不是 workflow，整检查是 na
        （checks.py:454 bool_and：or 会把这两个文件都拉进来扫，把 na 变成 fail）。"""
        with tempfile.TemporaryDirectory() as tmp:
            files = {"ci.yml": _wf("actions/checkout@v4"),
                     ".github/workflows/notes.md": "see `actions/checkout@v4`\n"}
            kit.make_repo(tmp, files)
            res = checks.check_actions_sha_pin(_ctx(tmp, sorted(files)))
            self.assertEqual(res["status"], "na")
            self.assertIn("no GitHub Actions workflows", res["summary"])


SUBSCRIPT_CALL = """import unittest


class T(unittest.TestCase):
    def test_subscript_callable(self):
        handlers = [lambda: None]
        handlers[0]()
"""

IO_IMPORT = """from urllib import request


def test_uses_request():
    assert request is not None
"""

HELPER_FIRST = """def _helper():
    pass


def test_no_assert():
    x = 1
    return x
"""


class SmellAstTestCase(unittest.TestCase):
    """AST 遍历的三个回落：非 Attribute/Name 的调用、ImportFrom 的模块名、非 test 函数跳过。"""

    def test_non_name_call_yields_empty_name(self):
        """checks._call_name: `return ""` — func 既不是 Attribute 也不是 Name（`handlers[0]()`）
        时返回空串，_node_flags 因此是 (False, False)（checks.py:473 return_none：
        _ASSERTISH.search(None) 直接 TypeError，test_smells 炸掉而不是 fail closed）。"""
        node = ast.parse("handlers[0]()\n").body[0].value
        self.assertEqual(checks._call_name(node), "")
        self.assertEqual(checks._node_flags(node), (False, False))

    def test_subscript_call_is_a_no_assert_smell_not_a_crash(self):
        """checks._smells_in_test: 下标调用不算断言形，所以那条 test 记 no-assert
        ——公共入口上复核 checks.py:473 return_none（TypeError 不在 fail-closed 白名单里）。"""
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"tests/test_a.py": SUBSCRIPT_CALL})
            res = checks.check_test_smells(_ctx(tmp, ["tests/test_a.py"]))
            self.assertEqual(res["status"], "fail")
            self.assertEqual(res["details"]["new"], ["no-assert:tests/test_a.py::T.test_subscript_callable"])

    def test_import_from_reports_the_module_not_the_alias(self):
        """checks._import_names: `return [node.module]` — 记的是**被导入的模块名本身**
        （`node.module`，含点号；alias 名不记），所以 `from urllib.request import urlopen`
        记 "urllib.request"——_IO_RE 尾部那个「点号或行尾」的分组正是为这种带点形式写的
        （checks.py:497 return_none：_io_imports 的推导式 `for n in None` → TypeError，
        unit 层的 real-io 体检整块失效）。"""
        self.assertEqual(checks._import_names(ast.parse("from os import path").body[0]), ["os"])
        self.assertEqual(
            checks._import_names(ast.parse("from urllib.request import urlopen").body[0]),
            ["urllib.request"])
        self.assertEqual(checks._io_imports(ast.parse("from urllib.request import urlopen\n")),
                         ["urllib.request"])
        self.assertEqual(checks._io_imports(ast.parse(IO_IMPORT)), ["urllib"])
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"tests/test_io.py": IO_IMPORT})
            res = checks.check_test_smells(_ctx(tmp, ["tests/test_io.py"]))
            self.assertEqual(res["status"], "fail")
            self.assertEqual(res["details"]["new"], ["real-io:tests/test_io.py::urllib"])

    def test_non_test_function_is_skipped_not_terminal(self):
        """checks._smell_violations: 非 test 函数 `continue` —— 排在 helper **后面**的
        test_no_assert 仍要被抓到（checks.py:511 loop_flow：break 会在第一个 helper 处
        停掉整个文件的扫描，任何「helper 写在测试上面」的文件从此零体检）。"""
        self.assertEqual([qual for qual, _ in cm.collect_functions(ast.parse(HELPER_FIRST))],
                         ["_helper", "test_no_assert"])
        self.assertEqual(checks._smell_violations("tests/test_a.py", HELPER_FIRST, False),
                         {"no-assert:tests/test_a.py::test_no_assert": 1.0})
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"tests/test_a.py": HELPER_FIRST})
            res = checks.check_test_smells(_ctx(tmp, ["tests/test_a.py"]))
            self.assertEqual(res["status"], "fail")
            self.assertEqual(res["details"]["new"], ["no-assert:tests/test_a.py::test_no_assert"])


class GitignoreLiteralsTestCase(unittest.TestCase):
    """.gitignore 的字面声明集：注释要剥、通配与空行要滤——它决定文档里的生成物算不算悬空。"""

    def test_text_before_a_hash_is_kept_as_the_literal(self):
        """checks._ignored_literals: `raw.split("#", 1)[0]` — 只取 `#` 之前的那段。注意本
        helper 的解析比真实 ignore 文件语法**宽松**：真语法里只有行首 `#` 才是注释，
        `act/_version.py # generated at install` 在工具眼里是一条字面 pattern；宽松是有意的
        ——这个集合只用来压 docs_drift 的误报，多认几个声明只会少报悬空、不会误报
        （checks.py:556 int_minus1 col30：maxsplit 0 留下整行；col33 `[0]`→`[-1]`
        只留下 `#` 之后那段——两种都让声明过的生成物变回悬空路径）。"""
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {".gitignore": "act/_version.py # generated at install\n"})
            self.assertEqual(checks._ignored_literals(tmp), {"act/_version.py"})

    def test_declared_generated_file_is_not_dangling_in_docs(self):
        """checks.check_docs_drift: 被声明行（`#` 之前那段）认下的生成物不算悬空——公共入口上
        复核 checks.py:556（任一变异 → docs/a.md::act/_version.py 变 NEW，整检查转 fail）。"""
        with tempfile.TemporaryDirectory() as tmp:
            files = {"act/x.py": "", "docs/a.md": "generated: `act/_version.py`, real: `act/x.py`\n",
                     ".gitignore": "act/_version.py # generated at install\n"}
            kit.make_repo(tmp, files)
            res = checks.check_docs_drift(_ctx(tmp, sorted(files)))
            self.assertEqual(res["status"], "pass")
            self.assertEqual(res["details"]["total"], 0)

    def test_blank_and_globbed_lines_are_not_literals(self):
        """checks._ignored_literals: `line and not any(ch in line ...)` — 空行与含通配符的行
        都不进字面集（checks.py:557 bool_and：or 会把 "" 和 "*.pyc" 也收进去，
        「已声明生成物」集合从此含噪）。"""
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {".gitignore": "*.pyc\n\nact/_version.py\n"})
            self.assertEqual(checks._ignored_literals(tmp), {"act/_version.py"})
            kit.make_repo(tmp, {".gitignore": "# only a comment\n   \nbuild/\n"})
            self.assertEqual(checks._ignored_literals(tmp), {"build"})


class DiffCoverageArithmeticTestCase(unittest.TestCase):
    """新增语句的 covered = measured - uncovered：摘要里的分子是人唯一会读的数字。"""

    def _ctx(self, tmp, added, cov):
        out = os.path.join(tmp, "out")
        lc.write_json(os.path.join(out, "coverage.json"), {"files": cov})
        return _ctx(tmp, sorted(added), out=out, diff=_diff(sorted(added), added=added))

    def test_summary_numerator_subtracts_the_uncovered(self):
        """checks.check_diff_coverage: `covered = measured - len(uncovered)` — 两条新增语句
        里一条未覆盖 → 摘要 "1/2 added statements covered"（checks.py:619 arith_sub：
        加号打印 3/2，分子比分母还大，报告里的结论彻底反了）。"""
        with tempfile.TemporaryDirectory() as tmp:
            cov = {"pkg/m.py": {"executed_lines": [1], "missing_lines": [2]}}
            res = checks.check_diff_coverage(self._ctx(tmp, {"pkg/m.py": [1, 2]}, cov))
            self.assertEqual(res["status"], "fail")
            self.assertTrue(res["summary"].startswith("1/2 added statements covered"))
            self.assertEqual((res["details"]["uncovered"], res["details"]["measured"]), (["pkg/m.py:2"], 2))

    def test_fully_uncovered_added_lines_report_zero(self):
        """checks.check_diff_coverage: 全未覆盖 → "0/2"（checks.py:619 arith_sub 的第二个
        观测点：加号会打成 4/2）。"""
        with tempfile.TemporaryDirectory() as tmp:
            cov = {"pkg/m.py": {"executed_lines": [], "missing_lines": [1, 2]}}
            res = checks.check_diff_coverage(self._ctx(tmp, {"pkg/m.py": [1, 2]}, cov))
            self.assertTrue(res["summary"].startswith("0/2 added statements covered"))


CC3 = "def f(a, b):\n    if a:\n        return 1\n    if b:\n        return 2\n    return 3\n"


class CrapFormulaTestCase(unittest.TestCase):
    """CRAP = cc² · (1 − cov)³ + cc，round 到一位小数；账本上的数值是 shrink-only 的比较对象。"""

    def test_crap_formula_exponent_and_rounding(self):
        """checks.crap_score: `round(cc * cc * (1.0 - cov) ** 3 + cc, 1)` — cc=3 / cov=0.5
        恰好 4.1（checks.py:705 指数 3→2 给 5.2、3→4 给 3.6；round 位数 1→0 给 4.0、
        1→2 给 4.12——四种都会让整个 CRAP 账本的数值重排）。"""
        self.assertEqual(checks.crap_score(3, 0.5), 4.1)
        self.assertEqual(checks.crap_score(4, 0.5), 6.0)
        self.assertEqual(checks.crap_score(3, 0.0), 12.0)
        self.assertEqual(checks.crap_score(3, 1.0), 3.0)

    def test_span_is_inclusive_of_the_end_line(self):
        """checks._span_cov: `set(range(start, end + 1))` — 1..3 含第 3 行，已知行 {1,2,3}
        里覆盖 {1,2} → 2/3（checks.py:709 arith_add `-` 只剩第 1 行给 1.0；int_minus1
        丢掉第 3 行给 1.0；int_plus1 多吞第 4 行给 3/4）。"""
        self.assertEqual(checks._span_cov(1, 3, {1, 2, 4}, {3}), 2.0 / 3.0)
        self.assertEqual(checks._span_cov(1, 3, {1, 2}, {3}), 2.0 / 3.0)
        self.assertEqual(checks._span_cov(5, 5, set(), {5}), 0.0)

    def test_span_without_known_lines_is_fully_covered_by_convention(self):
        """checks._span_cov: `return 1.0` — 整段没有任何已知行（纯签名/docstring 函数）按
        「全覆盖」记，CRAP 退化成 cc（checks.py:712 return_none：crap_score 拿到 None，
        `1.0 - None` 直接 TypeError，check_crap_fallback 整块炸掉）。"""
        self.assertEqual(checks._span_cov(1, 3, set(), set()), 1.0)
        self.assertEqual(checks.crap_score(2, checks._span_cov(1, 3, set(), set())), 2.0)


class CrapThresholdTestCase(unittest.TestCase):
    """阈值边界：恰等于 crap_max 不算违例——阈值 truth 在 qa/gates.toml，检查只读。"""

    def _run(self, tmp, files, cov, crap_max):
        out = os.path.join(tmp, "out")
        lc.write_json(os.path.join(out, "coverage.json"), {"files": cov})
        kit.make_repo(tmp, files)
        ctx = _ctx(tmp, sorted(files), out=out)
        ctx["det"]["thresholds"]["crap_max"] = crap_max
        return checks.check_crap_fallback(ctx)

    def test_score_exactly_at_threshold_is_not_a_violation(self):
        """checks.check_crap_fallback: `if v > threshold` — cc=3 全未覆盖 = CRAP 12.0，
        crap_max 12.0 时恰在线上，pass（checks.py:739 cmp_gt `>`→`>=`：把「恰好合规」
        判成 NEW 违例，任何把阈值调到实测值的项目都会立刻红）。"""
        cov = {"pkg/m.py": {"executed_lines": [], "missing_lines": [1, 2, 3, 4, 5, 6]}}
        with tempfile.TemporaryDirectory() as tmp:
            res = self._run(tmp, {"pkg/m.py": CC3}, cov, 12.0)
            self.assertEqual(res["status"], "pass")
            self.assertEqual(res["details"]["total"], 0)
            self.assertIn("no function above CRAP 12.0", res["summary"])
        with tempfile.TemporaryDirectory() as tmp:
            res = self._run(tmp, {"pkg/m.py": CC3}, cov, 11.9)
            self.assertEqual(res["status"], "fail")
            self.assertEqual(res["details"]["new"], ["pkg/m.py::f"])


if __name__ == "__main__":
    unittest.main()
