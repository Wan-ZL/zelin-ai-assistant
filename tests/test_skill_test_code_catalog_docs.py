"""test-code skill · CATALOG ⟷ 文档同源 + phase / est / timeout 契约判例（防腐 #5 文档指针纪律）。

姐妹判例 tests/test_skill_test_ui_catalog_docs.py 对 test-ui 做的事，这里对 test-code 做一遍：
CATALOG 那 54 行里的 `tier` / `phase` / `est` 三个数字，以前只被「在 1–5 之间」这类范围断言罩着，
改一位没人知道（2026-09 夜间变异跑出 279 个存活体全在这张表里，曾被误判成「时间估计常数、等价体」）。
这里逐个把它们钉回**各自真正的真源**：

- `tier` → references/tiers.md 的 `## 档 N` 表（核心圈）+ references/catalog.md 扩展表的「档」列
  （扩展圈）+ SKILL.md 档表；三份文档互为对照，任一方向漂移都红。
- `phase` → **执行顺序契约**，run_ladder.run_all 读它决定 phase 1 并行 / 2 串行 / 3 等 coverage.json。
  文档不载这个数字，所以这里钉的是它的**可观测后果**：phase 1 的层在线程池里跑（不在调用线程）、
  phase 2 全部早于 phase 3、phase 落在 {1,2,3} 之外的层会被 run_all 整个漏掉。
  诚实说明（和下面 `est` 那条同性质）：线程派发只是**观测通道**，判定用的 oracle 仍是 PHASE_GOLDEN
  这张照现状抄的表。把期望值改成从 `checks.CATALOG` 现算再跑一遍，同档间（1↔2、2↔3）的 66 个改动
  全部存活——也就是说这 66 体靠的是这张表本身，属于改动检测。真正不依赖表的只有三条：
  phase∈{1,2,3}（落到 0 或 4 会被 run_all 整轮漏掉，42 体）、internal 检查除消费 coverage 者外归
  phase 1、coverage 消费者必须严格晚于生产者（后两条的两端都是跑出来找的）。
- `est` → **wire 字段的值**。SKILL.md 步骤 1 写明 detect.json 的 `menu[].est_seconds` 这个键，
  但**逐层的秒数哪份文档都没写**——所以这里的 EST_GOLDEN 是一张 characterization 表（照现状钉），
  只不过是通过 build_menu 的 wire 输出去钉，不是回读 CATALOG 字段。诚实说明：改一秒能红，
  靠的就是这张表本身；下面四条派生不变式（None 只出现在无时限的第 5 档、不得越过本档时限、
  正整数、按文档的档位阶梯递增）单独一条都杀不掉「120→121」这类改动，它们防的是整表跑偏。
- `TIER_TIMEOUTS` → tiers.md 与 SKILL.md 两处逐字同文的散文行
  「Per-check timeouts: 档1 300 s · 档2 1800 s · 档3 3600 s · 档4 7200 s · 档5 none」。
- `TRIGGER_CHECKS` → references/triggers.md 的加挂层列 + SKILL.md 触发器表。

零子进程、零网络；真 IO 只在 tmpdir 里。
法典：CLAUDE.md 防腐 #5 / #7；docs/CONTRACT.md §57（变异靶区）、§58（项目门）；设计 vnext2-plan R2.8。
"""

import inspect
import os
import re
import shutil
import tempfile
import threading
import unittest

from tests import skill_test_code_testkit as kit

import checks  # noqa: E402
import run_ladder as rl  # noqa: E402

SKILL_ROOT = os.path.dirname(kit.SKILL_SCRIPTS)
REFS = os.path.join(SKILL_ROOT, "references")
SKILL_MD = os.path.join(SKILL_ROOT, "SKILL.md")

_ROW_ID = re.compile(r"^\| `([a-z_0-9]+)` \|")
_TIER_HEAD = re.compile(r"^## 档 (\d)")
_ID = re.compile(r"`([a-z_0-9]+)`")
_PARENS = re.compile(r"\([^)]*\)")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _ids(cell):
    """一个 markdown 单元格 → 里面的 check id；括号里的旁注（×`race_reruns` 之类）先剥掉。"""
    return _ID.findall(_PARENS.sub("", cell))


