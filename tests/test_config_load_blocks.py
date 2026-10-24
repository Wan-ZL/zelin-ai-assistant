"""config.load_config — every config.yaml block applier on its own (§15 / §26 /
§54 / §59 / §63 / §72 / §79).

Characterization net: non-mapping blocks read as {} and keep the defaults; the
yaml reader's three failure shapes; the execution block's one surviving pin
(claude_bin); redaction relative path anchor; recording lists; updates / recap /
language coercion; the block order. v2.0.0 retired the owner / approval /
retention / digest / registry / telemetry / switch / maintainer / feedback-sync /
features / self-improve appliers with the Act pipeline (full text in tag ``full``)
— their keys in an old config.yaml are read and ignored.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config


def _load(body: str) -> config.Config:
    path = Path(tempfile.mkdtemp(prefix="cfg-blocks-")) / "config.yaml"
    path.write_text(body, encoding="utf-8")
    with mock.patch.object(config, "CONFIG_PATH", path), \
            mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", path.with_name("none.json")):
        return config.load_config()


class YamlReaderTestCase(unittest.TestCase):
    def test_config_path_fallback(self):
        with mock.patch.object(config, "CONFIG_PATH", Path("/nonexistent/config.yaml")):
            self.assertEqual(config._config_path(), config.CONFIG_EXAMPLE_PATH)

    def test_load_yaml_dict_shapes(self):
        tmp = Path(tempfile.mkdtemp(prefix="cfg-yaml-"))
        self.assertEqual(config._load_yaml_dict(tmp / "missing.yaml"), {})
        bad = tmp / "bad.yaml"
        bad.write_text("a: [unclosed", encoding="utf-8")
        self.assertEqual(config._load_yaml_dict(bad), {})
        lst = tmp / "list.yaml"
        lst.write_text("- 1\n- 2\n", encoding="utf-8")
        self.assertEqual(config._load_yaml_dict(lst), {})
        ok = tmp / "ok.yaml"
        ok.write_text("owner:\n  name: Z\n", encoding="utf-8")
        self.assertEqual(config._load_yaml_dict(ok), {"owner": {"name": "Z"}})
        with mock.patch.object(config, "yaml", None):
            self.assertEqual(config._load_yaml_dict(ok), {})

    def test_non_mapping_blocks_keep_defaults(self):
        cfg = _load("sources: [1]\nexecution: null\nserver: x\nmodels: x\nrecording: x\n"
                    "redaction: x\nupdates: x\nrecap: x\nlanguage: 5\n")
        default = config.Config()
        for attr in ("claude_bin", "server_port", "models_pipeline",
                     "models_fallback", "recording_ignored_apps", "screenpipe_retention_days",
                     "redaction_enabled", "updates_check_enabled", "updates_auto_install",
                     "recap_enabled", "recap_default_language", "language"):
            self.assertEqual(getattr(cfg, attr), getattr(default, attr), attr)

    def test_retired_act_blocks_are_ignored_not_fatal(self):
        cfg = _load("approval:\n  poll_interval_seconds: 30\ntelemetry:\n  level: detailed\n"
                    "digest:\n  frequency: daily\nregistry:\n  backend: sqlite\n"
                    "self_improve:\n  enabled: true\nmaintainer:\n  repo_path: /r\n")
        for gone in ("poll_interval_seconds", "telemetry_level", "digest_frequency",
                     "registry_backend", "maintainer_repo_path"):
            self.assertFalse(hasattr(cfg, gone), gone)
        self.assertEqual(cfg.raw["approval"], {"poll_interval_seconds": 30})


class SourcesExecutionTestCase(unittest.TestCase):
    def test_obsidian_dirs_and_retired_source_switches(self):
        cfg = _load("sources:\n  obsidian_raw: /v/2 - raw\n  slack:\n    enabled: true\n")
        self.assertEqual(cfg.obsidian_raw, "/v/2 - raw")
        self.assertEqual(cfg.obsidian_wiki, "/v/4 - wiki")
        self.assertFalse(hasattr(cfg, "slack_enabled"))

    def test_execution_block_keeps_only_the_claude_bin_pin(self):
        cfg = _load("execution:\n  default_target_repo: /r\n  claude_bin: '  '\n"
                    "  dispatch_max_failures: -3\n")
        self.assertIsNone(cfg.claude_bin)
        self.assertFalse(hasattr(cfg, "default_target_repo"))
        cfg = _load("execution:\n  claude_bin: ' /c '\n  skip_permissions: 1\n")
        self.assertEqual(cfg.claude_bin, "/c")
        self.assertFalse(hasattr(cfg, "skip_permissions"))


class RedactionRecordingTestCase(unittest.TestCase):
    def test_redaction_relative_terms_anchor(self):
        cfg = _load("redaction:\n  terms_file: rel/terms.txt\n")
        self.assertEqual(cfg.redaction_terms_file, str(config.HOME / "rel/terms.txt"))
        cfg = _load("redaction:\n  terms_file: ~/abs.txt\n")
        self.assertEqual(cfg.redaction_terms_file, "~/abs.txt")
        cfg = _load("redaction:\n  terms_file: ''\n")
        self.assertEqual(cfg.redaction_terms_file, "")
        cfg = _load("redaction:\n  enabled: 'yes'\n  mask_secrets: junk\n")
        self.assertTrue(cfg.redaction_enabled)
        self.assertTrue(cfg.redaction_mask_secrets)

    def test_recording_apps(self):
        cfg = _load("recording:\n  ignored_apps:\n    - ' Zoom '\n    - null\n    - ''\n    - 7\n")
        self.assertEqual(cfg.recording_ignored_apps, ["Zoom", "7"])
        cfg = _load("recording:\n  ignored_apps: Zoom\n")
        self.assertEqual(cfg.recording_ignored_apps, config.Config().recording_ignored_apps)
        cfg = _load("recording:\n  ignored_apps: []\n")
        self.assertEqual(cfg.recording_ignored_apps, [])

    def test_recording_retention_edges(self):
        cfg = _load("recording:\n  retention_days: -4\n  media_retention_minutes: 3\n")
        self.assertEqual(cfg.screenpipe_retention_days, 0)
        self.assertEqual(cfg.screenpipe_media_retention_minutes, 60)
        cfg = _load("recording:\n  retention_days: '14'\n  media_retention_minutes: 120\n")
        self.assertEqual(cfg.screenpipe_retention_days, 14)
        self.assertEqual(cfg.screenpipe_media_retention_minutes, 120)


class MiscBlocksTestCase(unittest.TestCase):
    def test_updates_block(self):
        cfg = _load("updates:\n  check_enabled: 0\n  auto_install: 'yes'\n")
        self.assertFalse(cfg.updates_check_enabled)
        self.assertTrue(cfg.updates_auto_install)
        cfg = _load("updates:\n  check_enabled: maybe\n  auto_install: junk\n")
        self.assertTrue(cfg.updates_check_enabled)
        self.assertFalse(cfg.updates_auto_install)   # §79: default off, junk keeps it off

    def test_recap_and_language(self):
        cfg = _load("language: ' en '\nrecap:\n  enabled: 'no'\n  default_language: ZH\n")
        self.assertEqual(cfg.language, "en")
        self.assertFalse(cfg.recap_enabled)
        self.assertEqual(cfg.recap_default_language, "zh")
        cfg = _load("language: '  '\nrecap:\n  default_language: fr\n")
        self.assertEqual(cfg.language, config.Config().language)
        self.assertEqual(cfg.recap_default_language, "auto")

    def test_server_port_and_models(self):
        cfg = _load("server:\n  port: 70000\nmodels:\n  dispatch: claude-opus-5\n  pipeline: 7\n")
        self.assertEqual(cfg.server_port, config.DEFAULT_SERVER_PORT)
        self.assertEqual(cfg.models_pipeline, config.DEFAULT_MODEL_PIPELINE)
        self.assertFalse(hasattr(cfg, "models_dispatch"))
        cfg = _load("server:\n  port: 8123\nmodels:\n  pipeline: claude-sonnet-5\n  fallback: 'off'\n")
        self.assertEqual(cfg.server_port, 8123)
        self.assertEqual((cfg.models_pipeline, cfg.models_fallback), ("claude-sonnet-5", "off"))

    def test_block_order(self):
        names = [f.__name__ for f in config._BLOCK_APPLIERS]
        self.assertEqual(names, ["_apply_sources", "_apply_execution", "_apply_server_models",
                                 "_apply_recording", "_apply_redaction",
                                 "_apply_updates_recap_language"])

    def test_bool_word(self):
        self.assertTrue(config._bool_word(" ON "))
        self.assertFalse(config._bool_word("0"))
        with self.assertRaises(ValueError):
            config._bool_word("maybe")
        with self.assertRaises(ValueError):
            config.coerce_bool(2)
        with self.assertRaises(ValueError):
            config.coerce_bool(None)


if __name__ == "__main__":
    unittest.main()
