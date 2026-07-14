"""act/lib/config.py — Obsidian 管线三目录派生 + settings_overrides 覆盖 (v0.10.3 契约二).

Everything lives under the sandbox AIASSISTANT_HOME set in tests/__init__.py;
config.yaml / settings_overrides.json are written into the sandbox and removed
in tearDown so no other suite sees them. The real vault is never written to.
"""
import json
import tempfile
import unittest
from pathlib import Path

from act.lib import config


class ObsidianDirsTestCase(unittest.TestCase):
    def setUp(self):
        self._cleanup()
        # scratch vault the tests point obsidian_raw at
        self.tmp = tempfile.TemporaryDirectory(prefix="obsidian-vault-")
        self.vault = Path(self.tmp.name) / "MyVault"
        (self.vault / "2 - raw").mkdir(parents=True)

    def tearDown(self):
        self._cleanup()
        self.tmp.cleanup()

    @staticmethod
    def _cleanup():
        for p in (config.CONFIG_PATH, config.SETTINGS_OVERRIDES_PATH):
            if p.exists():
                p.unlink()

    def _write_yaml(self, body: str) -> None:
        config.CONFIG_PATH.write_text(body, encoding="utf-8")

    def _write_overrides(self, data: dict) -> None:
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps(data), encoding="utf-8"
        )

    # -- 默认派生 ------------------------------------------------------------ #
    def test_default_derivation_without_any_config(self):
        """无 config.yaml / overrides 时：内置 vault 默认路径 + 标准目录名."""
        cfg = config.load_config()
        vault = Path(config.DEFAULT_OBSIDIAN_VAULT).expanduser()
        self.assertEqual(cfg.obsidian_unprocessed, str(vault / "1 - unprocessed"))
        self.assertEqual(cfg.obsidian_change_summary, str(vault / "3 - change-summary"))
        self.assertEqual(cfg.obsidian_wiki, str(vault / "4 - wiki"))

    def test_derivation_follows_custom_raw_path(self):
        """obsidian_raw 自定义时，三目录跟随其 parent（vault 根）派生."""
        self._write_yaml(
            f'sources:\n  obsidian_raw: "{(self.vault / "2 - raw").as_posix()}"\n'
        )
        cfg = config.load_config()
        self.assertEqual(cfg.obsidian_unprocessed, str(self.vault / "1 - unprocessed"))
        self.assertEqual(cfg.obsidian_change_summary, str(self.vault / "3 - change-summary"))
        self.assertEqual(cfg.obsidian_wiki, str(self.vault / "4 - wiki"))

    def test_explicit_yaml_key_beats_derivation(self):
        """config.yaml 显式给出的键不被派生覆盖，未给的仍派生."""
        custom_wiki = (self.vault / "elsewhere" / "wiki").as_posix()
        self._write_yaml(
            "sources:\n"
            f'  obsidian_raw: "{(self.vault / "2 - raw").as_posix()}"\n'
            f'  obsidian_wiki: "{custom_wiki}"\n'
        )
        cfg = config.load_config()
        self.assertEqual(cfg.obsidian_wiki, custom_wiki)
        self.assertEqual(cfg.obsidian_unprocessed, str(self.vault / "1 - unprocessed"))
        self.assertEqual(cfg.obsidian_change_summary, str(self.vault / "3 - change-summary"))

    # -- settings_overrides 覆盖 ---------------------------------------------- #
    def test_overrides_flat_keys_win(self):
        """扁平键（与 obsidian_raw 同风格）覆盖 yaml 与派生."""
        self._write_yaml(
            f'sources:\n  obsidian_raw: "{(self.vault / "2 - raw").as_posix()}"\n'
        )
        self._write_overrides({
            "obsidian_unprocessed": "/tmp/ov-unprocessed",
            "obsidian_change_summary": "/tmp/ov-change-summary",
            "obsidian_wiki": "/tmp/ov-wiki",
        })
        cfg = config.load_config()
        self.assertEqual(cfg.obsidian_unprocessed, "/tmp/ov-unprocessed")
        self.assertEqual(cfg.obsidian_change_summary, "/tmp/ov-change-summary")
        self.assertEqual(cfg.obsidian_wiki, "/tmp/ov-wiki")

    def test_overrides_dotted_sources_form(self):
        """契约里的 sources.obsidian_* 点分形式同样生效."""
        self._write_overrides({
            "sources.obsidian_unprocessed": "/tmp/dotted-unprocessed",
            "sources.obsidian_wiki": "/tmp/dotted-wiki",
        })
        cfg = config.load_config()
        self.assertEqual(cfg.obsidian_unprocessed, "/tmp/dotted-unprocessed")
        self.assertEqual(cfg.obsidian_wiki, "/tmp/dotted-wiki")
        # 未覆盖的键仍走默认派生
        vault = Path(config.DEFAULT_OBSIDIAN_VAULT).expanduser()
        self.assertEqual(cfg.obsidian_change_summary, str(vault / "3 - change-summary"))

    def test_overridden_raw_repoints_derivation(self):
        """overrides 只改 obsidian_raw 时，未显式设置的三目录跟着新 vault 派生."""
        self._write_yaml(
            f'sources:\n  obsidian_raw: "{(self.vault / "2 - raw").as_posix()}"\n'
        )
        other = Path(self.tmp.name) / "OtherVault"
        self._write_overrides({"obsidian_raw": str(other / "2 - raw")})
        cfg = config.load_config()
        self.assertEqual(cfg.obsidian_raw, str(other / "2 - raw"))
        self.assertEqual(cfg.obsidian_unprocessed, str(other / "1 - unprocessed"))
        self.assertEqual(cfg.obsidian_change_summary, str(other / "3 - change-summary"))
        self.assertEqual(cfg.obsidian_wiki, str(other / "4 - wiki"))

    def test_empty_or_blank_values_fall_back_to_derivation(self):
        """空串（App 清空输入框）不算显式设置 — 仍派生."""
        self._write_yaml(
            "sources:\n"
            f'  obsidian_raw: "{(self.vault / "2 - raw").as_posix()}"\n'
            '  obsidian_wiki: ""\n'
        )
        self._write_overrides({"obsidian_unprocessed": "  "})
        cfg = config.load_config()
        self.assertEqual(cfg.obsidian_wiki, str(self.vault / "4 - wiki"))
        self.assertEqual(cfg.obsidian_unprocessed, str(self.vault / "1 - unprocessed"))

    # -- 真实 vault 只读核对 --------------------------------------------------- #
    @unittest.skipUnless(
        Path(config.DEFAULT_OBSIDIAN_VAULT).expanduser().is_dir(),
        "real Obsidian vault not present on this machine",
    )
    def test_real_vault_derived_dirs_exist(self):
        """只读核对：默认派生出的三目录在真实 vault 里确实存在."""
        cfg = config.load_config()
        for p in (cfg.obsidian_unprocessed, cfg.obsidian_change_summary, cfg.obsidian_wiki):
            self.assertTrue(Path(p).is_dir(), f"missing real dir: {p}")