def _tiers_md_tables():
    """tiers.md → (核心圈 {id: 档}, 扩展圈 {id: 档})。核心来自 `## 档 N` 下的表格行，
    扩展来自同一节的「Extended at this tier…」散文行（句号或破折号之前的部分才是 id 清单）。"""
    core, extended, tier = {}, {}, None
    for line in _read(os.path.join(REFS, "tiers.md")).splitlines():
        head = _TIER_HEAD.match(line)
        if head:
            tier = int(head.group(1))
            continue
        row = _ROW_ID.match(line)
        if row and tier:
            core[row.group(1)] = tier
        elif tier and line.startswith("Extended at this tier"):
            for cid in _ids(re.split(r"\.\s|\s—\s", line)[0]):
                extended[cid] = tier
    return core, extended


def _catalog_md_rows():
    """catalog.md「Wired extended checks」表 → {id: 「档」列原文}（docs_drift 那格写「5 (also by trigger)」）。"""
    out = {}
    for line in _read(os.path.join(REFS, "catalog.md")).splitlines():
        row = _ROW_ID.match(line)
        if row:
            out[row.group(1)] = line.split("|")[2].strip()
    return out


def _catalog_md_extended():
    """同上，只取档位数字。"""
    return {cid: int(re.match(r"(\d)", cell).group(1)) for cid, cell in _catalog_md_rows().items()}


def _catalog_md_dual_role():
    """catalog.md 明写「also by trigger」的扩展层——既在第 5 档扩展圈，又被触发器点名加挂。"""
    return {cid for cid, cell in _catalog_md_rows().items() if "also by trigger" in cell}


def _skill_md_tier_rows():
    """SKILL.md 档表 → {档: [核心 id]}（数据行以 `| **` 起头，第 3 列是核心圈清单）。"""
    rows = {}
    for line in _read(SKILL_MD).splitlines():
        if line.startswith("| **"):
            rows[int(line.split("**")[1].split()[0])] = _ids(line.split("|")[3])
    return rows


def _tiers_md_budget_ladder():
    """tiers.md 的 `## 档 N … — <量级词> (core)` 标题 → [(档, 量级词), …]，按文档里的出现顺序。"""
    ladder = []
    for line in _read(os.path.join(REFS, "tiers.md")).splitlines():
        head = re.match(r"^## 档 (\d) .* — (.+?) \(core\)\s*$", line)
        if head:
            ladder.append((int(head.group(1)), head.group(2).strip()))
    return ladder


def _skill_md_budget_ladder():
    """SKILL.md 档表的 Budget 列（第 2 列）→ 同样的 [(档, 量级词), …]；`**` 强调号剥掉。"""
    ladder = []
    for line in _read(SKILL_MD).splitlines():
        if line.startswith("| **"):
            tier = int(line.split("**")[1].split()[0])
            ladder.append((tier, line.split("|")[2].strip().strip("*").strip()))
    return ladder


def _documented_timeouts(path):
    """tiers.md / SKILL.md 的「Per-check timeouts: 档1 300 s · …· 档5 none」散文 → {档: 秒 or None}。"""
    text = _read(path)
    table = {int(t): int(s) for t, s in re.findall(r"档(\d) (\d+) s", text)}
    table.update({int(t): None for t in re.findall(r"档(\d) none", text)})
    return table


# --------------------------------------------------------------------------- #
# phase / est —— 文档不载的两个契约数字，golden 表就是契约本身（理由见模块 docstring）
# --------------------------------------------------------------------------- #

PHASE_GOLDEN = {
    # phase 1：静态门 + 自制检查，run_all 把它们丢进线程池并行跑。
    1: ["py_compile", "py_lint", "py_format", "ts_typecheck", "js_lint", "shellcheck", "swift_parse",
        "deps_direction", "length_caps", "structure", "secret_scan", "actions_sha_pin", "type_coverage",
        "duplication", "doc_coverage", "api_breaking", "complexity", "field_add_only", "test_smells",
        "security_scan", "arch_audit", "docs_drift", "dead_code", "license_check", "feedback_channel",
        "diff_minimality", "dependency_audit", "dependency_budget"],
    # phase 2：跑项目自己的测试 / 构建 / 变异，彼此抢 CPU 与端口，必须串行。
    2: ["bundle_size", "py_unit", "js_unit", "swift_unit", "py_coverage", "js_coverage", "py_integration",
        "js_e2e", "golden_contract", "migration_roundtrip", "mutation_changed", "property_tests",
        "flaky_detect", "mutation_full", "fuzz", "soak_race", "perf_budget", "clean_install",
        "crash_recovery", "fault_injection", "race_stress", "resource_leak", "corpus_regression",
        "contract_drift"],
    # phase 3：读 phase 2 产出的 coverage.json（下面 CoverageOrderTestCase 机器推导同一事实）。
    3: ["diff_coverage", "crap"],
}

