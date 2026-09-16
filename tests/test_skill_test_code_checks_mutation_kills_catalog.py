"""test-code skill · CATALOG 数据表 + TIER_TIMEOUTS + 菜单 reason 的判例（R-217 补测）。

这一份把 checks.py 里那张纯数据表逐格钉死：TIER_TIMEOUTS 的五档时限、每个 check 的
tier / phase / est_seconds / circle / trigger，以及 build_menu 拼 reason 的那一行。真源分工：
tier 与 circle 以 references/tiers.md + references/catalog.md 为准（人读的是文档，代码必须
与文档一致，改一边就必须改另一边）；phase 以 run_ladder.run_all 的契约为准（phase 1 静态/自制
可并行、phase 2 测试/覆盖/变异串行、phase 3 依赖本轮 coverage.json）；est_seconds 走
build_menu 的 wire key——那是一次性询问（SKILL.md step 1）里人看到的时间估计，改它 = 改 UI。

诚实声明：est_seconds 只进菜单那一格（run_ladder.timeout_for 走 entry["tier"] + TIER_TIMEOUTS，
从不读 est），所以 est ±1 的变异体属于 UI 常数钉桩、不是逻辑杀伤——本文件的杀伤数里约 85 条属此类，
读 §57 台账时别把它们算成覆盖率。保留这张金表是因为 est_seconds 是 SKILL.md step 1 发布的 wire key。

法典：docs/CONTRACT.md §57（存活变异体 = 补测试提案）、§58（skill 只读项目阈值）；
卡 R-217（nightly 2026-09-15：run=839 killed=435 survived=404，本文件负责 CATALOG 数据表
与菜单 reason 那一区）。设计 = docs/design/vnext2-plan.md R2.8。零子进程、零网络。
"""

import os
import re
import tempfile
import unittest

from tests import skill_test_code_testkit as kit

import checks  # noqa: E402

_REFS = os.path.join(os.path.dirname(kit.SKILL_SCRIPTS), "references")

PY_FILES = ["pkg/__init__.py", "pkg/m.py", "tests/__init__.py", "tests/test_m.py"]
JS_FILES = ["web/src/a.ts", "web/package.json"]
JS_PKG = {"dir": "web", "tsconfig": True, "eslint": True, "test_runner": "vitest",
          "bins": ["tsc", "vitest", "eslint"], "lock": "package-lock.json", "scripts": {},
          "coverage_provider": True, "coverage_thresholds": False, "playwright": True, "stryker": False}

# est_seconds 金表：菜单里人看到的静态时间估计（tiers.md：“Time estimates in the menu are
# static per check”）。改任何一格都是一次刻意的 UI 变更，必须连着改这张表。
EST = {
    "py_compile": 10, "py_lint": 20, "py_format": 15, "ts_typecheck": 60, "js_lint": 60,
    "shellcheck": 10, "swift_parse": 30, "deps_direction": 10, "length_caps": 10, "structure": 15,
    "secret_scan": 10, "actions_sha_pin": 5,
    "type_coverage": 120, "duplication": 120, "doc_coverage": 30, "api_breaking": 60, "bundle_size": 120,
    "py_unit": 120, "js_unit": 120, "swift_unit": 300, "py_coverage": 240, "js_coverage": 180,
    "diff_coverage": 5, "complexity": 10, "crap": 15,
    "py_integration": 600, "js_e2e": 600, "golden_contract": 120, "migration_roundtrip": 120,
    "field_add_only": 5,
    "mutation_changed": 1800, "property_tests": 300, "flaky_detect": 600, "test_smells": 10,
    "mutation_full": None, "fuzz": None, "soak_race": None, "security_scan": None, "arch_audit": None,
    "perf_budget": None, "docs_drift": None, "dead_code": None, "license_check": None,
    "clean_install": None, "feedback_channel": 5,
    "crash_recovery": 120, "fault_injection": 120, "race_stress": 600, "resource_leak": 120,
    "corpus_regression": 120, "contract_drift": 120, "diff_minimality": 5, "dependency_audit": 120,
    "dependency_budget": 5,
}

