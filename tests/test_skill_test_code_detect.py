"""test-code skill · detect 判例：技术栈/布局探测、阈值来源（项目 gates.toml > pyproject >
skill 默认值，坏文件 fail closed）、变异靶区解析、`git diff -U0` 解析（含 merge-base）、
触发器与 tier 推荐、端到端 detect()（FakeRunner 扮 git，零子进程）。

法典：docs/CONTRACT.md §58（阈值单源只读）/ §57（靶区 = 宪法模块）；设计 vnext2-plan
R2.8 / D14。负控制：坏 gates.toml 必须让 main 退出 2 而不是带着猜出来的阈值继续。
"""
import contextlib
import io
import json
import os
import tempfile
import unittest

from tests import skill_test_code_testkit as kit

import checks  # noqa: E402
import detect  # noqa: E402

lc = kit.lc

GATES = "[complexity]\nmax = 6\n\n[crap]\nmax = 6.0\ntolerance = 0.5\n\n[hygiene]\nmax_function_lines = 300\nmax_file_lines = 2000\n"
FIXTURE = {
    "pkg/__init__.py": "", "pkg/mod.py": "def f():\n    return 1\n",
    "tests/conftest.py": "", "tests/test_a.py": "from hypothesis import given\n",
    "tests/integration/__init__.py": "", "tests/integration/test_b.py": "",
    "requirements-dev.txt": "coverage\n", "script.sh": "#!/bin/sh\n",
    "web/package.json": json.dumps({"devDependencies": {"vitest": "^1", "@vitest/coverage-v8": "1"},
                                    "scripts": {"test": "vitest run", "test:e2e": "x"}}),
    "web/tsconfig.json": "{}", "web/package-lock.json": "{}", "web/node_modules/.bin/vitest": "",
    "web/vite.config.ts": "export default { test: { coverage: { thresholds: { lines: 80 } } } }\n",
    "ios/App.xcodeproj/xcshareddata/xcschemes/App.xcscheme": "<Scheme/>",
    "mac/Lib/Package.swift": "// swift-tools-version:5.9\n", "mac/Lib/Sources/a.swift": "",
    ".github/workflows/ci.yml": "on: push\n",
}
DIFF = """diff --git a/p.py b/p.py
index 111..222 100644
--- a/p.py
+++ b/p.py
@@ -1,2 +1,3 @@
+import os
+x = 1
-old
+y = 2
@@ -10 +11,0 @@
-gone
\\ No newline at end of file
diff --git a/del.py b/del.py
deleted file mode 100644
--- a/del.py
+++ /dev/null
@@ -1 +0,0 @@
-bye
diff --git a/q.sql b/q.sql
--- a/q.sql
+++ b/q.sql
@@ -1 +1 @@
--- a sql comment
+-- new comment
"""


class StacksAndLayoutTestCase(unittest.TestCase):
    def test_detect_stacks(self):
        self.assertEqual(detect.detect_stacks(sorted(FIXTURE)), ["python", "js", "swift", "shell", "actions"])
        self.assertEqual(detect.detect_stacks(["go.mod", "Cargo.toml", "x.sql", "pom.xml", "build.sbt"]),
                         ["go", "rust", "java", "scala", "sql"])
        self.assertEqual(detect.detect_stacks([]), [])

    def test_detect_layout_on_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, FIXTURE)
            files = [f for f in lc.walk_files(tmp)]
            layout = detect.detect_layout(tmp, files)
            self.assertEqual((layout["tests_dir"], layout["integration_dir"], layout["py_runner"]),
                             ("tests", "tests/integration", "pytest"))
            self.assertEqual(layout["property_tests"], ["tests/test_a.py"])
            self.assertEqual(layout["requirements"], ["requirements-dev.txt"])
            self.assertEqual((layout["py_roots"], layout["py_src_roots"]), (["pkg", "tests"], ["pkg"]))
            pkg = layout["js_packages"][0]
            self.assertEqual((pkg["dir"], pkg["tsconfig"], pkg["test_runner"], pkg["bins"], pkg["lock"]),
                             ("web", True, "vitest", ["vitest"], "package-lock.json"))
            self.assertTrue(pkg["coverage_provider"] and pkg["coverage_thresholds"])
            self.assertIn("test:e2e", pkg["scripts"])
            self.assertEqual(layout["swift"], {"package_dir": "mac/Lib",
                                               "schemes": [{"scheme": "App", "dir": "ios", "container": "App.xcodeproj"}]})
            self.assertFalse(layout["importlinter"] or layout["mutmut"] or layout["qlty"])

    def test_unittest_default_and_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"a.py": "", "tests/test_a.py": "", ".importlinter": "", ".qlty/qlty.toml": "",
                                "benchmarks/b.py": "", "fuzz/f.py": "",
                                "pyproject.toml": "[tool.mutmut]\n[tool.ruff.format]\n"})
            layout = detect.detect_layout(tmp, list(lc.walk_files(tmp)))
            self.assertEqual(layout["py_runner"], "unittest")
            self.assertEqual(layout["py_format"], "ruff")
            self.assertTrue(all(layout[k] for k in ("importlinter", "mutmut", "qlty", "benchmarks", "fuzz_dir")))
            self.assertIn("a.py", layout["py_roots"])