EST_GOLDEN = {
    "py_compile": 10, "py_lint": 20, "py_format": 15, "ts_typecheck": 60, "js_lint": 60,
    "shellcheck": 10, "swift_parse": 30, "deps_direction": 10, "length_caps": 10, "structure": 15,
    "secret_scan": 10, "actions_sha_pin": 5,
    "type_coverage": 120, "duplication": 120, "doc_coverage": 30,
    "api_breaking": 60, "bundle_size": 120,
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

_FIXTURE = {
    "pkg/__init__.py": "",
    "pkg/mod.py": "def f():\n    return 1\n",
    "tests/test_mod.py": "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        pass\n",
}


class _FixtureCase(unittest.TestCase):
    """一个能让大多数层真正建出 plan 的 mini python repo（只在 tmpdir 里落盘）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test-code-catalog-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = kit.make_repo(os.path.join(self.tmp, "repo"), _FIXTURE)
        self.out = os.path.join(self.tmp, "out")
        os.makedirs(self.out)
        self.det = kit.fake_det(sorted(_FIXTURE), pymods={"coverage": True})
        self.ctx = checks.make_ctx(self.repo, self.det, out=self.out)


class TierTableTestCase(unittest.TestCase):
    def test_tiers_md_core_tables_pin_every_core_tier(self):
        """checks.CATALOG: 核心圈每行的 tier = references/tiers.md `## 档 N` 表里那一档（±1 即红）。"""
        documented, _ = _tiers_md_tables()
        core = {e["id"]: e["tier"] for e in checks.CATALOG if e["circle"] == "core" and e["tier"] is not None}
        self.assertEqual(sorted(documented), sorted(core), "tiers.md 档表与 CATALOG 核心圈必须同集合")
        for cid, tier in sorted(documented.items()):
            self.assertEqual(checks.BY_ID[cid]["tier"], tier, cid)

    def test_catalog_md_extended_table_pins_every_extended_tier(self):
        """checks.CATALOG: 扩展圈每行的 tier = references/catalog.md 扩展表「档」列（±1 即红）。"""
        documented = _catalog_md_extended()
        extended = {e["id"]: e["tier"] for e in checks.CATALOG if e["circle"] == "extended"}
        self.assertEqual(sorted(documented), sorted(extended), "catalog.md 扩展表与 CATALOG 扩展圈必须同集合")
        for cid, tier in sorted(documented.items()):
            self.assertEqual(checks.BY_ID[cid]["tier"], tier, cid)

    def test_tiers_md_extended_lines_agree_with_catalog_md(self):
        """两份文档互校：tiers.md 每档尾巴的「Extended at this tier」清单 = catalog.md 扩展表的「档」列。"""
        _, from_tiers = _tiers_md_tables()
        self.assertEqual(from_tiers, _catalog_md_extended())

    def test_skill_md_tier_table_matches_catalog(self):
        """SKILL.md 档表（第三份文档）列的核心 id 与 tier 也必须和 CATALOG 一致。"""
        rows = _skill_md_tier_rows()
        self.assertEqual(sorted(rows), [1, 2, 3, 4, 5])
        for tier, ids in sorted(rows.items()):
            self.assertEqual(ids, [e["id"] for e in checks.CATALOG
                                   if e["tier"] == tier and e["circle"] == "core"], "档 %d" % tier)

    def test_skill_md_extended_circle_paragraph_lists_exactly_the_extended_ids(self):
        """SKILL.md「Extended circle」那段列的 id 集合 = CATALOG circle=extended 的集合。"""
        line = [ln for ln in _read(SKILL_MD).splitlines() if ln.startswith("**Extended circle**")]
        self.assertEqual(len(line), 1)
        listed = _ids(line[0].split("references/catalog.md):")[1].split(", plus")[0])
        self.assertEqual(listed, [e["id"] for e in checks.CATALOG if e["circle"] == "extended"])

    def test_every_catalog_id_is_documented_and_every_documented_id_exists(self):
        """集合对等（双向）：核心表 ∪ 扩展表 ∪ triggers.md 加挂层 = CATALOG 的 54 个 id，一个不多一个不少。"""
        core, _ = _tiers_md_tables()
        add_ons = {cid for ids in checks.TRIGGER_CHECKS.values() for cid in ids}
        documented = set(core) | set(_catalog_md_extended()) | add_ons
        self.assertEqual(documented, {e["id"] for e in checks.CATALOG})
        self.assertEqual(len(checks.CATALOG), len(checks.BY_ID), "id 不得重复")

    def test_tier_is_none_exactly_for_the_rows_that_carry_a_trigger(self):
        """tier 与 trigger 互斥：带 trigger 的行没有档（按选定档计时），有档的行不带 trigger。
        加挂层集合 = 这些无档行 + catalog.md 标了「also by trigger」的双身份层（docs_drift）。"""
        for e in checks.CATALOG:
            self.assertEqual(e["trigger"] is not None, e["tier"] is None, e["id"])
        own = {e["id"] for e in checks.CATALOG if e["trigger"] is not None}
        dual = _catalog_md_dual_role()
        self.assertEqual({cid for ids in checks.TRIGGER_CHECKS.values() for cid in ids}, own | dual)
        for cid in sorted(dual):  # 双身份层必须仍是第 5 档扩展圈的一员，不是偷偷加进来的核心层
            self.assertEqual((checks.BY_ID[cid]["tier"], checks.BY_ID[cid]["circle"]), (5, "extended"), cid)


class DefaultChecksPerTierTestCase(unittest.TestCase):
    def test_each_tier_adds_exactly_the_documented_core_rows(self):
        """tier 数字的行为后果：default_checks(档 N) 比 N-1 多出来的，正好是 tiers.md 档 N 表里的核心 id
        （加上 always 加挂的 diff_minimality）。tier 改一位 → 某层换档 → 这里红。"""
        documented, _ = _tiers_md_tables()
        det = {"triggers": []}
        previous = []
        for tier in range(1, 6):
            chosen = checks.default_checks(det, tier)
            added = [cid for cid in chosen if cid not in previous]
            expected = sorted(cid for cid, t in documented.items() if t == tier)
            if tier == 1:
                expected = sorted(expected + ["diff_minimality"])  # always 触发器恒在
            self.assertEqual(sorted(added), expected, "档 %d" % tier)
            previous = chosen
        self.assertEqual(len(previous), len(documented) + 1)


class TierTimeoutsTestCase(unittest.TestCase):
    def test_tier_timeouts_match_both_documented_tables(self):
        """checks.TIER_TIMEOUTS = tiers.md 与 SKILL.md 那句同文散文「档1 300 s · 档2 1800 s · 档3 3600 s ·
        档4 7200 s · 档5 none」；键或值改一位（1800→1799、档2→档3）即红。"""
        for path in (os.path.join(REFS, "tiers.md"), SKILL_MD):
            documented = _documented_timeouts(path)
            self.assertEqual(documented, checks.TIER_TIMEOUTS, os.path.basename(path))
        self.assertEqual(sorted(checks.TIER_TIMEOUTS), [1, 2, 3, 4, 5])

    def test_timeout_for_hands_each_check_its_documented_seconds(self):
        """消费端（run_ladder.timeout_for）真取到文档那个秒数：层按自己的 tier，加挂层按选定档，第 5 档无时限。"""
        documented = _documented_timeouts(os.path.join(REFS, "tiers.md"))
        for entry in checks.CATALOG:
            for chosen in (1, 2, 3, 4):
                expected = documented[entry["tier"] if entry["tier"] is not None else chosen]
                self.assertEqual(rl.timeout_for(entry, chosen, {}), expected,
                                 "%s @ 档 %d" % (entry["id"], chosen))
            self.assertIsNone(rl.timeout_for(entry, 5, {}), entry["id"])

    def test_timeouts_grow_strictly_with_the_tier(self):
        """tiers.md「Each tier adds layers on top of the previous one」的算术后果：档 1–4 的时限严格递增，
        第 5 档 None（无限大）。任一档的秒数越过邻档就红。"""
        seconds = [checks.TIER_TIMEOUTS[t] for t in (1, 2, 3, 4)]
        self.assertEqual(seconds, sorted(seconds))
        self.assertEqual(len(set(seconds)), 4)
        self.assertIsNone(checks.TIER_TIMEOUTS[5])


class TriggerTableTestCase(unittest.TestCase):
    def test_triggers_md_add_on_column_equals_trigger_checks(self):
        """checks.TRIGGER_CHECKS = references/triggers.md 每行的「Add-on」列（always 那行首列无反引号，单独取）。"""
        documented, always = {}, None
        for line in _read(os.path.join(REFS, "triggers.md")).splitlines():
            row = _ROW_ID.match(line)
            if row:
                documented[row.group(1)] = _ids(line.split("|")[3])
            elif line.startswith("| always |"):
                always = _ids(line.split("|")[3])
        self.assertEqual(documented, {k: v for k, v in checks.TRIGGER_CHECKS.items() if k != "always"})
        self.assertEqual(always, checks.TRIGGER_CHECKS["always"])

    def test_skill_md_trigger_table_agrees_with_triggers_md(self):
        """SKILL.md 的触发器表（第二份文档）逐行给出同样的加挂层。"""
        documented = {}
        for line in _read(SKILL_MD).splitlines():
            if line.startswith("| `") and " | `" in line:
                key = _ids(line.split("|")[1])
                if key and key[0] in checks.TRIGGER_CHECKS:
                    documented[key[0]] = _ids(line.split("|")[2])
        self.assertEqual(documented, {k: v for k, v in checks.TRIGGER_CHECKS.items() if k != "always"})

    def test_every_add_on_is_a_catalog_row_carrying_that_trigger_name(self):
        """加挂层与 CATALOG 的 trigger 字段互为反函数：TRIGGER_CHECKS[t] 里的每个 id，其 CATALOG 行的 trigger == t；
        唯一例外是 catalog.md 标了「also by trigger」的双身份层（docs_drift 走扩展圈，trigger 字段留空）。"""
        dual = _catalog_md_dual_role()
        for trigger, ids in sorted(checks.TRIGGER_CHECKS.items()):
            for cid in ids:
                self.assertIn(cid, checks.BY_ID, cid)
                if cid in dual:
                    self.assertIsNone(checks.BY_ID[cid]["trigger"], cid)
                else:
                    self.assertEqual(checks.BY_ID[cid]["trigger"], trigger, cid)


class PhaseContractTestCase(unittest.TestCase):
    """phase 是执行顺序契约（run_ladder.run_all 读 BY_ID[cid]["phase"]）；这里钉它的可观测后果。"""

    @staticmethod
    def _dispatch(ids):
        """给每个 id 一个假的 internal plan，跑 run_all，记录 (执行顺序, 线程)。零子进程。"""
        seen, lock = [], threading.Lock()

        def make(cid):
            def fn(_ctx):
                with lock:
                    seen.append((cid, threading.current_thread().ident))
                return {"status": "pass", "summary": "", "details": {}}
            fn.__name__ = "probe_" + cid
            return fn

        plans = {cid: {"kind": "internal", "fn": make(cid), "tool": "internal"} for cid in ids}
        ctx = checks.make_ctx(os.devnull, {}, out=None)
        results = rl.run_all(plans, ctx, None, {cid: None for cid in ids}, 4)
        return results, seen

    def test_every_catalog_phase_is_actually_dispatched_by_run_all(self):
        """run_all 只派发 phase 1/2/3；phase 落到 0 或 4 的层会被整轮漏掉（`results[cid]` KeyError），
        所以「54 层 54 个结果」这一条就钉死了 phase∈{1,2,3}。"""
        ids = [e["id"] for e in checks.CATALOG]
        results, seen = self._dispatch(ids)
        self.assertEqual(len(results), len(ids))
        self.assertEqual(sorted(cid for cid, _ in seen), sorted(ids))
        self.assertEqual({e["phase"] for e in checks.CATALOG}, {1, 2, 3})

    def test_phase_1_runs_in_the_pool_and_phase_2_finishes_before_phase_3(self):
        """并行/串行的真实分工：phase 1 的层在 ThreadPoolExecutor 的线程里跑（≠ 调用线程），
        phase 2、3 在调用线程里跑且 2 全部早于 3。

        诚实说明：线程派发是**观测通道**，oracle 仍是 PHASE_GOLDEN 这张照现状抄的表——把期望值改成
        从 `checks.CATALOG` 现算，同档内（1↔2、2↔3）的改动就杀不掉了（实测 66 体存活）。所以这条
        与下面那个逐档循环一起，对 `phase` 提供的是**改动检测**，性质同 EST_GOLDEN；真正不依赖表的
        是 test_every_catalog_phase_is_actually_dispatched_by_run_all（phase∈{1,2,3}）、
        test_internal_checks_are_phase_1_unless_they_consume_coverage 与 CoverageOrderTestCase。
        档内次序（phase 2 那几层谁先谁后）哪份文档都没定，所以只比集合、不比顺序——
        「2 全部早于 3」才是成文契约，用派发出来的 phase 序列单调递增来钉。"""
        ids = [e["id"] for e in checks.CATALOG]
        _, seen = self._dispatch(ids)
        here = threading.current_thread().ident
        pooled = sorted(cid for cid, ident in seen if ident != here)
        inline = [cid for cid, ident in seen if ident == here]
        self.assertEqual(pooled, sorted(PHASE_GOLDEN[1]))
        self.assertEqual(sorted(inline), sorted(PHASE_GOLDEN[2] + PHASE_GOLDEN[3]))
        inline_phases = [checks.BY_ID[cid]["phase"] for cid in inline]
        self.assertEqual(inline_phases, sorted(inline_phases), "phase 2 必须全部早于 phase 3")
        for phase, expected in sorted(PHASE_GOLDEN.items()):
            self.assertEqual(sorted(e["id"] for e in checks.CATALOG if e["phase"] == phase),
                             sorted(expected), "phase %d" % phase)

    def test_internal_checks_are_phase_1_unless_they_consume_coverage(self):
        """机器推导（无 golden）：纯 Python 的自制检查在进程内跑、互不抢资源 ⇒ 归 phase 1 并行，
        唯一例外是要等 coverage.json 的那两个（下一条推导出它们是谁）。"""
        internal = [e for e in checks.CATALOG if "_internal(" in inspect.getsource(e["build"])]
        self.assertGreaterEqual(len(internal), 8)
        for entry in internal:
            self.assertIn(entry["phase"], (1, 3), entry["id"])
            if entry["phase"] == 3:
                self.assertIn(entry["id"], ("diff_coverage", "crap"), entry["id"])


class CoverageOrderTestCase(_FixtureCase):
    def test_coverage_consumers_run_in_a_later_phase_than_the_producer(self):
        """机器推导（无 golden）：谁在没有 coverage.json 时报 unavailable = 消费者，消费者自己的报错里点名
        生产者（「select py_coverage」）；生产者的 argv 真写 <out>/coverage.json。
        契约 = phase(消费者) > phase(生产者)，否则 run_all 会在文件还没生出来时就跑消费者。"""
        consumers, producers = {}, set()
        for entry in checks.CATALOG:
            plan = entry["build"](self.ctx)
            if plan["kind"] == "internal":
                res = plan["fn"](self.ctx)
                named = re.search(r"no coverage\.json in this run \(select ([a-z_]+)\)", res["summary"] or "")
                if named:
                    consumers[entry["id"]] = named.group(1)
            if any(str(arg).endswith("coverage.json")
                   for step in plan.get("steps", []) for arg in step["argv"]):
                producers.add(entry["id"])
        self.assertEqual(sorted(consumers), ["crap", "diff_coverage"])
        self.assertEqual(producers, {"py_coverage"})
        for consumer, producer in sorted(consumers.items()):
            self.assertIn(producer, producers, consumer)
            self.assertGreater(checks.BY_ID[consumer]["phase"], checks.BY_ID[producer]["phase"],
                               "%s 必须晚于 %s" % (consumer, producer))


class EstSecondsWireTestCase(_FixtureCase):
    def test_menu_est_seconds_is_the_published_estimate_table(self):
        """est 是 wire 字段：detect.json 的 `menu[].est_seconds`（SKILL.md 步骤 1 写明），选档的人读的就是它。
        这里连 wire key 一起钉：build_menu 每行的 est_seconds ≡ EST_GOLDEN（改一秒即红）。"""
        menu = checks.build_menu(self.ctx)
        self.assertEqual([m["id"] for m in menu], [e["id"] for e in checks.CATALOG])
        self.assertEqual({m["id"]: m["est_seconds"] for m in menu}, EST_GOLDEN)
        self.assertEqual({e["id"]: e["est"] for e in checks.CATALOG}, EST_GOLDEN)

    def test_no_estimate_is_zero_or_negative(self):
        """估计值是秒数：非 None 的必须是 ≥1 的整数（0 或负数会让菜单显示「不花时间」）。"""
        for entry in checks.CATALOG:
            if entry["est"] is not None:
                self.assertIsInstance(entry["est"], int, entry["id"])
                self.assertGreaterEqual(entry["est"], 1, entry["id"])

    def test_est_is_none_exactly_where_the_tier_is_unbounded(self):
        """tiers.md 档 5 =「no time limit」⇒ 没法给估计：est=None 的行全在第 5 档；
        第 5 档里唯一带估计的是 feedback_channel（catalog.md 写它是 informational、永不判红）。"""
        without = {e["id"] for e in checks.CATALOG if e["est"] is None}
        self.assertEqual({checks.BY_ID[cid]["tier"] for cid in without}, {5})
        with_est = {e["id"] for e in checks.CATALOG if e["tier"] == 5 and e["est"] is not None}
        self.assertEqual(with_est, {"feedback_channel"})
        self.assertIsNone(checks.TIER_TIMEOUTS[5])

    def test_no_estimate_exceeds_its_own_tier_timeout(self):
        """自洽：菜单承诺的秒数不能大于 runner 给这层的硬时限，否则估计一到 runner 就把它 kill 成 FAIL。"""
        for entry in checks.CATALOG:
            limit = checks.TIER_TIMEOUTS.get(entry["tier"])
            if entry["est"] is not None and limit is not None:
                self.assertLessEqual(entry["est"], limit, entry["id"])

    def test_core_time_budget_rises_along_the_documented_magnitude_ladder(self):
        """档位阶梯不是硬编码的 range(1, 5)，是从**两份文档**读出来的：tiers.md 的
        `## 档 N … — <量级词> (core)` 标题与 SKILL.md 档表的 Budget 列必须给出同一条
        seconds → minutes → tens of minutes → up to hours → no time limit 阶梯；
        每档核心圈的估计总和就按这条文档顺序严格递增（有时限的那四档）。
        文档把哪两档对调 / 改量级词，这里就红。"""
        ladder = _tiers_md_budget_ladder()
        self.assertEqual(ladder, _skill_md_budget_ladder(), "tiers.md 与 SKILL.md 的量级阶梯必须同文")
        self.assertEqual([t for t, _ in ladder], [1, 2, 3, 4, 5], "档位在文档里必须 1→5 递增列出")
        bounded = [t for t, word in ladder if "no time limit" not in word]
        self.assertEqual(bounded, [1, 2, 3, 4], "只有第 5 档是无时限档")
        budgets = [sum(e["est"] for e in checks.CATALOG if e["tier"] == t and e["circle"] == "core")
                   for t in bounded]
        self.assertEqual(budgets, sorted(budgets))
        self.assertEqual(len(set(budgets)), len(bounded))


class MenuReasonTestCase(_FixtureCase):
    def test_menu_reason_falls_back_from_reason_to_note(self):
        """checks.build_menu: `plan.get("reason") or plan.get("note")` —— na/unavailable 的 plan 只带 reason、
        substituted 的只带 note，两种都必须原样出现在菜单里（or → and 会把两种都变成 None，菜单失去理由列）。"""
        menu = {m["id"]: m for m in checks.build_menu(self.ctx)}
        self.assertEqual(menu["shellcheck"]["kind"], "na")
        self.assertEqual(menu["shellcheck"]["reason"], "no .sh files")
        self.assertEqual(menu["flaky_detect"]["kind"], "substituted")
        self.assertIn("cannot detect whole-suite order dependence", menu["flaky_detect"]["reason"] or "")
        for row in menu.values():
            if row["kind"] in ("na", "unavailable", "substituted"):
                self.assertTrue(row["reason"], row["id"])
            elif row["kind"] == "cmd":
                self.assertIsNone(row["reason"], row["id"])


if __name__ == "__main__":
    unittest.main()
