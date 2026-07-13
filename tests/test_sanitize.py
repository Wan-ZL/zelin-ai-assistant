"""act/lib/sanitize.py — built-in secrets masking is default-on (P0-7).

Built-in _SECRET_PATTERNS masking (redaction_mask_secrets, default True) applies
regardless of redaction_enabled; user term-list masking stays opt-in behind
redaction_enabled. config.yaml lives in the sandbox AIASSISTANT_HOME set in
tests/__init__.py and is removed in tearDown.
"""
import tempfile
import unittest
from pathlib import Path

from act.lib import analytics, config, sanitize

SECRET = "sk-ant-api03-abcdefghijklmnop"


class SanitizeTestCase(unittest.TestCase):
    def setUp(self):
        self._cleanup()
        self.tmp = tempfile.TemporaryDirectory(prefix="sanitize-terms-")
        self.terms_file = Path(self.tmp.name) / "terms.txt"
        self.terms_file.write_text("ProjectPhoenix\n", encoding="utf-8")

    def tearDown(self):
        self._cleanup()
        self.tmp.cleanup()

    @staticmethod
    def _cleanup():
        for p in (config.CONFIG_PATH, config.SETTINGS_OVERRIDES_PATH):
            if p.exists():
                p.unlink()

    def _cfg(self, body: str = ""):
        if body:
            config.CONFIG_PATH.write_text(body, encoding="utf-8")
        return config.load_config()

    # -- 内置密钥掩码 default-on ------------------------------------------------ #
    def test_default_config_masks_secret_key(self):
        """默认配置（redaction 未开）也必须掩码 sk-ant- key."""
        cfg = self._cfg()
        self.assertFalse(cfg.redaction_enabled)
        self.assertTrue(cfg.redaction_mask_secrets)
        out, n = sanitize.scrub(f"env leaked {SECRET} on screen", cfg)
        self.assertNotIn(SECRET, out)
        self.assertIn(sanitize.MASK, out)
        self.assertEqual(n, 1)

    def test_mask_secrets_false_disables_builtin(self):
        """显式 mask_secrets: false 才关掉内置掩码."""
        cfg = self._cfg("redaction:\n  mask_secrets: false\n")
        out, n = sanitize.scrub(f"key {SECRET}", cfg)
        self.assertIn(SECRET, out)
        self.assertEqual(n, 0)

    # -- 用户词表维持 opt-in ---------------------------------------------------- #
    def test_user_terms_untouched_by_default(self):
        """redaction_enabled=False 时用户词表不生效."""
        cfg = self._cfg(f'redaction:\n  terms_file: "{self.terms_file.as_posix()}"\n')
        out, n = sanitize.scrub("ProjectPhoenix launch notes", cfg)
        self.assertIn("ProjectPhoenix", out)
        self.assertEqual(n, 0)

    def test_both_active_when_enabled(self):
        """enabled: true 时词表与内置密钥掩码同时生效."""
        cfg = self._cfg(
            "redaction:\n"
            "  enabled: true\n"
            f'  terms_file: "{self.terms_file.as_posix()}"\n'
        )
        out, n = sanitize.scrub(f"ProjectPhoenix uses {SECRET}", cfg)
        self.assertNotIn("ProjectPhoenix", out)
        self.assertNotIn(SECRET, out)
        self.assertEqual(n, 2)

    # -- 只记 count，绝不记内容 -------------------------------------------------- #
    def test_analytics_logs_count_never_content(self):
        events = []
        orig = analytics.log_event
        analytics.log_event = lambda name, **kw: events.append((name, kw))
        try:
            sanitize.scrub(f"leak {SECRET}", self._cfg())
        finally:
            analytics.log_event = orig
        self.assertEqual(events, [("redaction", {"masks": 1})])


if __name__ == "__main__":
    unittest.main()
