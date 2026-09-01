"""doctor rows for the model knobs (CONTRACT §57, D22).

- ``claude code model`` (file reads only, runs under --fast): what follow
  inherits from ~/.claude/settings.json + where both knobs point; WARN when a
  following knob inherits a non-canonical alias (the EAP-retirement incident),
  never FAIL (§56's rollback verdict must not turn on it).
- ``model dispatch`` / ``model pipeline`` (not under --fast): follow = OK,
  no call; explicit = one minimal ``claude -p ok --model <id>`` via
  probes.run; non-zero exit = FAIL ``model_unavailable`` in plain language.

Probes are injected (no real claude, no real ~/.claude); the knobs are set
through the sandbox settings_overrides.json (the web's write path).
"""
import json
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import doctor
from act.lib import config

OPUS = "claude-opus-5"


class FakeRun:
    def __init__(self, rc=0, out="ok"):
        self.calls = []
        self.rc, self.out = rc, out

    def __call__(self, cmd, env=None, timeout=None):
        self.calls.append({"cmd": list(cmd), "env": env, "timeout": timeout})
        return self.rc, self.out


def _probes(run=None, claude_code=None, which=True):
    return doctor.Probes(
        which=(lambda name: "/fake/bin/claude") if which else (lambda name: None),
        run=run or FakeRun(),
        claude_code_settings=lambda: dict(
            claude_code or {"model": None, "exists": False, "parseable": False}),
    )


def _row(results, name):
    return next(r for r in results if r.name == name)


class _Overrides(unittest.TestCase):
    def setUp(self):
        config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True)
        self.addCleanup(lambda: config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True))

    def _knobs(self, **doc):
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text(json.dumps(doc), encoding="utf-8")


class ClaudeCodeModelRowTestCase(_Overrides):
    def test_unset_global_default_is_ok_and_names_both_knobs(self):
        r = doctor._check_claude_code_model(_probes())
        self.assertEqual(r.status, doctor.OK)
        self.assertIn("dispatch: follow", r.detail)
        self.assertIn("pipeline: follow", r.detail)

    def test_canonical_global_default_is_ok(self):
        cc = {"model": "claude-fable-5", "exists": True, "parseable": True}
        r = doctor._check_claude_code_model(_probes(claude_code=cc))
        self.assertEqual(r.status, doctor.OK)
        self.assertIn("claude-fable-5", r.detail)

    def test_following_a_non_canonical_alias_warns_never_fails(self):
        cc = {"model": "claude-fable-5-1[1m]", "exists": True, "parseable": True}
        r = doctor._check_claude_code_model(_probes(claude_code=cc))
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("claude-fable-5-1[1m]", r.detail)
        self.assertIn("dispatch/pipeline", r.detail)
        self.assertTrue(r.fix)

    def test_alias_is_fine_when_no_knob_follows(self):
        self._knobs(models_dispatch=OPUS, models_pipeline="claude-sonnet-5")
        cc = {"model": "claude-fable-5-1[1m]", "exists": True, "parseable": True}
        r = doctor._check_claude_code_model(_probes(claude_code=cc))
        self.assertEqual(r.status, doctor.OK)
        self.assertIn("dispatch: %s" % OPUS, r.detail)

    def test_unparsable_settings_file_warns(self):
        cc = {"model": None, "exists": True, "parseable": False}
        r = doctor._check_claude_code_model(_probes(claude_code=cc))
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("settings.json", r.detail)

    def test_row_is_present_under_fast(self):
        results = doctor.run_checks(_probes(), fast=True)
        self.assertIn("claude code model", [r.name for r in results])
        self.assertNotIn("model dispatch", [r.name for r in results])


class ModelLivenessTestCase(_Overrides):
    def test_follow_knobs_spend_nothing(self):
        run = FakeRun()
        rows = doctor._check_model_liveness(_probes(run=run))
        self.assertEqual([r.status for r in rows], [doctor.OK, doctor.OK])
        self.assertEqual(run.calls, [])

    def test_explicit_knob_probes_with_that_model(self):
        self._knobs(models_dispatch=OPUS)
        run = FakeRun()
        rows = doctor._check_model_liveness(_probes(run=run))
        self.assertEqual(len(run.calls), 1)
        cmd = run.calls[0]["cmd"]
        self.assertEqual(cmd[1:], ["-p", "ok", "--model", OPUS,
                                   "--output-format", "text", "--max-turns", "1"])
        self.assertEqual(run.calls[0]["timeout"], doctor._MODEL_PROBE_TIMEOUT)
        self.assertEqual(_row(rows, "model dispatch").status, doctor.OK)
        self.assertEqual(_row(rows, "model pipeline").status, doctor.OK)   # follow

    def test_same_model_on_both_knobs_is_probed_once(self):
        self._knobs(models_dispatch=OPUS, models_pipeline=OPUS)
        run = FakeRun()
        rows = doctor._check_model_liveness(_probes(run=run))
        self.assertEqual(len(run.calls), 1)
        self.assertEqual([r.status for r in rows], [doctor.OK, doctor.OK])

    def test_unavailable_model_fails_in_plain_language(self):
        self._knobs(models_dispatch="claude-opus-5-eap", models_pipeline="claude-sonnet-5")
        run = FakeRun(rc=1, out="API Error: 404 model not found: claude-opus-5-eap")
        rows = doctor._check_model_liveness(_probes(run=run))
        d = _row(rows, "model dispatch")
        self.assertEqual(d.status, doctor.FAIL)
        self.assertIn("claude-opus-5-eap", d.detail)
        self.assertTrue("派工会全部失败" in d.detail or "every dispatch will fail" in d.detail)
        self.assertEqual(d.failure_id, "model_unavailable")
        self.assertTrue(d.fix)
        p = _row(rows, "model pipeline")
        self.assertEqual(p.status, doctor.FAIL)
        self.assertTrue("雷达" in p.detail or "radar" in p.detail)

    def test_missing_cli_skips_instead_of_blaming_the_model(self):
        self._knobs(models_pipeline=OPUS)
        run = FakeRun()
        rows = doctor._check_model_liveness(_probes(run=run, which=False))
        self.assertEqual(_row(rows, "model pipeline").status, doctor.WARN)
        self.assertEqual(run.calls, [])

    def test_run_checks_wires_the_rows_only_when_not_fast(self):
        self._knobs(models_dispatch=OPUS)
        run = FakeRun()
        names = [r.name for r in doctor.run_checks(_probes(run=run), fast=False)]
        self.assertIn("model dispatch", names)
        self.assertIn("model pipeline", names)
        self.assertTrue(any("--model" in c["cmd"] for c in run.calls))
        run2 = FakeRun()
        names_fast = [r.name for r in doctor.run_checks(_probes(run=run2), fast=True)]
        self.assertNotIn("model dispatch", names_fast)
        self.assertFalse(any("--model" in c["cmd"] for c in run2.calls))


if __name__ == "__main__":
    unittest.main()