# phase 金表：3 = 只读本轮 coverage.json 的两个尺子；2 = 真去跑项目测试/覆盖/变异/e2e/
# soak/fuzz/perf/装机/体积的层（串行，互相抢机器）；其余一律 1（静态或纯 Python 自制，可并行）。
PHASE_3 = {"diff_coverage", "crap"}
PHASE_2 = {
    "bundle_size", "py_unit", "js_unit", "swift_unit", "py_coverage", "js_coverage",
    "py_integration", "js_e2e", "golden_contract", "migration_roundtrip",
    "mutation_changed", "property_tests", "flaky_detect",
    "mutation_full", "fuzz", "soak_race", "perf_budget", "clean_install",
    # 六个真跑测试的触发器加挂层
    "crash_recovery", "fault_injection", "race_stress", "resource_leak",
    "corpus_regression", "contract_drift",
}

# 跑测试/覆盖/变异的 plan 特征：命中任一 = 必须串行（phase 2）
_SERIAL_POSTS = (checks._post_tests, checks._post_coverage_generic,
                 checks._post_coverage_project, checks._post_mutation)
_SERIAL_TOOLS = ("python-tests", "js-tests", "coverage")


def _ref(name):
    with open(os.path.join(_REFS, name), encoding="utf-8") as fh:
        return fh.read()


_TIER_HEAD_RE = re.compile(r"^## 档 (\d) ")
_DOC_ROW_RE = re.compile(r"^\|\s*`([a-z0-9_]+)`\s*\|")
_DOC_EXT_RE = re.compile(r"^Extended at this tier[^:]*:\s*((?:`[a-z0-9_]+`\s*)+)")
_CAT_ROW_RE = re.compile(r"^\|\s*`([a-z0-9_]+)`\s*\|\s*(\d+)")


def _tiers_doc():
    """tiers.md → {id: (tier, circle)}。每个「## 档 N」小节里：表格行 `| \\`id\\` |` = 该档核心圈；
    「Extended at this tier …: \\`a\\` \\`b\\`」行的前导反引号串 = 该档扩展圈（后面的散文不算）。"""
    out = {}
    tier = None
    for line in _ref("tiers.md").splitlines():
        head = _TIER_HEAD_RE.match(line)
        if head:
            tier = int(head.group(1))
            continue
        if tier is None:
            continue
        row = _DOC_ROW_RE.match(line)
        if row:
            out[row.group(1)] = (tier, "core")
            continue
        ext = _DOC_EXT_RE.match(line)
        if ext:
            for cid in re.findall(r"`([a-z0-9_]+)`", ext.group(1)):
                out[cid] = (tier, "extended")
    return out


def _catalog_doc():
    """catalog.md 的 “Wired extended checks” 表 → {id: 档}（docs_drift 写 “5 (also by trigger)”，取前导整数）。"""
    body = _ref("catalog.md").split("## Wired extended checks", 1)[1].split("\n## ", 1)[0]
    hits = (_CAT_ROW_RE.match(line) for line in body.splitlines())
    return {m.group(1): int(m.group(2)) for m in hits if m}


def _timeouts_doc():
    """tiers.md 的 “Per-check timeouts: 档1 300 s · …” 那一句 → {档: 秒}（none → None）。"""
    line = re.search(r"Per-check timeouts:([^\n]*)", _ref("tiers.md")).group(1)
    pairs = re.findall(r"档(\d)\s+(\d+|none)", line)
    return {int(t): (None if v == "none" else int(v)) for t, v in pairs}


def _ctx(tmp, files, **det_over):
    kit.make_repo(tmp, dict((f, "x = 1\n") for f in files))
    det = kit.fake_det(files, **det_over)
    return checks.make_ctx(tmp, det, out=os.path.join(tmp, "out"))


def _pyjs_ctx(tmp):
    """python + js 双栈 fixture：让尽量多的 builder 真的产出 cmd plan（才看得见 post/tool）。"""
    files = PY_FILES + JS_FILES + ["tests/integration/test_x.py", "tests/test_golden_wire.py",
                                   "tests/test_store_migration.py", "tests/test_corpus_x.py"]
    ctx = _ctx(tmp, files, tools={"npx": "/npx", "ruff": "/ruff"}, pymods={"coverage": True})
    det = ctx["det"]
    det["stacks"] = ["python", "js"]
    det["layout"]["js_packages"] = [dict(JS_PKG)]
    det["layout"]["integration_dir"] = "tests/integration"
    return ctx


