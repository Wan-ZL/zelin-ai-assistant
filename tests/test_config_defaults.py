"""Config defaults with privacy consequences.

- execution.create_github_repo (PRIVACY.md egress row 8): approving a card
  must NOT silently create GitHub repos for new users — default False.
- telemetry (PRIVACY.md egress row 10, docs/TELEMETRY.md): default ON at
  level "basic" with the maintainer URL; opt-out via config.yaml or the
  settings_overrides.json keys "telemetry.enabled"/"telemetry.level" (§15).

Explicit config.yaml values (either way) always keep their behavior.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import config


class CreateGithubRepoDefaultTestCase(unittest.TestCase):
    def _load_with_yaml(self, body: str) -> config.Config:
        path = Path(tempfile.mkdtemp(prefix="cfg-defaults-")) / "config.yaml"
        path.write_text(body, encoding="utf-8")
        with mock.patch.object(config, "CONFIG_PATH", path):
            return config.load_config()

    def test_dataclass_default_is_false(self):
        self.assertFalse(config.Config().create_github_repo)

    def test_missing_key_resolves_false(self):
        cfg = self._load_with_yaml("execution:\n  memory_inject: true\n")
        self.assertFalse(cfg.create_github_repo)

    def test_explicit_true_is_honored(self):
        cfg = self._load_with_yaml("execution:\n  create_github_repo: true\n")
        self.assertTrue(cfg.create_github_repo)

    def test_explicit_false_is_honored(self):
        cfg = self._load_with_yaml("execution:\n  create_github_repo: false\n")
        self.assertFalse(cfg.create_github_repo)


class TelemetryDefaultsTestCase(unittest.TestCase):
    def _load_with_yaml(self, body: str) -> config.Config:
        path = Path(tempfile.mkdtemp(prefix="cfg-tele-")) / "config.yaml"
        path.write_text(body, encoding="utf-8")
        with mock.patch.object(config, "CONFIG_PATH", path):
            return config.load_config()

    def test_dataclass_defaults_on_basic_maintainer_url(self):
        cfg = config.Config()
        self.assertTrue(cfg.telemetry_enabled)
        self.assertEqual(cfg.telemetry_level, "basic")
        self.assertEqual(cfg.telemetry_supabase_url,
                         config.DEFAULT_TELEMETRY_SUPABASE_URL)

    def test_missing_block_keeps_defaults(self):
        cfg = self._load_with_yaml("owner:\n  name: X\n")
        self.assertTrue(cfg.telemetry_enabled)
        self.assertEqual(cfg.telemetry_level, "basic")
        self.assertEqual(cfg.telemetry_supabase_url,
                         config.DEFAULT_TELEMETRY_SUPABASE_URL)

    def test_yaml_opt_out_and_level(self):
        cfg = self._load_with_yaml(
            "telemetry:\n  enabled: false\n  level: detailed\n")
        self.assertFalse(cfg.telemetry_enabled)
        self.assertEqual(cfg.telemetry_level, "detailed")

    def test_invalid_level_falls_back_to_basic(self):
        cfg = self._load_with_yaml("telemetry:\n  level: everything\n")
        self.assertEqual(cfg.telemetry_level, "basic")

    def test_empty_url_disables_uploads(self):
        # forks' hard off switch: explicit "" wins over the default URL
        cfg = self._load_with_yaml('telemetry:\n  supabase_url: ""\n')
        self.assertEqual(cfg.telemetry_supabase_url, "")

    def _load_with_overrides(self, data: dict) -> config.Config:
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps(data), encoding="utf-8")
        try:
            return config.load_config()
        finally:
            config.SETTINGS_OVERRIDES_PATH.unlink()

    def test_overrides_flat_keys_win(self):
        cfg = self._load_with_overrides(
            {"telemetry.enabled": False, "telemetry.level": "detailed"})
        self.assertFalse(cfg.telemetry_enabled)
        self.assertEqual(cfg.telemetry_level, "detailed")

    def test_overrides_nested_form_supports_level(self):
        # the Settings form writes the nested shape (shared with the
        # first-run permissions page's TelemetryConsent)
        cfg = self._load_with_overrides(
            {"telemetry": {"enabled": False, "level": "detailed"}})
        self.assertFalse(cfg.telemetry_enabled)
        self.assertEqual(cfg.telemetry_level, "detailed")

    def test_overrides_invalid_level_is_ignored(self):
        cfg = self._load_with_overrides({"telemetry.level": "verbose"})
        self.assertEqual(cfg.telemetry_level, "basic")


class FeatureFlagSemanticsTestCase(unittest.TestCase):
    """Config.feature() (CONTRACT §16): flags default on — known, unknown, or
    absent alike — and only an explicit false turns one off."""

    def test_absent_or_unknown_flag_defaults_on(self):
        self.assertTrue(config.Config().feature("digest"))
        self.assertTrue(config.Config().feature("no_such_flag"))

    def test_explicit_false_is_honored(self):
        cfg = config.Config(features={"digest": False})
        self.assertFalse(cfg.feature("digest"))


if __name__ == "__main__":
    unittest.main()
