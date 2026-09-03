"""act/analyze._extract_json — the tolerant balanced-brace scanner (§8).

The expansion agent answers in prose more often than not; the scanner must
find the FIRST ``{...}`` that parses as a dict while staying string-aware:
a ``}`` or an escaped quote inside a JSON string must not end the block.
Pinned here (P3 mutation net — these states had no killing test):

- whole-text fast path vs prose-wrapped object;
- ``}`` / ``{`` inside string values do not change the brace depth;
- ``\\"`` escapes inside strings do not close the string;
- an unbalanced first ``{`` is skipped in favour of the next one;
- a balanced but invalid chunk is skipped in favour of the next ``{``;
- a top-level list is not a dict — its first inner dict wins;
- empty / no-object text -> None.
"""
import json
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import analyze


class ExtractJsonTestCase(unittest.TestCase):
    def test_empty_and_no_object_are_none(self):
        self.assertIsNone(analyze._extract_json(""))
        self.assertIsNone(analyze._extract_json(None))
        self.assertIsNone(analyze._extract_json("no json here at all"))
        self.assertIsNone(analyze._extract_json("[1, 2, 3]"))   # list, no dict

    def test_whole_text_fast_path(self):
        self.assertEqual(analyze._extract_json('  {"a": 1}\n'), {"a": 1})

    def test_object_inside_prose(self):
        text = 'Sure — here:\n\n{"summary": "x", "plan": ["a"]}\n\nDone.'
        self.assertEqual(analyze._extract_json(text),
                         {"summary": "x", "plan": ["a"]})

    def test_braces_inside_strings_do_not_close_the_block(self):
        payload = {"summary": "use {curly} and } braces", "plan": ["{"]}
        text = "prefix " + json.dumps(payload) + " suffix"
        self.assertEqual(analyze._extract_json(text), payload)

    def test_escaped_quote_inside_string_keeps_the_string_open(self):
        payload = {"summary": 'she said \\"go\\" then }', "plan": []}
        text = "note: " + json.dumps(payload)
        self.assertEqual(analyze._extract_json(text), payload)

    def test_unbalanced_first_brace_falls_through_to_the_next(self):
        text = '{ broken start {"ok": true}'
        # first '{' never balances; second '{' does and parses
        self.assertEqual(analyze._extract_json(text), {"ok": True})

    def test_balanced_but_invalid_chunk_is_skipped(self):
        text = "{not json} then {\"ok\": 1}"
        self.assertEqual(analyze._extract_json(text), {"ok": 1})

    def test_top_level_list_yields_its_first_dict(self):
        text = '[{"first": 1}, {"second": 2}]'
        self.assertEqual(analyze._extract_json(text), {"first": 1})

    def test_first_of_several_objects_wins(self):
        text = '{"a": 1} {"b": 2}'
        self.assertEqual(analyze._extract_json(text), {"a": 1})

    def test_balanced_end_helper_reports_minus_one_when_unbalanced(self):
        self.assertEqual(analyze._balanced_end('{"a": 1', 0), -1)
        self.assertEqual(analyze._balanced_end('{"a": 1}', 0), 7)

    def test_step_string_state_machine(self):
        # (in_str, esc) transitions inside a JSON string literal
        self.assertEqual(analyze._step_string("x", esc=True), (True, False))
        self.assertEqual(analyze._step_string("\\", esc=False), (True, True))
        self.assertEqual(analyze._step_string('"', esc=False), (False, False))
        self.assertEqual(analyze._step_string("a", esc=False), (True, False))


if __name__ == "__main__":
    unittest.main()
