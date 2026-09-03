"""server/settings.py 的边角判例（CONTRACT §59 设置面）。

test_server_settings_models 走真 server 主线；这里直接调小件，钉此前无判例的
分支：overrides 文件不可读（OSError 非 FileNotFound）→ 409、overrides 是
JSON 但不是对象 → 409、空文件 = {}；config.yaml models 块坏形 → follow 且
present 仍为真；_backup_file 同秒两次不覆盖；_replace_settings 保留文件
mode；claude_code_default 对 model 非字符串 / 空白的容错。
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401

from server import settings
from server.errors import ConflictError, InvalidFieldError


class _Home(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-settings-edge-"))
        (self.home / "state").mkdir()


class OverridesReadTestCase(_Home):
    def test_empty_file_reads_as_empty_object(self):
        settings.settings_overrides_path(self.home).write_text("  \n", encoding="utf-8")
        self.assertEqual(settings.read_overrides(self.home), {})

    def test_non_object_is_conflict(self):
        settings.settings_overrides_path(self.home).write_text("[1]", encoding="utf-8")
        with self.assertRaises(ConflictError) as cm:
            settings.read_overrides(self.home)
        self.assertIn("JSON object", cm.exception.message)

    def test_unreadable_is_conflict_with_error_detail(self):
        p = settings.settings_overrides_path(self.home)
        p.write_text("{}", encoding="utf-8")
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("nope")):
            with self.assertRaises(ConflictError) as cm:
                settings.read_overrides(self.home)
        self.assertIn("unreadable", cm.exception.message)
        self.assertIn("nope", cm.exception.details["error"])


class ConfigModelsTestCase(_Home):
    def _cfg(self, text: str) -> None:
        (self.home / "config.yaml").write_text(text, encoding="utf-8")

    def test_bad_shape_reads_as_follow_but_present(self):
        self._cfg("models:\n  dispatch: 'has space'\n")
        values, present = settings._config_models(self.home)
        self.assertEqual(values["dispatch"], settings.MODEL_FOLLOW)
        self.assertTrue(present["dispatch"])
        self.assertFalse(present["pipeline"])

    def test_models_not_a_mapping_is_all_follow_none_present(self):
        self._cfg("models: 3\n")
        values, present = settings._config_models(self.home)
        self.assertEqual(set(values.values()), {settings.MODEL_FOLLOW})
        self.assertFalse(any(present.values()))

    def test_bad_yaml_and_missing_file_degrade(self):
        self.assertIsNone(settings._models_block(self.home))
        self._cfg("models: [unclosed\n")
        self.assertIsNone(settings._models_block(self.home))

    def test_pyyaml_absent_degrades(self):
        self._cfg("models:\n  dispatch: claude-opus-5\n")
        with mock.patch.object(settings, "yaml", None):
            self.assertIsNone(settings._models_block(self.home))

    def test_coerce_or_follow(self):
        self.assertEqual(settings._coerce_or_follow("claude-opus-5"), "claude-opus-5")
        self.assertEqual(settings._coerce_or_follow(123), settings.MODEL_FOLLOW)


class EffectiveKnobTestCase(unittest.TestCase):
    _BASE = {"dispatch": "follow", "pipeline": "claude-opus-5"}
    _PRESENT = {"dispatch": False, "pipeline": True}

    def test_override_wins_and_malformed_override_is_skipped(self):
        self.assertEqual(settings._effective_knob("dispatch", self._BASE, self._PRESENT,
                                                  {"models_dispatch": "claude-sonnet-5"}),
                         ("claude-sonnet-5", "override"))
        self.assertEqual(settings._effective_knob("dispatch", self._BASE, self._PRESENT,
                                                  {"models_dispatch": "bad id"}),
                         ("follow", "default"))
        self.assertEqual(settings._effective_knob("pipeline", self._BASE, self._PRESENT, {}),
                         ("claude-opus-5", "config"))


class WantedAndDiffTestCase(unittest.TestCase):
    def test_wanted_only_named_modes(self):
        self.assertEqual(settings._wanted_models({"pipeline": "Follow"}),
                         {"pipeline": settings.MODEL_FOLLOW})

    def test_wanted_malformed_names_the_field(self):
        with self.assertRaises(InvalidFieldError) as cm:
            settings._wanted_models({"dispatch": "no spaces allowed"})
        self.assertEqual(cm.exception.details, {"field": "dispatch"})

    def test_apply_diff_deletes_equal_and_writes_different(self):
        overrides = {"models_dispatch": "old", "other": 1}
        settings._apply_diff(overrides, {"dispatch": "follow", "pipeline": "claude-opus-5"},
                             {"dispatch": "follow", "pipeline": "follow"})
        self.assertEqual(overrides, {"other": 1, "models_pipeline": "claude-opus-5"})


class ClaudeCodeDefaultEdgesTestCase(_Home):
    def test_backup_names_never_collide(self):
        p = self.home / "settings.json"
        p.write_text("{}", encoding="utf-8")
        with mock.patch.object(settings._dt, "datetime") as dt:
            dt.now.return_value.strftime.return_value = "20260101T000000Z"
            b1 = settings._backup_file(p)
            b2 = settings._backup_file(p)
        self.assertNotEqual(b1, b2)
        self.assertTrue(b2.name.endswith("-1"))

    def test_replace_keeps_mode_when_asked(self):
        p = self.home / "settings.json"
        p.write_text("{}", encoding="utf-8")
        if os.name != "posix":
            self.skipTest("mode bits are POSIX")
        os.chmod(p, 0o600)
        settings._replace_settings(p, {"model": "x"}, keep_mode=True)
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"model": "x"})

    def test_copymode_failure_is_swallowed(self):
        p = self.home / "settings.json"
        p.write_text("{}", encoding="utf-8")
        with mock.patch.object(settings.shutil, "copymode", side_effect=OSError("x")):
            settings._replace_settings(p, {"model": "y"}, keep_mode=True)
        self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"model": "y"})

    def test_previous_model_normalizes(self):
        self.assertIsNone(settings._previous_model({}))
        self.assertIsNone(settings._previous_model({"model": "  "}))
        self.assertIsNone(settings._previous_model({"model": 3}))
        self.assertEqual(settings._previous_model({"model": " a "}), "a")

    def test_explicit_model_rejects_follow_and_malformed(self):
        with self.assertRaises(InvalidFieldError):
            settings._explicit_model("follow")
        with self.assertRaises(InvalidFieldError):
            settings._explicit_model("bad id")
        self.assertEqual(settings._explicit_model(" claude-opus-5 "), "claude-opus-5")

    def test_default_read_tolerates_non_string_model(self):
        p = self.home / "settings.json"
        p.write_text(json.dumps({"model": 5}), encoding="utf-8")
        out = settings.claude_code_default(p)
        self.assertTrue(out["parseable"])
        self.assertIsNone(out["model"])

    def test_default_read_unreadable_is_honest(self):
        p = self.home / "settings.json"
        p.write_text("{}", encoding="utf-8")
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("x")):
            out = settings.claude_code_default(p)
        self.assertFalse(out["parseable"])

    def test_set_refuses_non_object_file(self):
        p = self.home / "settings.json"
        p.write_text("[]", encoding="utf-8")
        with self.assertRaises(ConflictError):
            settings.set_claude_code_default("claude-opus-5", p)


if __name__ == "__main__":
    unittest.main()
