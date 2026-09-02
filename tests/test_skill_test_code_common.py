"""test-code skill · ladder_common 判例：账本 shrink-only 语义、TOML 子集 fail-loud、
文本读取（帽 / 二进制）、git 基线解析与 git_lines（fake runner，零子进程）。

法典：docs/CONTRACT.md §58.4（账本语义同源——new/worse 判红，stale 只提示）；
设计 vnext2-plan R2.8。负控制：坏 TOML 行必须抛、new 违例必须判红。
"""
import os
import tempfile
import unittest

from tests import skill_test_code_testkit as kit

lc = kit.lc


class RunResultTestCase(unittest.TestCase):
    def test_ok_requires_rc_zero_and_no_timeout(self):
        self.assertTrue(lc.RunResult(0).ok)
        self.assertFalse(lc.RunResult(1).ok)
        self.assertFalse(lc.RunResult(0, timed_out=True).ok)

    def test_text_joins_stdout_and_stderr(self):
        self.assertEqual(lc.RunResult(0, "out", "err").text(), "out\nerr")
        self.assertEqual(lc.RunResult(0, "out", "").text(), "out")


class LedgerTestCase(unittest.TestCase):
    def test_ledger_line_shapes(self):
        self.assertIsNone(lc.ledger_line("# comment"))
        self.assertIsNone(lc.ledger_line("   "))
        self.assertEqual(lc.ledger_line("a::b"), ("a::b", 1.0))
        self.assertEqual(lc.ledger_line("a::b 12.5 # note"), ("a::b", 12.5))
        self.assertEqual(lc.ledger_line("k#2 3"), ("k#2", 3.0))

    def test_load_missing_is_empty(self):
        self.assertEqual(lc.load_ledger(None), {})
        self.assertEqual(lc.load_ledger("/nonexistent/ledger.txt"), {})

    def test_write_then_load_round_trips_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "l.txt")
            n = lc.write_ledger(path, {"b": 2, "a": 1.5}, "hdr")
            self.assertEqual(n, 2)
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
            self.assertTrue(body.startswith("# hdr\n"))
            self.assertLess(body.index("a 1.5"), body.index("b 2"))
            self.assertEqual(lc.load_ledger(path), {"a": 1.5, "b": 2.0})

    def test_compare_new_is_red(self):
        result = lc.compare_ledger({"x": 1.0}, {})
        self.assertEqual(result["new"], ["x"])
        self.assertFalse(result["ok"])

    def test_compare_worse_is_red_equal_is_green(self):
        self.assertFalse(lc.compare_ledger({"x": 9.0}, {"x": 8.0})["ok"])
        self.assertTrue(lc.compare_ledger({"x": 8.0}, {"x": 8.0})["ok"])
        self.assertTrue(lc.compare_ledger({"x": 7.0}, {"x": 8.0})["ok"])

    def test_compare_stale_is_advisory_only(self):
        result = lc.compare_ledger({}, {"gone": 1.0})
        self.assertEqual(result["stale"], ["gone"])
        self.assertTrue(result["ok"])

    def test_format_value(self):
        self.assertEqual(lc.format_value(3.0), "3")
        self.assertEqual(lc.format_value(3.25), "3.2")


class TomlSubsetTestCase(unittest.TestCase):
    def test_parses_sections_and_scalars(self):
        text = '# c\n[a]\nx = 1\ny = 2.5 # trailing\nz = true\ns = "str"\n\n[b.c]\nq = false\n'
        self.assertEqual(lc.parse_toml_subset(text),
                         {"a": {"x": 1, "y": 2.5, "z": True, "s": "str"}, "b.c": {"q": False}})

    def test_garbage_line_fails_loud(self):
        with self.assertRaises(ValueError):
            lc.parse_toml_subset("[a]\nthis is not toml\n")

    def test_key_before_section_fails_loud(self):
        with self.assertRaises(ValueError):
            lc.parse_toml_subset("x = 1\n")

    def test_array_value_fails_loud(self):
        with self.assertRaises(ValueError):
            lc.parse_toml_subset('[a]\nx = ["not", "supported"]\n')


class ReadTextTestCase(unittest.TestCase):
    def test_text_binary_cap_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"t.txt": "hello\n"})
            with open(os.path.join(tmp, "b.bin"), "wb") as fh:
                fh.write(b"\x00\x01binary")
            self.assertEqual(lc.read_text(os.path.join(tmp, "t.txt")), "hello\n")
            self.assertIsNone(lc.read_text(os.path.join(tmp, "b.bin")))
            self.assertIsNone(lc.read_text(os.path.join(tmp, "t.txt"), cap=2))
            with self.assertRaises(OSError):
                lc.read_text(os.path.join(tmp, "missing.txt"))
            self.assertEqual(lc.read_text_or_empty(os.path.join(tmp, "missing.txt")), "")

    def test_walk_files_skips_dependency_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"a/z.py": "", "a/b.py": "", "node_modules/x.js": "", ".git/HEAD": "",
                                "build/out.txt": ""})
            self.assertEqual(list(lc.walk_files(tmp)), ["a/b.py", "a/z.py"])


class GitHelpersTestCase(unittest.TestCase):
    def test_git_lines_none_on_failure_and_drops_blank(self):
        runner = kit.FakeRunner([("ls-files", (0, "a\n\nb\n", ""))], default=(128, "", "fatal"))
        self.assertEqual(lc.git_lines(runner, "/r", ["ls-files"]), ["a", "b"])
        self.assertIsNone(lc.git_lines(runner, "/r", ["status"]))

    def test_resolve_base_prefers_request_then_falls_back(self):
        runner = kit.FakeRunner([(lambda argv: argv[-1] == "main^{commit}", (0, "", ""))], default=(1, "", ""))
        self.assertEqual(lc.resolve_base(runner, "/r", None), "main")
        runner = kit.FakeRunner([("release^{commit}", (0, "", ""))], default=(1, "", ""))
        self.assertEqual(lc.resolve_base(runner, "/r", "release"), "release")
        self.assertIsNone(lc.resolve_base(kit.FakeRunner(default=(1, "", "")), "/r", None))
        self.assertIsNone(lc.resolve_base(kit.FakeRunner(default=(1, "", "")), "/r", "nope"))

    def test_json_round_trip_and_stamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x", "y.json")
            lc.write_json(path, {"b": 1, "a": [1, 2]})
            self.assertEqual(lc.read_json(path), {"a": [1, 2], "b": 1})
        self.assertRegex(lc.utc_stamp(), r"^\d{8}T\d{6}Z$")
        self.assertIn("T", lc.utc_iso())


if __name__ == "__main__":
    unittest.main()
