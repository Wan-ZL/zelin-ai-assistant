"""CI 门「Version pins untouched」判例（CONTRACT §56.1）：纯函数 check(diff, latest_tag)。

  - version.PIN_FILES 里的 MARKETING_VERSION 行任何增删 = FAIL（v2.0.0 起清单为空：
    退役的 ios/ 路径上的同形行不再判）；
  - act/__init__.py 的 `__version__ = "…"` 行：改到 == 最新 tag（刷新）放行，
    别的值 = FAIL，只删不加 = FAIL；
  - CHANGELOG.md 新增 `## [X.Y.Z]` 标题或 `[X.Y.Z]: https://…` 链接 = FAIL；
  - CHANGELOG.md 新增顶格 bullet / `### 组` 标题 = FAIL（§56.7：[Unreleased] 冻结，写 changelog.d/ fragment）；
    删行、头部散文、缩进续行放行；
  - （v2.0.0 退役）vnext2-plan §8 表行规则：计划书只剩历史，追加同形行不再判；
  - act/_version.py 出现在 diff 里 = FAIL；
  - 无关文件里出现同名字串不算（只看路径匹配的文件）；
  - 被删文件（`+++ /dev/null`）的 `-` 行记在它自己名下，不串到 diff 里上一个文件；
  - CLI：--legacy-base 只 notice 不 FAIL（在飞 PR 的过渡放行）。
"""
import io
import os
import sys
import unittest
from unittest import mock

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


def deleted(path, removed):
    """一个被删文件的 unified diff（`+++ /dev/null`）。"""
    lines = ["diff --git a/%s b/%s" % (path, path), "deleted file mode 100644", "--- a/%s" % path,
             "+++ /dev/null", "@@ -1,%d +0,0 @@" % len(removed)]
    lines += ["-" + r for r in removed]
    return "\n".join(lines) + "\n"


class PinsTestCase(unittest.TestCase):
    def test_no_committed_pins_since_ios_retired(self):
        self.assertEqual(vpc.ver.PIN_FILES, ())
        # the retired iOS pin files are ordinary paths now — deleting them is not a bump
        for path in ("ios/project.yml", "ios/ZelinAIAssistant.xcodeproj/project.pbxproj"):
            self.assertEqual(vpc.check(deleted(path, ['    MARKETING_VERSION: "0.0.0-dev"']), "v0.48.16"), [])

    def test_a_pin_file_edit_fails_when_one_is_listed(self):
        with mock.patch.object(vpc.ver, "PIN_FILES", ("build/pin.yml",)):
            problems = vpc.check(diff("build/pin.yml", ['    MARKETING_VERSION: "0.0.0-dev"'],
                                      ['    MARKETING_VERSION: "0.48.17"']), "v0.48.16")
            self.assertEqual(len(problems), 1, problems)
            self.assertIn("MARKETING_VERSION", problems[0])
            self.assertIn("build/pin.yml", problems[0])
            self.assertEqual(vpc.check(diff("build/pin.yml", ['    SWIFT_VERSION: "5.0"'],
                                            ['    SWIFT_VERSION: "6.0"']), "v0.48.16"), [])

    def test_marketing_version_in_unrelated_file_is_ignored(self):
        self.assertEqual(vpc.check(diff("docs/CONTRACT.md", added=["MARKETING_VERSION = 0.48.17;"]), "v0.48.16"), [])


class DeletedFileAttributionTestCase(unittest.TestCase):
    """`+++ /dev/null` 的被删文件：它的 `-` 行归它自己，不归 diff 里上一个文件。"""

    def test_deleted_file_after_init_does_not_count_as_removing_the_fallback(self):
        text = (diff("act/__init__.py", added=["# comment"])
                + deleted("act/old_module.py", ['__version__ = "0.1.0"', "x = 1"]))
        self.assertEqual(vpc.check(text, "v0.48.16"), [])

    def test_deleting_the_init_file_itself_still_fails(self):
        problems = vpc.check(deleted("act/__init__.py", ['__version__ = "0.48.16"']), "v0.48.16")
        self.assertTrue(any("removed" in p for p in problems), problems)

    def test_deleted_file_after_the_stamp_rule_is_not_blamed_on_it(self):
        text = (diff("CHANGELOG.md", ["- old shipped entry"], [])
                + deleted("ios/project.yml", ['MARKETING_VERSION: "0.0.0-dev"'])
                + diff("README.md", added=["x"]))
        self.assertEqual(vpc.check(text, "v0.48.16"), [])


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


class RetiredPlanTableRuleTestCase(unittest.TestCase):
    def test_vnext2_plan_rows_are_no_longer_judged(self):
        self.assertFalse(hasattr(vpc, "PLAN_FILE"))
        self.assertEqual(vpc.check(diff("docs/design/vnext2-plan.md",
                                        added=["| 2026-09-02 | `ci/x` | P2 | did x | §56.7 |"]), "v0.48.16"), [])


class StampFileTestCase(unittest.TestCase):
    def test_committed_stamp_fails(self):
        problems = vpc.check(diff("act/_version.py", added=['__version__ = "0.48.17"']), "v0.48.16")
        self.assertEqual(len(problems), 1)
        self.assertIn("git-ignored", problems[0])


class MultiFileAndCliTestCase(unittest.TestCase):
    def test_multi_file_diff_reports_each_once(self):
        text = (diff("act/_version.py", added=['__version__ = "0.48.17"', "x = 1"])
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
