"""changelog.d/ fragment 形状判例（CONTRACT §56.7）：scripts/ci/changelog_fragments.py。

  - parse_fragment：首个非空行 `type: <kind>`（六个 Keep a Changelog 组，不分大小写）、
    其后全部顶格 bullet、缩进行归上一条；文件名 kebab-case `.md`；散文行 / 无 bullet /
    坏 type / 坏名 都是问题，且一次报全；
  - load_fragments：README.md 与点文件跳过、子目录是问题、不存在的目录 = 空；按文件名排序；
  - section_lines：按 TYPES 顺序拼 `### Title` + 条目 + 空行，空组不出现；
  - blob_sha 与 git hash-object 逐字节一致（空 blob / "hello\\n" 两个已知值）；
  - CLI：check 坏 → ::error:: + rc 1，好 → rc 0；render 打拼好的段落、坏 fragment 只 warning。
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

_CI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "ci")
if _CI_DIR not in sys.path:
    sys.path.insert(0, _CI_DIR)
import changelog_fragments as cf  # noqa: E402

GOOD = "type: added\n- **thing**: does X\n  continues here\n  - sub item\n- second entry\n"


class ParseTestCase(unittest.TestCase):
    def test_good_fragment(self):
        frag, problems = cf.parse_fragment("my-feature.md", GOOD)
        self.assertEqual(problems, [])
        self.assertEqual(frag.kind, "added")
        self.assertEqual(frag.blocks, [["- **thing**: does X", "  continues here", "  - sub item"], ["- second entry"]])

    def test_type_is_case_insensitive_and_may_follow_blank_lines(self):
        frag, problems = cf.parse_fragment("x.md", "\n\nType: FIXED\n* star bullet\n")
        self.assertEqual((problems, frag.kind, frag.blocks), ([], "fixed", [["* star bullet"]]))

    def test_all_six_kinds_accepted(self):
        for kind in ("added", "changed", "deprecated", "removed", "fixed", "security"):
            frag, problems = cf.parse_fragment("k.md", "type: %s\n- x\n" % kind)
            self.assertEqual(problems, [], kind)
            self.assertEqual(frag.kind, kind)

    def test_bad_type_missing_type_and_no_bullets_each_reported(self):
        _, problems = cf.parse_fragment("x.md", "type: improved\n- x\n")
        self.assertEqual(len(problems), 1)
        self.assertIn("type:", problems[0])
        _, problems = cf.parse_fragment("x.md", "- bullet without a type line\n")
        self.assertTrue(any("type:" in p for p in problems), problems)
        _, problems = cf.parse_fragment("x.md", "type: added\n")
        self.assertTrue(any("no entries" in p for p in problems), problems)

    def test_loose_prose_is_a_problem(self):
        _, problems = cf.parse_fragment("x.md", "type: added\nThis is prose, not a bullet\n- ok\n")
        self.assertEqual(len(problems), 1)
        self.assertIn("loose line", problems[0])

    def test_indented_line_before_any_bullet_is_loose(self):
        _, problems = cf.parse_fragment("x.md", "type: added\n  indented orphan\n- ok\n")
        self.assertTrue(any("loose line" in p for p in problems), problems)

    def test_file_name_must_be_kebab_case_md(self):
        for bad in ("My-Feature.md", "my_feature.md", "feature.txt", "-lead.md", "a--b.md", "notes"):
            _, problems = cf.parse_fragment(bad, GOOD)
            self.assertTrue(any("kebab-case" in p for p in problems), bad)
        for good in ("a.md", "ci-changelog-fragments.md", "v2-fix-3.md"):
            _, problems = cf.parse_fragment(good, GOOD)
            self.assertEqual(problems, [], good)

    def test_all_problems_reported_together(self):
        frag, problems = cf.parse_fragment("Bad Name.md", "type: nope\nprose\n")
        self.assertIsNone(frag)
        self.assertEqual(len(problems), 4, problems)  # name, type, loose line, no entries


class DirectoryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cf-")

    def write(self, name, text):
        with open(os.path.join(self.tmp, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_load_sorted_skips_readme_and_dotfiles_flags_subdirs(self):
        self.write("zeta.md", "type: fixed\n- z\n")
        self.write("alpha.md", "type: added\n- a\n")
        self.write("README.md", "# docs, not a fragment\n")
        self.write(".gitkeep", "")
        os.mkdir(os.path.join(self.tmp, "nested"))
        fragments, problems = cf.load_fragments(self.tmp)
        self.assertEqual([f.name for f in fragments], ["alpha.md", "zeta.md"])
        self.assertEqual(len(problems), 1)
        self.assertIn("nested: not a file", problems[0])

    def test_bad_fragment_reported_with_name_and_others_still_load(self):
        self.write("good.md", "type: added\n- a\n")
        self.write("bad.md", "type: wrong\n- b\n")
        fragments, problems = cf.load_fragments(self.tmp)
        self.assertEqual([f.name for f in fragments], ["good.md"])
        self.assertTrue(problems[0].startswith("bad.md: "), problems)

    def test_missing_dir_is_empty(self):
        self.assertEqual(cf.load_fragments(os.path.join(self.tmp, "nope")), ([], []))
        self.assertEqual(cf.fragment_names(os.path.join(self.tmp, "nope")), [])


class SectionLinesTestCase(unittest.TestCase):
    def test_groups_in_canonical_order_with_blank_separators(self):
        fixed = cf.parse_fragment("a-fix.md", "type: fixed\n- f1\n")[0]
        added = cf.parse_fragment("b-add.md", "type: added\n- a1\n  more\n")[0]
        added2 = cf.parse_fragment("c-add.md", "type: added\n- a2\n")[0]
        lines = cf.section_lines([fixed, added, added2])
        self.assertEqual(lines, ["### Added", "- a1", "  more", "- a2", "", "### Fixed", "- f1", ""])

    def test_empty(self):
        self.assertEqual(cf.section_lines([]), [])

    def test_title_for(self):
        self.assertEqual([cf.title_for(k) for k in cf.TYPES],
                         ["Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"])


class BlobShaTestCase(unittest.TestCase):
    def test_matches_git_hash_object(self):
        # `printf '' | git hash-object --stdin` and `printf 'hello\n' | git hash-object --stdin`
        self.assertEqual(cf.blob_sha(b""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")
        self.assertEqual(cf.blob_sha(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cf-cli-")

    def write(self, name, text):
        with open(os.path.join(self.tmp, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stderr(err):
            rc = cf.main(argv, stdout=out)
        return rc, out.getvalue(), err.getvalue()

    def test_check_ok_and_fail(self):
        self.write("ok.md", "type: changed\n- c\n")
        rc, out, _ = self.run_main(["check", self.tmp])
        self.assertEqual((rc, out), (0, "changelog fragments: ok (1)\n"))
        self.write("bad.md", "nope\n")
        rc, out, _ = self.run_main(["check", self.tmp])
        self.assertEqual(rc, 1)
        self.assertIn("::error::changelog.d/bad.md: ", out)
        self.assertIn("FAIL", out)

    def test_render_prints_section_and_only_warns_on_bad(self):
        self.write("ok.md", "type: security\n- s\n")
        self.write("bad.md", "type: nope\n")
        rc, out, err = self.run_main(["render", self.tmp])
        self.assertEqual((rc, out), (0, "### Security\n- s\n"))
        self.assertIn("::warning::changelog.d/bad.md", err)

    def test_render_empty_dir_prints_nothing(self):
        rc, out, _ = self.run_main(["render", self.tmp])
        self.assertEqual((rc, out), (0, ""))


if __name__ == "__main__":
    unittest.main()
