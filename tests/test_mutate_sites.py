"""scripts/qa/mutate.py 的判例（CONTRACT §57）—— site 生成 / 跳过规则 /
TOML 子集 / 预算与续跑调度（注入假 runner 与假 clock，本文件零 subprocess）。

真子进程的杀伤判定住 tests/integration/test_mutation_runner.py（防腐 #7）。
"""
import importlib.util
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

_SPEC = importlib.util.spec_from_file_location(
    "qa_mutate",
    Path(__file__).resolve().parent.parent / "scripts" / "qa" / "mutate.py",
)
mutate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mutate)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mutation" / "sample_target.py"


class SiteGenerationTestCase(unittest.TestCase):
    """fixture 的 site 总数与算子分布被钉死——算子集合的任何增删改都在这里挂红。"""

    def setUp(self):
        self.source = FIXTURE.read_text(encoding="utf-8")

    def test_exact_site_count_on_fixture(self):
        sites = mutate.collect_sites_from_source(self.source)
        self.assertEqual(len(sites), 22)

    def test_operator_distribution_on_fixture(self):
        sites = mutate.collect_sites_from_source(self.source)
        self.assertEqual(Counter(s.op for s in sites), Counter({
            "return_none": 5, "int_plus1": 3, "int_minus1": 3, "loop_flow": 2,
            "cmp_lt": 1, "cmp_gt": 1, "cmp_eq": 1, "cmp_gte": 1, "cmp_is": 1,
            "cmp_in": 1, "bool_or": 1, "arith_add": 1, "const_bool": 1,
        }))

    def test_site_ids_are_deterministic(self):
        first = [s.site_id for s in mutate.collect_sites_from_source(self.source)]
        second = [s.site_id for s in mutate.collect_sites_from_source(self.source)]
        self.assertEqual(first, second)

    def test_every_mutant_compiles_and_differs(self):
        import ast
        pristine = ast.unparse(ast.parse(self.source))
        sites = mutate.collect_sites_from_source(self.source)
        rendered = set()
        for index in range(len(sites)):
            mutant = mutate.render_mutant(self.source, index)
            compile(mutant, "<mutant>", "exec")  # 变异体必须仍是合法 Python
            self.assertNotEqual(mutant, pristine,
                                f"site {sites[index].site_id} 生成了等价文本")
            rendered.add(mutant)
        # ±1 与翻转都指向不同文本——22 个 site 至少 21 个互不相同
        # （0 -> -1 与任何其它变异都不同；这里钉全部互异，撞车说明算子重叠）
        self.assertEqual(len(rendered), len(sites))


class SkipRuleTestCase(unittest.TestCase):
    """等价变异体高发区的三条跳过规则 + 字符串常量天然免疫。"""

    def _ops(self, source):
        return [s.op for s in mutate.collect_sites_from_source(source)]

    def test_logging_calls_are_skipped_entirely(self):
        self.assertEqual(self._ops(
            "logger.warning('n=%d', n + 1)\n"
            "log.error('x', 2 + 2)\n"
            "log_event('e', value=3 - 1)\n"), [])

    def test_repr_bodies_are_skipped(self):
        source = ("class A:\n"
                  "    def __repr__(self):\n"
                  "        return 'A(' + str(self.x != 1) + ')'\n")
        self.assertEqual(self._ops(source), [])

    def test_main_guard_is_skipped(self):
        self.assertEqual(self._ops(
            "if __name__ == '__main__':\n    run(1 + 2)\n"), [])

    def test_docstrings_and_strings_are_never_mutated(self):
        source = '"""doc with numbers 42 and True"""\nNAME = "x == y"\n'
        self.assertEqual(self._ops(source), [])

    def test_non_logging_calls_still_mutate(self):
        self.assertIn("arith_add", self._ops("process(n + 1)\n"))

    def test_return_none_not_mutated_to_itself(self):
        self.assertEqual(self._ops("def f():\n    return None\n"), [])
        self.assertEqual(self._ops("def f():\n    return\n"), [])


