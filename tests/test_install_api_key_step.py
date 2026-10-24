"""install.sh step 2: the Anthropic key file and the §78 isolation dirs (v2.0.0).

§19 / §78: every headless claude runs on ANTHROPIC_API_KEY alone (`--bare`
never reads the owner's Claude Code login or the keychain), and the canonical
place for it is config/secrets/anthropic-api-key.txt (dir 0700, file 0600).
`install_api_key` fills that file when it is missing and the key exists in
$ANTHROPIC_API_KEY or ~/.config/anthropic-key.txt: asked in an interactive run
with a terminal, copied directly otherwise. Pinned here by sourcing the real
install.sh against a throwaway $HOME and fixture repo:

  - an existing key file is never touched;
  - $ANTHROPIC_API_KEY wins over the legacy file; the first non-empty line is
    written, trimmed, mode 0600 in a 0700 dir; the key value never reaches
    stdout / stderr;
  - nothing to copy = `anthropic_key=skipped` (a warn, never a failed step);
  - `ensure_claude_config_dirs` creates claude-config/ + cwd/ (0700) and honours
    AIASSISTANT_CLAUDE_CONFIG_DIR, the same seam as act/lib/config.py.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_WIN = sys.platform.startswith("win")
KEY = "sk-ant-fixture-0123456789"


@unittest.skipIf(_WIN, "install.sh is POSIX-only")
class ApiKeyStepTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-key-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.dst = self.repo / "config" / "secrets" / "anthropic-api-key.txt"

    def _run(self, body, env=None, stdin=subprocess.DEVNULL):
        script = ('source "$INSTALL_SH"\nREPO_ROOT="$FIXTURE_REPO"\n' + body
                  + '\nprintf "@@%s" "$REPORT_STEPS"\n')
        full = {"PATH": "/usr/bin:/bin", "HOME": str(self.home),
                "INSTALL_SH": str(REPO / "install.sh"), "FIXTURE_REPO": str(self.repo),
                **(env or {})}
        proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              timeout=60, env=full, stdin=stdin)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn(KEY, proc.stdout + proc.stderr, "the key value is never printed")
        out, _, report = proc.stdout.partition("@@")
        return out, report.strip()

    def _legacy(self, text):
        p = self.home / ".config" / "anthropic-key.txt"
        p.parent.mkdir(parents=True)
        p.write_text(text, encoding="utf-8")

    def test_existing_key_file_is_left_alone(self):
        self.dst.parent.mkdir(parents=True)
        self.dst.write_text("sk-ant-mine\n")
        _, report = self._run("install_api_key", env={"ANTHROPIC_API_KEY": KEY})
        self.assertEqual(report, "anthropic_key=ok:config/secrets/anthropic-api-key.txt present")
        self.assertEqual(self.dst.read_text(), "sk-ant-mine\n")

    def test_env_key_is_copied_trimmed_with_private_modes(self):
        self._legacy("sk-ant-legacy\n")
        _, report = self._run("NON_INTERACTIVE=1\ninstall_api_key",
                              env={"ANTHROPIC_API_KEY": "  %s  \n" % KEY})
        self.assertEqual(report, "anthropic_key=ok:copied from $ANTHROPIC_API_KEY")
        self.assertEqual(self.dst.read_text(), KEY + "\n")
        self.assertEqual(stat.S_IMODE(self.dst.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.dst.parent.stat().st_mode), 0o700)

    def test_legacy_file_is_copied_first_line_only(self):
        self._legacy("\n%s\nsecond-line\n" % KEY)
        _, report = self._run("install_api_key")   # interactive, but stdin is no terminal
        self.assertTrue(report.startswith("anthropic_key=ok:copied from "), report)
        self.assertTrue(report.endswith(".config/anthropic-key.txt"), report)
        self.assertEqual(self.dst.read_text(), KEY + "\n")

    def test_nothing_to_copy_is_a_skip_not_a_failure(self):
        out, report = self._run("install_api_key\nfailed_deploy_steps")
        self.assertEqual(report.splitlines()[0],
                         "anthropic_key=skipped:no key found; paste one in Settings > Credentials")
        self.assertFalse(self.dst.exists())
        self.assertIn("Settings > Credentials", out)

    def test_isolation_dirs_are_private_and_honour_the_override(self):
        target = self.tmp / "cc"
        _, report = self._run("ensure_claude_config_dirs",
                              env={"AIASSISTANT_CLAUDE_CONFIG_DIR": str(target)})
        self.assertEqual(report, "claude_config=ok:%s" % target)
        for d in (target, target / "cwd"):
            self.assertTrue(d.is_dir())
            self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)
        self.assertEqual(os.listdir(target / "cwd"), [], "cwd stays empty")

    def test_default_isolation_dir_matches_config(self):
        _, report = self._run("ensure_claude_config_dirs")
        expected = self.home / "Library" / "Application Support" / "ZelinAIAssistant" / "claude-config"
        self.assertEqual(report, "claude_config=ok:%s" % expected)
        self.assertFalse((self.home / ".claude").exists(), "nothing under the owner's ~/.claude")


if __name__ == "__main__":
    unittest.main()
