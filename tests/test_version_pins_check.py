"""CI 门「Version pins untouched」判例（CONTRACT §56.1）：纯函数 check(diff, latest_tag)。

  - iOS 两处 MARKETING_VERSION 行任何增删 = FAIL；
  - act/__init__.py 的 `__version__ = "…"` 行：改到 == 最新 tag（刷新）放行，
    别的值 = FAIL，只删不加 = FAIL；
  - CHANGELOG.md 新增 `## [X.Y.Z]` 标题或 `[X.Y.Z]: https://…` 链接 = FAIL；
  - CHANGELOG.md 新增顶格 bullet / `### 组` 标题 = FAIL（§56.7：[Unreleased] 冻结，写 changelog.d/ fragment）；
    删行、头部散文、缩进续行放行；
  - docs/design/vnext2-plan.md 新增 `| YYYY-MM-DD |` 表格行 = FAIL（§8 进度表冻结，写 progress/ fragment）；
    别的行、删行放行；别的 markdown 里的同形行不算；
  - act/_version.py 出现在 diff 里 = FAIL；
  - 无关文件里出现同名字串不算（只看路径匹配的文件）；
  - CLI：--legacy-base 只 notice 不 FAIL（在飞 PR 的过渡放行）。
"""
import io
import os
import sys
import unittest

_CI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "ci")
if _CI_DIR not in sys.path:
    sys.path.insert(0, _CI_DIR)
import version_pins_check as vpc  # noqa: E402


def diff(path, removed=(), added=()):
    """一个文件的最小 unified diff。"""
    lines = ["diff --git a/%s b/%s" % (path, path), "--- a/%s" % path, "+++ b/%s" % path, "@@ -1,1 +1,1 @@"]
    lines += ["-" + r for r in removed]
    lines += ["+" + a for a in added]
    return "\n".join(lines) + "\n"


class PinsTestCase(unittest.TestCase):
    def test_ios_pins_edit_fails(self):
        for path in ("ios/project.yml", "ios/ZelinAIAssistant.xcodeproj/project.pbxproj"):
            problems = vpc.check(diff(path, ['    MARKETING_VERSION: "0.0.0-dev"'],
                                      ['    MARKETING_VERSION: "0.48.17"']), "v0.48.16")
            self.assertEqual(len(problems), 1, problems)
            self.assertIn("MARKETING_VERSION", problems[0])
            self.assertIn(path, problems[0])

    def test_other_ios_edits_pass(self):
        self.assertEqual(vpc.check(diff("ios/project.yml", ['    SWIFT_VERSION: "5.0"'], ['    SWIFT_VERSION: "6.0"']),
                                   "v0.48.16"), [])

    def test_marketing_version_in_unrelated_file_is_ignored(self):
        self.assertEqual(vpc.check(diff("docs/CONTRACT.md", added=["MARKETING_VERSION = 0.48.17;"]), "v0.48.16"), [])


class FallbackLineTestCase(unittest.TestCase):
    def test_hand_bump_fails(self):
        problems = vpc.check(diff("act/__init__.py", ['__version__ = "0.48.16"'], ['__version__ = "0.48.17"']), "v0.48.16")
        self.assertEqual(len(problems), 1)
        self.assertIn("hand bumps are rejected", problems[0])

    def test_refresh_to_latest_tag_passes(self):
        self.assertEqual(vpc.check(diff("act/__init__.py", ['__version__ = "0.48.10"'], ['__version__ = "0.48.16"']),
                                   "v0.48.16"), [])

    def test_removing_the_line_fails(self):
        problems = vpc.check(diff("act/__init__.py", ['__version__ = "0.48.16"'], []), "v0.48.16")
        self.assertTrue(any("removed" in p for p in problems), problems)

    def test_other_init_edits_pass(self):
        self.assertEqual(vpc.check(diff("act/__init__.py", added=["# a comment", "from act.lib import version"]),
                                   "v0.48.16"), [])

    def test_no_latest_tag_known_rejects_any_change(self):
        problems = vpc.check(diff("act/__init__.py", ['__version__ = "0.48.16"'], ['__version__ = "0.48.17"']), "")
        self.assertEqual(len(problems), 1)


