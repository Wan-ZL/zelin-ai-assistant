"""config.yaml ``models:`` + overrides ``models_dispatch`` / ``models_pipeline``
(CONTRACT §57, D22) — parsing, coercion and the canonical-list warning flag.

Both files live in the sandbox AIASSISTANT_HOME (tests/__init__.py) and are
removed after every test so nothing leaks into the other suites.
"""
import json
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config


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
    def test_both_knobs_default_to_follow(self):
        cfg = config.load_config()
        self.assertEqual(cfg.models_dispatch, config.MODEL_FOLLOW)
        self.assertEqual(cfg.models_pipeline, config.MODEL_FOLLOW)
        self.assertEqual(config.MODEL_FOLLOW, "follow")
        self.assertEqual(config.MODEL_MODES, ("dispatch", "pipeline"))

    def test_canonical_list_is_the_d22_four(self):
        self.assertEqual(config.CANONICAL_MODELS, (
            "claude-fable-5", "claude-opus-5", "claude-sonnet-5",
            "claude-haiku-4-5-20251001"))


class YamlBlockTestCase(_Files):
    def test_explicit_ids_are_read(self):
        self._yaml("models:\n  dispatch: claude-opus-5\n  pipeline: claude-sonnet-5\n")
        cfg = config.load_config()
        self.assertEqual(cfg.models_dispatch, "claude-opus-5")
        self.assertEqual(cfg.models_pipeline, "claude-sonnet-5")

    def test_follow_spelled_any_case_and_blank_is_follow(self):
        self._yaml("models:\n  dispatch: Follow\n  pipeline: ''\n")
        cfg = config.load_config()
        self.assertEqual((cfg.models_dispatch, cfg.models_pipeline), ("follow", "follow"))

    def test_free_text_alias_is_kept_verbatim(self):
        self._yaml("models:\n  dispatch: 'claude-fable-5-1[1m]'\n")
        self.assertEqual(config.load_config().models_dispatch, "claude-fable-5-1[1m]")

    def test_bad_shape_degrades_to_follow(self):
        self._yaml("models:\n  dispatch: 'has space'\n  pipeline: 12\n")
        cfg = config.load_config()
        self.assertEqual((cfg.models_dispatch, cfg.models_pipeline), ("follow", "follow"))

    def test_non_mapping_block_is_ignored(self):
        self._yaml("models: claude-opus-5\n")
        self.assertEqual(config.load_config().models_dispatch, "follow")


class OverridesTestCase(_Files):
    def test_flat_keys_win_over_yaml(self):
        self._yaml("models:\n  dispatch: claude-opus-5\n")
        self._overrides({"models_dispatch": "claude-sonnet-5",
                         "models_pipeline": "claude-haiku-4-5-20251001"})
        cfg = config.load_config()
        self.assertEqual(cfg.models_dispatch, "claude-sonnet-5")
        self.assertEqual(cfg.models_pipeline, "claude-haiku-4-5-20251001")

    def test_override_follow_beats_yaml_explicit(self):
        self._yaml("models:\n  dispatch: claude-opus-5\n")
        self._overrides({"models_dispatch": "follow"})
        self.assertEqual(config.load_config().models_dispatch, "follow")

    def test_bad_override_is_skipped_and_yaml_value_stays(self):
        self._yaml("models:\n  dispatch: claude-opus-5\n")
        self._overrides({"models_dispatch": "bad value with spaces", "models_pipeline": None})
        cfg = config.load_config()
        self.assertEqual(cfg.models_dispatch, "claude-opus-5")
        self.assertEqual(cfg.models_pipeline, "follow")

    def test_keys_are_in_the_allowlist(self):
        self.assertIs(config._OVERRIDE_FIELDS["models_dispatch"], config.coerce_model)
        self.assertIs(config._OVERRIDE_FIELDS["models_pipeline"], config.coerce_model)


class CoercionTestCase(unittest.TestCase):
    def test_coerce_model_table(self):
        self.assertEqual(config.coerce_model(None), "follow")
        self.assertEqual(config.coerce_model(""), "follow")
        self.assertEqual(config.coerce_model("  FOLLOW "), "follow")
        self.assertEqual(config.coerce_model(" claude-opus-5 "), "claude-opus-5")
        self.assertEqual(config.coerce_model("claude-fable-5-1[1m]"), "claude-fable-5-1[1m]")
        for bad in ("a b", "x\ny", "-lead", "a" * 65, 5, True, ["x"], "q'uote"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                config.coerce_model(bad)

    def test_canonical_flag(self):
        self.assertTrue(config.model_is_canonical("follow"))
        self.assertTrue(config.model_is_canonical(None))
        self.assertTrue(config.model_is_canonical("claude-opus-5"))
        self.assertFalse(config.model_is_canonical("claude-fable-5-1[1m]"))
        self.assertFalse(config.model_is_canonical("claude-opus-5-eap"))


if __name__ == "__main__":
    unittest.main()
