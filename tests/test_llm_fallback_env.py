"""``runner_env`` pins Claude Code's ``opus`` alias to the fallback (CONTRACT
§59 D53): ``ANTHROPIC_DEFAULT_OPUS_MODEL=<fallback>`` whenever the fallback knob
is on and its id is an Opus id — so every Opus the CLI reaches for on its own
(sub-agents, ``opusplan``, ``/model opus``, its unknown-model fallback) is the
same Opus 5 and never the retired 4.8. Off / non-Opus fallback → the variable
is left as inherited. ``DISABLE_AUTOUPDATER=1`` (§55 第五幕) is unchanged.

No claude is spawned; the env is inspected, never exported.
"""
import json
import os
import subprocess
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act import

from act import doctor, llm
from act.lib import config

DEFAULT = config.DEFAULT_MODEL_FALLBACK
ENV = llm.OPUS_ALIAS_ENV


def _cfg(**over):
    cfg = config.Config()
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def _write_overrides(doc):
    config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.SETTINGS_OVERRIDES_PATH.write_text(json.dumps(doc), encoding="utf-8")


class RunnerEnvPinTestCase(unittest.TestCase):
    def setUp(self):
        config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True)
        self.addCleanup(lambda: config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True))
        # a clean inherited environment for the variable under test
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(ENV, None)

    def test_variable_name(self):
        self.assertEqual(ENV, "ANTHROPIC_DEFAULT_OPUS_MODEL")

    def test_default_knob_pins_the_alias(self):
        env = llm.runner_env(_cfg())
        self.assertEqual(env[ENV], DEFAULT)
        self.assertEqual(env["DISABLE_AUTOUPDATER"], "1")
        self.assertNotIn(ENV, os.environ, "our own environ is untouched")

    def test_explicit_opus_id_pins_that_id(self):
        self.assertEqual(llm.runner_env(_cfg(models_fallback="claude-opus-5"))[ENV], "claude-opus-5")

    def test_off_leaves_the_variable_alone(self):
        self.assertNotIn(ENV, llm.runner_env(_cfg(models_fallback="off")))
        with mock.patch.dict(os.environ, {ENV: "claude-opus-4-8"}):
            # inherited value survives untouched when the knob is off
            self.assertEqual(llm.runner_env(_cfg(models_fallback="off"))[ENV], "claude-opus-4-8")

    def test_non_opus_fallback_does_not_lie_about_the_alias(self):
        self.assertNotIn(ENV, llm.runner_env(_cfg(models_fallback="claude-sonnet-5")))

    def test_pin_overrides_an_inherited_old_opus(self):
        # the launchd plist / login shell may carry the CLI's old alias — ours wins
        with mock.patch.dict(os.environ, {ENV: "claude-opus-4-8"}):
            self.assertEqual(llm.runner_env(_cfg())[ENV], DEFAULT)

    def test_garbage_knob_pins_the_default(self):
        self.assertEqual(llm.runner_env(_cfg(models_fallback="bad id"))[ENV], DEFAULT)

    def test_cfg_none_reads_fresh_config(self):
        self.assertEqual(llm.runner_env()[ENV], DEFAULT)
        _write_overrides({"models_fallback": "off"})
        self.assertNotIn(ENV, llm.runner_env())


class SitesReceiveThePinTestCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(ENV, None)
        bin_patch = mock.patch("act.lib.config.resolve_claude_bin", return_value="claude")
        bin_patch.start()
        self.addCleanup(bin_patch.stop)

    def test_run_hands_the_pin_to_the_runner(self):
        rec = []

        def runner(argv, **kw):
            rec.append(kw["env"])
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        llm.run("hi", runner=runner, timeout=1, cfg=_cfg())
        self.assertEqual(rec[0][ENV], DEFAULT)
        llm.run("hi", runner=runner, timeout=1, cfg=_cfg(models_fallback="off"))
        self.assertNotIn(ENV, rec[1])

    def test_doctor_probe_env_carries_the_pin_but_argv_no_fallback(self):
        # the probe tests THIS id (no --fallback-model) while the session env is
        # the one real launches get — a probe must not run in a foreign env
        calls = []

        def run(cmd, env=None, timeout=None):
            calls.append((list(cmd), env))
            return 0, "ok"

        probes = doctor.Probes(which=lambda name: "/fake/bin/claude", run=run,
                               claude_code_settings=lambda: {})
        doctor._model_row(probes, _cfg(), "dispatch", "claude-opus-5", {})
        cmd, env = calls[0]
        self.assertNotIn("--fallback-model", cmd)
        self.assertEqual(env[ENV], DEFAULT)


if __name__ == "__main__":
    unittest.main()
