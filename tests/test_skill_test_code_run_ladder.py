"""test-code skill · run_ladder 判例：一层的执行语义（na/unavailable 原样、missing = fail、
自制检查崩 = fail、超时 = fail 且 post 不许翻案、substituted 永不写 pass）、phase 顺序、
选择构造、退出码（0 绿 / 1 红 / 3 不完整 / 2 用法）、报告 JSON/markdown 组装、
--init-baselines 祖父条款。全程 FakeRunner，零子进程。

法典：docs/CONTRACT.md §58 / §57；设计 vnext2-plan R2.8 / D14（R2.8.2 两种调用）。
负控制：一条失败测试必须让整梯红且排在 fix-first 第一位。
"""
import contextlib
import io
import json
import os
import tempfile
import types
import unittest

from tests import skill_test_code_testkit as kit

import checks  # noqa: E402
import run_ladder as rl  # noqa: E402

lc = kit.lc
PY_FILES = ["pkg/__init__.py", "pkg/m.py", "tests/__init__.py", "tests/test_m.py"]
FIXTURE = {"pkg/__init__.py": "", "pkg/m.py": "def f():\n    return 1\n", "tests/__init__.py": "",
           "tests/test_m.py": "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n"
                              "        self.assertEqual(1, 1)\n"}
FAILING_OUT = "FAIL: test_a (tests.test_m.T)\nRan 1 test in 0.0s\n\nFAILED (failures=1)\n"


def _ctx(repo="/r", out=None, sel=None, files=PY_FILES, init=False, **over):
    return checks.make_ctx(repo, kit.fake_det(files, **over), sel=sel, out=out, init_baselines=init)


def _cmd_plan(argv=("echo",), kind="cmd", post=None, steps=None):
    steps = steps or [{"argv": list(argv), "cwd": "/r", "env": None}]
    return {"kind": kind, "steps": steps, "tool": "python", "post": post, "note": "substitute note"}


def _sel(checks_, tier=1, **extra):
    sel = {"tier": tier, "checks": checks_,
           "ask": {"recommended": 2, "reason": "t", "chosen": tier, "chosen_by": "user"}}
    sel.update(extra)
    return sel


class ExecuteTestCase(unittest.TestCase):
    def test_na_unavailable_missing_pass_through(self):
        for kind, expected in (("na", "na"), ("unavailable", "unavailable"), ("missing", "fail")):
            with self.subTest(kind=kind):
                res = rl.execute("py_compile", {"kind": kind, "reason": "why", "steps": []}, _ctx(), None, 1)
                self.assertEqual((res["status"], res["summary"]), (expected, "why"))

    def test_internal_crash_fails_closed(self):
        def boom(ctx):
            raise RuntimeError("kaboom")
        res = rl.execute("secret_scan", {"kind": "internal", "fn": boom, "tool": "internal", "steps": []}, _ctx(), None, 1)
        self.assertEqual(res["status"], "fail")
        self.assertIn("kaboom", res["summary"])
        ok = {"kind": "internal", "fn": lambda ctx: {"status": "na", "summary": "s", "details": {"d": 1}},
              "tool": "internal", "steps": []}
        res = rl.execute("secret_scan", ok, _ctx(), None, 1)
        self.assertEqual((res["status"], res["details"]), ("na", {"d": 1}))

    def test_command_outcomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _ctx(out=tmp)
            res = rl.execute("py_compile", _cmd_plan(), ctx, kit.FakeRunner(default=(1, "", "")), 5)
            self.assertEqual((res["status"], res["summary"], res["rc"]), ("fail", "exit code 1 (step 1/1)", 1))
            self.assertTrue(os.path.exists(os.path.join(tmp, "logs", "py_compile.log")))
            res = rl.execute("py_compile", _cmd_plan(), ctx, kit.FakeRunner(default=(-2, "", "no such file")), 5)
            self.assertIn("could not start", res["summary"])
            res = rl.execute("py_compile", _cmd_plan(), ctx, kit.FakeRunner(default=(0, "ok", "")), 5)
            self.assertEqual((res["status"], res["steps_run"]), ("pass", 1))

    def test_timeout_is_fail_and_post_cannot_override(self):
        timed = lc.RunResult(-1, "Ran 1 test\nOK", "", timed_out=True)
        plan = _cmd_plan(post=lambda ctx, plan, runs: {"status": "pass", "summary": "should not win"})
        res = rl.execute("py_unit", plan, _ctx(), kit.FakeRunner(default=timed), 7)
        self.assertEqual(res["status"], "fail")
        self.assertIn("timed out after 7s", res["summary"])
        self.assertTrue(res["timed_out"])

    def test_substituted_never_pass_and_post_override(self):
        runner = kit.FakeRunner(default=(0, "", ""))
        res = rl.execute("flaky_detect", _cmd_plan(kind="substituted"), _ctx(), runner, 5)
        self.assertEqual((res["status"], res["summary"]), ("substituted", "substitute note"))
        post = lambda ctx, plan, runs: {"status": "pass", "summary": "post says pass"}  # noqa: E731
        res = rl.execute("flaky_detect", _cmd_plan(kind="substituted", post=post), _ctx(), runner, 5)
        self.assertEqual(res["status"], "substituted")
        post_fail = lambda ctx, plan, runs: {"status": "fail", "summary": "post says fail"}  # noqa: E731
        res = rl.execute("flaky_detect", _cmd_plan(kind="substituted", post=post_fail), _ctx(), runner, 5)
        self.assertEqual(res["status"], "fail")
        res = rl.execute("py_unit", _cmd_plan(post=post), _ctx(), kit.FakeRunner(default=(1, "", "")), 5)
        self.assertEqual((res["status"], res["summary"]), ("pass", "post says pass"))

    def test_post_crash_fails_closed_and_steps_stop_at_first_failure(self):
        def bad_post(ctx, plan, runs):
            raise KeyError("x")
        res = rl.execute("py_unit", _cmd_plan(post=bad_post), _ctx(), kit.FakeRunner(default=(0, "", "")), 5)
        self.assertEqual(res["status"], "fail")
        self.assertIn("post-processing crashed", res["summary"])
        steps = [{"argv": ["a"], "cwd": "/r", "env": None}, {"argv": ["b"], "cwd": "/r", "env": None},
                 {"argv": ["c"], "cwd": "/r", "env": None}]
        runner = kit.FakeRunner([("b", (1, "", ""))])
        res = rl.execute("py_compile", _cmd_plan(steps=steps), _ctx(), runner, 5)
        self.assertEqual((res["status"], res["steps_run"], res["summary"]), ("fail", 2, "exit code 1 (step 2/3)"))


