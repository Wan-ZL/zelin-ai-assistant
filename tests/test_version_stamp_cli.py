"""scripts/version_stamp.py 的纯文本部分判例（CONTRACT §56.1）：占位门。

  - v2.0.0 起 ``version.PIN_FILES`` 为空（iOS 的两处 MARKETING_VERSION 占位随 ios/
    退役）：``--check-pins`` 对真源树恒 rc 0，``--ios`` 已不是合法参数；
  - pin_values 抽出每处当前值（yml 与 pbxproj 两种形状）；
  - do_check_pins 的判定本身仍在：注入一份 pin 文件清单时，全是占位 → 0，
    出现别的值或一处都没有 → 1（将来哪个构建物再要提交的 pin，加进
    PIN_FILES 就复用这把尺）。
零 subprocess（git 夹具在 tests/integration/test_version_git_fixture.py）。
"""
import contextlib
import io
import os
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


class PinValuesTestCase(unittest.TestCase):
    def test_yaml_shape(self):
        self.assertEqual(version_stamp.pin_values(YML), ["0.0.0-dev"])

    def test_pbxproj_shape_both_configurations(self):
        self.assertEqual(version_stamp.pin_values(PBX), ["0.0.0-dev", "0.0.0-dev"])

    def test_no_pin(self):
        self.assertEqual(version_stamp.pin_values("nothing here\n"), [])


class NoCommittedPinsTestCase(unittest.TestCase):
    def test_pin_files_are_empty_since_ios_retired(self):
        self.assertEqual(ver.PIN_FILES, ())
        self.assertEqual(ver.PIN_PLACEHOLDER, "0.0.0-dev")

    def test_check_pins_on_the_real_tree_passes(self):
        self.assertEqual(version_stamp.do_check_pins(REPO), 0)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(version_stamp.main(["--check-pins", "--repo-root", str(REPO)]), 0)

    def test_ios_flag_is_gone(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            version_stamp.main(["--ios"])
        self.assertEqual(cm.exception.code, 2)


class CheckPinsJudgementTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pins-"))
        (self.tmp / "a.yml").write_text(YML, encoding="utf-8")
        (self.tmp / "b.pbxproj").write_text(PBX, encoding="utf-8")
        self.files = ("a.yml", "b.pbxproj")

    def _check(self):
        with contextlib.redirect_stderr(io.StringIO()):
            return version_stamp.do_check_pins(self.tmp, self.files)

    def test_all_placeholders_pass(self):
        self.assertEqual(self._check(), 0)

    def test_mixed_values_fail(self):
        path = self.tmp / "b.pbxproj"
        path.write_text(PBX.replace("0.0.0-dev", "0.48.17", 1), encoding="utf-8")
        self.assertEqual(self._check(), 1)

    def test_a_file_without_any_pin_fails(self):
        (self.tmp / "a.yml").write_text("settings: {}\n", encoding="utf-8")
        self.assertEqual(self._check(), 1)


if __name__ == "__main__":
    unittest.main()
