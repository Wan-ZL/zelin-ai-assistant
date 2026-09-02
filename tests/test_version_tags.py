"""release-on-merge 的 tag 算术判例（CONTRACT §56.2）：纯函数 + scripts/ci/release_tags.py 壳。

  - next_tag = 现有最高 vX.Y.Z + 1 patch（非版本形状的 tag 忽略；没有 tag → v0.0.1）；
    minor / major 抬档归零低位；
  - previous_tag = 低于 current 的最高 tag（release notes 的比较基线）；
  - bump_from_labels：`release: major` > `release: minor` > patch，空格/大小写宽松；
  - pr_number_from_subject：merge/squash 首行末尾的 `(#N)`；
  - CLI：stdin 进 stdout 出，找不到 → 空行 + rc 1。
"""
import io
import os
import sys
import unittest

from act.lib import version as ver

_CI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "ci")
if _CI_DIR not in sys.path:
    sys.path.insert(0, _CI_DIR)
import release_tags  # noqa: E402

TAGS = ["v0.48.14", "v0.48.15", "v0.48.16", "v0.47.9", "v0.10.3", "vnext", "backup-2026", "v1.0"]


class NextTagTestCase(unittest.TestCase):
    def test_patch_from_highest_numeric_tag(self):
        self.assertEqual(ver.next_tag(TAGS), "v0.48.17")

    def test_minor_and_major_reset_lower_parts(self):
        self.assertEqual(ver.next_tag(TAGS, "minor"), "v0.49.0")
        self.assertEqual(ver.next_tag(TAGS, "major"), "v1.0.0")

    def test_numeric_not_lexical(self):
        self.assertEqual(ver.next_tag(["v0.48.9", "v0.48.10"]), "v0.48.11")

    def test_no_tags(self):
        self.assertEqual(ver.next_tag([]), "v0.0.1")
        self.assertEqual(ver.next_tag(["garbage"], "minor"), "v0.1.0")
        self.assertEqual(ver.next_tag([], "major"), "v1.0.0")

    def test_unknown_bump_is_patch(self):
        self.assertEqual(ver.next_tag(TAGS, "huge"), "v0.48.17")

    def test_highest_and_previous(self):
        self.assertEqual(ver.highest_tag(TAGS), (0, 48, 16))
        self.assertIsNone(ver.highest_tag(["x"]))
        self.assertEqual(ver.previous_tag(TAGS, "v0.48.16"), "v0.48.15")
        self.assertEqual(ver.previous_tag(TAGS, "v0.48.15"), "v0.48.14")
        self.assertEqual(ver.previous_tag(TAGS, "v0.11.0"), "v0.10.3")
        self.assertIsNone(ver.previous_tag(TAGS, "v0.10.3"))
        self.assertIsNone(ver.previous_tag(TAGS, "nonsense"))


class LabelsAndSubjectTestCase(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(ver.bump_from_labels([]), "patch")
        self.assertEqual(ver.bump_from_labels(["bug", "release: minor"]), "minor")
        self.assertEqual(ver.bump_from_labels(["release:minor"]), "minor")
        self.assertEqual(ver.bump_from_labels(["Release: Major", "release: minor"]), "major")
        self.assertEqual(ver.bump_from_labels(["release"]), "patch")

    def test_pr_number(self):
        self.assertEqual(ver.pr_number_from_subject("feat(x): y (#141)"), 141)
        self.assertEqual(ver.pr_number_from_subject("feat(x): y (#141)\n\nbody (#7)"), 141)
        self.assertEqual(ver.pr_number_from_subject("fix: no pr"), None)
        self.assertEqual(ver.pr_number_from_subject(""), None)
        # merge commits (the merge queue's MERGE method produces these) carry the number up front
        self.assertEqual(ver.pr_number_from_subject("Merge pull request #12 from Wan-ZL/feat/x\n\nfeat: x"), 12)
        self.assertEqual(ver.pr_number_from_subject("Merge branch 'main' into feat/x"), None)


class CliTestCase(unittest.TestCase):
    def run_cli(self, argv, stdin_text):
        out = io.StringIO()
        rc = release_tags.main(argv, stdin=io.StringIO(stdin_text), stdout=out)
        return rc, out.getvalue()

    def test_next_and_previous_and_highest(self):
        tags = "\n".join(TAGS) + "\n"
        self.assertEqual(self.run_cli(["next"], tags), (0, "v0.48.17\n"))
        self.assertEqual(self.run_cli(["next", "--bump", "minor"], tags), (0, "v0.49.0\n"))
        self.assertEqual(self.run_cli(["previous", "v0.48.16"], tags), (0, "v0.48.15\n"))
        self.assertEqual(self.run_cli(["previous", "v0.10.3"], tags), (1, "\n"))
        self.assertEqual(self.run_cli(["highest"], tags), (0, "v0.48.16\n"))
        self.assertEqual(self.run_cli(["highest"], ""), (1, "\n"))

    def test_pr_number_and_labels(self):
        self.assertEqual(self.run_cli(["pr-number"], "ci: x (#5)\n\nbody"), (0, "5\n"))
        self.assertEqual(self.run_cli(["pr-number"], "ci: x"), (1, "\n"))
        self.assertEqual(self.run_cli(["bump-from-labels"], "bug\nrelease: major\n"), (0, "major\n"))
        self.assertEqual(self.run_cli(["bump-from-labels"], ""), (0, "patch\n"))


if __name__ == "__main__":
    unittest.main()
