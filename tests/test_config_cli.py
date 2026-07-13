"""act/lib/config.py CLI — ``--print-path`` used by the ingest scripts (P1-6).

Runs the module as a subprocess exactly the way the shell scripts do, with a
per-test sandboxed AIASSISTANT_HOME so the real config.yaml is never read.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from act.lib import config

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VAULT = Path(config.DEFAULT_OBSIDIAN_VAULT).expanduser()


class ConfigCliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="config-cli-home-")
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args):
        env = dict(os.environ, AIASSISTANT_HOME=str(self.home))
        return subprocess.run(
            [sys.executable, "-m", "act.lib.config", *args],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

    def _write_yaml(self, body: str) -> None:
        (self.home / "config.yaml").write_text(body, encoding="utf-8")

    # -- 默认（无 config.yaml） ------------------------------------------------ #
    def test_default_unprocessed_without_config(self):
        proc = self._run("--print-path", "obsidian_unprocessed")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), str(DEFAULT_VAULT / "1 - unprocessed"))

    def test_default_raw_without_config(self):
        proc = self._run("--print-path", "obsidian_raw")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), str(DEFAULT_VAULT / "2 - raw"))

    # -- config.yaml 生效 ------------------------------------------------------ #
    def test_configured_vault_repoints_unprocessed(self):
        vault = self.home / "MyVault"
        self._write_yaml(f'sources:\n  obsidian_raw: "{(vault / "2 - raw").as_posix()}"\n')
        proc = self._run("--print-path", "obsidian_unprocessed")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), str(vault / "1 - unprocessed"))

    def test_explicit_key_with_tilde_is_expanded(self):
        self._write_yaml(
            'sources:\n  obsidian_unprocessed: "~/SomeVault/1 - unprocessed"\n'
        )
        proc = self._run("--print-path", "obsidian_unprocessed")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            proc.stdout.strip(), str(Path("~/SomeVault/1 - unprocessed").expanduser())
        )

    # -- silent-on-error ------------------------------------------------------- #
    def test_malformed_yaml_prints_default(self):
        self._write_yaml("sources: [\n")  # unclosed flow sequence -> YAMLError
        proc = self._run("--print-path", "obsidian_unprocessed")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), str(DEFAULT_VAULT / "1 - unprocessed"))
        self.assertEqual(proc.stderr, "")

    # -- 非法用法 → 非零退出（脚本侧走 fallback） ------------------------------- #
    def test_unknown_key_fails(self):
        proc = self._run("--print-path", "not_a_key")
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_missing_flag_fails(self):
        proc = self._run()
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main()
