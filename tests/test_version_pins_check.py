"""CI 门「Version pins untouched」判例（CONTRACT §56.1）：纯函数 check(diff, latest_tag)。

  - iOS 两处 MARKETING_VERSION 行任何增删 = FAIL；
  - act/__init__.py 的 `__version__ = "…"` 行：改到 == 最新 tag（刷新）放行，
    别的值 = FAIL，只删不加 = FAIL；
  - CHANGELOG.md 新增 `## [X.Y.Z]` 标题或 `[X.Y.Z]: https://…` 链接 = FAIL；
    [Unreleased] 下的条目、删旧标题都放行；
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
        self.assertEqual(len(problems), 1)
        self.assertIn("[Unreleased]", problems[0])

    def test_added_compare_link_fails(self):
        problems = vpc.check(diff("CHANGELOG.md", added=[
            "[0.48.17]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.48.16...v0.48.17"]), "v0.48.16")
        self.assertEqual(len(problems), 1)

    def test_unreleased_entries_and_deletions_pass(self):
        self.assertEqual(vpc.check(diff("CHANGELOG.md", ["## [0.48.15] - 2026-09-01"],
                                        ["### Changed", "- **x**: y", "## [Unreleased]"]), "v0.48.16"), [])

    def test_heading_in_other_markdown_is_ignored(self):
        self.assertEqual(vpc.check(diff("docs/RELEASES.md", added=["## [0.48.17] - x"]), "v0.48.16"), [])


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
