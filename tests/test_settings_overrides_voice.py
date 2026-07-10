"""voice_enabled settings-override wire (docs/VOICE.md master switch).

The Settings window's 「启用语气注入 · Voice injection」 toggle diff-writes the
flat key ``voice_enabled`` to state/settings_overrides.json (same pattern as
redaction_enabled/skip_permissions). Layering must be: override → config.yaml
``voice.enabled`` → built-in default True. Regression guard: the key must stay
listed in config._OVERRIDE_FIELDS — without it the app toggle is silently
ignored by the pipeline.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import config


class VoiceEnabledOverrideTestCase(unittest.TestCase):
    def _load(self, overrides: dict, yaml_body: str = "") -> config.Config:
        tmp = Path(tempfile.mkdtemp(prefix="cfg-voice-ov-"))
        cfg_path = tmp / "config.yaml"
        cfg_path.write_text(yaml_body, encoding="utf-8")
        ov_path = tmp / "settings_overrides.json"
        ov_path.write_text(json.dumps(overrides), encoding="utf-8")
        with mock.patch.object(config, "CONFIG_PATH", cfg_path), \
                mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", ov_path):
            return config.load_config()

    def test_default_is_on(self):
        self.assertTrue(self._load({}).voice_enabled)

    def test_yaml_voice_enabled_false_honored(self):
        cfg = self._load({}, yaml_body="voice:\n  enabled: false\n")
        self.assertFalse(cfg.voice_enabled)

    def test_app_override_off_wins_over_yaml_on(self):
        # the exact gap this guards: voice_enabled missing from
        # _OVERRIDE_FIELDS made the app toggle a no-op.
        cfg = self._load({"voice_enabled": False},
                         yaml_body="voice:\n  enabled: true\n")
        self.assertFalse(cfg.voice_enabled)

    def test_app_override_on_wins_over_yaml_off(self):
        cfg = self._load({"voice_enabled": True},
                         yaml_body="voice:\n  enabled: false\n")
        self.assertTrue(cfg.voice_enabled)


if __name__ == "__main__":
    unittest.main()
