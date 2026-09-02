"""scripts/version_stamp.py 的纯文本部分判例（CONTRACT §56.1）：iOS pin 盖章与占位门。

  - stamp_pins：project.yml 的 `MARKETING_VERSION: "0.0.0-dev"` 与 pbxproj 的
    `MARKETING_VERSION = 0.0.0-dev;` 两种形状都换值、别的键不动；
  - pin_values 抽出每处当前值；
  - do_check_pins：仓库里提交的两份 pin 文件此刻必须全是占位（本判例同时钉住
    真源树——有人手 bump 就在这里红）；
  - do_ios 在 fixture 副本上改写、真源树零触碰；找不到 pin = rc 1。
零 subprocess（git 夹具在 tests/integration/test_version_git_fixture.py）。
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from act.lib import version as ver

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
import version_stamp  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

YML = '''settings:
  base:
    SWIFT_VERSION: "5.0"
    MARKETING_VERSION: "0.0.0-dev"
    CURRENT_PROJECT_VERSION: "1"
'''
PBX = '''\t\t\t\tMARKETING_VERSION = 0.0.0-dev;
\t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = com.zelin.ai;
\t\t\t\tMARKETING_VERSION = 0.0.0-dev;
'''


class StampPinsTestCase(unittest.TestCase):
    def test_yaml_shape(self):
        new, n = version_stamp.stamp_pins(YML, "0.48.17")
        self.assertEqual(n, 1)
        self.assertIn('MARKETING_VERSION: "0.48.17"', new)
        self.assertIn('CURRENT_PROJECT_VERSION: "1"', new)
        self.assertEqual(version_stamp.pin_values(new), ["0.48.17"])

    def test_pbxproj_shape_both_configurations(self):
        new, n = version_stamp.stamp_pins(PBX, "0.48.17+3")
        self.assertEqual(n, 2)
        self.assertEqual(new.count("MARKETING_VERSION = 0.48.17+3;"), 2)
        self.assertIn("PRODUCT_BUNDLE_IDENTIFIER = com.zelin.ai;", new)
        self.assertEqual(version_stamp.pin_values(new), ["0.48.17+3", "0.48.17+3"])

    def test_no_pin_no_change(self):
        self.assertEqual(version_stamp.stamp_pins("nothing here\n", "1.2.3"), ("nothing here\n", 0))


class PinFilesOnDiskTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pins-"))
        for rel in ver.PIN_FILES:
            dst = self.tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(REPO / rel), str(dst))

    def test_committed_pins_are_the_placeholder(self):
        # the real tree: a hand bump anywhere in the two files turns this red
        self.assertEqual(version_stamp.do_check_pins(REPO), 0)
        for rel in ver.PIN_FILES:
            values = version_stamp.pin_values((REPO / rel).read_text(encoding="utf-8"))
            self.assertTrue(values, rel)
            self.assertEqual(set(values), {ver.PIN_PLACEHOLDER}, rel)

    def test_do_ios_rewrites_the_copy_only(self):
        self.assertEqual(version_stamp.do_ios("0.48.17", self.tmp), 0)
        for rel in ver.PIN_FILES:
            self.assertEqual(set(version_stamp.pin_values((self.tmp / rel).read_text(encoding="utf-8"))),
                             {"0.48.17"}, rel)
            # the real tree is untouched
            self.assertEqual(set(version_stamp.pin_values((REPO / rel).read_text(encoding="utf-8"))),
                             {ver.PIN_PLACEHOLDER}, rel)
        self.assertEqual(version_stamp.do_check_pins(self.tmp), 1, "stamped copies must fail the placeholder gate")

    def test_do_ios_fails_when_layout_changed(self):
        (self.tmp / ver.PIN_FILES[0]).write_text("settings: {}\n", encoding="utf-8")
        self.assertEqual(version_stamp.do_ios("0.48.17", self.tmp), 1)

    def test_check_pins_fails_on_mixed_values(self):
        path = self.tmp / ver.PIN_FILES[1]
        text = path.read_text(encoding="utf-8").replace("MARKETING_VERSION = 0.0.0-dev;", "MARKETING_VERSION = 0.48.17;", 1)
        path.write_text(text, encoding="utf-8")
        self.assertEqual(version_stamp.do_check_pins(self.tmp), 1)


if __name__ == "__main__":
    unittest.main()
