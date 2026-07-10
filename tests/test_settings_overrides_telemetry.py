"""act/lib/config.py — telemetry.enabled settings override (v0.13, §15 note).

The Mac app's first-run permissions page writes {"telemetry": {"enabled":
false}} into state/settings_overrides.json when the user unchecks the
anonymous-usage-stats checkbox. Only the ``enabled`` flag is app-overridable;
``supabase_url`` / ``key_path`` must stay config.yaml-only.

Everything lives under the sandbox AIASSISTANT_HOME set in tests/__init__.py.
"""
import json
import unittest

from act.lib import config


class TelemetryOverrideTestCase(unittest.TestCase):
    def setUp(self):
        self._cleanup()

    def tearDown(self):
        self._cleanup()

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

    def test_nested_disable_wins_over_yaml(self):
        """{"telemetry": {"enabled": false}} beats config.yaml enabled: true."""
        self._write_yaml("telemetry:\n  enabled: true\n")
        self._write_overrides({"telemetry": {"enabled": False}})
        cfg = config.load_config()
        self.assertFalse(cfg.telemetry_enabled)

    def test_nested_enable(self):
        self._write_overrides({"telemetry": {"enabled": True}})
        cfg = config.load_config()
        self.assertTrue(cfg.telemetry_enabled)

    def test_flat_form(self):
        """Flat "telemetry.enabled" mirrors the features.* flat convention."""
        self._write_yaml("telemetry:\n  enabled: true\n")
        self._write_overrides({"telemetry.enabled": False})
        cfg = config.load_config()
        self.assertFalse(cfg.telemetry_enabled)

    def test_other_telemetry_keys_are_not_overridable(self):
        """supabase_url / key_path in overrides are ignored (yaml-only)."""
        self._write_yaml(
            "telemetry:\n"
            "  enabled: false\n"
            '  supabase_url: "https://example.supabase.co"\n'
        )
        self._write_overrides({
            "telemetry": {
                "enabled": True,
                "supabase_url": "https://evil.example",
                "key_path": "/tmp/evil-key",
            }
        })
        cfg = config.load_config()
        self.assertTrue(cfg.telemetry_enabled)  # enabled IS overridable
        self.assertEqual(cfg.telemetry_supabase_url, "https://example.supabase.co")
        self.assertNotEqual(cfg.telemetry_key_path, "/tmp/evil-key")

    def test_absent_override_keeps_yaml_value(self):
        self._write_yaml("telemetry:\n  enabled: true\n")
        self._write_overrides({"language": "en"})
        cfg = config.load_config()
        self.assertTrue(cfg.telemetry_enabled)

    def test_malformed_telemetry_value_is_ignored(self):
        """A non-dict telemetry override must not crash nor flip the flag."""
        self._write_yaml("telemetry:\n  enabled: true\n")
        self._write_overrides({"telemetry": "nope"})
        cfg = config.load_config()
        self.assertTrue(cfg.telemetry_enabled)


if __name__ == "__main__":
    unittest.main()