class TierTimeoutsTestCase(unittest.TestCase):
    def test_tier_timeouts_table_is_exact(self):
        """checks.TIER_TIMEOUTS: {1: 300, 2: 1800, 3: 3600, 4: 7200, 5: None} — 每档的 per-check 硬时限，
        第 5 档（通宵/通几天）显式无时限（checks.py:33 int_plus1/int_minus1：任何键或秒数 ±1 都会
        让某一档的超时判决错档——档 2 的 1800 s 变 1801 s 就不再是文档承诺的那个预算）。"""
        self.assertEqual(checks.TIER_TIMEOUTS, {1: 300, 2: 1800, 3: 3600, 4: 7200, 5: None})
        self.assertEqual(sorted(checks.TIER_TIMEOUTS), [1, 2, 3, 4, 5])
        self.assertIsNone(checks.TIER_TIMEOUTS[5])

    def test_tier_timeouts_match_the_sentence_humans_read(self):
        """checks.TIER_TIMEOUTS vs references/tiers.md “Per-check timeouts: 档1 300 s · …” — 文档是人读的
        那一份，代码与文档必须同值（checks.py:33 int_plus1/int_minus1：改秒数而不改 tiers.md
        = 文档谎报预算；改键 = 某一档在文档里根本不存在）。"""
        self.assertEqual(_timeouts_doc(), checks.TIER_TIMEOUTS)

    def test_every_estimate_fits_its_tier_timeout(self):
        """CATALOG est vs TIER_TIMEOUTS[tier]: 时间估计不得超过本档的硬时限，否则菜单等于预告一次必然超时。
        不杀任何变异体（现表离时限还很远，est/tier ±1 跨不过去），钉的是「菜单不许预告一次必然超时」
        这条不变式——将来谁把某层的 est 往上抬或把它降档，这条会先响。"""
        for entry in checks.CATALOG:
            est, cap = entry["est"], checks.TIER_TIMEOUTS.get(entry["tier"])
            if est is None:
                continue
            self.assertIsInstance(est, int)
            self.assertGreater(est, 0, entry["id"])
            if cap is not None:
                self.assertLessEqual(est, cap, entry["id"])


class CatalogDocTruthTestCase(unittest.TestCase):
    def test_tier_and_circle_are_what_tiers_md_publishes(self):
        """CATALOG tier/circle vs references/tiers.md 的「## 档 N」表格 + “Extended at this tier” 行 —
        档位与圈层的真源是文档（人按文档选档，default_checks 按 tier ≤ chosen 且 circle == core 勾选）
        （checks.py:1386–1431 int_plus1/int_minus1：tier ±1 = 这层悄悄换档，选档 2 的人会多跑或漏跑它）。"""
        doc = _tiers_doc()
        code = {e["id"]: (e["tier"], e["circle"]) for e in checks.CATALOG if e["tier"] is not None}
        # 先挡住解析噪声：档小节里若新增一张非 check 的表，幽灵 id 会让下面的 diff 读不懂
        self.assertEqual(sorted(set(doc) - set(checks.BY_ID)), [], "tiers.md 表格行不是 check id")
        self.assertEqual(len(code), 45, "tiered entries in CATALOG")
        self.assertEqual(code, doc)

    def test_catalog_md_wired_extended_table_matches_tier(self):
        """CATALOG（circle == extended）vs references/catalog.md “Wired extended checks” 表的「档」列 —
        扩展圈的第二份人读清单也必须同档（checks.py:1398–1431 int_plus1/int_minus1：tier ±1 会让
        菜单里的扩展层和 catalog.md 的档号打架）。"""
        code = {e["id"]: e["tier"] for e in checks.CATALOG if e["circle"] == "extended"}
        self.assertEqual(len(code), 14, "extended entries in CATALOG")
        self.assertEqual(_catalog_doc(), code)

    def test_trigger_addons_are_exactly_the_trigger_table(self):
        """CATALOG（tier is None）vs TRIGGER_CHECKS: 加挂层不属于任何档，只由触发器点名；docs_drift 是
        「有档也被触发器点名」的唯一一例，故加挂层 = TRIGGER_CHECKS 的值集减 docs_drift，共 9 个。
        不杀变异体（这九行上只有 phase/est 两个整数，由 phase/est 两张表钉），钉的是加挂层的身份：
        一律 core 圈、trigger 必须指回点它的那把触发器，两边名单不许单边漂移。"""
        addons = [e["id"] for e in checks.CATALOG if e["tier"] is None]
        named = set(sum(checks.TRIGGER_CHECKS.values(), []))
        self.assertEqual(sorted(addons), sorted(named - {"docs_drift"}))
        self.assertEqual(len(addons), 9)
        for entry in checks.CATALOG:
            if entry["tier"] is None:
                self.assertEqual(entry["circle"], "core", entry["id"])
                self.assertIn(entry["id"], checks.TRIGGER_CHECKS[entry["trigger"]])

    def test_catalog_ids_are_unique_and_indexed(self):
        """CATALOG / BY_ID: 54 行一表、id 唯一且 BY_ID 逐行索引（报告顺序 = CATALOG 顺序）。"""
        ids = [e["id"] for e in checks.CATALOG]
        self.assertEqual(len(ids), 54)
        self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(sorted(checks.BY_ID), sorted(ids))


