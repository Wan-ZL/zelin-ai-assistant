"""test-code skill · R-301 补网：internal 检查的**算术与扫描器**变异体（checks.py bucket C）。
2026-09-16 夜跑 scripts/qa/mutate.py 在 skills/test-code/scripts/checks.py 上留下 404 个存活体，
这一档钉死其中属于「自制检查自己算出来的数」的那批 —— CRAP 公式、span 覆盖率、diff 覆盖计数、
账本 key 宽度、变异体汇总的缺省值、.gitignore 注释剥离、action ref 切分、测试文件识别、
smell 扫描器的循环控制。这些函数就是产品本身（internal check = skill 自己算 pass/fail），
所以每条断言都指向一个可观测后果：报告里的数字、账本里的 key、或者 pass/fail 本身。
每个测试的 docstring 写明它杀的是哪一行的哪个变异（fn: 表达式 — 变异 → 后果）。零子进程。

法典指针：docs/CONTRACT.md §57（存活变异体 = 补测试提案；timeout 记 killed 侧）、
§58（internal 检查一律 fail closed、阈值 truth = qa/gates.toml 只读）。
CRAP 公式 truth = skills/test-code/references/tiers.md 第 2 档 `crap` 行。
设计 = docs/design/vnext2-plan.md R2.8。
"""
import ast
import os
import re
import sys
import tempfile
import unittest

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "skills", "test-code", "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import checks  # noqa: E402
from tests import skill_test_code_testkit as kit  # noqa: E402

lc = kit.lc

# 运行时拼出来：本文件永远不含 key 形状的字面量（否则 secret_scan 扫自己就红）
FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
# sha1("abc") 的官方测试向量（RFC 3174 §7.1 / FIPS 180-1 附录 A）——与 checks.py 无关的独立真源
SHA1_ABC = "a9993e364706816aba3e25717850c26c9cd0d89d"
# 40 位十六进制 = 合法的 action SHA pin
SHA40 = "a" * 40
# 环复杂度 3 的样本函数（1 + 两个 if），占 1–6 行
CC3 = "def f(a, b):\n    if a:\n        return 1\n    if b:\n        return 2\n    return 3\n"


def _ctx(repo, files, out=None, sel=None, init=False, **det_over):
    det = kit.fake_det(files, **det_over)
    return checks.make_ctx(repo, det, sel=sel, out=out, init_baselines=init)


def _diff_det(added):
    return {"base": "origin/main", "base_commit": "abc", "changed_files": sorted(added),
            "added": added, "added_text": {}, "removed": {}, "untracked": []}


class CrapFormulaTestCase(unittest.TestCase):
    """crap_score = CC² × (1−cov)³ + CC，保留 1 位小数。
    公式 truth = skills/test-code/references/tiers.md 第 2 档 `crap` 行。"""

    def test_exponent_is_three_not_two_or_four(self):
        """checks.crap_score: `(1.0 - cov) ** 3` — 指数 3（3 → 2 会高估、3 → 4 会低估风险）。
        只有 0 < cov < 1 的输入能分开三个指数：cov=0 时 (1−0)^n 恒为 1，cov=1 时恒为 0，
        既有判例的 crap_score(3, 0.0)=12 / crap_score(6, 1.0)=6 两个样本对指数是盲的。
        手算（tiers.md 公式）：CC=4、cov=0.5 → 16 × 0.5³ + 4 = 16 × 0.125 + 4 = 6.0；
        指数 2 会给 16 × 0.25 + 4 = 8.0，指数 4 会给 16 × 0.0625 + 4 = 5.0。"""
        self.assertEqual(checks.crap_score(4, 0.5), 6.0)
        # CC=5、cov=0.6 → 25 × 0.4³ + 5 = 25 × 0.064 + 5 = 6.6（指数 2 → 9.0，指数 4 → 5.6）
        self.assertEqual(checks.crap_score(5, 0.6), 6.6)

    def test_rounds_to_one_decimal(self):
        """checks.crap_score: `round(..., 1)` — 报告与账本里的 CRAP 恒为 1 位小数（1 → 0 会
        把分数抹成整数、1 → 2 会让账本 key 的值多一位、shrink-only 比较随之漂移）。
        手算：CC=3、cov=0.7 → 9 × 0.3³ + 3 = 9 × 0.027 + 3 = 3.243 → 3.2；
        0 位小数会给 3.0，2 位小数会给 3.24。"""
        self.assertEqual(checks.crap_score(3, 0.7), 3.2)
        # CC=4、cov=0.75 → 16 × 0.25³ + 4 = 16 × 0.015625 + 4 = 4.25 → 4.2（0 位 → 4.0，2 位 → 4.25）
        self.assertEqual(checks.crap_score(4, 0.75), 4.2)
        # 写进账本的值要能被 format_value 还原成一位小数，不能是 4.25 这种两位数
        self.assertEqual(lc.format_value(checks.crap_score(4, 0.75)), "4.2")


