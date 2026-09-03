"""§64 UI 对齐契约：Swift 源扫描原语（scripts/ui/ui_common.py）的判例。

三视图等长、注释与字符串互不干扰、插值整体留在字符串里、括号树的调用链能认出
`Button { } label: { Text(L()) }` 这种 trailing-closure 结构——清单提取器的一切
归类都建立在这几条上。
"""
import os
import sys
import tempfile
import unittest

_UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "ui")
if _UI_DIR not in sys.path:
    sys.path.insert(0, _UI_DIR)

import ui_common as uc  # noqa: E402

SAMPLE = '''// header comment with L("no", "no")
struct Demo: View {
    /* block /* nested */ still comment L("x", "y") */
    var body: some View {
        Button {
            doIt()
        } label: {
            Text(L("批准", "Approve"))
        }
        Toggle(L("开关 \\"引号\\"", "Switch \\"quoted\\""), isOn: $on)
        Text(L("请求 (\\(count))", "Ask (\\(count))")).tag("x")
        let s = """
        multi L("in", "triple")
        """
        Picker("", selection: $v) {
            Text(L("关", "Off")).tag("off")
        }
        .help(L("提示", "Hint"))
    }

    private func helper() -> String { "{" }
}
'''


class ScanViewsTestCase(unittest.TestCase):
    def setUp(self):
        self.stripped, self.masked = uc.scan_views(SAMPLE)

    def test_views_are_equal_length_and_keep_newlines(self):
        self.assertEqual(len(self.stripped), len(SAMPLE))
        self.assertEqual(len(self.masked), len(SAMPLE))
        self.assertEqual(self.stripped.count("\n"), SAMPLE.count("\n"))

    def test_comments_are_blanked_but_strings_survive_in_stripped(self):
        self.assertNotIn("header comment", self.stripped)
        self.assertNotIn("nested", self.stripped)
        self.assertIn('"批准"', self.stripped)
        self.assertNotIn("multi L", self.masked)      # 三引号字符串内容在 masked 里被遮
        self.assertIn("multi L", self.stripped)

    def test_brace_inside_string_does_not_break_structure(self):
        self.assertNotIn('"{"', self.masked)
        spans = uc.top_level_spans(self.masked)
        self.assertEqual([(s[0], s[1]) for s in spans], [("struct", "Demo")])

    def test_l_calls_skip_comments_and_unescape(self):
        calls = uc.find_l_calls(self.stripped, self.masked)
        labels = [(zh, en) for _, zh, en in calls]
        self.assertEqual(labels, [
            ("批准", "Approve"),
            ('开关 "引号"', 'Switch "quoted"'),
            ("请求 ({count})", "Ask ({count})"),
            ("关", "Off"),
            ("提示", "Hint"),
        ])

    def test_call_chain_resolves_trailing_label_closure_to_button(self):
        calls = uc.find_l_calls(self.stripped, self.masked)
        tree = uc.BraceTree(self.masked)
        chains = {en: tree.chain(off) for off, _, en in calls}
        self.assertEqual(chains["Approve"][:2], ["Text", "Button"])
        self.assertEqual(chains["Switch \"quoted\""][0], "Toggle")
        self.assertEqual(chains["Off"][:2], ["Text", "Picker"])
        self.assertEqual(chains["Hint"][0], "help")
        self.assertIn("View", chains["Approve"])   # body 的边界可见

    def test_member_spans_and_innermost(self):
        spans = uc.top_level_spans(self.masked)
        members = uc.member_spans(self.masked, spans[0][2], spans[0][3])
        self.assertEqual([m[1] for m in members], ["body", "helper"])
        off = self.stripped.index('"批准"')
        self.assertEqual(uc.innermost(members, off)[1], "body")
        self.assertIsNone(uc.innermost(members, 0))


class HelpersTestCase(unittest.TestCase):
    def test_line_index(self):
        idx = uc.LineIndex("a\nbb\nccc")
        self.assertEqual([idx.line_of(0), idx.line_of(2), idx.line_of(5), idx.line_of(8)], [1, 2, 3, 3])

    def test_match_close_and_open(self):
        text = "f(a, (b)) { x }"
        self.assertEqual(uc.match_close(text, 1), 8)
        self.assertEqual(uc.match_open(text, 8), 1)
        self.assertEqual(uc.match_close("(", 0), -1)
        self.assertEqual(uc.match_open(")", 0), -1)

    def test_unescape_handles_unicode_and_backslashes(self):
        self.assertEqual(uc.unescape_swift("a\\u{1b}b\\\\c\\n"), "a\x1bb\\c\n")

    def test_slugify(self):
        self.assertEqual(uc.slugify("Fix with AI!"), "fix-with-ai")
        self.assertEqual(uc.slugify("回答…"), "回答")
        self.assertEqual(uc.slugify("!!!"), "item")
        self.assertEqual(len(uc.slugify("x" * 80)), 48)

    def test_ledger_parsing_ignores_comments_and_keeps_first_token(self):
        text = "# head\n\ncontrol:a:b  reason with #119 ref\nlane:debt\n  # indented comment\n"
        self.assertEqual(uc.parse_ledger(text), {"control:a:b": "reason with #119 ref", "lane:debt": ""})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ledger.txt")
            self.assertEqual(uc.load_ledger(path), {})
            uc.write_text(path, text)
            self.assertEqual(set(uc.load_ledger(path)), {"control:a:b", "lane:debt"})

    def test_dump_json_is_sorted_and_utf8(self):
        self.assertEqual(uc.dump_json({"b": 1, "a": "中"}), '{\n  "a": "中",\n  "b": 1\n}\n')

    def test_iter_swift_files_missing_dir_is_empty(self):
        self.assertEqual(uc.iter_swift_files("/nonexistent/dir"), [])

    def test_brace_tree_enclosing_before_any_opener(self):
        tree = uc.BraceTree("abc (x) {y}")
        self.assertEqual(tree.enclosing(1), -1)
        self.assertEqual(tree.enclosing(5), 4)
        self.assertEqual(tree.enclosing(10), 8)
        self.assertEqual(tree.chain(1), [])

    def test_decl_span_skips_stored_properties(self):
        masked = "struct A {\n    let x = { 1 }()\n    var y: Int { 2 }\n}\n"
        spans = uc.top_level_spans(masked)
        members = uc.member_spans(masked, spans[0][2], spans[0][3])
        self.assertEqual([m[1] for m in members], ["y"])


if __name__ == "__main__":
    unittest.main()