class EffectiveObsidianRawTestCase(unittest.TestCase):
    """effective_obsidian_raw — vault-mirror mode routing (claude TCC isolation).

    Readers (radar, weekly digest) must use the repo-local mirror ONLY when
    the ingest chain declared mirror mode AND the mirrored raw dir exists;
    every other state falls back to the real vault (mirror mode is an
    upgrade, never a requirement — Linux/Windows/fresh installs never see it).
    """

    MODE_FILE = config.STATE_DIR / "vault_sync_mode"
    MIRROR_RAW = config.STATE_DIR / "vault-mirror" / "2 - raw"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="obsidian-vault-")
        self.vault_raw = Path(self.tmp.name) / "MyVault" / "2 - raw"
        self.vault_raw.mkdir(parents=True)
        self.cfg = config.Config()
        self.cfg.obsidian_raw = str(self.vault_raw)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        if self.MODE_FILE.exists():
            self.MODE_FILE.unlink()
        if self.MIRROR_RAW.exists():
            self.MIRROR_RAW.rmdir()
        self.tmp.cleanup()

    def _set_mode(self, mode: str) -> None:
        self.MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.MODE_FILE.write_text(mode + "\n", encoding="utf-8")

    def test_no_mode_file_reads_the_real_vault(self):
        self.assertEqual(config.effective_obsidian_raw(self.cfg), self.vault_raw)

    def test_direct_mode_reads_the_real_vault(self):
        self._set_mode("direct")
        self.assertEqual(config.effective_obsidian_raw(self.cfg), self.vault_raw)

    def test_mirror_mode_with_mirror_present_reads_the_mirror(self):
        self._set_mode("mirror")
        self.MIRROR_RAW.mkdir(parents=True)
        self.assertEqual(config.effective_obsidian_raw(self.cfg), self.MIRROR_RAW)

    def test_mirror_mode_without_mirror_dir_falls_back_to_the_vault(self):
        # chain said mirror but the mirror is gone (wiped state/, first run
        # after an update) — never point readers at a missing dir.
        self._set_mode("mirror")
        self.assertEqual(config.effective_obsidian_raw(self.cfg), self.vault_raw)

    def test_unset_obsidian_raw_returns_none(self):
        self.cfg.obsidian_raw = ""
        self.assertIsNone(config.effective_obsidian_raw(self.cfg))


if __name__ == "__main__":
    unittest.main()