class CatalogPhaseTestCase(unittest.TestCase):
    def _expected(self, cid):
        return 3 if cid in PHASE_3 else (2 if cid in PHASE_2 else 1)

    def test_phase_table_is_exact(self):
        """CATALOG phase: run_ladder.run_all 的契约 —— phase 1 静态/自制可并行、phase 2 真跑测试串行、
        phase 3 只读本轮 coverage.json；这里钉死每一行属于哪一相（checks.py:1386–1441
        int_plus1/int_minus1：phase 1→2 会把一个静态检查从并行池赶进串行池拖慢整轮，
        phase 2→1 会让两个抢机器的测试套并发对跑、phase 3→2 会在 coverage.json 还没产出前就读它）。"""
        self.assertEqual(PHASE_3 & PHASE_2, set())
        code = {e["id"]: e["phase"] for e in checks.CATALOG}
        self.assertEqual(code, {cid: self._expected(cid) for cid in code})
        self.assertEqual(sorted(cid for cid in code if code[cid] == 3), ["crap", "diff_coverage"])
        self.assertEqual(len([cid for cid in code if code[cid] == 2]), 24)
        self.assertEqual(len([cid for cid in code if code[cid] == 1]), 28)

    def test_every_test_running_plan_is_serial(self):
        """CATALOG phase（导出式）：凡是 plan 带 _post_tests/_post_coverage_*/_post_mutation 或
        tool ∈ {python-tests, js-tests, coverage} 的层，都真的去跑项目的测试套 → 必须 phase 2
        （checks.py:1403–1418、1437–1438 int_minus1 phase 2→1：这些层一旦并行就互相抢 CPU 与端口，
        覆盖率与 flaky 判决都会失真）。length_caps/complexity 的 _post_ledger_verdict 不在此列。"""
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _pyjs_ctx(tmp)
            serial = set()
            for entry in checks.CATALOG:
                plan = entry["build"](ctx)
                if plan.get("post") in _SERIAL_POSTS or plan.get("tool") in _SERIAL_TOOLS:
                    serial.add(entry["id"])
        self.assertTrue({"py_unit", "js_unit", "py_coverage", "py_integration", "golden_contract",
                         "migration_roundtrip", "flaky_detect", "contract_drift"} <= serial, serial)
        self.assertEqual(sorted(cid for cid in serial if checks.BY_ID[cid]["phase"] != 2), [])

    def test_phase_3_checks_are_the_coverage_json_readers(self):
        """CATALOG phase == 3（导出式）：这一相只有两把尺子，且在本轮没有 coverage.json 时必须报
        unavailable 并点名 coverage.json，而不是假 pass（checks.py:1408/1410 int_plus1/int_minus1：
        phase 3→2/3→4 会把它们排到 py_coverage 之前或排出 run_all 认识的相）。"""
        ids = [e["id"] for e in checks.CATALOG if e["phase"] == 3]
        self.assertEqual(ids, ["diff_coverage", "crap"])
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _ctx(tmp, PY_FILES)
            for cid in ids:
                plan = checks.BY_ID[cid]["build"](ctx)
                self.assertEqual(plan["kind"], "internal")
                res = plan["fn"](ctx)
                self.assertEqual(res["status"], "unavailable", cid)
                self.assertIn("coverage.json", res["summary"], cid)

    def test_phase_3_estimates_are_seconds_not_minutes(self):
        """CATALOG est（phase 3）：只读一个 JSON 的层估计 ≤ 15 s（checks.py:1408/1410 int_plus1：
        est 变大会让菜单谎称这两把尺子要花时间，人会因此不勾）。"""
        for entry in checks.CATALOG:
            if entry["phase"] == 3:
                self.assertLessEqual(entry["est"], 15, entry["id"])


