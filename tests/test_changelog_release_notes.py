"""Release 正文提取判例（CONTRACT §56.2）：CHANGELOG [Unreleased] 相对上一个 tag 的增量。

  - unreleased_section 只取 `## [Unreleased]` 到下一个 `## ` 之间；没有该段 → []；
  - release_notes：上一个 tag 的 [Unreleased] 里已有的行去掉，`### 组` 标题下没剩条目
    的整组消失，顺序保留、连续空行压缩；
  - 没有 previous（首个 release）= 整段；两份一样 = 空串；
  - main()：文件不存在的 --previous 当空；stdout 末尾恰一个换行、空正文零输出。
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
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
        self.assertEqual(crn.release_notes(cur, ""), "- bare bullet\n\n- another")


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


if __name__ == "__main__":
    unittest.main()
