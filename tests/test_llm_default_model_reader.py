"""llm.read_claude_code_default_model — the two helpers behind it (§59).

Pins the P3b split: a JSON array / scalar file is "exists but not parseable"
(same verdict as broken JSON), a blank / non-string ``model`` reads as None
while the file still counts as parseable, and the default path fallback.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import llm


class DefaultModelReaderTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="llm-reader-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "settings.json"

    def test_json_object_helper(self):
        self.assertIsNone(llm._json_object("[1]"))
        self.assertIsNone(llm._json_object("42"))
        self.assertIsNone(llm._json_object("{"))
        self.assertEqual(llm._json_object('{"a": 1}'), {"a": 1})

    def test_model_key_helper(self):
        self.assertIsNone(llm._model_key({}))
        self.assertIsNone(llm._model_key({"model": "   "}))
        self.assertIsNone(llm._model_key({"model": 5}))
        self.assertEqual(llm._model_key({"model": " m "}), "m")

    def test_non_object_file_is_not_parseable(self):
        self.path.write_text(json.dumps(["model"]), encoding="utf-8")
        self.assertEqual(llm.read_claude_code_default_model(self.path),
                         {"model": None, "exists": True, "parseable": False})

    def test_blank_model_is_none_but_parseable(self):
        self.path.write_text(json.dumps({"model": "  "}), encoding="utf-8")
        self.assertEqual(llm.read_claude_code_default_model(self.path),
                         {"model": None, "exists": True, "parseable": True})

    def test_default_path_is_used_when_none_given(self):
        with mock.patch.object(llm, "claude_code_settings_path", return_value=self.path):
            self.assertFalse(llm.read_claude_code_default_model()["exists"])


if __name__ == "__main__":
    unittest.main()