class TomlSubsetTestCase(unittest.TestCase):
    """parse_targets_toml 只认声明的子集——真靶区文件必须能解析。"""

    def test_real_targets_file_parses(self):
        text = (Path(__file__).resolve().parent.parent
                / "qa" / "mutation_targets.toml").read_text(encoding="utf-8")
        tables = mutate.parse_targets_toml(text)
        self.assertIn("config", tables)
        self.assertIn("targets", tables)
        self.assertGreater(int(tables["config"]["time_budget_seconds"]), 0)
        for module, tests in tables["targets"].items():
            self.assertTrue(module.endswith(".py"), module)
            self.assertIsInstance(tests, list)
            for test in tests:
                self.assertTrue((Path(__file__).resolve().parent.parent
                                 / test).is_file(),
                                f"{module} 映射的 {test} 不存在")

    def test_subset_values(self):
        tables = mutate.parse_targets_toml(
            '# comment\n[config]\nn = 5\nflag = true\nname = "x # not comment"\n'
            '[targets]\n"a/b.py" = ["t/one.py", "t/two.py"]\nempty = []\n')
        self.assertEqual(tables["config"], {"n": 5, "flag": True,
                                            "name": "x # not comment"})
        self.assertEqual(tables["targets"],
                         {"a/b.py": ["t/one.py", "t/two.py"], "empty": []})

    def test_rejects_out_of_subset_syntax(self):
        for bad in ("key = 1\n",                 # key 在任何 table 之前
                    "[t]\nkey = [1, 2]\n",       # 非字符串数组
                    "[t]\nkey = 1.5\n",          # float 不在子集
                    "[t]\njust a line\n"):
            with self.assertRaises(ValueError, msg=bad):
                mutate.parse_targets_toml(bad)


class RoundRobinTestCase(unittest.TestCase):
    def test_interleaves_across_modules(self):
        self.assertEqual(
            mutate.round_robin([["a1", "a2", "a3"], ["b1"], ["c1", "c2"]]),
            ["a1", "b1", "c1", "a2", "c2", "a3"])

    def test_empty(self):
        self.assertEqual(mutate.round_robin([]), [])
        self.assertEqual(mutate.round_robin([[], []]), [])


class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _FakeRunner:
    """注入缝 runner：记录调用序，按 (module, detail) 决定杀/存/超时，
    每次调用把假钟拨快 step 秒。baseline（mutant_source None）默认 pass。"""

    def __init__(self, clock, step=10.0, baseline=None, verdict=None):
        self.clock = clock
        self.step = step
        self.baseline = baseline or {}
        self.verdict = verdict or (lambda module, source: "fail")
        self.calls = []

    def __call__(self, module, mutant_source, tests, timeout):
        self.clock.t += self.step
        kind = "baseline" if mutant_source is None else "mutant"
        self.calls.append((kind, module))
        if mutant_source is None:
            return self.baseline.get(module, "pass")
        return self.verdict(module, mutant_source)


def _write_module(root, rel, body):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


# 4 个 site：arith_add / int_plus1 / int_minus1 / return_none
_SMALL = "def f(a):\n    return a + 1\n"


