"""doctor ``claude code model`` row with the fallback knob (CONTRACT §59 D53).

- The row always names the fallback the daemon will use (``fallback: <id>|off``).
- Following a NON-canonical global default WARNs while the fallback is ``off``
  **or** itself a non-canonical alias/suffix (same rule as
  ``server/settings.py::fallback_warning``: only the D53 default and canonical
  ids count); with such a fallback on the row is OK and says which model
  headless calls land on when the alias retires (Claude Code ≥ 2.1.152
  switches for the rest of the session on -p; ≥ 2.1.166 honours the flag in
  interactive / --bg sessions; truth = Claude Code CHANGELOG).
- Still never FAIL (§56's rollback verdict must not turn on it); the
  unparsable-settings WARN and the canonical-default OK are unchanged.

Probes are injected (no real claude, no real ~/.claude); knobs go through the
sandbox settings_overrides.json (the web's write path).
"""
import json
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import doctor
from act.lib import config

DEFAULT = config.DEFAULT_MODEL_FALLBACK
ALIAS = "claude-fable-5-1[1m]"


def _probes(claude_code=None):
    return doctor.Probes(
        which=lambda name: "/fake/bin/claude",
        run=lambda cmd, env=None, timeout=None: (0, "ok"),
        claude_code_settings=lambda: dict(
            claude_code or {"model": None, "exists": False, "parseable": False}),
    )


def _cc(model=ALIAS):
    return {"model": model, "exists": True, "parseable": True}


class _Overrides(unittest.TestCase):
    def setUp(self):
        config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True)
        self.addCleanup(lambda: config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True))

    def _knobs(self, **doc):
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text(json.dumps(doc), encoding="utf-8")


class RowNamesTheFallbackTestCase(_Overrides):
    def test_default_row_names_the_default_fallback(self):
        r = doctor._check_claude_code_model(_probes())
        self.assertEqual(r.status, doctor.OK)
        self.assertIn("fallback: %s" % DEFAULT, r.detail)
        self.assertIn("dispatch: follow", r.detail)

    def test_off_is_spelled_out(self):
        self._knobs(models_fallback="off")
        r = doctor._check_claude_code_model(_probes())
        self.assertEqual(r.status, doctor.OK)          # canonical-free default: nothing to warn about
        self.assertIn("fallback: off", r.detail)

    def test_explicit_fallback_id_is_named(self):
        self._knobs(models_fallback="claude-sonnet-5")
        r = doctor._check_claude_code_model(_probes(_cc("claude-fable-5")))
        self.assertEqual(r.status, doctor.OK)
        self.assertIn("fallback: claude-sonnet-5", r.detail)

    def test_unparseable_settings_still_warns_and_names_fallback(self):
        r = doctor._check_claude_code_model(_probes({"model": None, "exists": True, "parseable": False}))
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("fallback: %s" % DEFAULT, r.detail)


class AliasRiskSoftenedTestCase(_Overrides):
    def test_following_alias_with_default_fallback_is_ok_and_names_it(self):
        r = doctor._check_claude_code_model(_probes(_cc()))
        self.assertEqual(r.status, doctor.OK)
        self.assertIn(ALIAS, r.detail)
        self.assertIn("dispatch/pipeline", r.detail)
        self.assertTrue(("回退到 %s" % DEFAULT) in r.detail or ("fall back to %s" % DEFAULT) in r.detail)
        self.assertFalse(r.fix)                           # nothing to fix — the row informs

    def test_following_alias_with_fallback_off_warns(self):
        self._knobs(models_fallback="off")
        r = doctor._check_claude_code_model(_probes(_cc()))
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn(ALIAS, r.detail)
        self.assertIn("fallback: off", r.detail)
        self.assertTrue(r.fix)
        self.assertTrue("回退" in r.fix or "fallback" in r.fix)   # the fix names the third way out

    def test_following_alias_with_explicit_fallback_is_ok(self):
        self._knobs(models_fallback="claude-opus-5")
        r = doctor._check_claude_code_model(_probes(_cc()))
        self.assertEqual(r.status, doctor.OK)
        self.assertIn("claude-opus-5", r.detail)

    def test_following_alias_with_noncanonical_fallback_still_warns(self):
        # the net is itself an alias/suffix — same state the settings page warns
        # about (server fallback_warning); the doctor must not read it as OK
        self._knobs(models_fallback="claude-opus-5-eap")
        r = doctor._check_claude_code_model(_probes(_cc()))
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn(ALIAS, r.detail)
        self.assertIn("claude-opus-5-eap", r.detail)
        self.assertIn("fallback: claude-opus-5-eap", r.detail)
        self.assertTrue(r.fix)
        self.assertTrue("canonical id" in r.fix)

    def test_fallback_equal_to_the_retiring_alias_is_no_net(self):
        self._knobs(models_fallback=ALIAS)
        r = doctor._check_claude_code_model(_probes(_cc()))
        self.assertEqual(r.status, doctor.WARN)

    def test_doctor_and_server_agree_on_which_fallbacks_soften(self):
        from server import settings as srv
        for fb in (DEFAULT, "claude-opus-5", "claude-sonnet-5", "claude-opus-5-eap", ALIAS, "claude-opus-5[1m]-x"):
            with self.subTest(fallback=fb):
                self._knobs(models_fallback=fb)
                r = doctor._check_claude_code_model(_probes(_cc()))
                softened = r.status == doctor.OK
                self.assertEqual(softened, srv.fallback_warning(fb) is None)

    def test_no_knob_following_needs_no_softening(self):
        self._knobs(models_dispatch="claude-opus-5", models_pipeline="claude-sonnet-5",
                    models_fallback="off")
        r = doctor._check_claude_code_model(_probes(_cc()))
        self.assertEqual(r.status, doctor.OK)

    def test_never_fail_in_any_state(self):
        for fb in ("off", DEFAULT, "claude-sonnet-5", "bad id"):
            with self.subTest(fallback=fb):
                self._knobs(models_fallback=fb)
                r = doctor._check_claude_code_model(_probes(_cc()))
                self.assertNotEqual(r.status, doctor.FAIL)

    def test_row_rides_under_fast(self):
        rows = doctor.run_checks(_probes(_cc()), fast=True)
        row = next(r for r in rows if r.name == "claude code model")
        self.assertEqual(row.status, doctor.OK)
        self.assertIn(DEFAULT, row.detail)


if __name__ == "__main__":
    unittest.main()
