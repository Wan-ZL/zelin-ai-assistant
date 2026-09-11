"""act/lib/config.py CLI — ``--print-path`` / ``--print-value`` used by the ingest
scripts (P1-6; §71 for the value half).

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

    def test_print_path_and_print_value_are_mutually_exclusive(self):
        proc = self._run("--print-path", "obsidian_raw",
                         "--print-value", "recording_media_retention_minutes")
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    # -- §71 --print-value：prune 脚本读保留期的那条缝 ------------------------ #
    def test_print_value_default(self):
        proc = self._run("--print-value", "recording_media_retention_minutes")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), str(config.DEFAULT_MEDIA_RETENTION_MINUTES))

    def test_print_value_reads_config_yaml(self):
        self._write_yaml("recording:\n  media_retention_minutes: 180\n")
        proc = self._run("--print-value", "recording_media_retention_minutes")
        self.assertEqual(proc.stdout.strip(), "180")

    def test_print_value_clamps_and_falls_back_on_junk(self):
        self._write_yaml("recording:\n  media_retention_minutes: 1\n")
        self.assertEqual(self._run("--print-value", "recording_media_retention_minutes").stdout.strip(),
                         str(config.MIN_MEDIA_RETENTION_MINUTES))
        self._write_yaml("recording:\n  media_retention_minutes: soon\n")
        self.assertEqual(self._run("--print-value", "recording_media_retention_minutes").stdout.strip(),
                         str(config.DEFAULT_MEDIA_RETENTION_MINUTES))

    def test_print_value_reads_the_override(self):
        (self.home / "state").mkdir(parents=True, exist_ok=True)
        (self.home / "state" / "settings_overrides.json").write_text(
            '{"recording_media_retention_minutes": 240}', encoding="utf-8")
        proc = self._run("--print-value", "recording_media_retention_minutes")
        self.assertEqual(proc.stdout.strip(), "240")

    def test_print_value_unknown_key_fails(self):
        proc = self._run("--print-value", "not_a_key")
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")


class ConfigCliInProcessTestCase(unittest.TestCase):
    """同一个 ``main()``，在进程内跑一遍（子进程那几条量的是「脚本调用它」的
    契约，这里量的是函数本身——包括加载炸了也照打默认值那条 silent-on-error 路）。"""

    def _main(self, *args) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = config.main(list(args))
        self.assertEqual(rc, 0)
        return buf.getvalue().strip()

    def test_print_value_prints_the_effective_value(self):
        with mock.patch.object(config, "load_config") as load:
            load.return_value = config.Config(recording_media_retention_minutes=123)
            self.assertEqual(self._main("--print-value", "recording_media_retention_minutes"), "123")

    def test_print_value_prints_the_default_when_loading_blows_up(self):
        with mock.patch.object(config, "load_config", side_effect=RuntimeError("boom")):
            self.assertEqual(self._main("--print-value", "recording_media_retention_minutes"),
                             str(config.DEFAULT_MEDIA_RETENTION_MINUTES))

    def test_print_value_prints_the_default_when_the_field_is_none(self):
        cfg = config.Config()
        cfg.recording_media_retention_minutes = None       # type: ignore[assignment]
        with mock.patch.object(config, "load_config", return_value=cfg):
            self.assertEqual(self._main("--print-value", "recording_media_retention_minutes"),
                             str(config.DEFAULT_MEDIA_RETENTION_MINUTES))


if __name__ == "__main__":
    unittest.main()