class SelectionTestCase(unittest.TestCase):
    def _args(self, **over):
        base = {"selection": None, "tier": 1, "checks": None, "skip": None, "chosen_by": "headless", "declared": None}
        base.update(over)
        return types.SimpleNamespace(**base)

    def test_from_args_defaults_skip_and_chosen_by(self):
        det = kit.fake_det(PY_FILES)
        sel = rl.build_selection(self._args(skip="py_lint, secret_scan", chosen_by="user", declared=["pkg/*"]), det)
        self.assertNotIn("py_lint", sel["checks"])
        self.assertNotIn("secret_scan", sel["checks"])
        self.assertIn("py_compile", sel["checks"])
        self.assertEqual(sel["ask"]["chosen_by"], "user")
        self.assertEqual(sel["declared_files"], ["pkg/*"])
        sel = rl.build_selection(self._args(checks="py_unit,crap"), det)
        self.assertEqual((sel["checks"], sel["ask"]["chosen_by"]), (["py_unit", "crap"], "recommended, not confirmed"))

    def test_errors(self):
        det = kit.fake_det(PY_FILES)
        with self.assertRaises(rl.SelectionError):
            rl.build_selection(self._args(tier=None), det)
        with self.assertRaises(rl.SelectionError):
            rl.build_selection(self._args(checks="py_unit,nope"), det)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sel.json")
            lc.write_json(path, {"checks": ["py_unit"]})
            with self.assertRaises(rl.SelectionError):
                rl.build_selection(self._args(selection=path), det)
            lc.write_json(path, {"tier": 2, "checks": ["py_unit"]})
            sel = rl.build_selection(self._args(selection=path), det)
            self.assertEqual(sel["ask"]["chosen_by"], "recommended, not confirmed")

    def test_timeout_for(self):
        self.assertEqual(rl.timeout_for(checks.BY_ID["py_compile"], 2, {}), 300)
        self.assertEqual(rl.timeout_for(checks.BY_ID["py_unit"], 1, {}), 1800)
        self.assertEqual(rl.timeout_for(checks.BY_ID["diff_minimality"], 3, {}), 3600)
        self.assertIsNone(rl.timeout_for(checks.BY_ID["py_compile"], 5, {}))
        self.assertEqual(rl.timeout_for(checks.BY_ID["py_compile"], 2, {"timeout_seconds": 5}), 5.0)


def _r(cid, status, tool="internal", details=None, summary="s", reason=None):
    return {"id": cid, "status": status, "tool": tool, "details": details or {}, "summary": summary,
            "reason": reason, "tier": checks.BY_ID[cid]["tier"], "trigger": None, "label": cid, "duration_s": 0.0}


