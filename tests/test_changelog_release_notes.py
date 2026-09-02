"""Release 正文提取判例（CONTRACT §56.2 / §56.7）：changelog.d fragments ∪ CHANGELOG legacy [Unreleased]
相对上一个 tag 树的增量。

  - unreleased_section 只取 `## [Unreleased]` 到下一个 `## ` 之间；没有该段 → []；
  - release_notes：上一个 tag 的 [Unreleased] / fragments 里已有的**条目**去掉（整块比较），
    `### 组` 按 Keep a Changelog 顺序（Added / Changed / Deprecated / Removed / Fixed / Security）
    合并同名组、空组消失、未知组排在已知组之后、裸条目最前；
  - fragments 的条目并入同名组（legacy 在前、fragments 在后）；上一版树里已有的 fragment 条目剔除
    ——晚清、不清 fragment 都不会发两次；
  - 没有 previous（首个 release）= 全部；两份一样 = 空串；
  - main()：文件不存在的 --previous / 目录不存在的 --fragments 当空；坏 fragment 只 ::warning:: 跳过；
    stdout 末尾恰一个换行、空正文零输出。
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_CI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "ci")
if _CI_DIR not in sys.path:
    sys.path.insert(0, _CI_DIR)
import changelog_fragments as cf  # noqa: E402
import changelog_release_notes as crn  # noqa: E402

PREV = """# Changelog

## Releasing

Some procedure text mentioning `## [Unreleased]` in backticks.

## [Unreleased]

### Changed
- old entry A
- old entry B

### Fixed
- old fix

## [0.48.16] - 2026-09-02

### Fixed
- released thing
"""

CURR = """# Changelog

## Releasing

Some procedure text mentioning `## [Unreleased]` in backticks.

## [Unreleased]

### Added
- new feature X

### Changed
- old entry A
- new entry C
- old entry B

### Fixed
- old fix

## [0.48.16] - 2026-09-02

