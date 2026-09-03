"""ask — the section scorer, answer parser and CLI split out in P3b (§27).

Pins: section splitting (headingless prefix, empty bodies), the score table
(heading ×3, body occurrences capped at 5), the answer parser's four exits
(non-zero rc with stderr/stdout/neither, JSON answer with/without citation,
prose fallback, empty output), the runner failure classification, and
``_main``'s exit codes.
"""
import io
import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import ask


class SectionsTestCase(unittest.TestCase):
    def test_split_sections(self):
        text = "intro line\n# A\nbody a\n## B\n\n### C\nbody c\n"
        self.assertEqual(ask._split_sections(text),
                         [("", "intro line"), ("A", "body a"), ("B", ""), ("C", "body c")])
        self.assertEqual(ask._split_sections(""), [])
        self.assertEqual(ask._split_sections("# only\n"), [("only", "")])
        self.assertEqual(ask._split_sections("##### five\nx"), [("", "##### five\nx")])

    def test_section_score(self):
        toks = {"gmail", "token"}
        self.assertEqual(ask._section_score(toks, "Gmail setup", ""), 0)
        self.assertEqual(ask._section_score(toks, "Gmail setup", "token token token token token token token"),
                         3 + 5)
        self.assertEqual(ask._section_score(toks, "misc", "nothing here"), 0)
        self.assertEqual(ask._section_score({"x"}, "X", "x"), 3 + 1)

    def test_scored_sections_and_relevant(self):
        corpus = [("a.md", "# Gmail\ntoken here\n# Other\nnope\n"), ("b.md", "# Z\ngmail gmail\n")]
        scored = ask._scored_sections({"gmail"}, corpus)
        self.assertEqual(sorted((s, r, h) for s, r, h, _ in scored), [(2, "b.md", "Z"), (3, "a.md", "Gmail")])
        self.assertEqual(ask.relevant_sections("gmail", corpus, top=1), [("a.md", "Gmail", "token here")])


class AnswerParserTestCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(ask.analytics, "log_event")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_proc_streams(self):
        self.assertEqual(ask._proc_streams(SimpleNamespace()), (1, "", ""))
        self.assertEqual(ask._proc_streams(SimpleNamespace(returncode=0, stdout=" a ", stderr=None)),
                         (0, "a", ""))

    def test_rc_failure_message_precedence(self):
        with mock.patch.object(ask.failures, "classify", return_value="fid"):
            self.assertEqual(ask._rc_failure(3, "out", "err", 1.0)["error"], "err")
            self.assertEqual(ask._rc_failure(3, "out", "", 1.0)["error"], "out")
            res = ask._rc_failure(3, "", "", 1.26)
        self.assertEqual((res["error"], res["failure_id"], res["elapsed_s"]), ("claude -p exited 3", "fid", 1.3))

    def test_has_json_answer(self):
        self.assertTrue(ask._has_json_answer({"answer": " yes "}))
        self.assertFalse(ask._has_json_answer({"answer": "  "}))
        self.assertFalse(ask._has_json_answer({"answer": None}))
        self.assertFalse(ask._has_json_answer(["answer"]))

    def test_parse_answer_exits(self):
        proc = SimpleNamespace(returncode=0, stdout=json.dumps({"answer": " A ", "citation": " c "}), stderr="")
        self.assertEqual(ask._parse_answer(proc, 1.0, "zh"), ("A", "c", None))
        proc = SimpleNamespace(returncode=0, stdout=json.dumps({"answer": "A", "citation": ""}), stderr="")
        self.assertEqual(ask._parse_answer(proc, 1.0, "zh"), ("A", None, None))
        proc = SimpleNamespace(returncode=0, stdout="plain prose", stderr="")
        self.assertEqual(ask._parse_answer(proc, 1.0, "zh"), ("plain prose", None, None))
        proc = SimpleNamespace(returncode=0, stdout="   ", stderr="")
        text, cit, failure = ask._parse_answer(proc, 1.0, "en")
        self.assertEqual((text, cit, failure["ok"]), (None, None, False))
        self.assertIn("empty answer", failure["error"])
        proc = SimpleNamespace(returncode=2, stdout="", stderr="boom")
        self.assertEqual(ask._parse_answer(proc, 1.0, "en")[2]["error"], "boom")

    def test_run_model_failures(self):
        cfg = SimpleNamespace()
        with mock.patch.object(ask, "build_bundle", return_value="b"):
            def timeout(_p):
                raise subprocess.TimeoutExpired("claude", ask.ASK_TIMEOUT)

            proc, failure = ask._run_model("q", cfg, "en", timeout, 0.0)
            self.assertIsNone(proc)
            self.assertTrue(failure["timeout"])

            def spawn(_p):
                raise FileNotFoundError("claude: not found")

            proc, failure = ask._run_model("q", cfg, "en", spawn, 0.0)
            self.assertFalse(failure["timeout"])
            self.assertIn("not found", failure["error"])
            proc, failure = ask._run_model("q", cfg, "en", lambda p: "PROC", 0.0)
            self.assertEqual((proc, failure), ("PROC", None))

    def test_precheck(self):
        self.assertIsNone(ask._precheck(SimpleNamespace(ask_enabled=True), "q", "en", 0.0))
        self.assertTrue(ask._precheck(SimpleNamespace(ask_enabled=False), "q", "en", 0.0)["disabled"])
        empty = ask._precheck(SimpleNamespace(ask_enabled=True), "", "zh", 0.0)
        self.assertNotIn("disabled", empty)
        self.assertIn("问题是空的", empty["error"])


class CliTestCase(unittest.TestCase):
    def test_main_exit_codes(self):
        with mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(ask._main([]), 2)
        self.assertIn("usage", json.loads(out.getvalue())["error"])
        with mock.patch.object(ask, "answer", return_value={"ok": True}), \
                mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(ask._main(["q"]), 0)
        with mock.patch.object(ask, "answer", return_value={"ok": False, "disabled": True}), \
                mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(ask._main(["q"]), 2)
        with mock.patch.object(ask, "answer", return_value={"ok": False}), \
                mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(ask._main(["a", "b"]), 1)


if __name__ == "__main__":
    unittest.main()