class ThresholdsTestCase(unittest.TestCase):
    def test_project_gates_win(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"qa/gates.toml": GATES, "qa/coverage_floor.txt": "83.2\n"})
            thr = detect.detect_thresholds(tmp)
            self.assertEqual((thr["complexity_max"], thr["crap_max"], thr["crap_tolerance"]), (6, 6.0, 0.5))
            self.assertEqual((thr["max_function_lines"], thr["max_file_lines"]), (300, 2000))
            self.assertEqual((thr["source"], thr["coverage"]), ("qa/gates.toml", "floor:qa/coverage_floor.txt"))

    def test_pyproject_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"pyproject.toml": "[tool.ruff.lint.mccabe]\nmax-complexity = 8\n"
                                                  "[tool.coverage.report]\nfail_under = 85\n"})
            thr = detect.detect_thresholds(tmp)
            self.assertEqual((thr["complexity_max"], thr["source"]), (8, "pyproject.toml"))
            self.assertEqual(thr["coverage"], "floor:pyproject.toml fail_under=85")
        with tempfile.TemporaryDirectory() as tmp:
            thr = detect.detect_thresholds(tmp)
            self.assertEqual((thr["complexity_max"], thr["crap_max"], thr["source"]), (10, 30.0, "skill-defaults"))
            self.assertIn("Bob-strict = 6", thr["note"])

    def test_negative_control_broken_gates_fail_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"qa/gates.toml": "[complexity]\nmax = [6]\n"})
            with self.assertRaises(ValueError):
                detect.detect_thresholds(tmp)

    def test_mutation_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"qa/mutation_targets.toml":
                                '[config]\ntime_budget_seconds = 1\n\n[targets]\n"act/lib/a.py" = ["t"]\n'
                                '# note\n"act/lib/b.py" = []\n'})
            self.assertEqual(detect.mutation_targets(tmp), ["act/lib/a.py", "act/lib/b.py"])
            self.assertEqual(detect.mutation_targets(os.path.join(tmp, "none")), [])


class DiffTestCase(unittest.TestCase):
    def test_parser_added_removed_and_header_state(self):
        parser = detect.DiffParser()
        for raw in DIFF.splitlines():
            parser.feed(raw)
        self.assertEqual(parser.added["p.py"], [1, 2, 3])
        self.assertEqual(parser.added_text["p.py"], ["import os", "x = 1", "y = 2"])
        self.assertEqual(parser.removed["p.py"], ["old", "gone"])
        self.assertNotIn("del.py", parser.added)
        self.assertEqual(parser.removed["q.sql"], ["-- a sql comment"])
        self.assertEqual(parser.added_text["q.sql"], ["-- new comment"])

    def test_detect_diff_uses_merge_base_and_adds_untracked(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"new.py": "a = 1\nb = 2\n"})
            runner = kit.FakeRunner(kit.git_ok_rules(diff_text=DIFF))
            diff = detect.detect_diff(runner, tmp, None, ["new.py"])
            self.assertEqual((diff["base"], diff["base_commit"]), ("origin/main", "cafebabe" * 5))
            diff_calls = [c["argv"] for c in runner.calls if "-U0" in c["argv"]]
            self.assertEqual(diff_calls[0][-1], "cafebabe" * 5)
            self.assertEqual(diff["changed_files"], ["new.py", "p.py", "q.sql"])
            self.assertEqual(diff["added"]["new.py"], [1, 2])
            self.assertEqual(diff["untracked"], ["new.py"])

    def test_no_base_means_empty_diff(self):
        runner = kit.FakeRunner(default=(1, "", ""))
        diff = detect.detect_diff(runner, "/r", None, [])
        self.assertEqual((diff["base"], diff["changed_files"], diff["added"]), (None, [], {}))