class AssembleTestCase(unittest.TestCase):
    def test_verdict_and_not_run(self):
        self.assertEqual(rl.verdict([_r("py_unit", "pass"), _r("secret_scan", "na")]), ("green", 0))
        self.assertEqual(rl.verdict([_r("py_unit", "pass"), _r("py_lint", "unavailable")]), ("incomplete", 3))
        self.assertEqual(rl.verdict([_r("flaky_detect", "substituted")]), ("incomplete", 3))
        self.assertEqual(rl.verdict([_r("py_unit", "fail"), _r("py_lint", "unavailable")]), ("red", 1))
        split = rl.not_run([_r("py_lint", "unavailable", reason="no ruff"), _r("py_format", "na"), _r("py_unit", "pass")])
        self.assertEqual(split["unavailable"], [{"id": "py_lint", "reason": "no ruff"}])
        self.assertEqual([i["id"] for i in split["na"]], ["py_format"])

    def test_fix_first_ranking(self):
        det = kit.fake_det(PY_FILES, mutation_targets=["act/lib/x.py"],
                           diff={"changed_files": ["pkg/m.py"], "added": {}, "added_text": {}, "removed": {},
                                 "untracked": [], "base": "b", "base_commit": "c"})
        results = [
            _r("py_unit", "fail", tool="python-tests", details={"new": ["tests.T.test_a"]}),
            _r("crap", "fail", tool="python", details={"new": ["pkg/m.py::f", "other/z.py::g"]}),
            _r("mutation_changed", "fail", tool="python", details={"survivors": [
                {"module": "act/lib/x.py", "location": "act/lib/x.py:1", "op": "cmp"},
                {"module": "act/y.py", "location": "act/y.py:2", "op": "cmp"}]}),
            _r("complexity", "fail", tool="python", details={"new": ["pkg/m.py::g"]}),
            _r("swift_parse", "fail", tool="swiftc", summary="exit code 1"),
        ]
        items = rl.fix_first(results, det)
        self.assertEqual([i["rank"] for i in items], [1, 2, 3, 4, 5, 6])
        self.assertEqual(items[0]["item"], "tests.T.test_a")
        self.assertEqual(items[1]["item"], "pkg/m.py::f")
        self.assertEqual(items[2]["item"], "act/lib/x.py:1 cmp")
        self.assertEqual(items[5]["check"], "swift_parse")
        self.assertEqual([s["location"] for s in rl.surviving_mutants(results)], ["act/lib/x.py:1", "act/y.py:2"])

    def test_baseline_note(self):
        results = [_r("py_unit", "pass", tool="python-tests", details={"pre_existing": ["t.b", "t.a"], "new": []}),
                   _r("secret_scan", "pass", details={"total": 3, "new": []}),
                   _r("docs_drift", "fail", details={"total": 2, "new": ["x"]})]
        note = rl.baseline_note(results)
        self.assertEqual(note["pre_existing_failing_tests"], ["t.a", "t.b"])
        self.assertEqual(note["ledger_pre_existing"], {"secret_scan": 3})


