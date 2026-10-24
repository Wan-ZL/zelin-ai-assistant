"""doctor rows for the model knobs (CONTRACT §59, D22 / D53).

- ``models`` (config reads only, runs under --fast): where the pipeline knob and
  the fallback point; WARN only when the pipeline id is non-canonical **and** the
  fallback cannot catch it (off, or itself non-canonical) — never FAIL.
- ``model pipeline`` (not under --fast): one minimal ``claude -p ok --model <id>``
  under the §78 isolation (``llm.probe_argv`` + ``runner_env`` + headless cwd);
  non-zero exit = FAIL ``model_unavailable`` in plain language.

v2.0.0: the ``claude code model`` row (it read ~/.claude/settings.json for the
"follow" mode) and the ``model dispatch`` row retired with follow mode and the
executor (§78; full text in tag ``full``).

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

    def __call__(self, cmd, env=None, timeout=None, cwd=None):
        self.calls.append({"cmd": list(cmd), "env": env, "timeout": timeout, "cwd": cwd})
        return self.rc, self.out


def _probes(run=None, which=True):
    return doctor.Probes(
        which=(lambda name: "/fake/bin/claude") if which else (lambda name: None),
        run=run or FakeRun(),
    )


class _Overrides(unittest.TestCase):
    def setUp(self):
        config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True)
        self.addCleanup(lambda: config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True))

    def _knobs(self, **doc):
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text(json.dumps(doc), encoding="utf-8")


class ModelsRowTestCase(_Overrides):
    def test_defaults_are_ok_and_name_both_knobs(self):
        r = doctor._check_models(_probes())
        self.assertEqual(r.status, doctor.OK)
        self.assertEqual(r.detail, "pipeline: %s · fallback: %s" % (OPUS, config.DEFAULT_MODEL_FALLBACK))

    def test_non_canonical_pipeline_with_the_default_fallback_is_ok(self):
        self._knobs(models_pipeline="claude-fable-5-1[1m]")
        self.assertEqual(doctor._check_models(_probes()).status, doctor.OK)

    def test_non_canonical_pipeline_with_fallback_off_warns_never_fails(self):
        self._knobs(models_pipeline="claude-fable-5-1[1m]", models_fallback="off")
        r = doctor._check_models(_probes())
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("claude-fable-5-1[1m]", r.detail)
        self.assertIn("fallback: off", r.detail)
        self.assertTrue(r.fix)

    def test_row_is_present_under_fast_and_the_retired_rows_are_not(self):
        names = [r.name for r in doctor.run_checks(_probes(), fast=True)]
        self.assertIn("models", names)
        for gone in ("claude code model", "model dispatch", "model pipeline"):
            self.assertNotIn(gone, names)


class ModelLivenessTestCase(_Overrides):
    def test_the_pipeline_model_is_probed_once_under_the_isolation(self):
        self._knobs(models_pipeline="claude-sonnet-5")
        run = FakeRun()
        r = doctor._check_model_liveness(_probes(run=run))
        self.assertEqual(r.status, doctor.OK)
        self.assertEqual(len(run.calls), 1)
        call = run.calls[0]
        self.assertEqual(call["cmd"][1:8], ["-p", "ok", "--model", "claude-sonnet-5",
                                            "--output-format", "text", "--max-turns"])
        self.assertIn("--bare", call["cmd"])
        self.assertNotIn("--fallback-model", call["cmd"])     # D53: ask THIS id
        self.assertEqual(call["timeout"], doctor._MODEL_PROBE_TIMEOUT)
        self.assertEqual(call["cwd"], config.headless_cwd())
        self.assertEqual(call["env"]["CLAUDE_CONFIG_DIR"], str(config.claude_config_dir()))

    def test_unavailable_model_fails_in_plain_language(self):
        self._knobs(models_pipeline="claude-opus-5-eap")
        run = FakeRun(rc=1, out="API Error: 404 model not found: claude-opus-5-eap")
        r = doctor._check_model_liveness(_probes(run=run))
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("claude-opus-5-eap", r.detail)
        self.assertTrue("会议纪要会全部失败" in r.detail or "every meeting recap will fail" in r.detail)
        self.assertEqual(r.failure_id, "model_unavailable")
        self.assertTrue(r.fix)

    def test_missing_cli_skips_instead_of_blaming_the_model(self):
        run = FakeRun()
        r = doctor._check_model_liveness(_probes(run=run, which=False))
        self.assertEqual(r.status, doctor.WARN)
        self.assertEqual(run.calls, [])

    def test_run_checks_wires_the_row_only_when_not_fast(self):
        run = FakeRun()
        names = [r.name for r in doctor.run_checks(_probes(run=run), fast=False)]
        self.assertIn("model pipeline", names)
        self.assertTrue(any("--model" in c["cmd"] for c in run.calls))
        run2 = FakeRun()
        names_fast = [r.name for r in doctor.run_checks(_probes(run=run2), fast=True)]
        self.assertNotIn("model pipeline", names_fast)
        self.assertFalse(any("--model" in c["cmd"] for c in run2.calls))


if __name__ == "__main__":
    unittest.main()
