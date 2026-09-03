"""ingest/vault-sync.sh `find_vault_sync_helper` — where the Documents courier lives.

The vault-sync-helper ships inside the LEGACY menu-bar app (bundle id
com.zelin.ai-engineer, the identity holding the one-time Documents grant —
CONTRACT §12). The §54 name swap (owner 2026-09-02) moved that bundle to
"Zelin's AI Assistant (old).app" and gave the product name to the board shell.
Since P4 (CONTRACT §68.13) the shell bundle ALSO ships a helper copy (shell/build.sh
compiles shell/Helpers/VaultSyncHelper.swift) — a fresh machine without the legacy
app syncs through the shell's identity; when both are installed the legacy app's
existing grant wins. Pinned here (the real function, sourced from the real
script, against a temp apps dir + temp HOME; mdfind is a fake on PATH):

  - "(old)" in the apps dir or ~/Applications → its helper;
  - a pre-swap legacy bundle still under the product name → still found (the
    shell under that name has no helper, so nothing is ever picked by name
    alone); "(old)" wins when both exist;
  - neither fixed home → Spotlight by bundle id (`mdfind kMDItemCFBundleIdentifier
    == 'com.zelin.ai-engineer'`, limited to the two app dirs) — a relocated legacy
    bundle is still found; mdfind is NOT consulted when a fixed home answers;
  - a helper that is not executable, or no bundle at all → non-zero (the chain
    stays in direct mode, its existing fallback).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VAULT_SYNC_SH = REPO_ROOT / "ingest" / "vault-sync.sh"
HELPER = Path("Contents") / "MacOS" / "vault-sync-helper"
OLD_APP = "Zelin's AI Assistant (old).app"
PRODUCT_APP = "Zelin's AI Assistant.app"

FAKE_MDFIND = """#!/bin/bash
printf 'mdfind %s\\n' "$*" >> "$CALLS"
[ -n "${FAKE_MDFIND_RESULT:-}" ] && printf '%s\\n' "$FAKE_MDFIND_RESULT"
exit 0
"""


@unittest.skipIf(sys.platform.startswith("win"), "bash-only ingest chain")
class HelperResolutionTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vault-sync-helper-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.apps = self.tmp / "Applications"
        self.home = self.tmp / "home"
        self.apps.mkdir()
        self.home.mkdir()
        self.calls = self.tmp / "calls.log"
        self.fakebin = self.tmp / "fakebin"
        self.fakebin.mkdir()
        mdfind = self.fakebin / "mdfind"
        mdfind.write_text(FAKE_MDFIND, encoding="utf-8")
        mdfind.chmod(0o755)

    def _plant(self, app_dir, executable=True):
        helper = app_dir / HELPER
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        helper.chmod(0o755 if executable else 0o644)
        return helper

    def _resolve(self, mdfind_result=""):
        env = dict(os.environ, HOME=str(self.home), AIASSISTANT_HOME=str(self.tmp),
                   AIASSISTANT_UI_APPS_DIR=str(self.apps), CALLS=str(self.calls),
                   FAKE_MDFIND_RESULT=mdfind_result,
                   PATH=os.pathsep.join([str(self.fakebin), os.environ.get("PATH", "")]))
        proc = subprocess.run(["bash", "-c", '. "$1"; find_vault_sync_helper', "bash", str(VAULT_SYNC_SH)],
                              env=env, capture_output=True, text=True, timeout=60)
        return proc.returncode, proc.stdout.strip()

    def _mdfind_calls(self):
        return self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else []

    def test_nothing_installed_is_not_found_after_asking_spotlight(self):
        rc, out = self._resolve()
        self.assertEqual((rc, out), (1, ""))
        calls = self._mdfind_calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertIn("kMDItemCFBundleIdentifier == 'com.zelin.ai-engineer'", calls[0])
        self.assertIn("-onlyin %s" % self.apps, calls[0])
        self.assertIn("-onlyin %s" % (self.home / "Applications"), calls[0])

    def test_old_bundle_in_the_apps_dir_wins_without_spotlight(self):
        helper = self._plant(self.apps / OLD_APP)
        rc, out = self._resolve(mdfind_result=str(self.apps / "elsewhere.app"))
        self.assertEqual((rc, out), (0, str(helper)))
        self.assertEqual(self._mdfind_calls(), [], "a fixed home answered — Spotlight not consulted")

    def test_old_bundle_in_home_applications(self):
        helper = self._plant(self.home / "Applications" / OLD_APP)
        self.assertEqual(self._resolve(), (0, str(helper)))

    def test_pre_swap_legacy_under_the_product_name_is_still_found_and_old_is_preferred(self):
        product = self._plant(self.apps / PRODUCT_APP)
        self.assertEqual(self._resolve(), (0, str(product)), "an install that never ran the swap still syncs")
        old = self._plant(self.apps / OLD_APP)
        self.assertEqual(self._resolve(), (0, str(old)), '"(old)" is the legacy app\'s home now')

    def test_p4_shell_helper_is_used_when_no_legacy_app_and_loses_to_the_legacy_when_both(self):
        # §68.13: the shell bundle carries its own helper (shell/build.sh) — a fresh machine syncs
        # through it without Spotlight; the legacy "(old)" grant still wins while it is installed
        shell_helper = self._plant(self.apps / PRODUCT_APP)
        (self.apps / PRODUCT_APP / "Contents" / "MacOS" / "ZelinAIBoard").write_text("", encoding="utf-8")
        self.assertEqual(self._resolve(), (0, str(shell_helper)))
        self.assertEqual(self._mdfind_calls(), [])
        old = self._plant(self.apps / OLD_APP)
        self.assertEqual(self._resolve(), (0, str(old)))

    def test_a_pre_p4_shell_under_the_product_name_has_no_helper_so_spotlight_finds_the_moved_legacy(self):
        # a board shell built before P4 (no vault-sync-helper inside) under the product name
        (self.apps / PRODUCT_APP / "Contents" / "MacOS").mkdir(parents=True)
        (self.apps / PRODUCT_APP / "Contents" / "MacOS" / "ZelinAIBoard").write_text("", encoding="utf-8")
        relocated = self.apps / "Archive" / "Zelin legacy.app"
        helper = self._plant(relocated)
        rc, out = self._resolve(mdfind_result=str(relocated))
        self.assertEqual((rc, out), (0, str(helper)))
        self.assertEqual(len(self._mdfind_calls()), 1)

    def test_spotlight_hit_without_an_executable_helper_is_not_found(self):
        broken = self.apps / "Somewhere.app"
        self._plant(broken, executable=False)
        self.assertEqual(self._resolve(mdfind_result=str(broken)), (1, ""))

    def test_non_executable_helper_in_a_fixed_home_is_not_found(self):
        self._plant(self.apps / OLD_APP, executable=False)
        self.assertEqual(self._resolve(), (1, ""))


if __name__ == "__main__":
    unittest.main()
