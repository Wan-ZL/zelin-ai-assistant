"""shell/build.sh 的 codesign 有界回落——真 bash 跑一遍（CONTRACT §54.3「构建」条 2026-09-12 修正）。

住在 tests/integration/（防腐 #7：真 subprocess 只许住这里；单文件预算见 BUDGET_SECONDS）。

为什么非真跑不可：install.sh 的 `ui` 步在 launchd 下跑 shell/build.sh，而
`mac/scripts/make-signing-cert.sh` 里那句 `security set-key-partition-list`（给钥匙的 ACL
加 `-T /usr/bin/codesign`）是**可跳过**的一步——跳过了的机器上 codesign 会停在一个没人能点
的 keychain 授权框上。`|| echo WARN` 接得住失败、接不住 hang，一次 hang 就把
AIASSISTANT_UI_BUDGET（600 s）烧光 → UI_SHELL_STATUS=fail → 自动部署失败。静态断言只能证明
脚本里写了墙钟，证不了它真的会松手，所以这里把 build.sh 里的 codesign 段原样抠出来，配一对
假 `security` / 假 `codesign` 跑：

  - 假 security 认出身份 + 假 codesign 挂住 → 超时后回落 `-s -` 重签一次，整段仍退 0；
  - 假 codesign 直接失败（非 hang）→ 同样回落；
  - 假 security 认不出身份 → 一开始就 ad-hoc，只签一次（没有多余的重试）。
"""
import os
import re
import shutil
import subprocess
import time
import unittest
from pathlib import Path
from tests.scratch_testkit import scratch_dir

REPO = Path(__file__).resolve().parent.parent.parent
BUILD_SH = REPO / "shell" / "build.sh"
BUDGET_SECONDS = 60

# 抠出 build.sh 的 codesign 段：从身份赋值到最后那句兜底 WARN（含）
START = 'SIGN_ID="Zelin AI Engineer Dev"'
END = '|| echo "WARN: codesign failed (app may still run after Gatekeeper prompt)."'


def _codesign_block():
    text = BUILD_SH.read_text(encoding="utf-8")
    start = text.index(START)
    end = text.index(END, start) + len(END)
    return text[start:end]


class ShellBuildCodesignBudgetTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t0 = time.time()
        cls.block = _codesign_block()

    @classmethod
    def tearDownClass(cls):
        assert time.time() - cls.t0 < BUDGET_SECONDS, "integration budget blown"

    def setUp(self):
        self.tmp = Path(scratch_dir(self, prefix="shell-codesign-budget-"))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.calls = self.tmp / "codesign.calls"

    def _fake(self, name, body):
        p = self.bin / name
        p.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8")
        p.chmod(0o755)

    def _run(self):
        script = self.tmp / "harness.sh"
        script.write_text(
            "set -euo pipefail\n"
            'APP_DIR="%s/app"\n' % self.tmp
            + self.block + "\n", encoding="utf-8")
        env = dict(os.environ)
        env["PATH"] = "%s:%s" % (self.bin, env.get("PATH", ""))
        env["ZAI_CODESIGN_BUDGET_S"] = "1"
        env["CALLS"] = str(self.calls)
        return subprocess.run(["bash", str(script)], env=env, capture_output=True,
                              text=True, timeout=BUDGET_SECONDS)

    def _identities(self):
        return 'echo "  1) ABC \\"Zelin AI Engineer Dev\\""'

    def _logged(self):
        if not self.calls.exists():
            return []
        return [ln for ln in self.calls.read_text(encoding="utf-8").splitlines() if ln]

    def test_a_hanging_codesign_times_out_and_ad_hoc_signs_instead(self):
        self._fake("security", self._identities())
        # the stable identity hangs (keychain prompt nobody can click); ad-hoc is instant.
        # `exec sleep` so the killed pid IS the sleeper — no orphan holding the pipe open
        self._fake("codesign",
                   'printf "%s\\n" "$*" >> "$CALLS"\n'
                   'case "$*" in *"Zelin AI Engineer Dev"*) exec sleep 30 ;; esac\n'
                   "exit 0")
        r = self._run()
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        calls = self._logged()
        self.assertEqual(2, len(calls), calls)
        self.assertIn("Zelin AI Engineer Dev", calls[0])
        self.assertIn("--force --deep -s -", calls[1])
        self.assertIn("hung past 1s", r.stdout)
        self.assertIn("Falling back to ad-hoc", r.stdout)
        # the bundle really did get signed — no "codesign failed" bottom line
        self.assertNotIn("WARN: codesign failed", r.stdout)

    def test_a_failing_codesign_also_falls_back_to_ad_hoc(self):
        self._fake("security", self._identities())
        self._fake("codesign",
                   'printf "%s\\n" "$*" >> "$CALLS"\n'
                   'case "$*" in *"Zelin AI Engineer Dev"*) exit 1 ;; esac\n'
                   "exit 0")
        r = self._run()
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        calls = self._logged()
        self.assertEqual(2, len(calls), calls)
        self.assertIn("--force --deep -s -", calls[1])
        self.assertIn("failed (exit 1)", r.stdout)

    def test_no_identity_signs_ad_hoc_once_and_warns_about_the_grants(self):
        self._fake("security", "exit 0")   # nothing in the keychain
        self._fake("codesign", 'printf "%s\\n" "$*" >> "$CALLS"\nexit 0')
        r = self._run()
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        calls = self._logged()
        self.assertEqual(1, len(calls), calls)
        self.assertIn("--force --deep -s -", calls[0])
        self.assertIn("TCC grants will reset on reinstall", r.stdout)

    def test_the_stable_identity_path_signs_once_with_the_cert(self):
        self._fake("security", self._identities())
        self._fake("codesign", 'printf "%s\\n" "$*" >> "$CALLS"\nexit 0')
        r = self._run()
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        calls = self._logged()
        self.assertEqual(1, len(calls), calls)
        self.assertIn('--force --deep -s Zelin AI Engineer Dev', calls[0])
        self.assertIn("stable identity, TCC-safe", r.stdout)
        self.assertNotIn("Falling back", r.stdout)

    def test_the_block_shellchecks_clean_in_isolation(self):
        if not shutil.which("shellcheck"):
            self.skipTest("shellcheck not installed")
        script = self.tmp / "block.sh"
        script.write_text("#!/bin/bash\nset -euo pipefail\nAPP_DIR=/tmp/x\n"
                          + self.block + "\n", encoding="utf-8")
        r = subprocess.run(["shellcheck", str(script)], capture_output=True, text=True)
        self.assertEqual(0, r.returncode, r.stdout)

    def test_the_budget_knob_is_overridable_and_has_a_default(self):
        self.assertIsNotNone(re.search(r'ZAI_CODESIGN_BUDGET_S:-\d+', self.block))


if __name__ == "__main__":
    unittest.main()
