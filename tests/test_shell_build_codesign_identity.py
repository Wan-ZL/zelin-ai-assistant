"""shell/build.sh 的签名契约（CONTRACT §54.2 2026-09-12 修正，issue #316）。

壳 app 的 TCC 授权（屏幕录制 / 麦克风 / 自动化 / ~/Documents 笔记库）按代码签名的
designated requirement 记账。ad-hoc（`-s -`）的 requirement = 二进制 cdhash，每次构建
都变——install.sh 装一次，macOS 就把壳当成陌生 app：系统设置里开关还显示着「开」，抓屏
却一次次重弹（#316）。本文件钉住修法后的三件事：

1. 稳定身份优先：`security find-identity -p codesigning`（**不加 `-v`**——那张自签证书
   不受信任，`-v` 会把它藏起来）认出 `Zelin AI Engineer Dev` 就用它签；
2. `--deep` 不是装饰：`Contents/MacOS` 里还有 §68.13 的 vault-sync-helper / framegrab
   两个可执行，`--deep` 是让它们与壳同一身份的那一下；
3. 签名有界且可回落：本脚本由 install.sh 的 `ui` 步在 launchd 下跑（§56.5），没人能点
   keychain 弹出的授权框，所以 codesign 跑在自算的墙钟里，超时 / 失败都回落 `-s -`。

真跑一遍那条回落（假 security + 假 codesign）的判例住
tests/integration/test_shell_build_codesign_budget.py（防腐 #7：真 IO 只在 integration/）。
"""
import re
import unittest
from pathlib import Path

BUILD_SH = Path(__file__).resolve().parent.parent / "shell" / "build.sh"


class ShellBuildCodesignIdentityTestCase(unittest.TestCase):
    def setUp(self):
        self.build = BUILD_SH.read_text(encoding="utf-8")

    def test_stable_identity_is_probed_without_v_and_falls_back_to_ad_hoc(self):
        self.assertIn('SIGN_ID="Zelin AI Engineer Dev"', self.build)
        probe = re.search(r"^.*security find-identity.*$", self.build, re.M)
        self.assertIsNotNone(probe, "the identity probe disappeared")
        self.assertIn("-p codesigning", probe.group(0))
        # `-v` (valid/trusted only) would hide the untrusted self-signed cert
        self.assertNotIn(" -v", probe.group(0))
        self.assertIn('SIGN_ID="-"', self.build)

    def test_codesign_keeps_deep_for_the_bundled_helper_executables(self):
        # §68.13: vault-sync-helper / framegrab live in Contents/MacOS — without
        # --deep they would sit unsigned inside a cert-signed bundle
        self.assertIn('codesign --force --deep -s "$SIGN_ID" "$APP_DIR"', self.build)
        self.assertIn("vault-sync-helper", self.build)
        self.assertIn("framegrab", self.build)

    def test_signing_runs_under_a_wall_clock_and_falls_back_on_timeout(self):
        # `|| echo WARN` catches a failure but NOT a hang on the keychain prompt;
        # a hang would eat install.sh's whole AIASSISTANT_UI_BUDGET → deploy fail
        self.assertIn('CODESIGN_BUDGET_S="${ZAI_CODESIGN_BUDGET_S:-60}"', self.build)
        self.assertIn("run_with_budget()", self.build)
        self.assertIn("return 124", self.build)
        block = self.build[self.build.index('SIGN_ID="Zelin AI Engineer Dev"'):]
        self.assertEqual(
            2, block.count('run_with_budget "$CODESIGN_BUDGET_S" codesign'),
            "the ad-hoc retry after a timeout/failure is gone")
        self.assertIn('[ "$SIGN_RC" -ne 0 ] && [ "$SIGN_ID" != "-" ]', block)

    def test_the_ad_hoc_fallback_says_grants_will_reset(self):
        # the owner must be able to tell from ui-build.log which identity signed
        self.assertIn("TCC grants will reset on reinstall", self.build)
        self.assertIn("Falling back to ad-hoc", self.build)


if __name__ == "__main__":
    unittest.main()
