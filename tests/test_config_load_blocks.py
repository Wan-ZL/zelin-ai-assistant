"""config.load_config — every config.yaml block applier on its own (§15 / §17 /
§48 / §53 / §54 / §59 / §63 / §64 / §70).

Characterization net for the P3b split: non-mapping blocks read as {} and keep
the defaults; the yaml reader's three failure shapes; weekly_digest range
edges; the approval poll knobs; execution repo/claude_bin/max-failures;
telemetry level + capture_input consent flag; redaction relative path anchor;
switch blocks with junk shapes; maintainer / feedback_sync blanks; language /
output format / features coercion; the block order and the two post-steps.
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
        cfg = _load("owner: hi\nsources: [1]\napproval: 3\nexecution: null\ntrash: x\n"
                    "archive: x\ndigest: x\nregistry: x\nmodels: x\nvoice: x\nrecording: x\n"
                    "telemetry: x\nredaction: x\nremote: x\ndoctor: [1]\nupdates: x\nask: x\n"
                    "maintainer: x\nfeedback_sync: x\nfeatures: x\nlanguage: 5\n")
        default = config.Config()
        for attr in ("owner_name", "slack_channels", "poll_interval_seconds", "memory_inject",
                     "trash_retention_days", "archive_after_days", "digest_frequency",
                     "registry_backend", "models_dispatch", "voice_enabled",
                     "recording_ignored_apps", "telemetry_level", "redaction_enabled",
                     "remote_allow_direct_run", "doctor_ai_fix_enabled", "updates_check_enabled",
                     "ask_enabled", "maintainer_repo_path", "feedback_sync_repo", "language"):
            self.assertEqual(getattr(cfg, attr), getattr(default, attr), attr)


class SourcesTestCase(unittest.TestCase):
    def test_lists_and_switches(self):
        cfg = _load("sources:\n  slack_channels: null\n  watch_people:\n    - a\n"
                    "  slack:\n    enabled: 'no'\n  obsidian: 7\n  gmail:\n    enabled: yes\n"
                    "    address: a@b\n")
        self.assertEqual((cfg.slack_channels, cfg.watch_people), ([], ["a"]))
        self.assertFalse(cfg.slack_enabled)
        self.assertTrue(cfg.obsidian_enabled)
        self.assertTrue(cfg.gmail_enabled)
        self.assertEqual(cfg.gmail_address, "a@b")

    def test_weekly_digest_ranges(self):
        cfg = _load("sources:\n  weekly_digest:\n    enabled: true\n    day: 6\n    hour: 23\n")
        self.assertEqual((cfg.weekly_digest_enabled, cfg.weekly_digest_day, cfg.weekly_digest_hour),
                         (True, 6, 23))
        default = config.Config()
        cfg = _load("sources:\n  weekly_digest:\n    day: 7\n    hour: -1\n")
        self.assertEqual((cfg.weekly_digest_day, cfg.weekly_digest_hour),
                         (default.weekly_digest_day, default.weekly_digest_hour))
        cfg = _load("sources:\n  weekly_digest:\n    day: monday\n    hour: '5'\n")
        self.assertEqual((cfg.weekly_digest_day, cfg.weekly_digest_hour), (default.weekly_digest_day, 5))
        cfg = _load("sources:\n  weekly_digest: nope\n")
        self.assertEqual(cfg.weekly_digest_day, default.weekly_digest_day)
        self.assertIsNone(config._int_in_range("x", 0, 1))
        self.assertIsNone(config._int_in_range(None, 0, 1))
        self.assertEqual(config._int_in_range(True, 0, 1), 1)


class ApprovalExecutionTestCase(unittest.TestCase):
    def test_poll_and_thresholds(self):
        cfg = _load("approval:\n  poll_interval_seconds: '30'\n  cost_thresholds:\n"
                    "    show_cost_above_usd: 2.5\n    require_text_confirm_above_usd: bad\n")
        self.assertEqual((cfg.poll_interval_seconds, cfg.show_cost_above_usd), (30, 2.5))
        self.assertEqual(cfg.require_text_confirm_above_usd,
                         config.Config().require_text_confirm_above_usd)
        cfg = _load("approval:\n  poll_interval_minutes: 5\n")
        self.assertEqual(cfg.poll_interval_seconds, config.Config().poll_interval_seconds)

    def test_execution_block(self):
        cfg = _load("execution:\n  default_target_repo: ''\n  claude_bin: '  '\n"
                    "  dispatch_max_failures: -3\n  quality_gate:\n    self_check: 'off'\n")
        self.assertFalse(cfg.default_target_repo_configured)
        self.assertEqual(cfg.default_target_repo, "")
        self.assertEqual(cfg.claude_bin, config.Config().claude_bin)
        self.assertEqual(cfg.dispatch_max_failures, 0)
        self.assertFalse(cfg.self_check)
        cfg = _load("execution:\n  default_target_repo: /r\n  claude_bin: ' /c '\n"
                    "  auto_resume: 'false'\n  skip_permissions: 1\n")
        self.assertTrue(cfg.default_target_repo_configured)
        self.assertEqual(cfg.claude_bin, "/c")
        self.assertEqual((cfg.auto_resume, cfg.skip_permissions), (False, True))


class TelemetryRedactionTestCase(unittest.TestCase):
    def test_telemetry(self):
        cfg = _load("telemetry:\n  level: LOUD\n  capture_input: maybe\n  supabase_url: null\n")
        self.assertEqual(cfg.telemetry_level, "basic")
        self.assertFalse(cfg.telemetry_capture_input_explicit)
        self.assertEqual(cfg.telemetry_supabase_url, "")
        cfg = _load("telemetry:\n  level: ' Detailed '\n  capture_input: 'yes'\n")
        self.assertEqual((cfg.telemetry_level, cfg.telemetry_capture_input,
                          cfg.telemetry_capture_input_explicit), ("detailed", True, True))
        cfg = _load("telemetry:\n  level: null\n")
        self.assertEqual(cfg.telemetry_level, "basic")

    def test_redaction_relative_terms_anchor(self):
        cfg = _load("redaction:\n  terms_file: rel/terms.txt\n")
        self.assertEqual(cfg.redaction_terms_file, str(config.HOME / "rel/terms.txt"))
        cfg = _load("redaction:\n  terms_file: ~/abs.txt\n")
        self.assertEqual(cfg.redaction_terms_file, "~/abs.txt")
        cfg = _load("redaction:\n  terms_file: ''\n")
        self.assertEqual(cfg.redaction_terms_file, "")

    def test_recording_apps(self):
        cfg = _load("recording:\n  ignored_apps:\n    - ' Zoom '\n    - null\n    - ''\n    - 7\n")
        self.assertEqual(cfg.recording_ignored_apps, ["Zoom", "7"])
        cfg = _load("recording:\n  ignored_apps: Zoom\n")
        self.assertEqual(cfg.recording_ignored_apps, config.Config().recording_ignored_apps)


class MiscBlocksTestCase(unittest.TestCase):
    def test_switch_blocks(self):
        cfg = _load("remote:\n  allow_direct_run: 'yes'\ndoctor:\n  ai_fix_enabled: 'off'\n"
                    "updates:\n  check_enabled: 0\nask:\n  enabled: junk\n")
        self.assertTrue(cfg.remote_allow_direct_run)
        self.assertFalse(cfg.doctor_ai_fix_enabled)
        self.assertFalse(cfg.updates_check_enabled)
        self.assertEqual(cfg.ask_enabled, config.Config().ask_enabled)

    def test_maintainer_and_feedback_sync(self):
        cfg = _load("maintainer:\n  repo_path: '  '\n  session_id: ' s1 '\n"
                    "feedback_sync:\n  repo: ''\n  token_path: ' /t '\n")
        self.assertEqual(cfg.maintainer_repo_path, config.Config().maintainer_repo_path)
        self.assertEqual(cfg.maintainer_session_id, "s1")
        self.assertEqual(cfg.feedback_sync_repo, config.Config().feedback_sync_repo)
        self.assertEqual(cfg.feedback_sync_token_path, "/t")
        self.assertIsNone(config._nonblank(None))
        self.assertIsNone(config._nonblank("  "))
        self.assertEqual(config._nonblank(5), "5")

    def test_language_format_features(self):
        cfg = _load("language: ' en '\ndefault_output_format: HTML\nfeatures:\n  digest: 'no'\n"
                    "  weird: maybe\n  3: false\n")
        self.assertEqual((cfg.language, cfg.default_output_format), ("en", "html"))
        self.assertEqual((cfg.features["digest"], cfg.features["weird"], cfg.features["3"]),
                         (False, True, False))
        cfg = _load("language: '  '\ndefault_output_format: pdf\n")
        self.assertEqual((cfg.language, cfg.default_output_format),
                         (config.Config().language, "markdown"))

    def test_digest_registry_server_models(self):
        cfg = _load("digest:\n  frequency: Every-2-Days\nregistry:\n  backend: SQLite\n"
                    "server:\n  port: 70000\nmodels:\n  dispatch: claude-opus-5\n  pipeline: 7\n")
        self.assertEqual((cfg.digest_frequency, cfg.registry_backend), ("every2days", "sqlite"))
        self.assertEqual(cfg.server_port, config.DEFAULT_SERVER_PORT)
        self.assertEqual((cfg.models_dispatch, cfg.models_pipeline), ("claude-opus-5", "follow"))
        cfg = _load("digest:\n  weekly: monday\nregistry: {}\n")
        self.assertEqual((cfg.digest_frequency, cfg.registry_backend),
                         (config.Config().digest_frequency, config.Config().registry_backend))

    def test_block_order_is_the_historical_one(self):
        names = [f.__name__ for f in config._BLOCK_APPLIERS]
        self.assertEqual(names, ["_apply_owner", "_apply_sources", "_apply_approval",
                                 "_apply_execution", "_apply_retention",
                                 "_apply_digest_registry_server", "_apply_models_voice",
                                 "_apply_recording", "_apply_telemetry", "_apply_redaction",
                                 "_apply_switch_blocks", "_apply_maintainer_feedback",
                                 "_apply_language_format_features"])

    def test_bool_word_and_channel_entry(self):
        self.assertTrue(config._bool_word(" ON "))
        self.assertFalse(config._bool_word("0"))
        with self.assertRaises(ValueError):
            config._bool_word("maybe")
        with self.assertRaises(ValueError):
            config._coerce_bool(2)
        with self.assertRaises(ValueError):
            config._coerce_bool(None)
        self.assertEqual(config._channel_entry({"id": 1, "name": ""}), {"id": "1"})
        self.assertEqual(config._channel_entry({"id": "C", "name": 9}), {"id": "C", "name": "9"})
        self.assertEqual(config._clean_slack_channels([{"id": ""}, " C1 ", "", 5, {"id": "C2"}]),
                         ["C1", {"id": "C2"}])


if __name__ == "__main__":
    unittest.main()
