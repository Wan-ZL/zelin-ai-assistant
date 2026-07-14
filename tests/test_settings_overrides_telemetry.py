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


class _OverridesSandboxBase(unittest.TestCase):
    """setUp/helpers shared by the extra override suites (no test methods of
    its own, so subclassing does not re-run the telemetry suite above)."""

    def setUp(self):
        self._cleanup()
        self.addCleanup(self._cleanup)

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


class StringBoolOverrideTestCase(_OverridesSandboxBase):
    """布尔键的字符串值：bool("false") == True 的经典坑 —— 用户手写 "false"/
    "no" 想关遥测/关 --dangerously-skip-permissions，粗暴 bool() 会把关闭
    意图反转成全开（CONTRACT §15：关闭意图必须可靠生效）。"""

    def test_string_false_disables_everything_it_says(self):
        self._write_overrides({
            "telemetry": {"enabled": "false", "capture_input": "false"},
            "skip_permissions": "no",
            "gmail_enabled": "false",
            "features": {"digest": "false"},
        })
        cfg = config.load_config()
        self.assertFalse(cfg.telemetry_enabled)
        self.assertFalse(cfg.telemetry_capture_input)
        self.assertTrue(cfg.telemetry_capture_input_explicit)  # 有效值=知情选择
        self.assertFalse(cfg.capture_input_active())
        self.assertFalse(cfg.skip_permissions)
        self.assertFalse(cfg.gmail_enabled)
        self.assertFalse(cfg.feature("digest"))

    def test_string_true_still_enables(self):
        self._write_yaml("telemetry:\n  enabled: false\n")
        self._write_overrides({"telemetry": {"enabled": "true"}})
        self.assertTrue(config.load_config().telemetry_enabled)

    def test_garbage_bool_is_skipped_keeps_effective_value(self):
        """类型完全说不通的值（"banana"）按 docstring「wrong types are
        silently ignored」跳过 —— 保留原生效值，且不算知情选择。"""
        self._write_yaml("telemetry:\n  enabled: true\n")
        self._write_overrides({
            "telemetry": {"enabled": "banana", "capture_input": "banana"},
            "skip_permissions": [1, 2],
        })
        cfg = config.load_config()
        self.assertTrue(cfg.telemetry_enabled)             # 保留 yaml 值
        self.assertTrue(cfg.telemetry_capture_input)       # 保留默认
        self.assertFalse(cfg.telemetry_capture_input_explicit)  # 坏值≠知情选择
        self.assertTrue(cfg.skip_permissions)

    def test_yaml_quoted_false_also_honored(self):
        """config.yaml 路径同病：sources.gmail.enabled: "false" 不能变 True。"""
        self._write_yaml(
            'sources:\n  gmail:\n    enabled: "false"\n'
            'execution:\n  skip_permissions: "no"\n'
        )
        cfg = config.load_config()
        self.assertFalse(cfg.gmail_enabled)
        self.assertFalse(cfg.skip_permissions)


class LanguageOverrideTestCase(_OverridesSandboxBase):
    """overrides 的 language 与 yaml 路径一致：strip 后非空才生效（空串会让
    双语文案选择器拿到既非 zh 也非 en 的值）。"""

    def test_empty_language_falls_back_to_default(self):
        self._write_overrides({"language": ""})
        self.assertEqual(config.load_config().language, "zh")

    def test_blank_language_keeps_yaml_value(self):
        self._write_yaml("language: en\n")
        self._write_overrides({"language": "   "})
        self.assertEqual(config.load_config().language, "en")

    def test_language_is_stripped(self):
        self._write_overrides({"language": "  en  "})
        self.assertEqual(config.load_config().language, "en")


if __name__ == "__main__":
    unittest.main()
