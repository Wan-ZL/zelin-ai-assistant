"""test-code skill · complexity_min 判例：计数口径对齐 §58.1、行数上限、账本比较、
CLI 退出码（0 干净 / 1 新违例 / 2 读不到 = fail closed）。

法典：docs/CONTRACT.md §58.1（口径）/ §58.4（账本）；设计 vnext2-plan R2.8。
负控制：已知超线 fixture 必须退出 1；语法错 fixture 必须退出 2 而不是 0。
"""
import contextlib
import io
import json
import os
import tempfile
import textwrap
import unittest

from tests import skill_test_code_testkit  # noqa: F401 - puts skills/test-code/scripts on sys.path

import complexity_min as cm  # noqa: E402


def _cc(source):
    measured = cm.measure_source(textwrap.dedent(source))
    return {qual: cc for qual, cc, _lines, _s, _e in measured["functions"]}


class CountingTestCase(unittest.TestCase):
    def test_straight_line_is_one_and_if_else_is_two(self):
        self.assertEqual(_cc("def f(a):\n    return a"), {"f": 1})
        self.assertEqual(_cc("def f(a):\n    if a:\n        return 1\n    else:\n        return 2"), {"f": 2})

    def test_elif_boolop_loop_except_ternary(self):
        src = """
        def f(a, b, c):
            if a == 1:
                return 1
            elif a and b or c:
                return 2
            for _ in range(3):
                try:
                    pass
                except ValueError:
                    pass
            return 1 if c else 0
        """
        # 1 + if + elif + and + or + for + except + ternary = 8
        self.assertEqual(_cc(src), {"f": 8})

    def test_nested_def_counted_separately_and_with_is_free(self):
        src = """
        def outer(x):
            with open(x) as fh:
                data = fh.read()
            def inner(y):
                if y:
                    return 1
                return 0
            return [d for d in data if d]
        """
        self.assertEqual(_cc(src), {"outer": 2, "outer.inner": 2})

    def test_methods_get_class_qualname(self):
        self.assertEqual(_cc("class C:\n    def m(self):\n        return 1"), {"C.m": 1})

    def test_measure_reports_lines_and_file_lines(self):
        measured = cm.measure_source("def f():\n    return 1\n\n\ndef g():\n    pass\n")
        self.assertEqual(measured["file_lines"], 6)
        self.assertEqual([(q, lines) for q, _cc, lines, _s, _e in measured["functions"]], [("f", 2), ("g", 2)])


class ViolationsTestCase(unittest.TestCase):
    def test_cc_and_lengths_keys(self):
        measured = {"functions": [("f", 12, 5, 1, 5), ("g", 2, 150, 7, 156)], "file_lines": 2000}
        caps = {"max_cc": 10, "max_func_lines": 100, "max_file_lines": 1000}
        self.assertEqual(cm.violations_for("p.py", measured, caps),
                         {"cc:p.py::f": 12.0, "func-lines:p.py::g": 150.0, "file-lines:p.py": 2000.0})
        self.assertEqual(cm.violations_for("p.py", measured, caps, only="cc"), {"cc:p.py::f": 12.0})
        self.assertEqual(set(cm.violations_for("p.py", measured, caps, only="lengths")),
                         {"func-lines:p.py::g", "file-lines:p.py"})

    def test_scan_collects_errors_for_unparseable_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_test_code_testkit.make_repo(tmp, {"ok.py": "def f():\n    return 1\n", "bad.py": "def (:\n",
                                                    "node_modules/skip.py": "def (:\n"})
            violations, errors = cm.scan([tmp], tmp, cm.DEFAULTS)
            self.assertEqual(violations, {})
            self.assertEqual([rel for rel, _ in errors], ["bad.py"])


_BAD = "def hot(a):\n" + "".join("    if a == %d:\n        return %d\n" % (i, i) for i in range(12)) + "    return 0\n"


class CliTestCase(unittest.TestCase):
    def _run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cm.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_clean_tree_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_test_code_testkit.make_repo(tmp, {"pkg/m.py": "def f():\n    return 1\n"})
            rc, out, _ = self._run("--root", tmp, os.path.join(tmp, "pkg"))
            self.assertEqual(rc, 0)
            self.assertIn("complexity_min: OK", out)
            self.assertIn("Bob-strict = 6", out)

    def test_negative_control_known_bad_exits_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_test_code_testkit.make_repo(tmp, {"pkg/m.py": _BAD})
            rc, out, _ = self._run("--root", tmp, "--max-cc", "10", os.path.join(tmp, "pkg"))
            self.assertEqual(rc, 1)
            self.assertIn("NEW cc:pkg/m.py::hot = 13", out)

    def test_negative_control_syntax_error_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_test_code_testkit.make_repo(tmp, {"pkg/m.py": "def (:\n"})
            rc, _, err = self._run("--root", tmp, os.path.join(tmp, "pkg"))
            self.assertEqual(rc, 2)
            self.assertIn("fail closed", err)

    def test_baseline_grandfathers_then_ratchets(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_test_code_testkit.make_repo(tmp, {"pkg/m.py": _BAD})
            ledger = os.path.join(tmp, ".test-code", "baselines", "complexity.txt")
            rc, out, _ = self._run("--root", tmp, "--baseline", ledger, "--write-baseline", os.path.join(tmp, "pkg"))
            self.assertEqual(rc, 0)
            self.assertIn("wrote 1 entries", out)
            rc, _, _ = self._run("--root", tmp, "--baseline", ledger, os.path.join(tmp, "pkg"))
            self.assertEqual(rc, 0)
            skill_test_code_testkit.make_repo(tmp, {"pkg/n.py": _BAD.replace("hot", "hot2")})
            rc, out, _ = self._run("--root", tmp, "--baseline", ledger, os.path.join(tmp, "pkg"))
            self.assertEqual(rc, 1)
            self.assertIn("NEW cc:pkg/n.py::hot2", out)

    def test_write_baseline_without_path_is_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_test_code_testkit.make_repo(tmp, {"pkg/m.py": "x = 1\n"})
            rc, _, err = self._run("--root", tmp, "--write-baseline", os.path.join(tmp, "pkg"))
            self.assertEqual(rc, 2)
            self.assertIn("--baseline", err)

    def test_json_output_and_only_lengths(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_test_code_testkit.make_repo(tmp, {"pkg/m.py": _BAD})
            rc, out, _ = self._run("--root", tmp, "--json", "--only", "lengths", "--max-func-lines", "5",
                                   os.path.join(tmp, "pkg"))
            self.assertEqual(rc, 1)
            payload = json.loads(out)
            self.assertEqual(payload["new"], ["func-lines:pkg/m.py::hot"])
            self.assertEqual(payload["caps"]["max_func_lines"], 5)
            self.assertIn("note", payload)


if __name__ == "__main__":
    unittest.main()
