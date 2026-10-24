"""config.yaml ``models:`` + overrides ``models_pipeline`` (CONTRACT §59, D22) —
parsing, coercion and the canonical-list warning flag.

v2.0.0: the ``models.dispatch`` knob retired with the executor and the "follow the
Claude Code global default" mode retired with the reader of the owner's Claude Code
settings (§78: the app never reads ~/.claude). A stored ``follow`` (yaml or
override, any case) reads as the factory pipeline model.

Both files live in the sandbox AIASSISTANT_HOME (tests/__init__.py) and are
removed after every test so nothing leaks into the other suites.
"""
import json
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config

DEFAULT = config.DEFAULT_MODEL_PIPELINE


class _Files(unittest.TestCase):
    def setUp(self):
        for p in (config.CONFIG_PATH, config.SETTINGS_OVERRIDES_PATH):
            p.unlink(missing_ok=True)
            self.addCleanup(lambda p=p: p.unlink(missing_ok=True))

    def _yaml(self, text: str) -> None:
        config.CONFIG_PATH.write_text(text, encoding="utf-8")

    def _overrides(self, doc) -> None:
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text(json.dumps(doc), encoding="utf-8")


class DefaultsTestCase(_Files):
    def test_pipeline_defaults_to_the_factory_model(self):
        cfg = config.load_config()
        self.assertEqual(cfg.models_pipeline, DEFAULT)
        self.assertEqual(DEFAULT, "claude-opus-5")
        self.assertEqual(config.MODEL_MODES, ("pipeline",))
        self.assertFalse(hasattr(cfg, "models_dispatch"))

    def test_canonical_list_is_the_d22_four(self):
        self.assertEqual(config.CANONICAL_MODELS, (
            "claude-fable-5", "claude-opus-5", "claude-sonnet-5",
            "claude-haiku-4-5-20251001"))


class YamlBlockTestCase(_Files):
    def test_explicit_id_is_read(self):
        self._yaml("models:\n  pipeline: claude-sonnet-5\n")
        self.assertEqual(config.load_config().models_pipeline, "claude-sonnet-5")

    def test_legacy_follow_any_case_and_blank_read_as_the_default(self):
        for raw in ("Follow", "''", "follow"):
            with self.subTest(raw=raw):
                self._yaml("models:\n  pipeline: %s\n" % raw)
                self.assertEqual(config.load_config().models_pipeline, DEFAULT)

    def test_free_text_alias_is_kept_verbatim(self):
        self._yaml("models:\n  pipeline: 'claude-fable-5-1[1m]'\n")
        self.assertEqual(config.load_config().models_pipeline, "claude-fable-5-1[1m]")

    def test_bad_shape_degrades_to_the_default(self):
        self._yaml("models:\n  pipeline: 12\n")
        self.assertEqual(config.load_config().models_pipeline, DEFAULT)
        self._yaml("models:\n  pipeline: 'has space'\n")
        self.assertEqual(config.load_config().models_pipeline, DEFAULT)

    def test_non_mapping_block_is_ignored(self):
        self._yaml("models: claude-sonnet-5\n")
        self.assertEqual(config.load_config().models_pipeline, DEFAULT)


class OverridesTestCase(_Files):
    def test_flat_key_wins_over_yaml(self):
        self._yaml("models:\n  pipeline: claude-sonnet-5\n")
        self._overrides({"models_pipeline": "claude-haiku-4-5-20251001"})
        self.assertEqual(config.load_config().models_pipeline, "claude-haiku-4-5-20251001")

    def test_override_follow_reads_as_the_default(self):
        self._yaml("models:\n  pipeline: claude-sonnet-5\n")
        self._overrides({"models_pipeline": "follow"})
        self.assertEqual(config.load_config().models_pipeline, DEFAULT)

    def test_bad_override_is_skipped_and_yaml_value_stays(self):
        self._yaml("models:\n  pipeline: claude-sonnet-5\n")
        self._overrides({"models_pipeline": "bad value with spaces", "models_dispatch": "x"})
        self.assertEqual(config.load_config().models_pipeline, "claude-sonnet-5")

    def test_key_is_in_the_allowlist_and_dispatch_is_not(self):
        self.assertIs(config._OVERRIDE_FIELDS["models_pipeline"], config.coerce_model)
        self.assertNotIn("models_dispatch", config._OVERRIDE_FIELDS)


class CoercionTestCase(unittest.TestCase):
    def test_coerce_model_table(self):
        self.assertEqual(config.coerce_model(None), DEFAULT)
        self.assertEqual(config.coerce_model(""), DEFAULT)
        self.assertEqual(config.coerce_model("  FOLLOW "), DEFAULT)
        self.assertEqual(config.coerce_model(" claude-opus-5 "), "claude-opus-5")
        self.assertEqual(config.coerce_model("claude-fable-5-1[1m]"), "claude-fable-5-1[1m]")
        for bad in ("a b", "x\ny", "-lead", "a" * 65, 5, True, ["x"], "q'uote"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                config.coerce_model(bad)

    def test_canonical_flag(self):
        self.assertTrue(config.model_is_canonical(None))
        self.assertTrue(config.model_is_canonical(""))
        self.assertTrue(config.model_is_canonical("claude-opus-5"))
        self.assertFalse(config.model_is_canonical("claude-fable-5-1[1m]"))


if __name__ == "__main__":
    unittest.main()