class MenuEstimateTestCase(unittest.TestCase):
    def _rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            return {row["id"]: row for row in checks.build_menu(_ctx(tmp, PY_FILES))}

    def test_menu_estimates_are_the_golden_table(self):
        """checks.build_menu: wire key est_seconds 逐行 = EST 金表 —— 这是一次性询问里人看到的
        时间估计，改任何一格都是刻意的 UI 变更，必须连着改这张表（checks.py:1386–1441
        int_plus1/int_minus1：est ±1 会让菜单报出一个没人拍过板的预算）。"""
        rows = self._rows()
        self.assertEqual({cid: row["est_seconds"] for cid, row in rows.items()}, EST)
        self.assertEqual({e["id"]: e["est"] for e in checks.CATALOG}, EST)

    def test_tier_5_is_unbounded_except_the_informational_row(self):
        """CATALOG est（tier 5）：第 5 档无时限 → 核心与扩展层一律不给估计（None）；唯一例外是纯信息项
        feedback_channel（扫一遍文件名，5 s）（checks.py:1420–1431 int_plus1/int_minus1：
        给通宵层安一个数字估计、或把 feedback_channel 的 5 s 改掉，都会误导人排预算）。"""
        for entry in checks.CATALOG:
            if entry["tier"] == 5:
                expected = 5 if entry["id"] == "feedback_channel" else None
                self.assertEqual(entry["est"], expected, entry["id"])
        self.assertEqual(self._rows()["feedback_channel"]["est_seconds"], 5)
        self.assertIsNone(self._rows()["mutation_full"]["est_seconds"])


class MenuWireKeysTestCase(unittest.TestCase):
    def _rows(self, ctx):
        return {row["id"]: row for row in checks.build_menu(ctx)}

    def test_menu_mirrors_tier_trigger_circle_and_label(self):
        """checks.build_menu: 菜单每行的 tier / trigger / circle / label 逐字镜像 CATALOG，且 tier+circle
        与 tiers.md 同值；加挂层 tier 为 None 且 trigger 指回点它的触发器（checks.py:1386–1441
        int_plus1/int_minus1：tier ±1 直接改的是人在菜单上看到的档号）。"""
        doc = _tiers_doc()
        with tempfile.TemporaryDirectory() as tmp:
            rows = self._rows(_ctx(tmp, PY_FILES))
        self.assertEqual(list(rows), [e["id"] for e in checks.CATALOG])
        for cid, row in rows.items():
            self.assertTrue(row["label"].strip(), cid)
            if row["tier"] is None:
                self.assertEqual(row["circle"], "core", cid)
                self.assertIn(cid, checks.TRIGGER_CHECKS[row["trigger"]])
            else:
                self.assertEqual((row["tier"], row["circle"]), doc[cid], cid)
                self.assertIsNone(row["trigger"], cid)

    def test_menu_reason_prefers_reason_then_note(self):
        """checks.build_menu: `plan.get("reason") or plan.get("note")` —— na/unavailable 给的是 reason、
        substituted 给的是 note，菜单这一格要把两者都露出来（checks.py:1481 bool_or or→and：
        只带 reason 的 na 行会变成空原因，人看到「不跑」却看不到「为什么不跑」）。"""
        with tempfile.TemporaryDirectory() as tmp:
            rows = self._rows(_ctx(tmp, PY_FILES))
        self.assertEqual((rows["swift_unit"]["kind"], rows["swift_unit"]["reason"]),
                         ("na", "no swift sources"))
        self.assertEqual(rows["flaky_detect"]["kind"], "substituted")
        self.assertIn("3 plain reruns", rows["flaky_detect"]["reason"])
        self.assertEqual((rows["py_compile"]["kind"], rows["py_compile"]["reason"]), ("cmd", None))


if __name__ == "__main__":
    unittest.main()
