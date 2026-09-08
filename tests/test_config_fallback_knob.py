"""The third model knob ``models.fallback`` (CONTRACT §59 D53; §15 add-only key
``models_fallback``) — coercion, config.yaml block, overrides precedence.

Default is the literal ``claude-opus-5[1m]`` (the owner's ~/.claude/settings.json
``fallbackModel``); ``off`` disables the flag; a malformed value degrades to the
default on the yaml path and is skipped on the overrides path (the effective
value stays) — never to ``off``, because a typo must not quietly hand the
session back to the CLI's own fallback (Opus 4.8).

Both files live in the sandbox AIASSISTANT_HOME (tests/__init__.py) and are
removed after every test.
"""
import json
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config

DEFAULT = "claude-opus-5[1m]"


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


class ConstantsTestCase(unittest.TestCase):
    def test_default_is_the_owner_settings_literal(self):
        self.assertEqual(config.DEFAULT_MODEL_FALLBACK, DEFAULT)
        self.assertEqual(config.MODEL_FALLBACK_OFF, "off")
        self.assertEqual(config.Config().models_fallback, DEFAULT)

    def test_fallback_is_not_a_mode(self):
        # llm.run(mode=) never takes "fallback": it is a cross-cutting flag, not a site
        self.assertNotIn("fallback", config.MODEL_MODES)

    def test_default_passes_the_id_shape_gate(self):
        self.assertTrue(config.MODEL_ID_RE.match(DEFAULT))


class CoercionTestCase(unittest.TestCase):
    def test_table(self):
        c = config.coerce_fallback_model
        self.assertEqual(c(None), DEFAULT)                    # default
        self.assertEqual(c(""), DEFAULT)
        self.assertEqual(c("   "), DEFAULT)
        self.assertEqual(c(" claude-opus-5[1m] "), DEFAULT)  # explicit id, stripped
        self.assertEqual(c("claude-sonnet-5"), "claude-sonnet-5")
        self.assertEqual(c("off"), "off")                     # off
        self.assertEqual(c(" OFF "), "off")
        self.assertEqual(c("Off"), "off")
        self.assertEqual(c(False), "off")                    # YAML 1.1: a bare `off` parses as False

    def test_garbage_raises_for_the_callers_to_decide(self):
        for bad in ("a b", "x\ny", "-lead", "a" * 65, 5, True, ["x"], "q'uote", "claude-opus-5,claude-opus-4-8"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                config.coerce_fallback_model(bad)

    def test_lenient_helper_degrades_to_default_not_off(self):
        self.assertEqual(config._fallback_or("has space", DEFAULT), DEFAULT)
        self.assertEqual(config._fallback_or(12, DEFAULT), DEFAULT)
        self.assertEqual(config._fallback_or("off", DEFAULT), "off")


class YamlBlockTestCase(_Files):
    def test_explicit_id_is_read(self):
        self._yaml("models:\n  fallback: claude-sonnet-5\n")
        self.assertEqual(config.load_config().models_fallback, "claude-sonnet-5")

    def test_off_is_read_bare_and_quoted(self):
        # bare `off` is YAML 1.1 False, quoted 'off' is a string — both must disable
        self._yaml("models:\n  fallback: off\n")
        self.assertEqual(config.load_config().models_fallback, "off")
        self._yaml("models:\n  fallback: 'OFF'\n")
        self.assertEqual(config.load_config().models_fallback, "off")

    def test_true_is_garbage_and_degrades_to_default(self):
        self._yaml("models:\n  fallback: on\n")
        self.assertEqual(config.load_config().models_fallback, DEFAULT)

    def test_absent_key_keeps_the_default(self):
        self._yaml("models:\n  dispatch: claude-opus-5\n")
        cfg = config.load_config()
        self.assertEqual(cfg.models_fallback, DEFAULT)
        self.assertEqual(cfg.models_dispatch, "claude-opus-5")   # siblings untouched

    def test_bad_shape_degrades_to_default(self):
        self._yaml("models:\n  fallback: 'has space'\n")
        self.assertEqual(config.load_config().models_fallback, DEFAULT)
        self._yaml("models:\n  fallback: 12\n")
        self.assertEqual(config.load_config().models_fallback, DEFAULT)

    def test_non_mapping_block_is_ignored(self):
        self._yaml("models: off\n")
        self.assertEqual(config.load_config().models_fallback, DEFAULT)


class OverridesTestCase(_Files):
    def test_flat_key_wins_over_yaml(self):
        self._yaml("models:\n  fallback: claude-sonnet-5\n")
        self._overrides({"models_fallback": "off"})
        self.assertEqual(config.load_config().models_fallback, "off")

    def test_override_id_over_yaml_off(self):
        self._yaml("models:\n  fallback: off\n")
        self._overrides({"models_fallback": "claude-opus-5"})
        self.assertEqual(config.load_config().models_fallback, "claude-opus-5")

    def test_bad_override_is_skipped_and_yaml_value_stays(self):
        self._yaml("models:\n  fallback: claude-sonnet-5\n")
        self._overrides({"models_fallback": "bad value"})
        self.assertEqual(config.load_config().models_fallback, "claude-sonnet-5")

    def test_null_override_reads_as_default(self):
        self._overrides({"models_fallback": None})
        self.assertEqual(config.load_config().models_fallback, DEFAULT)

    def test_key_is_in_the_allowlist_with_its_own_coercion(self):
        self.assertIs(config._OVERRIDE_FIELDS["models_fallback"], config.coerce_fallback_model)

    def test_two_d22_knobs_unaffected(self):
        self._overrides({"models_fallback": "off"})
        cfg = config.load_config()
        self.assertEqual((cfg.models_dispatch, cfg.models_pipeline), ("follow", "follow"))


if __name__ == "__main__":
    unittest.main()
