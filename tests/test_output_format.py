"""default_output_format setting (CONTRACT §15 v0.28) — the Settings 「交付物默认
格式」 picker diff-writes the flat key ``default_output_format`` (markdown|html)
to state/settings_overrides.json (same pattern as language/voice_enabled).

Two halves:
1. Config layering: override → config.yaml top-level → built-in default
   "markdown"; invalid/typo values fail safe to markdown (yaml AND override
   paths). Regression guard: the key must stay in config._OVERRIDE_FIELDS, else
   the app picker is silently ignored by the pipeline.
2. Executor wire: build_prompt injects an HTML-authoring block ONLY when the
   effective format is html — markdown leaves the prompt byte-identical (zero
   regression for the default install).
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import executor
from act.lib import config
from act.lib.registry import Requirement


class OutputFormatConfigTestCase(unittest.TestCase):
    def _load(self, overrides: dict, yaml_body: str = "") -> config.Config:
        tmp = Path(tempfile.mkdtemp(prefix="cfg-outfmt-"))
        cfg_path = tmp / "config.yaml"
        cfg_path.write_text(yaml_body, encoding="utf-8")
        ov_path = tmp / "settings_overrides.json"
        ov_path.write_text(json.dumps(overrides), encoding="utf-8")
        with mock.patch.object(config, "CONFIG_PATH", cfg_path), \
                mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", ov_path):
            return config.load_config()

    def test_default_is_markdown(self):
        self.assertEqual(self._load({}).default_output_format, "markdown")

    def test_yaml_html_honored(self):
        cfg = self._load({}, yaml_body="default_output_format: html\n")
        self.assertEqual(cfg.default_output_format, "html")

    def test_yaml_typo_degrades_to_markdown(self):
        cfg = self._load({}, yaml_body="default_output_format: HTLM\n")
        self.assertEqual(cfg.default_output_format, "markdown")

    def test_yaml_uppercase_normalized(self):
        cfg = self._load({}, yaml_body="default_output_format: HTML\n")
        self.assertEqual(cfg.default_output_format, "html")

    def test_app_override_html_wins_over_yaml_markdown(self):
        # the exact gap this guards: default_output_format missing from
        # _OVERRIDE_FIELDS would make the app picker a no-op.
        cfg = self._load({"default_output_format": "html"},
                         yaml_body="default_output_format: markdown\n")
        self.assertEqual(cfg.default_output_format, "html")

    def test_app_override_typo_fails_safe_to_markdown(self):
        cfg = self._load({"default_output_format": "pdf"},
                         yaml_body="default_output_format: html\n")
        self.assertEqual(cfg.default_output_format, "markdown")

    def test_key_registered_in_override_fields(self):
        self.assertIn("default_output_format", config._OVERRIDE_FIELDS)


class OutputFormatPromptTestCase(unittest.TestCase):
    def _req(self):
        return Requirement(id="R-100", title="Draft the weekly update",
                           summary="Write a short status note.")

    def _prompt(self, fmt: str) -> str:
        cfg = config.Config()
        cfg.memory_inject = False  # stay off the real ~/.claude memory
        cfg.voice_enabled = False  # keep the prompt minimal/deterministic
        cfg.default_output_format = fmt
        with tempfile.TemporaryDirectory(prefix="outfmt-target-") as td:
            return executor.build_prompt(self._req(), cfg, target=Path(td))

    def test_html_injects_authoring_block(self):
        prompt = self._prompt("html")
        self.assertIn("OUTPUT FORMAT", prompt)
        self.assertIn("HTML", prompt)

    def test_markdown_injects_nothing(self):
        # default path must not carry the HTML block — proves zero regression.
        self.assertNotIn("OUTPUT FORMAT", self._prompt("markdown"))

    def test_html_block_precedes_delivery_instruction(self):
        prompt = self._prompt("html")
        self.assertLess(prompt.index("OUTPUT FORMAT"), prompt.index("When finished"))


if __name__ == "__main__":
    unittest.main()