class SchedulerTestCase(unittest.TestCase):
    """预算封顶、round-robin、断点续跑、hash 作废、baseline 红跳过——全部无 spawn。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mutate-sched-")
        _write_module(self.root, "m1.py", _SMALL)
        _write_module(self.root, "m2.py", _SMALL)
        self.targets = {"m1.py": ["t/t1.py"], "m2.py": ["t/t2.py"]}

    def _fresh_state(self):
        return {"schema": mutate.SCHEMA,
                "runner_version": mutate.RUNNER_VERSION, "modules": {}}

    def test_budget_cap_stops_midway_and_resume_finishes(self):
        clock = _FakeClock()
        runner = _FakeRunner(clock)  # 每调用 10s：2 baseline + 2 mutant = 40s
        state = self._fresh_state()
        report, state = mutate.run_targets(
            self.root, self.targets, budget_seconds=35, mutant_timeout=1,
            state=state, clock=clock, subset_runner=runner, log=lambda *_: None)
        self.assertTrue(report["budget_hit"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["executed_this_run"], 2)
        # round-robin：预算内每个模块都被访问（m1 一个、m2 一个）
        self.assertEqual([c for c in runner.calls if c[0] == "mutant"],
                         [("mutant", "m1.py"), ("mutant", "m2.py")])

        clock2 = _FakeClock()
        runner2 = _FakeRunner(clock2)
        report2, state = mutate.run_targets(
            self.root, self.targets, budget_seconds=10_000, mutant_timeout=1,
            state=state, clock=clock2, subset_runner=runner2, log=lambda *_: None)
        self.assertTrue(report2["complete"])
        self.assertFalse(report2["budget_hit"])
        # 续跑只补剩下的 6 个，绝不重跑已记账的 2 个
        self.assertEqual(report2["executed_this_run"], 6)
        for module in ("m1.py", "m2.py"):
            self.assertEqual(report2["modules"][module]["executed"], 4)
            self.assertEqual(report2["modules"][module]["pending"], 0)

    def test_completed_run_reexecutes_nothing(self):
        clock = _FakeClock()
        runner = _FakeRunner(clock)
        state = self._fresh_state()
        mutate.run_targets(self.root, self.targets, budget_seconds=10_000,
                           mutant_timeout=1, state=state, clock=clock,
                           subset_runner=runner, log=lambda *_: None)
        runner2 = _FakeRunner(clock)
        report, _state = mutate.run_targets(
            self.root, self.targets, budget_seconds=10_000, mutant_timeout=1,
            state=state, clock=clock, subset_runner=runner2, log=lambda *_: None)
        self.assertEqual(report["executed_this_run"], 0)
        self.assertEqual(runner2.calls, [])  # 连 baseline 都不用跑
        self.assertTrue(report["complete"])

    def test_content_hash_change_invalidates_only_that_module(self):
        clock = _FakeClock()
        state = self._fresh_state()
        mutate.run_targets(self.root, self.targets, budget_seconds=10_000,
                           mutant_timeout=1, state=state, clock=clock,
                           subset_runner=_FakeRunner(clock), log=lambda *_: None)
        _write_module(self.root, "m1.py", "def f(a):\n    return a - 1\n")
        runner = _FakeRunner(clock)
        report, _state = mutate.run_targets(
            self.root, self.targets, budget_seconds=10_000, mutant_timeout=1,
            state=state, clock=clock, subset_runner=runner, log=lambda *_: None)
        self.assertEqual(report["executed_this_run"], 4)  # 只有 m1 重跑
        self.assertEqual({c for c in runner.calls if c[0] == "mutant"},
                         {("mutant", "m1.py")})

    def test_red_baseline_skips_module_and_marks_report(self):
        clock = _FakeClock()
        runner = _FakeRunner(clock, baseline={"m2.py": "fail"})
        report, _state = mutate.run_targets(
            self.root, self.targets, budget_seconds=10_000, mutant_timeout=1,
            state=self._fresh_state(), clock=clock, subset_runner=runner,
            log=lambda *_: None)
        self.assertEqual(report["modules"]["m2.py"]["status"], "baseline_failed")
        self.assertEqual(report["modules"]["m2.py"]["executed"], 0)
        self.assertFalse(report["complete"])
        self.assertNotIn(("mutant", "m2.py"), runner.calls)
        self.assertEqual(report["modules"]["m1.py"]["executed"], 4)

    def test_survivor_and_timeout_classification(self):
        clock = _FakeClock()

        def verdict(module, source):
            if module == "m1.py":
                return "pass"       # 全部存活
            return "timeout"        # 全部超时（记 killed 侧的独立列）

        runner = _FakeRunner(clock, verdict=verdict)
        report, _state = mutate.run_targets(
            self.root, self.targets, budget_seconds=10_000, mutant_timeout=1,
            state=self._fresh_state(), clock=clock, subset_runner=runner,
            log=lambda *_: None)
        m1 = report["modules"]["m1.py"]
        self.assertEqual((m1["survived"], m1["killed"], m1["score"]), (4, 0, 0.0))
        self.assertEqual(len(m1["survivors"]), 4)
        for survivor in m1["survivors"]:
            self.assertTrue(survivor["location"].startswith("m1.py:"))
        m2 = report["modules"]["m2.py"]
        self.assertEqual((m2["timeout"], m2["score"]), (4, 1.0))

    def test_single_module_run_never_prunes_other_state(self):
        clock = _FakeClock()
        state = self._fresh_state()
        mutate.run_targets(self.root, self.targets, budget_seconds=10_000,
                           mutant_timeout=1, state=state, clock=clock,
                           subset_runner=_FakeRunner(clock), log=lambda *_: None)
        mutate.run_targets(self.root, {"m1.py": ["t/t1.py"]},
                           budget_seconds=10_000, mutant_timeout=1, state=state,
                           clock=clock, subset_runner=_FakeRunner(clock),
                           log=lambda *_: None, prune_state=False)
        self.assertIn("m2.py", state["modules"])  # 单模块运行不清别人的账

    def test_checkpoint_fires_every_interval(self):
        from unittest import mock
        clock = _FakeClock()
        hits = []
        with mock.patch.object(mutate, "CHECKPOINT_EVERY", 3):
            mutate.run_targets(
                self.root, self.targets, budget_seconds=10_000, mutant_timeout=1,
                state=self._fresh_state(), clock=clock,
                subset_runner=_FakeRunner(clock), log=lambda *_: None,
                checkpoint=lambda: hits.append(1))
        self.assertEqual(len(hits), 2)  # 8 个变异体、每 3 个落一次账

    def test_missing_module_reported_not_crashed(self):
        clock = _FakeClock()
        report, _state = mutate.run_targets(
            self.root, {"gone.py": ["t/t.py"]}, budget_seconds=10_000,
            mutant_timeout=1, state=self._fresh_state(), clock=clock,
            subset_runner=_FakeRunner(clock), log=lambda *_: None)
        self.assertEqual(report["modules"]["gone.py"]["status"], "missing")
        self.assertFalse(report["complete"])


class StateFileTestCase(unittest.TestCase):
    def test_roundtrip_and_stale_version_discarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = {"schema": mutate.SCHEMA,
                     "runner_version": mutate.RUNNER_VERSION,
                     "modules": {"m.py": {"content_hash": "h", "results": {}}}}
            mutate.save_state(path, state)
            self.assertEqual(mutate.load_state(path), state)
            stale = dict(state, runner_version=mutate.RUNNER_VERSION - 1)
            mutate.save_state(path, stale)
            self.assertEqual(mutate.load_state(path)["modules"], {})

    def test_unreadable_state_starts_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(mutate.load_state(path)["modules"], {})
            self.assertEqual(mutate.load_state(Path(tmp) / "absent.json")
                             ["runner_version"], mutate.RUNNER_VERSION)


class MarkdownReportTestCase(unittest.TestCase):
    """pinned issue 的 body：存活体带 file:line，D5「永不作为 PR 门」明文在场。"""

    def _sample_report(self):
        root = tempfile.mkdtemp(prefix="mutate-md-")
        _write_module(root, "m1.py", _SMALL)
        clock = _FakeClock()
        runner = _FakeRunner(clock, verdict=lambda module, source: "pass")
        report, _state = mutate.run_targets(
            root, {"m1.py": ["t/t1.py"]}, budget_seconds=10_000,
            mutant_timeout=1,
            state={"schema": mutate.SCHEMA,
                   "runner_version": mutate.RUNNER_VERSION, "modules": {}},
            clock=clock, subset_runner=runner, log=lambda *_: None)
        return report

    def test_report_names_survivors_and_the_no_gate_rule(self):
        body = mutate.render_markdown(self._sample_report())
        self.assertIn("# Nightly mutation report", body)
        self.assertIn("Never a PR gate", body)          # D5 / R2.3.4
        self.assertIn("`m1.py:2`", body)                # 存活体 file:line
        self.assertIn("Surviving mutants (4)", body)
        self.assertIn("scripts/qa/mutate.py --all", body)  # 本地跑法（无 launchd）

    def test_report_json_is_serializable(self):
        report = self._sample_report()
        parsed = json.loads(json.dumps(report))
        self.assertEqual(parsed["schema"], mutate.SCHEMA)
        self.assertEqual(
            parsed["modules"]["m1.py"]["survivors"][0]["location"], "m1.py:2")


if __name__ == "__main__":
    unittest.main()