class SpanCoverageTestCase(unittest.TestCase):
    """_span_cov(start, end, executed, missing)：函数体 [start, end] **闭区间**内
    「执行过的行 / 有覆盖数据的行」之比。"""

    def test_span_is_inclusive_on_both_ends(self):
        """checks._span_cov: `range(start, end + 1)` — 闭区间（+ → - 会丢掉末两行、
        1 → 0 会丢掉末行、1 → 2 会把函数后面那一行也算进来）。
        构造：函数占 1–3 行，第 1、2 行执行过，第 3 行漏测，**第 4 行（函数外）也执行过**。
        真实 = 3 行 span 里 2 行执行 → 2/3；`end+0` 只看 1–2 行 → 1.0；`end-1` 只看第 1 行 → 1.0；
        `end+2` 把第 4 行算进来 → 3/4。四个值两两不同，所以第 4 行必须有覆盖数据。"""
        cov = checks._span_cov(1, 3, {1, 2, 4}, {3})
        self.assertAlmostEqual(cov, 2 / 3, places=12)
        # 末行是 missing 时也必须被算进分母：漏掉它会把 2/3 谎报成 1.0
        self.assertNotEqual(cov, 1.0)

    def test_empty_span_counts_as_fully_covered(self):
        """checks._span_cov: `return 1.0` — span 内一行覆盖数据都没有时返回 1.0（不是 None）。
        这是 CRAP 的 fail-open 决定：没有覆盖数据的函数按「全覆盖」记，CRAP 退化成 CC 本身。
        变异成 None 会让下游 crap_score 里的 `1.0 - None` 抛 TypeError —— 而 _crap_file 只
        捕获 OSError/SyntaxError/ValueError，异常会穿透整个 check（见下一条端到端判例）。"""
        self.assertEqual(checks._span_cov(10, 12, set(), set()), 1.0)
        self.assertEqual(checks.crap_score(3, checks._span_cov(10, 12, set(), set())), 3.0)

    def test_file_without_line_data_stays_pass_instead_of_raising(self):
        """checks._span_cov: `return 1.0` 的端到端后果 —— coverage.json 里某文件的
        executed_lines / missing_lines 都是空表时，check_crap_fallback 仍要给出 pass，
        不能因为 None 参与算术而崩掉（internal 检查崩溃 = 整个 ladder 拿不到结论）。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            lc.write_json(os.path.join(out, "coverage.json"),
                          {"files": {"pkg/m.py": {"executed_lines": [], "missing_lines": []}}})
            kit.make_repo(tmp, {"pkg/m.py": CC3})
            ctx = _ctx(tmp, ["pkg/m.py"], out=out)
            ctx["det"]["thresholds"]["crap_max"] = 6.0
            res = checks.check_crap_fallback(ctx)
            self.assertEqual(res["status"], "pass")
            self.assertEqual(res["details"]["total"], 0)


class CrapThresholdBoundaryTestCase(unittest.TestCase):
    def test_score_exactly_at_threshold_is_not_a_violation(self):
        """checks.check_crap_fallback: `v > threshold` — 恰好等于阈值不算违例（> → >= 会误红）。
        构造：CC=3 的函数整段漏测 → cov=0.0 → CRAP = 9 × 1 + 3 = 12.0（tiers.md 公式手算）。
        阈值正好 12.0 时必须 pass；阈值降到 11.9 才判红 —— 两侧一起把边界钉死。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            lc.write_json(os.path.join(out, "coverage.json"),
                          {"files": {"pkg/m.py": {"executed_lines": [], "missing_lines": [1, 2, 3, 4, 5, 6]}}})
            kit.make_repo(tmp, {"pkg/m.py": CC3})
            self.assertEqual(checks.crap_score(3, 0.0), 12.0)
            for threshold, status in ((12.0, "pass"), (11.9, "fail")):
                with self.subTest(threshold=threshold):
                    ctx = _ctx(tmp, ["pkg/m.py"], out=out)
                    ctx["det"]["thresholds"]["crap_max"] = threshold
                    self.assertEqual(checks.check_crap_fallback(ctx)["status"], status)


