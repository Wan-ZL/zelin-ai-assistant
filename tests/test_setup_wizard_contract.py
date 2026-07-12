"""Setup-wizard ↔ Python-side contract guards (CONTRACT §15 v0.14 note).

The Swift setup wizard (mac/Sources/SetupWizard.swift) writes exactly three
things the pipeline consumes:

- settings_overrides.json "language"      (welcome step)
- settings_overrides.json "obsidian_raw"  (vault step, diff-write)
- config/secrets/anthropic-api-key.txt    (engine step, verified key, 0600)

These tests pin the Python side of that contract so a rename/allowlist change
over there can never silently orphan what the wizard writes.
"""
import json
import os
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import config, secrets


def _write_overrides(payload) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        config.SETTINGS_OVERRIDES_PATH.write_text(payload, encoding="utf-8")
    else:
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps(payload), encoding="utf-8")


def _clear_overrides() -> None:
    try:
        config.SETTINGS_OVERRIDES_PATH.unlink()
    except FileNotFoundError:
        pass


class WizardOverrideKeysTestCase(unittest.TestCase):
    """The two overrides keys the wizard writes stay allowlisted."""

    def tearDown(self):
        _clear_overrides()

    def test_wizard_keys_are_allowlisted(self):
        for key in ("language", "obsidian_raw"):
            self.assertIn(key, config._OVERRIDE_FIELDS,
                          "%s left _OVERRIDE_FIELDS - the setup wizard "
                          "writes this key (CONTRACT §15 v0.14)" % key)

    def test_language_override_applies(self):
        _write_overrides({"language": "en"})
        self.assertEqual(config.load_config().language, "en")

    def test_obsidian_raw_override_applies_and_derives_siblings(self):
        vault = Path(TMP_HOME) / "WizardVault"
        raw = vault / "2 - raw"
        _write_overrides({"obsidian_raw": str(raw)})
        cfg = config.load_config()
        self.assertEqual(cfg.obsidian_raw, str(raw))
        # the wizard only writes obsidian_raw; the other three pipeline dirs
        # must re-derive from the new vault root (v0.10.3 契约二)
        self.assertEqual(cfg.obsidian_unprocessed, str(vault / "1 - unprocessed"))
        self.assertEqual(cfg.obsidian_change_summary, str(vault / "3 - change-summary"))
        self.assertEqual(cfg.obsidian_wiki, str(vault / "4 - wiki"))


class WizardOverridesRobustnessTestCase(unittest.TestCase):
    """A corrupt overrides file (the wizard's write target) never crashes
    load_config — the marker-missing/corrupt path must stay survivable."""

    def tearDown(self):
        _clear_overrides()

    def test_corrupt_json_is_ignored(self):
        _write_overrides("{not json at all")
        cfg = config.load_config()  # must not raise
        self.assertIn(cfg.language, ("zh", "en"))

    def test_non_dict_json_is_ignored(self):
        _write_overrides(json.dumps(["language", "en"]))
        config.load_config()  # must not raise

    def test_wrong_typed_values_are_skipped(self):
        _write_overrides({"language": {"nested": True}, "obsidian_raw": 42})
        cfg = config.load_config()  # bad entries skipped, not fatal
        self.assertIsInstance(cfg.language, str)


class WizardSecretsFileTestCase(unittest.TestCase):
    """The engine step saves to the §19 canonical anthropic file name."""

    def test_anthropic_file_name_is_canonical(self):
        # CONTRACT §19: the Swift side (SecretsIO.anthropicFile) and the
        # Python side must agree on this exact name.
        self.assertEqual(secrets.ANTHROPIC_API_KEY_FILE, "anthropic-api-key.txt")

    def test_saved_key_resolves_first(self):
        path = secrets.write_secret(secrets.ANTHROPIC_API_KEY_FILE, "sk-ant-test-123 ")
        try:
            self.assertEqual(secrets.read_secret(secrets.ANTHROPIC_API_KEY_FILE),
                             "sk-ant-test-123")
            # 0600 file / 0700 dir — what the wizard promises in its copy
            if os.name != "nt":  # NTFS has no POSIX mode bits
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(secrets.SECRETS_DIR.stat().st_mode & 0o777, 0o700)
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