class ChangelogTestCase(unittest.TestCase):
    def test_added_version_heading_fails(self):
        problems = vpc.check(diff("CHANGELOG.md", added=["## [0.48.17] - 2026-09-02", "", "### Fixed", "- thing"]),
                             "v0.48.16")
        # the heading, the group heading and the bullet are three distinct violations (§56.1 + §56.7)
        self.assertEqual(len(problems), 3, problems)
        self.assertTrue(any("version heading/link added" in p for p in problems), problems)
        self.assertEqual(vpc.check(diff("CHANGELOG.md", added=["## [0.48.17] - 2026-09-02"]), "v0.48.16")[0][:12],
                         "CHANGELOG.md")

    def test_added_compare_link_fails(self):
        problems = vpc.check(diff("CHANGELOG.md", added=[
            "[0.48.17]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.48.16...v0.48.17"]), "v0.48.16")
        self.assertEqual(len(problems), 1)

    def test_deletions_prose_and_continuations_pass(self):
        # §56.7: [Unreleased] is shrink-only — deleting shipped entries, editing the header prose
        # and (re)adding the section heading itself are fine; only new entries are rejected.
        self.assertEqual(vpc.check(diff("CHANGELOG.md",
                                        ["## [0.48.15] - 2026-09-01", "### Changed", "- **old**: shipped"],
                                        ["## [Unreleased]", "1. Write a fragment in changelog.d/ instead.",
                                         "  indented continuation of an existing bullet"]), "v0.48.16"), [])

    def test_added_entry_or_group_heading_fails_pointing_at_fragments(self):
        for line in ("- **x**: y", "* star bullet", "### Changed"):
            problems = vpc.check(diff("CHANGELOG.md", added=[line]), "v0.48.16")
            self.assertEqual(len(problems), 1, (line, problems))
            self.assertIn("changelog.d/", problems[0])
            self.assertIn("frozen", problems[0])

    def test_heading_in_other_markdown_is_ignored(self):
        self.assertEqual(vpc.check(diff("docs/RELEASES.md", added=["## [0.48.17] - x"]), "v0.48.16"), [])


class PlanProgressTableTestCase(unittest.TestCase):
    PLAN = "docs/design/vnext2-plan.md"

    def test_added_progress_row_fails_pointing_at_fragments(self):
        problems = vpc.check(diff(self.PLAN, added=["| 2026-09-02 | `ci/x` | P2 | did x | §56.7 |"]), "v0.48.16")
        self.assertEqual(len(problems), 1)
        self.assertIn("docs/design/progress/", problems[0])

    def test_other_plan_edits_and_row_deletions_pass(self):
        self.assertEqual(vpc.check(diff(self.PLAN, ["| 2026-09-01 | `a` | P0 | did a | §1 |"],
                                        ["| D23 | a new decision | quote | 09-02 |", "| #99 | closed | why |",
                                         "**2026-09-02 起本表冻结** — prose mentioning a date is fine",
                                         "| 日期 | PR / 分支 | 阶段 | 做了什么 | 法典 |"]), "v0.48.16"), [])

    def test_date_row_in_other_markdown_is_ignored(self):
        self.assertEqual(vpc.check(diff("docs/design/progress/README.md",
                                        added=["| 2026-09-02 | example | row |"]), "v0.48.16"), [])


class StampFileTestCase(unittest.TestCase):
    def test_committed_stamp_fails(self):
        problems = vpc.check(diff("act/_version.py", added=['__version__ = "0.48.17"']), "v0.48.16")
        self.assertEqual(len(problems), 1)
        self.assertIn("git-ignored", problems[0])


class MultiFileAndCliTestCase(unittest.TestCase):
    def test_multi_file_diff_reports_each_once(self):
        text = (diff("ios/project.yml", ['MARKETING_VERSION: "0.0.0-dev"'], ['MARKETING_VERSION: "0.48.17"'])
                + diff("CHANGELOG.md", added=["## [0.48.17] - d"])
                + diff("act/__init__.py", ['__version__ = "0.48.16"'], ['__version__ = "0.48.17"'])
                + diff("README.md", added=["hello"]))
        problems = vpc.check(text, "v0.48.16")
        self.assertEqual(len(problems), 3, problems)

    def test_empty_diff_passes(self):
        self.assertEqual(vpc.check("", "v0.48.16"), [])

    def run_cli(self, argv, text):
        out = io.StringIO()
        rc = vpc.main(argv, stdin=io.StringIO(text), stdout=out)
        return rc, out.getvalue()

    def test_cli_fail_and_ok(self):
        bad = diff("CHANGELOG.md", added=["## [0.48.17] - d"])
        rc, out = self.run_cli(["--latest-tag", "v0.48.16"], bad)
        self.assertEqual(rc, 1)
        self.assertIn("::error::", out)
        rc, out = self.run_cli(["--latest-tag", "v0.48.16"], diff("README.md", added=["x"]))
        self.assertEqual((rc, "ok" in out), (0, True))

    def test_cli_legacy_base_tolerates(self):
        bad = diff("act/__init__.py", ['__version__ = "0.48.16"'], ['__version__ = "0.48.17"'])
        rc, out = self.run_cli(["--latest-tag", "v0.48.16", "--legacy-base"], bad)
        self.assertEqual(rc, 0)
        self.assertIn("::notice::", out)
        self.assertNotIn("::error::", out)


if __name__ == "__main__":
    unittest.main()
