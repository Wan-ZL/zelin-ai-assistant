"""settings_overrides.json execution keys (CONTRACT §15.3 v0.14, add-only).

The Settings window promotes three execution.* config keys to in-app
controls: default_target_repo / skip_permissions / create_github_repo.
The app diff-writes them (key present only when the user's choice differs
from the config.yaml/default effective value); the read side must simply
honor a present key and fall back when absent.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import config


class ExecutionOverridesTestCase(unittest.TestCase):
    def _load_with_overrides(self, data: dict, yaml_body: str = "") -> config.Config:
        tmp = Path(tempfile.mkdtemp(prefix="cfg-exec-ov-"))
        cfg_path = tmp / "config.yaml"
        cfg_path.write_text(yaml_body, encoding="utf-8")
        ov_path = tmp / "settings_overrides.json"
        ov_path.write_text(json.dumps(data), encoding="utf-8")
        with mock.patch.object(config, "CONFIG_PATH", cfg_path), \
                mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", ov_path):
            return config.load_config()

    def test_default_target_repo_override_wins(self):
        cfg = self._load_with_overrides(
            {"default_target_repo": "~/Projects/my-workbench"},
            yaml_body="execution:\n  default_target_repo: ~/elsewhere\n",
        )
        self.assertEqual(cfg.default_target_repo, "~/Projects/my-workbench")

    def test_absent_keys_fall_back_to_yaml_then_defaults(self):
        cfg = self._load_with_overrides(
            {}, yaml_body="execution:\n  skip_permissions: false\n")
        self.assertFalse(cfg.skip_permissions)          # from config.yaml
        self.assertFalse(cfg.create_github_repo)        # built-in default
        self.assertEqual(cfg.default_target_repo,
                         "~/Projects/your-workbench")   # built-in default

    def test_skip_permissions_override_wins_over_yaml(self):
        cfg = self._load_with_overrides(
            {"skip_permissions": False},
            yaml_body="execution:\n  skip_permissions: true\n",
        )
        self.assertFalse(cfg.skip_permissions)

    def test_create_github_repo_override_enables(self):
        cfg = self._load_with_overrides({"create_github_repo": True})
        self.assertTrue(cfg.create_github_repo)

    def test_bad_entry_is_skipped_good_execution_key_still_applies(self):
        cfg = self._load_with_overrides(
            {"show_cost_above_usd": "not-a-number",   # float() raises → skipped
             "default_target_repo": "~/Projects/wb"})
        self.assertEqual(cfg.show_cost_above_usd, 5.0)  # default kept
        self.assertEqual(cfg.default_target_repo, "~/Projects/wb")


if __name__ == "__main__":
    unittest.main()