class RunEndToEndTestCase(unittest.TestCase):
    def test_green_report_files_and_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, FIXTURE)
            det = kit.fake_det(PY_FILES, repo=tmp,
                               diff={"base": "origin/main", "base_commit": "c", "changed_files": ["pkg/m.py"],
                                     "added": {}, "added_text": {}, "removed": {}, "untracked": []})
            sel = _sel(["secret_scan", "actions_sha_pin", "test_smells", "diff_minimality"], declared_files=["pkg/*", "tests/*"])
            out = os.path.join(tmp, "out")
            runner = kit.FakeRunner(kit.git_ok_rules())
            report = rl.run(tmp, det, sel, out, runner=runner)
            self.assertEqual((report["verdict"], report["exit_code"]), ("green", 0))
            for name in ("report.json", "report.md", "selection.json", "detect.json"):
                self.assertTrue(os.path.exists(os.path.join(out, name)), name)
            self.assertEqual(report["schemaVersion"], 1)
            self.assertEqual([c["status"] for c in report["checks"]], ["pass", "na", "pass", "pass"])
            self.assertEqual(report["source_state"]["commit"], "deadbeef" * 5)
            self.assertEqual(report["tool_versions"]["test-code"], lc.SKILL_VERSION)
            self.assertIn("--selection", report["rerun"])
            self.assertTrue(report["notes"][0].startswith("add `.test-code/reports/`"))
            with open(os.path.join(out, "report.md"), encoding="utf-8") as fh:
                md = fh.read()
            for section in ("## Layers", "## Layers not run", "## Fix first", "## Baseline note", "## Rerun",
                            "**Verdict: GREEN**", "tier 1/5** (user;"):
                self.assertIn(section, md)

    def test_negative_control_failing_test_is_red_and_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, FIXTURE)
            runner = kit.FakeRunner([("unittest", (1, FAILING_OUT, ""))] + kit.git_ok_rules())
            report = rl.run(tmp, kit.fake_det(PY_FILES), _sel(["py_unit", "secret_scan"], tier=2), os.path.join(tmp, "o"),
                            runner=runner)
            self.assertEqual((report["verdict"], report["exit_code"]), ("red", 1))
            self.assertEqual(report["fix_first"][0], {"rank": 1, "kind": "failing test", "item": "tests.test_m.T.test_a",
                                                      "check": "py_unit"})
            self.assertIn("unittest discover", runner.commands()[0])
            self.assertEqual(report["checks"][0]["log"], "logs/py_unit.log")

    def test_incomplete_when_tool_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, FIXTURE)
            report = rl.run(tmp, kit.fake_det(PY_FILES), _sel(["py_lint"]), os.path.join(tmp, "o"),
                            runner=kit.FakeRunner(kit.git_ok_rules()))
            self.assertEqual((report["verdict"], report["exit_code"]), ("incomplete", 3))
            self.assertEqual(report["not_run"]["unavailable"][0]["id"], "py_lint")

    def test_phase_three_sees_phase_two_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, FIXTURE)
            out = os.path.join(tmp, "o")

            def fake_coverage(argv, cwd):
                if "json" in argv:
                    lc.write_json(os.path.join(out, "coverage.json"),
                                  {"totals": {"percent_covered": 90.0},
                                   "files": {"pkg/m.py": {"executed_lines": [1, 2], "missing_lines": []}}})
                return lc.RunResult(0, "Ran 1 test\nOK", "")
            runner = kit.FakeRunner([("coverage", fake_coverage)] + kit.git_ok_rules())
            det = kit.fake_det(PY_FILES, pymods={"coverage": True},
                               diff={"base": "b", "base_commit": "c", "changed_files": ["pkg/m.py"],
                                     "added": {"pkg/m.py": [1, 2]}, "added_text": {}, "removed": {}, "untracked": []})
            report = rl.run(tmp, det, _sel(["diff_coverage", "py_coverage", "secret_scan"], tier=2), out, runner=runner)
            by = {c["id"]: c for c in report["checks"]}
            self.assertEqual(by["diff_coverage"]["status"], "pass")
            self.assertIn("2/2", by["diff_coverage"]["summary"])
            self.assertEqual(by["py_coverage"]["status"], "substituted")  # 无 no-drop 基线 → 永不写 pass
            self.assertEqual(report["verdict"], "incomplete")

    def test_init_baselines_grandfathers_then_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = dict(FIXTURE, **{"pkg/k.py": 'KEY = "%s"\n' % ("AKIA" + "Z" * 16)})
            kit.make_repo(tmp, files)
            det = kit.fake_det(sorted(files))
            runner = kit.FakeRunner(kit.git_ok_rules())
            report = rl.run(tmp, det, _sel(["secret_scan"]), os.path.join(tmp, "o1"), runner=runner, init_baselines=True)
            self.assertEqual(report["verdict"], "green")
            self.assertTrue(report["init_baselines"])
            self.assertIn("grandfathered", report["checks"][0]["summary"])
            report = rl.run(tmp, det, _sel(["secret_scan"]), os.path.join(tmp, "o2"), runner=runner)
            self.assertEqual(report["verdict"], "green")
            self.assertEqual(report["baseline_note"]["ledger_pre_existing"], {"secret_scan": 1})

    def test_render_md_caps_long_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, FIXTURE)
            report = rl.run(tmp, kit.fake_det(PY_FILES), _sel(["secret_scan"]), os.path.join(tmp, "o"),
                            runner=kit.FakeRunner(kit.git_ok_rules()))
            report["fix_first"] = [{"rank": 5, "kind": "k", "item": "i%d" % i, "check": "secret_scan"} for i in range(45)]
            md = rl.render_md(report)
            self.assertIn("and 5 more", md)
            self.assertIn("- r5 [k] i0 (`secret_scan`)", md)


class MainTestCase(unittest.TestCase):
    def test_dry_run_and_usage_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, FIXTURE)
            det_path = os.path.join(tmp, "det.json")
            lc.write_json(det_path, kit.fake_det(PY_FILES))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = rl.main(["--repo", tmp, "--detect", det_path, "--tier", "1", "--dry-run"])
                self.assertEqual(rc, 0)
                self.assertEqual(rl.main(["--repo", tmp, "--detect", det_path]), 2)
                self.assertEqual(rl.main(["--repo", os.path.join(tmp, "nope")]), 2)
                self.assertEqual(rl.main(["--repo", tmp, "--detect", det_path, "--tier", "1", "--checks", "nope"]), 2)
            plan = json.loads(out.getvalue())
            self.assertEqual(plan["tier"], 1)
            self.assertIn("py_compile", [row["id"] for row in plan["plan"]])
            self.assertIn("--tier N is required", err.getvalue())
            self.assertIn("unknown check id", err.getvalue())


if __name__ == "__main__":
    unittest.main()
