"""server/settings.py 的边角判例（CONTRACT §59 设置面）。

test_server_settings_models 走真 server 主线；这里直接调小件，钉此前无判例的
分支：overrides 文件不可读（OSError 非 FileNotFound）→ 409、overrides 是
JSON 但不是对象 → 409、空文件 = {}；config.yaml models 块坏形 → 出厂值且
present 仍为真；payload 校验。v2.0.0：~/.claude/settings.json 的读写（备份、
mode 保留、claude_code_default 容错）随 follow 模式退役（§78）。
"""
from __future__ import annotations

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

    def test_bad_shape_reads_as_the_default_but_present(self):
        self._cfg("models:\n  pipeline: 'has space'\n")
        values, present = settings._config_models(self.home)
        self.assertEqual(values["pipeline"], settings.DEFAULT_MODEL_PIPELINE)
        self.assertTrue(present["pipeline"])
        self.assertFalse(present["fallback"])

    def test_models_not_a_mapping_is_all_default_none_present(self):
        self._cfg("models: 3\n")
        values, present = settings._config_models(self.home)
        self.assertEqual(values, {"pipeline": settings.DEFAULT_MODEL_PIPELINE,
                                  "fallback": settings.DEFAULT_MODEL_FALLBACK})
        self.assertFalse(any(present.values()))

    def test_bad_yaml_missing_file_and_absent_pyyaml_degrade(self):
        self.assertEqual(settings.config_yaml_doc(self.home), {})
        self._cfg("models: [unclosed\n")
        self.assertEqual(settings.config_yaml_doc(self.home), {})
        self._cfg("models:\n  pipeline: claude-sonnet-5\n")
        with mock.patch.object(settings, "yaml", None):
            self.assertEqual(settings.config_yaml_doc(self.home), {})

    def test_lenient_knob(self):
        self.assertEqual(settings._lenient_knob("pipeline", "claude-sonnet-5"), "claude-sonnet-5")
        self.assertEqual(settings._lenient_knob("pipeline", 123), settings.DEFAULT_MODEL_PIPELINE)
        self.assertEqual(settings._lenient_knob("pipeline", "follow"), settings.DEFAULT_MODEL_PIPELINE)
        self.assertEqual(settings._lenient_knob("fallback", 123), settings.DEFAULT_MODEL_FALLBACK)


class EffectiveKnobTestCase(unittest.TestCase):
    _BASE = {"pipeline": "claude-opus-5", "fallback": "claude-opus-5[1m]"}
    _PRESENT = {"pipeline": True, "fallback": False}

    def test_override_wins_and_malformed_override_is_skipped(self):
        self.assertEqual(settings._effective_knob("pipeline", self._BASE, self._PRESENT,
                                                  {"models_pipeline": "claude-sonnet-5"}),
                         ("claude-sonnet-5", "override"))
        self.assertEqual(settings._effective_knob("pipeline", self._BASE, self._PRESENT,
                                                  {"models_pipeline": "bad id"}),
                         ("claude-opus-5", "config"))
        self.assertEqual(settings._effective_knob("fallback", self._BASE, self._PRESENT, {}),
                         ("claude-opus-5[1m]", "default"))


class ValidatedPayloadTestCase(unittest.TestCase):
    def test_only_named_knobs_and_follow_normalizes(self):
        self.assertEqual(settings._validated({"pipeline": "Follow"}),
                         {"pipeline": settings.DEFAULT_MODEL_PIPELINE})

    def test_malformed_names_the_field(self):
        with self.assertRaises(InvalidFieldError) as cm:
            settings._validated({"pipeline": "no spaces allowed"})
        self.assertEqual(cm.exception.details, {"field": "pipeline"})

    def test_retired_dispatch_is_unknown(self):
        from server.errors import UnknownFieldError
        with self.assertRaises(UnknownFieldError):
            settings._validated({"dispatch": "claude-opus-5"})

    def test_the_claude_code_settings_writer_is_gone(self):
        for gone in ("claude_code_default", "claude_code_settings_path", "_backup_file",
                     "_replace_settings", "MODEL_FOLLOW", "MODEL_DISPATCH"):
            self.assertFalse(hasattr(settings, gone), gone)


if __name__ == "__main__":
    unittest.main()
