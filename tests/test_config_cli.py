"""act/lib/config.py CLI — ``--print-path`` / ``--print-value`` used by the ingest scripts
(P1-6；``--print-value`` = CONTRACT §72.4 的媒体保留分钟数，screenpipe-cleanup.sh 的消费面).

Runs the module as a subprocess exactly the way the shell scripts do, with a
per-test sandboxed AIASSISTANT_HOME so the real config.yaml is never read.
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    # -- 相对路径锚定 AIASSISTANT_HOME（cron 消费方 cwd 不定） ------------------ #
    def test_relative_path_is_anchored_at_home(self):
        self._write_yaml('sources:\n  obsidian_raw: "rel/2 - raw"\n')
        proc = self._run("--print-path", "obsidian_raw")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), str(self.home / "rel/2 - raw"))

    def test_relative_derived_dir_is_anchored_too(self):
        self._write_yaml('sources:\n  obsidian_raw: "rel/2 - raw"\n')
        proc = self._run("--print-path", "obsidian_unprocessed")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(),
                         str(self.home / "rel/1 - unprocessed"))

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

    # -- --print-value（§72.4 媒体保留分钟数：cron 拿它当 find 的参数） ---------- #
    def test_print_value_without_config_is_the_factory_default(self):
        proc = self._run("--print-value", "screenpipe_media_retention_minutes")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), str(config.DEFAULT_MEDIA_RETENTION_MINUTES))

    def test_print_value_reads_config_then_overrides(self):
        self._write_yaml("recording:\n  media_retention_minutes: 600\n")
        self.assertEqual(self._run("--print-value", "screenpipe_media_retention_minutes").stdout.strip(), "600")
        (self.home / "state").mkdir(exist_ok=True)
        (self.home / "state" / "settings_overrides.json").write_text(
            '{"screenpipe_media_retention_minutes": 120}', encoding="utf-8")
        self.assertEqual(self._run("--print-value", "screenpipe_media_retention_minutes").stdout.strip(), "120")

    def test_print_value_is_silent_on_error(self):
        self._write_yaml("recording: [\n")      # 坏 YAML：cron 仍要拿到一个能用的数
        proc = self._run("--print-value", "screenpipe_media_retention_minutes")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), str(config.DEFAULT_MEDIA_RETENTION_MINUTES))
        self.assertEqual(proc.stderr, "")

    def test_print_value_unknown_key_fails_and_the_two_flags_are_exclusive(self):
        self.assertNotEqual(self._run("--print-value", "not_a_key").returncode, 0)
        both = self._run("--print-path", "obsidian_raw", "--print-value", "screenpipe_media_retention_minutes")
        self.assertNotEqual(both.returncode, 0)

    def test_print_value_in_process_falls_back_when_the_config_layer_blows_up(self):
        # 进程内跑同一条路径：load_config 整个炸了（坏权限 / 坏 YAML 之外的任何意外）也要打出厂值、退出 0
        buf = io.StringIO()
        with mock.patch.object(config, "load_config", side_effect=RuntimeError("boom")), \
             contextlib.redirect_stdout(buf):
            rc = config.main(["--print-value", "screenpipe_media_retention_minutes"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), str(config.DEFAULT_MEDIA_RETENTION_MINUTES))


if __name__ == "__main__":
    unittest.main()