class DiffCoverageTallyTestCase(unittest.TestCase):
    def test_covered_count_is_measured_minus_uncovered(self):
        """checks.check_diff_coverage: `covered = measured - len(uncovered)` — 减号（- → + 会
        报出「6/4 已覆盖」这种超过分母的数）。既有判例的 pass 分支 uncovered 为空表，
        加减同值，杀不掉；必须用一个真有漏测行的 fixture。
        构造：新增 1、2、4、5 四行都在 coverage 里（1–3 executed、4–5 missing）→ measured=4、
        漏测 2 行 → 摘要必须读作 `2/4`；并且摘要里的分子要与 details.uncovered 对得上（报告自洽）。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            lc.write_json(os.path.join(out, "coverage.json"),
                          {"files": {"pkg/m.py": {"executed_lines": [1, 2, 3], "missing_lines": [4, 5]}}})
            added = {"pkg/m.py": [1, 2, 4, 5]}
            ctx = checks.make_ctx(tmp, kit.fake_det(["pkg/m.py"], diff=_diff_det(added)), out=out)
            res = checks.check_diff_coverage(ctx)
            self.assertEqual(res["status"], "fail")
            hit = re.match(r"^(\d+)/(\d+) added statements covered", res["summary"])
            self.assertIsNotNone(hit, res["summary"])
            covered, measured = int(hit.group(1)), int(hit.group(2))
            self.assertEqual((covered, measured), (2, 4))
            self.assertEqual(measured, res["details"]["measured"])
            # 覆盖数是被测量行的子集计数，永远不可能超过分母
            self.assertLessEqual(covered, measured)
            self.assertEqual(covered, measured - len(res["details"]["uncovered"]))


class LedgerKeyHashTestCase(unittest.TestCase):
    """_line_hash 是写进 baselines/*.txt 的 key 后缀 —— 持久化格式，宽度变了旧账本就对不上。"""

    def test_hash_is_the_first_ten_hex_of_sha1(self):
        """checks._line_hash: `hexdigest()[:10]` — 10 位（return None 会把 key 后缀变成 "None"，
        [:9] / [:11] 会改掉持久化 key 的宽度）。
        摘要算法的真源 = sha1("abc") 的官方测试向量（RFC 3174 §7.1），与 checks.py 的实现无关；
        10 这个**宽度**的真源不是 RFC 而是账本格式本身（见下一条：写进
        baselines/secret_scan.txt 的 key 后缀就是 10 位），两条合起来才钉死这一行。
        顺带钉住 .strip()：行首尾空白不参与 hash，改一行的缩进不该让账本条目漂移。"""
        self.assertEqual(checks._line_hash("abc"), SHA1_ABC[:10])
        self.assertEqual(len(checks._line_hash("abc")), 10)
        self.assertEqual(checks._line_hash("  abc\t\n"), checks._line_hash("abc"))

    def test_written_ledger_key_ends_in_ten_hex_chars(self):
        """checks._line_hash: `[:10]` 的持久化后果 —— --init-baselines 写出的
        secret_scan 账本 key 形如 `<file>::<rule>::<10 位十六进制>`。宽度是格式的一部分：
        变异成 9 / 11 位或 None 会写出一个下次加载对不上的 key。"""
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"src/a.py": 'KEY = "%s"\n' % FAKE_AWS})
            res = checks.check_secret_scan(_ctx(tmp, ["src/a.py"], init=True))
            self.assertEqual(res["status"], "pass")
            text = lc.read_text(os.path.join(tmp, ".test-code", "baselines", "secret_scan.txt"))
            keys = [line.split(" ")[0] for line in text.splitlines() if not line.startswith("#")]
            self.assertEqual(len(keys), 1)
            parts = keys[0].split("::")
            self.assertEqual(parts[:2], ["src/a.py", "aws-access-key"])
            self.assertEqual(len(parts[2]), 10)
            self.assertTrue(set(parts[2]) <= set("0123456789abcdef"), parts[2])
            # 同一棵树再扫一遍（不 init）必须与账本逐字对上 —— key 宽度是真持久化契约
            self.assertEqual(checks.check_secret_scan(_ctx(tmp, ["src/a.py"]))["details"]["new"], [])


class TallySurvivorsTestCase(unittest.TestCase):
    def test_missing_counter_keys_default_to_zero(self):
        """checks._tally_survivors: `mod.get("killed", 0)` / `mod.get("timeout", 0)` /
        `mod.get("executed", 0)` — 缺席的计数键读作 0（缺省值变 ±1 会凭空加减分母分子）。
        构造：一个模块三个计数键齐全，另一个模块**三个键全缺**（runner 没跑到它，
        报告里只留了 survivors），缺省值因此真正被读到。
        手算：killed = (5 + 1) + (0 + 0) = 6；executed = 20 + 0 = 20。
        timeout 记在 killed 侧是 §57 的口径（见 _tally_survivors 自己的 docstring）。"""
        report = {"modules": {
            "a.py": {"killed": 5, "timeout": 1, "executed": 20,
                     "survivors": [{"location": "a.py:11"}, {"location": "a.py:22"}]},
            "b.py": {"survivors": [{"location": "b.py:33"}]},
        }}
        survivors, killed, total = checks._tally_survivors(report, {"a.py:22"})
        self.assertEqual((killed, total), (6, 20))
        self.assertEqual([s["location"] for s in survivors], ["a.py:11", "b.py:33"])
        self.assertEqual([s["module"] for s in survivors], ["a.py", "b.py"])
        # 分子不能超过分母：缺省值被改成 +1 时 killed 会比 executed 的真实来源多算
        self.assertLessEqual(killed, total)


class IgnoredLiteralsTestCase(unittest.TestCase):
    """_ignored_literals 读 .gitignore 的字面路径：文档提到它们 = 声明过的生成物，不算悬空。"""

    GITIGNORE = "# generated on install\nact/_version.py  # written by the installer\n*.pyc\n"

    def test_strips_trailing_comment_and_skips_globs(self):
        """checks._ignored_literals: `raw.split("#", 1)[0]` + `if line and not any(...)` ——
        杀三个变异：maxsplit 1 → 0（不切，注释留在路径里）、`[0]` → `[-1]`（取到注释而不是路径）、
        `and` → `or`（空行和带通配符的行都被当成字面声明收进来）。
        fixture 三行各打一个洞：纯注释行、**行尾带注释的路径**、通配符行。
        真实结果只有 {"act/_version.py"}；maxsplit=0 会给
        {"# generated on install", "act/_version.py  # written by the installer"}，
        `[-1]` 会给 {"generated on install", "written by the installer"}，
        `or` 会给 {"", "act/_version.py", "*.pyc"}。"""
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {".gitignore": self.GITIGNORE})
            self.assertEqual(checks._ignored_literals(tmp), {"act/_version.py"})

    def test_declared_generated_file_is_not_dangling_in_docs(self):
        """checks._ignored_literals 的端到端后果 —— 文档里反引号引用 `act/_version.py`
        （.gitignore 行尾带注释地声明过的生成物）不该被 docs_drift 判成悬空路径。
        注释剥离一坏，这条就从 pass 变 fail。"""
        with tempfile.TemporaryDirectory() as tmp:
            files = {"act/x.py": "", "docs/a.md": "install writes `act/_version.py`\n",
                     ".gitignore": self.GITIGNORE}
            kit.make_repo(tmp, files)
            res = checks.check_docs_drift(_ctx(tmp, sorted(files)))
            self.assertEqual((res["status"], res["details"]["new"]), ("pass", []))


class ActionRefPinTestCase(unittest.TestCase):
    """_unpinned：action ref = **最后一个** @ 之后的那段；只有 40 位十六进制算 pin 住。"""

    def test_ref_is_the_segment_after_the_last_at(self):
        """checks._unpinned: `uses.rsplit("@", 1)[1]` — maxsplit 1（1 → 2 时 [1] 会取到
        中间那段，两个 @ 的 ref 判断整体反转）。两个方向都钉：
        `owner/repo@<40hex>@v1` 的真 ref 是 `v1` → 未 pin（maxsplit=2 会读成 40hex → 误放行）；
        `owner/repo@v1@<40hex>` 的真 ref 是 40hex → 已 pin（maxsplit=2 会读成 v1 → 误判红）。"""
        self.assertIs(checks._unpinned("owner/repo@" + SHA40 + "@v1"), True)
        self.assertIs(checks._unpinned("owner/repo@v1@" + SHA40), False)
        self.assertIs(checks._unpinned("owner/repo@" + SHA40), False)
        self.assertIs(checks._unpinned("owner/repo@v4"), True)

    def test_double_at_ref_is_reported_as_unpinned(self):
        """checks._unpinned 的端到端后果 —— `uses: owner/repo@<40hex>@v1` 实际解析到的是
        可变标签 v1，必须进违例表（漏放行 = 供应链 pin 被绕过）。"""
        with tempfile.TemporaryDirectory() as tmp:
            uses = "owner/repo@" + SHA40 + "@v1"
            wf = ".github/workflows/ci.yml"
            kit.make_repo(tmp, {wf: "on: push\njobs:\n  b:\n    steps:\n      - uses: %s\n" % uses})
            res = checks.check_actions_sha_pin(_ctx(tmp, [wf]))
            self.assertEqual((res["status"], res["details"]["new"]), ("fail", [wf + "::" + uses]))

    def test_only_workflow_yaml_files_are_scanned(self):
        """checks.check_actions_sha_pin: `f.startswith(".github/workflows/") and
        f.endswith((".yml", ".yaml"))` — 两个条件都要（and → or 会把
        .github/workflows/README.md 和仓库里任意一个 docs/*.yml 都拖进扫描）。
        fixture 四个文件把两条件的真值表走全，且**正反两面都断言**（只断言「没红」的话，
        一个永远 pass 的实现也能混过去）：`ci.yml` 两条件都中且已 SHA pin（干净）、
        `release.yaml` 两条件都中但挂着可变 tag（必须且只有它进违例表——顺带钉住
        `.yaml` 也在扫描范围内）、`.github/workflows/README.md` 与 `docs/example.yml`
        各只满足一个条件却都写着 `uses: <tag>`，被误扫就会多出两条违例。"""
        with tempfile.TemporaryDirectory() as tmp:
            tagged = "on: push\njobs:\n  b:\n    steps:\n      - uses: actions/checkout@v4\n"
            wf_yaml = ".github/workflows/release.yaml"
            files = {".github/workflows/ci.yml":
                     "on: push\njobs:\n  b:\n    steps:\n      - uses: actions/checkout@%s\n" % SHA40,
                     wf_yaml: tagged,
                     ".github/workflows/README.md": "example:\n\n      - uses: actions/setup-node@v4\n",
                     "docs/example.yml": tagged}
            kit.make_repo(tmp, files)
            res = checks.check_actions_sha_pin(_ctx(tmp, sorted(files)))
            self.assertEqual(res["details"]["new"], [wf_yaml + "::actions/checkout@v4"])
            self.assertEqual((res["status"], res["details"]["total"]), ("fail", 1))


class TestFileRecognitionTestCase(unittest.TestCase):
    def test_all_three_conditions_are_required(self):
        """checks._is_test_file: `rel.startswith(tests_dir + "/") and base.startswith("test")
        and base.endswith(".py")` — 三个条件缺一不可（and → or 会让只满足一条的文件也算测试）。
        真值表：三条全中才 True；只在 tests/ 下、只叫 test*、只是 .py 的三种单腿输入都是 False。"""
        self.assertEqual(
            [checks._is_test_file(rel, "tests") for rel in
             ("tests/test_a.py", "tests/helper.py", "src/test_a.py", "tests/test_a.txt")],
            [True, False, False, False])

    def test_non_test_helper_under_tests_dir_is_not_smell_scanned(self):
        """checks._is_test_file 的端到端后果 —— tests/ 下的 helper 模块不叫 test*，
        不该进 smell 扫描；它里面那个恰好叫 test_* 的本地函数因此也不该被报 no-assert。
        and → or 会把它拖进来，pass 变 fail。"""
        with tempfile.TemporaryDirectory() as tmp:
            files = {"tests/test_ok.py": ("import unittest\n\n\nclass T(unittest.TestCase):\n"
                                          "    def test_ok(self):\n        self.assertEqual(1, 1)\n"),
                     "tests/helper.py": "def test_probe():\n    return 1\n"}
            kit.make_repo(tmp, files)
            res = checks.check_test_smells(_ctx(tmp, sorted(files)))
            self.assertEqual((res["status"], res["details"]["total"]), ("pass", 0))


class SmellScannerTestCase(unittest.TestCase):
    def test_non_test_function_is_skipped_not_scan_stopping(self):
        """checks._smell_violations: 非 test* 函数上的 `continue`（continue → break 会让
        扫描在第一个 helper 处提前收摊，后面的测试函数一个都不看）。
        构造：helper **排在前面**，带 no-assert 的测试函数排在后面 —— 既有 fixture 里 helper
        在最后，break 掉的尾巴恰好没有 smell，所以杀不掉。"""
        text = "def helper():\n    pass\n\n\ndef test_no_assert():\n    x = 1\n    return x\n"
        self.assertEqual(checks._smell_violations("tests/test_a.py", text, True),
                         {"no-assert:tests/test_a.py::test_no_assert": 1.0})

    def test_unknown_call_shape_yields_empty_name(self):
        """checks._call_name: `return ""` — func 既不是 Attribute 也不是 Name（下标调用、
        lambda 立即调用）时给空串，不是 None。None 会让 _node_flags 里的
        `_ASSERTISH.search(name)` 抛 TypeError，而 _scan_files 只捕获
        OSError/SyntaxError/ValueError —— 异常会穿透 check_test_smells。"""
        calls = ast.parse("handlers[0]()\n(lambda: 1)()\nfoo()\nobj.bar()\n").body
        self.assertEqual([checks._call_name(node.value) for node in calls], ["", "", "foo", "bar"])

    def test_subscript_call_in_test_does_not_break_the_scan(self):
        """checks._call_name: `return ""` 的端到端后果 —— 测试里写 `handlers[0]()` 这种
        下标调用时，smell 扫描要正常给出「无 smell」，不能崩（internal 检查崩 = 拿不到结论）。"""
        text = "def test_dispatch():\n    handlers[0]()\n    assert True\n"
        self.assertEqual(checks._smell_violations("tests/test_a.py", text, False), {})

    def test_from_import_reports_the_module_name(self):
        """checks._import_names: ImportFrom 分支 `return [node.module]` — 返回列表
        （变成 None 会让 _io_imports 的 `for n in _import_names(node)` 抛 TypeError）。
        `import subprocess` 走的是另一条分支，所以必须用 `from subprocess import run` 才踩到这里。"""
        node = ast.parse("from subprocess import run\n").body[0]
        self.assertEqual(checks._import_names(node), ["subprocess"])
        self.assertEqual(checks._import_names(ast.parse("from . import x\n").body[0]), [])

    def test_from_import_of_io_module_is_a_real_io_smell(self):
        """checks._import_names 的端到端后果 —— 单元层测试里 `from subprocess import run`
        必须被记成 real-io violation（漏掉 = 单元层偷偷起子进程没人管）。"""
        text = "from subprocess import run\n\n\ndef test_x():\n    assert run\n"
        self.assertEqual(checks._smell_violations("tests/test_a.py", text, True),
                         {"real-io:tests/test_a.py::subprocess": 1.0})


class TestCountTestCase(unittest.TestCase):
    def test_last_run_count_wins_on_reruns(self):
        """checks._test_count: `hits[-1]` — 取**最后**一段 `Ran N tests`（-1 → 0 会报第一段）。
        ×N 重跑的输出里有 N 段计数，摘要该反映最后一次实际跑了多少条。"""
        text = "Ran 3 tests in 0.010s\n\nOK\nRan 5 tests in 0.020s\n\nOK\n"
        self.assertEqual(checks._test_count(text), "5 tests")
        self.assertEqual(checks._test_count("Ran 1 test in 0.001s\n\nOK\n"), "1 tests")
        self.assertEqual(checks._test_count("nothing here"), "test count unknown")
        verdict = checks._tests_verdict([], [], set(), True, text, runs=2)
        self.assertEqual(verdict["summary"], "5 tests, 0 failures ×2 runs")


class FeedbackChannelCapTestCase(unittest.TestCase):
    """check_feedback_channel 是信息项（永不判红），列出的证据文件有上限，防报告膨胀。"""

    def test_lists_at_most_twelve_files(self):
        """checks.check_feedback_channel: `[:12]` — 列表上限 12（12 → 11 会在恰好 12 个时丢一条、
        12 → 13 会在 14 个时多列一条）。两侧一起把上限钉在 12：
        恰好 12 个候选时一条都不许丢；14 个候选时只列字典序最前的那 12 个，
        且摘要里的计数必须与列表长度一致（用户看到的数字）。
        诚实边界：12 这个数**没有**外部真源（SKILL.md / catalog.md / gates.toml 都没写），
        它是报告体积的自定上限 —— 本判例即它的契约，改上限就得连同这里一起改。"""
        twelve = ["src/telemetry_%02d.py" % i for i in range(1, 13)]
        res = checks.check_feedback_channel(_ctx("/r", twelve))
        self.assertEqual(res["details"]["found"], twelve)
        self.assertEqual(res["summary"], "feedback channel present (12 file(s))")

        fourteen = twelve + ["src/telemetry_13.py", "src/telemetry_14.py"]
        res = checks.check_feedback_channel(_ctx("/r", fourteen))
        self.assertEqual(res["status"], "pass")
        self.assertEqual(res["details"]["found"], twelve)
        self.assertEqual(res["summary"], "feedback channel present (12 file(s))")


if __name__ == "__main__":
    unittest.main()