### Fixed
- released thing
"""


class SectionTestCase(unittest.TestCase):
    def test_section_bounds(self):
        lines = crn.unreleased_section(PREV)
        self.assertIn("- old entry A", lines)
        self.assertIn("### Fixed", lines)
        self.assertNotIn("- released thing", lines)
        self.assertNotIn("## [Unreleased]", lines)

    def test_backticked_mention_is_not_a_heading(self):
        self.assertNotIn("Some procedure text mentioning `## [Unreleased]` in backticks.",
                         crn.unreleased_section(PREV))

    def test_missing_section(self):
        self.assertEqual(crn.unreleased_section("# nothing\n\n## [0.1.0]\n- x\n"), [])
        self.assertEqual(crn.unreleased_section(""), [])

    def test_case_insensitive_heading(self):
        self.assertEqual(crn.unreleased_section("## [unreleased]\n- a\n"), ["- a"])


class DeltaTestCase(unittest.TestCase):
    def test_delta_keeps_new_lines_and_drops_empty_groups(self):
        body = crn.release_notes(CURR, PREV)
        self.assertEqual(body, "### Added\n- new feature X\n\n### Changed\n- new entry C")

    def test_no_previous_is_whole_section(self):
        body = crn.release_notes(PREV, "")
        self.assertTrue(body.startswith("### Changed\n- old entry A"))
        self.assertIn("### Fixed\n- old fix", body)

    def test_identical_is_empty(self):
        self.assertEqual(crn.release_notes(PREV, PREV), "")

    def test_no_unreleased_section_is_empty(self):
        self.assertEqual(crn.release_notes("# x\n## [1.0.0]\n- a\n", PREV), "")

    def test_entries_without_group_heading_survive(self):
        cur = "## [Unreleased]\n\n- bare bullet\n\n\n- another\n## [0.1.0]\n"
        self.assertEqual(crn.release_notes(cur, ""), "- bare bullet\n- another")

    def test_delta_compares_whole_entries_not_lines(self):
        # Codex review #142 P2: two different bullets sharing an identical
        # sub-item line — the new bullet must keep its sub-item.
        prev = "## [Unreleased]\n\n### Added\n- old thing\n  - macOS\n  - Linux\n"
        cur = ("## [Unreleased]\n\n### Added\n- old thing\n  - macOS\n  - Linux\n"
               "- new thing\n  - macOS\n  - Windows\n")
        self.assertEqual(crn.release_notes(cur, prev), "### Added\n- new thing\n  - macOS\n  - Windows")
        # an edited continuation line makes the whole entry new (it IS a change)
        cur2 = "## [Unreleased]\n\n### Added\n- old thing\n  - macOS\n  - Linux (fixed)\n"
        self.assertEqual(crn.release_notes(cur2, prev), "### Added\n- old thing\n  - macOS\n  - Linux (fixed)")

    def test_entries_split_on_top_level_bullets_only(self):
        blocks = crn._entries(["### A", "- one", "  wrapped line", "  - sub", "* two", "", "loose text"])
        self.assertEqual(blocks, [["### A"], ["- one", "  wrapped line", "  - sub"], ["* two", "loose text"]])

    def test_groups_reordered_canonically_and_same_name_merged(self):
        cur = ("## [Unreleased]\n\n### Fixed\n- f1\n\n### Added\n- a1\n\n### Docs\n- d1\n\n"
               "### fixed\n- f2\n\n### Security\n- s1\n")
        self.assertEqual(crn.release_notes(cur, ""),
                         "### Added\n- a1\n\n### Fixed\n- f1\n- f2\n\n### Security\n- s1\n\n### Docs\n- d1")

    def test_headless_entries_come_first(self):
        cur = "## [Unreleased]\n\n- bare\n\n### Added\n- a1\n"
        self.assertEqual(crn.release_notes(cur, ""), "- bare\n\n### Added\n- a1")


def _lines(*specs):
    """(name, text) 对 → section_lines（测试里造 fragment 的捷径）。"""
    fragments = [cf.parse_fragment(name, text)[0] for name, text in specs]
    return cf.section_lines(fragments)


class FragmentsTestCase(unittest.TestCase):
    def test_fragments_merge_into_legacy_groups_after_legacy_entries(self):
        frag = _lines(("x.md", "type: changed\n- from fragment\n  cont\n"), ("y.md", "type: security\n- sec\n"))
        body = crn.release_notes(CURR, PREV, frag)
        self.assertEqual(body, "### Added\n- new feature X\n\n### Changed\n- new entry C\n- from fragment\n  cont"
                               "\n\n### Security\n- sec")

    def test_fragment_entries_already_in_previous_tag_are_dropped(self):
        prev_frag = _lines(("old.md", "type: added\n- shipped last time\n"))
        cur_frag = _lines(("old.md", "type: added\n- shipped last time\n"), ("new.md", "type: added\n- brand new\n"))
        self.assertEqual(crn.release_notes("", "", cur_frag, prev_frag), "### Added\n- brand new")
        # same fragment, unpruned, and nothing else = empty release paragraph
        self.assertEqual(crn.release_notes("", "", prev_frag, prev_frag), "")

    def test_fragment_moved_into_legacy_or_back_is_not_new(self):
        # the comparison is by entry text, not by where the entry lives
        prev = "## [Unreleased]\n\n### Added\n- same words\n"
        self.assertEqual(crn.release_notes("", prev, _lines(("f.md", "type: added\n- same words\n"))), "")

    def test_fragments_only_no_changelog(self):
        body = crn.release_notes("", "", _lines(("f.md", "type: fixed\n- only fragment\n")))
        self.assertEqual(body, "### Fixed\n- only fragment")


class MainTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="crn-"))
        (self.tmp / "cur.md").write_text(CURR, encoding="utf-8")
        (self.tmp / "prev.md").write_text(PREV, encoding="utf-8")

    def run_main(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = crn.main(argv)
        return rc, out.getvalue()

    def test_main_with_previous(self):
        rc, out = self.run_main([str(self.tmp / "cur.md"), "--previous", str(self.tmp / "prev.md")])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "### Added\n- new feature X\n\n### Changed\n- new entry C\n")

    def test_main_missing_previous_is_whole_section(self):
        rc, out = self.run_main([str(self.tmp / "cur.md"), "--previous", str(self.tmp / "nope.md")])
        self.assertEqual(rc, 0)
        self.assertIn("- old entry A", out)

    def test_main_identical_prints_nothing(self):
        rc, out = self.run_main([str(self.tmp / "prev.md"), "--previous", str(self.tmp / "prev.md")])
        self.assertEqual((rc, out), (0, ""))

    def test_main_with_fragments_and_previous_fragments(self):
        cur_dir = self.tmp / "changelog.d"
        prev_dir = self.tmp / "prev" / "changelog.d"
        cur_dir.mkdir()
        prev_dir.mkdir(parents=True)
        (cur_dir / "shipped.md").write_text("type: added\n- shipped\n", encoding="utf-8")
        (prev_dir / "shipped.md").write_text("type: added\n- shipped\n", encoding="utf-8")
        (cur_dir / "fresh.md").write_text("type: fixed\n- fresh fix\n", encoding="utf-8")
        (cur_dir / "broken.md").write_text("no type line\n", encoding="utf-8")
        (cur_dir / "README.md").write_text("# not a fragment\n", encoding="utf-8")
        err = io.StringIO()
        with redirect_stderr(err):
            rc, out = self.run_main([str(self.tmp / "prev.md"), "--previous", str(self.tmp / "prev.md"),
                                     "--fragments", str(cur_dir), "--previous-fragments", str(prev_dir)])
        self.assertEqual((rc, out), (0, "### Fixed\n- fresh fix\n"))
        self.assertIn("::warning::changelog.d/broken.md", err.getvalue())
        self.assertNotIn("README", err.getvalue())

    def test_main_missing_fragment_dirs_are_empty(self):
        rc, out = self.run_main([str(self.tmp / "cur.md"), "--previous", str(self.tmp / "prev.md"),
                                 "--fragments", str(self.tmp / "nope"), "--previous-fragments", str(self.tmp / "nope2")])
        self.assertEqual((rc, out), (0, "### Added\n- new feature X\n\n### Changed\n- new entry C\n"))


if __name__ == "__main__":
    unittest.main()
