"""这个仓库自己的 README.md 必须零过期主张（docs/CONTRACT.md §58 / §66；§77.5 README 真实性审计）。

判例的意义：README 是产品的第一面，而它历来是最容易和代码脱节的一页（退役的
问问助手页、iMessage 通道、菜单栏 app、写死的旧版本号都曾挂在上面）。审计器
（scripts/qa/readme_audit.py）能判的三类主张——路径、退役面、UI 标签——在这里
被钉成硬门：改 README 或改 UI 之后，这条测试是第一个喊的人。截图同理：README
承诺的 docs/images/*.png 必须真的在仓库里（且是真渲染出来的 PNG，不是占位）。
"""
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_QA_DIR = os.path.join(REPO_ROOT, "scripts", "qa")
if _QA_DIR not in sys.path:
    sys.path.insert(0, _QA_DIR)

import readme_audit  # noqa: E402

MIN_SCREENSHOTS = 4


class RepoReadmeIsCurrentTest(unittest.TestCase):
    def setUp(self):
        self.report = readme_audit.audit(REPO_ROOT)

    def test_no_stale_claims(self):
        detail = "\n".join("line %d: %s — %s" % (c.line, "; ".join(c.reasons),
                                                 c.text[:120])
                           for c in self.report.stale)
        self.assertEqual(self.report.stale, [], "README has stale claims:\n" + detail)

    def test_screenshots_are_referenced_and_present(self):
        self.assertGreaterEqual(len(self.report.images), MIN_SCREENSHOTS)
        for rel in self.report.images:
            path = os.path.join(REPO_ROOT, rel)
            self.assertTrue(os.path.exists(path), "missing screenshot: " + rel)
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(8), b"\x89PNG\r\n\x1a\n",
                                 rel + " is not a real PNG")

    def test_summary_line_shape(self):
        self.assertRegex(self.report.summary(),
                         r"^README claims=\d+ stale=\d+ images=\d+$")


if __name__ == "__main__":
    unittest.main()