class TriggersAndRecommendTestCase(unittest.TestCase):
    def _diff(self, changed, added_text=None):
        added = {p: list(range(1, len(lines) + 1)) for p, lines in (added_text or {}).items()}
        return {"base": "b", "base_commit": "c", "changed_files": changed, "added": added,
                "added_text": added_text or {}, "removed": {}, "untracked": []}

    def test_triggers_from_text_and_names(self):
        diff = self._diff(["a.py", "README.md", "requirements.txt"],
                          {"a.py": ["conn = sqlite3.connect(p)", "t = threading.Thread()", "print(1)"]})
        trig = detect.detect_triggers(diff)
        self.assertEqual([t["id"] for t in trig], ["concurrency", "deps_changed", "documented_behavior", "persisted_state"])
        by = {t["id"]: t for t in trig}
        self.assertEqual(by["persisted_state"]["evidence"], ["a.py:1: conn = sqlite3.connect(p)"])
        self.assertEqual(by["deps_changed"]["evidence"], ["requirements.txt: (manifest changed)"])
        self.assertEqual(detect.detect_triggers(self._diff([])), [])

    def test_recommend_rules(self):
        def rec(changed, fired=(), targets=()):
            det = {"diff": self._diff(changed), "triggers": [{"id": f} for f in fired], "mutation_targets": list(targets)}
            return detect.recommend(det)["tier"]
        self.assertEqual(rec([]), 2)
        self.assertEqual(rec(["docs/a.md", "README"]), 1)
        self.assertEqual(rec(["a.py"], fired=["persisted_state"]), 4)
        self.assertEqual(rec(["act/lib/x.py"], targets=["act/lib/x.py"]), 4)
        self.assertEqual(rec(["a.py"], fired=["boundary"]), 3)
        self.assertEqual(rec(["f%d.py" % i for i in range(16)]), 3)
        self.assertEqual(rec(["a.py", "b.py"]), 2)


class ProbesAndFilesTestCase(unittest.TestCase):
    def test_probe_pymods(self):
        runner = kit.FakeRunner([("importlib.util", (0, '{"coverage": true, "pytest": false}\n', ""))])
        self.assertEqual(detect.probe_pymods(runner, "py"), {"coverage": True, "pytest": False})
        self.assertFalse(any(detect.probe_pymods(kit.FakeRunner(default=(1, "", "")), "py").values()))
        self.assertFalse(any(detect.probe_pymods(kit.FakeRunner(default=(0, "garbage", "")), "py").values()))

    def test_probe_tools_uses_injected_which(self):
        tools = detect.probe_tools(which=lambda name: "/bin/" + name if name == "git" else None)
        self.assertEqual(tools["git"], "/bin/git")
        self.assertIsNone(tools["ruff"])

    def test_list_files_git_and_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"a.py": "", "u.py": "", "node_modules/x.js": ""})
            files, untracked, is_git = detect.list_files(kit.FakeRunner(default=(128, "", "fatal")), tmp)
            self.assertEqual((files, untracked, is_git), (["a.py", "u.py"], [], False))
            runner = kit.FakeRunner([("ls-files --others", (0, "u.py\n", "")), ("ls-files", (0, "a.py\nmissing.py\n", ""))])
            files, untracked, is_git = detect.list_files(runner, tmp)
            self.assertEqual((files, untracked, is_git), (["a.py", "u.py"], ["u.py"], True))


class EndToEndTestCase(unittest.TestCase):
    def _runner(self, tracked, diff_text=""):
        rules = kit.git_ok_rules(diff_text=diff_text, tracked=tracked)
        rules.append(("importlib.util", (0, json.dumps({m: False for m in detect.PYMODS}), "")))
        return kit.FakeRunner(rules)

    def test_detect_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, dict(FIXTURE, **{"qa/gates.toml": GATES}))
            tracked = [f for f in lc.walk_files(tmp)]
            det = detect.detect(tmp, runner=self._runner(tracked), which=lambda n: None, py="py3")
            self.assertEqual(det["schemaVersion"], 1)
            self.assertTrue(det["is_git"])
            self.assertEqual(det["thresholds"]["source"], "qa/gates.toml")
            self.assertEqual(len(det["menu"]), len(checks.CATALOG))
            self.assertEqual(det["diff"]["changed_files"], [])
            self.assertEqual(det["recommendation"]["tier"], 2)
            self.assertEqual(det["python"], "py3")
            json.dumps(det)  # JSON-able

    def test_main_writes_out_and_fails_closed_on_bad_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"a.py": ""})
            out = os.path.join(tmp, "det.json")
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                self.assertEqual(detect.main(["--repo", os.path.join(tmp, "nope")]), 2)
                kit.make_repo(tmp, {"qa/gates.toml": "[complexity]\nmax = [6]\n"})
                self.assertEqual(detect.main(["--repo", tmp, "--out", out]), 2)
            self.assertIn("fail closed", err.getvalue())
            self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
