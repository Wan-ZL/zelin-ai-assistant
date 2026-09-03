"""config._apply_settings_overrides — the per-key dispatch (§15 / §15.3 / §16).

Characterization net for the P3b split: the reader's failure shapes, exact-key
handlers on type mismatches (silent no-op), the nested features block's
per-flag skip and its precedence over flat ``features.*`` keys, gmail /
feedback_sync / cost_thresholds / telemetry nested forms field by field, the
flat telemetry keys with None, list fields, ``sources.*`` list + scalar +
unknown, the scalar table incl. ``default_target_repo_configured``, a coercion
error skipping just that entry, and the CLI ``--print-path`` in-process.
"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config


class ReaderTestCase(unittest.TestCase):
    def test_read_overrides_shapes(self):
        tmp = Path(tempfile.mkdtemp(prefix="ovr-"))
        with mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", tmp / "none.json"):
            self.assertIsNone(config._read_overrides())
        bad = tmp / "bad.json"
        bad.write_text("{", encoding="utf-8")
        with mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", bad):
            self.assertIsNone(config._read_overrides())
        lst = tmp / "list.json"
        lst.write_text("[1]", encoding="utf-8")
        with mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", lst):
            self.assertIsNone(config._read_overrides())
        ok = tmp / "ok.json"
        ok.write_text('{"language": "en"}', encoding="utf-8")
        with mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", ok):
            self.assertEqual(config._read_overrides(), {"language": "en"})
            cfg = config.Config()
            config._apply_settings_overrides(cfg)
            self.assertEqual(cfg.language, "en")


class HandlerTestCase(unittest.TestCase):
    def setUp(self):
        self.cfg = config.Config()

    def _apply(self, key, value, nested=None):
        config._apply_override(self.cfg, key, value, nested or {})

    def test_type_mismatch_is_a_silent_noop(self):
        before = json.dumps(self.cfg.__dict__, default=str)
        for key, value in (("features", "x"), ("gmail", []), ("feedback_sync", 1),
                           ("cost_thresholds", "x"), ("telemetry", None), ("slack_channels", "C1"),
                           ("watch_people", {"a": 1}), ("telemetry.enabled", None),
                           ("telemetry.level", None), ("telemetry.capture_input", None),
                           ("unknown_key", 1), ("sources.unknown", 1), ("sources.slack_channels", "x"),
                           ("language", None)):
            self._apply(key, value)
        self.assertEqual(json.dumps(self.cfg.__dict__, default=str), before)

    def test_nested_features_per_flag_skip_and_precedence(self):
        self._apply("features", {"digest": "false", "weird": "maybe", "on": 1})
        self.assertEqual((self.cfg.features["digest"], self.cfg.features["on"]), (False, True))
        self.assertNotIn("weird", self.cfg.features)
        nested = {"digest": True}
        self._apply("features.digest", True, nested)          # nested wins → flat ignored
        self.assertFalse(self.cfg.features["digest"])
        self._apply("features.other", "off", nested)
        self.assertFalse(self.cfg.features["other"])
        with self.assertRaises(ValueError):
            self._apply("features.bad", "maybe", nested)

    def test_gmail_nested(self):
        self._apply("gmail", {"address": "a@b", "app_password_path": None, "enabled": "maybe",
                              "fetch_command": 7})
        self.assertEqual((self.cfg.gmail_address, self.cfg.gmail_fetch_command), ("a@b", "7"))
        self.assertEqual(self.cfg.gmail_app_password_path, config.Config().gmail_app_password_path)
        self.assertEqual(self.cfg.gmail_enabled, config.Config().gmail_enabled)
        self._apply("gmail", {"enabled": "no"})
        self.assertFalse(self.cfg.gmail_enabled)

    def test_feedback_sync_and_cost_thresholds_nested(self):
        self._apply("feedback_sync", {"repo": "  ", "token_path": " /t "})
        self.assertEqual(self.cfg.feedback_sync_repo, config.Config().feedback_sync_repo)
        self.assertEqual(self.cfg.feedback_sync_token_path, "/t")
        self._apply("cost_thresholds", {"show_cost_above_usd": "3", "require_text_confirm_above_usd": None})
        self.assertEqual(self.cfg.show_cost_above_usd, 3.0)
        self.assertEqual(self.cfg.require_text_confirm_above_usd,
                         config.Config().require_text_confirm_above_usd)
        with self.assertRaises(ValueError):
            self._apply("cost_thresholds", {"show_cost_above_usd": "lots"})

    def test_telemetry_nested_and_flat(self):
        self._apply("telemetry", {"enabled": "maybe", "level": "LOUD", "capture_input": "junk"})
        self.assertEqual(self.cfg.telemetry_enabled, config.Config().telemetry_enabled)
        self.assertEqual(self.cfg.telemetry_level, "basic")
        self.assertFalse(self.cfg.telemetry_capture_input_explicit)
        self._apply("telemetry", {"enabled": "false", "level": " detailed ", "capture_input": True})
        self.assertEqual((self.cfg.telemetry_enabled, self.cfg.telemetry_level,
                          self.cfg.telemetry_capture_input, self.cfg.telemetry_capture_input_explicit),
                         (False, "detailed", True, True))
        self._apply("telemetry.enabled", "yes")
        self._apply("telemetry.level", "nope")
        self.assertEqual((self.cfg.telemetry_enabled, self.cfg.telemetry_level), (True, "basic"))
        with self.assertRaises(ValueError):
            self._apply("telemetry.enabled", "maybe")
        cfg2 = config.Config()
        config._apply_override(cfg2, "telemetry.capture_input", "0", {})
        self.assertEqual((cfg2.telemetry_capture_input, cfg2.telemetry_capture_input_explicit),
                         (False, True))
        with self.assertRaises(ValueError):
            config._apply_override(cfg2, "telemetry.capture_input", "maybe", {})

    def test_list_fields_and_sources_prefix(self):
        self._apply("slack_channels", [{"id": "C1", "name": "g"}, "C2", 3])
        self.assertEqual(self.cfg.slack_channels, [{"id": "C1", "name": "g"}, "C2"])
        self._apply("watch_people", ["a", 5, None, " "])
        self.assertEqual(self.cfg.watch_people, ["a", "5"])
        self._apply("sources.slack_channels", [])
        self.assertEqual(self.cfg.slack_channels, [])
        self._apply("sources.watch_people", ["z"])
        self.assertEqual(self.cfg.watch_people, ["z"])
        self._apply("sources.obsidian_wiki", "/w")
        self.assertEqual(self.cfg.obsidian_wiki, "/w")
        self._apply("sources.default_target_repo", "/dtr")
        self.assertEqual((self.cfg.default_target_repo, self.cfg.default_target_repo_configured),
                         ("/dtr", True))
        self._apply("sources.slack_enabled", "off")
        self.assertFalse(self.cfg.slack_enabled)

    def test_scalar_table(self):
        self._apply("default_target_repo", "/x")
        self.assertTrue(self.cfg.default_target_repo_configured)
        self._apply("trash_retention_days", "9")
        self.assertEqual(self.cfg.trash_retention_days, 9)
        with self.assertRaises(ValueError):
            self._apply("trash_retention_days", "nine")
        with self.assertRaises(ValueError):
            self._apply("daily_loop_time", "25:99")
        self._apply("daily_loop_time", "3:05")
        self.assertEqual(self.cfg.daily_loop_time, "03:05")

    def test_bad_entry_skips_only_itself(self):
        tmp = Path(tempfile.mkdtemp(prefix="ovr-skip-"))
        path = tmp / "settings_overrides.json"
        path.write_text(json.dumps({"trash_retention_days": "nine", "language": "en",
                                    "features": {"a": "no"}, "features.a": "yes",
                                    "features.b": "yes"}), encoding="utf-8")
        cfg = config.Config()
        with mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", path):
            config._apply_settings_overrides(cfg)
        self.assertEqual(cfg.language, "en")
        self.assertEqual(cfg.trash_retention_days, config.Config().trash_retention_days)
        self.assertEqual((cfg.features["a"], cfg.features["b"]), (False, True))


class CliInProcessTestCase(unittest.TestCase):
    def test_print_path_resolution(self):
        cfg = config.Config()
        cfg.obsidian_wiki = "rel/wiki"
        with mock.patch.object(config, "load_config", return_value=cfg), \
                mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(config.main(["--print-path", "obsidian_wiki"]), 0)
        self.assertEqual(out.getvalue().strip(), str(config.HOME / "rel/wiki"))
        cfg.obsidian_raw = "~/Vault/2 - raw"
        with mock.patch.object(config, "load_config", return_value=cfg), \
                mock.patch("sys.stdout", io.StringIO()) as out:
            config.main(["--print-path", "obsidian_raw"])
        self.assertEqual(out.getvalue().strip(), str(Path("~/Vault/2 - raw").expanduser()))

    def test_print_path_defaults_on_blank_or_crash(self):
        cfg = config.Config()
        cfg.obsidian_change_summary = "   "
        with mock.patch.object(config, "load_config", return_value=cfg), \
                mock.patch("sys.stdout", io.StringIO()) as out:
            config.main(["--print-path", "obsidian_change_summary"])
        self.assertEqual(out.getvalue().strip(), config._cli_default_path("obsidian_change_summary"))
        with mock.patch.object(config, "load_config", side_effect=RuntimeError("boom")), \
                mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(config.main(["--print-path", "obsidian_raw"]), 0)
        self.assertEqual(out.getvalue().strip(), str(Path(config.DEFAULT_OBSIDIAN_VAULT).expanduser()
                                                     / "2 - raw"))

    def test_unknown_key_exits_two(self):
        with mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                config.main(["--print-path", "nope"])
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
