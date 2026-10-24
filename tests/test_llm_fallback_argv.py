"""``--fallback-model`` placement in every argv act/llm.py builds (CONTRACT §59
D53; §78 isolation ordering).

Fixed head of every launch: ``--output-format <fmt>`` → ``--model`` (always
explicit since v2.0.0) → ``--fallback-model`` → ``ISOLATION_ARGV`` → the variable
tail (``extra_argv`` / the ``arg_last`` prompt). ``off`` drops exactly the pair;
garbage on the knob degrades to the default id — never to a bare flag, never to
argv junk; a fallback equal to the primary is left off (the CLI refuses it). The
doctor's probe stays fallback-free on purpose. (The ``--bg`` dispatch argv and
actd's per-pass refresh retired with the executor — full text in tag ``full``.)

No claude is spawned: subprocess.run is faked (tests/__init__.py guard).
"""
import json
import subprocess
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act import

from act import llm
from act.lib import config

DEFAULT = config.DEFAULT_MODEL_FALLBACK
FB = ["--fallback-model", DEFAULT]
OPUS = "claude-opus-5"
MODEL = ["--model", config.DEFAULT_MODEL_PIPELINE]
ISO = list(llm.ISOLATION_ARGV)


def _cfg(**over):
    cfg = config.Config()
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def _write_overrides(doc):
    config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.SETTINGS_OVERRIDES_PATH.write_text(json.dumps(doc), encoding="utf-8")


class _Sandbox(unittest.TestCase):
    def setUp(self):
        config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True)
        config.CONFIG_PATH.unlink(missing_ok=True)
        self.addCleanup(lambda: config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True))
        self.addCleanup(lambda: config.CONFIG_PATH.unlink(missing_ok=True))
        patcher = mock.patch("act.lib.config.resolve_claude_bin", return_value="claude")
        patcher.start()
        self.addCleanup(patcher.stop)


class FallbackModelResolverTestCase(_Sandbox):
    def test_default_explicit_off_garbage(self):
        self.assertEqual(llm.fallback_model(_cfg()), DEFAULT)
        self.assertEqual(llm.fallback_model(_cfg(models_fallback="claude-sonnet-5")), "claude-sonnet-5")
        self.assertIsNone(llm.fallback_model(_cfg(models_fallback="off")))
        self.assertIsNone(llm.fallback_model(_cfg(models_fallback=" OFF ")))
        # garbage → the default, never argv junk and never silently off
        self.assertEqual(llm.fallback_model(_cfg(models_fallback="has space\n")), DEFAULT)
        self.assertEqual(llm.fallback_model(_cfg(models_fallback=12)), DEFAULT)

    def test_cfg_none_reads_fresh_config(self):
        self.assertEqual(llm.fallback_model(), DEFAULT)
        _write_overrides({"models_fallback": "off"})
        self.assertIsNone(llm.fallback_model())
        _write_overrides({"models_fallback": "claude-sonnet-5"})
        self.assertEqual(llm.fallback_model(), "claude-sonnet-5")

    def test_constants_mirror_config(self):
        self.assertEqual(llm.DEFAULT_FALLBACK, config.DEFAULT_MODEL_FALLBACK)
        self.assertEqual(llm.FALLBACK_OFF, config.MODEL_FALLBACK_OFF)


class HeadlessArgvTestCase(_Sandbox):
    """``claude -p`` — the pair sits behind --output-format / --model, ahead of the
    isolation tuple, extra_argv and the arg_last prompt."""

    def test_arg_prompt_first(self):
        self.assertEqual(llm.build_argv("P", cfg=_cfg()),
                         ["claude", "-p", "P", "--output-format", "text", *MODEL, *FB, *ISO])

    def test_behind_model_ahead_of_variadic_tail(self):
        argv = llm.build_argv("P", cfg=_cfg(models_pipeline="claude-sonnet-5"),
                              extra_argv=["--tools", ""])
        self.assertEqual(argv, ["claude", "-p", "P", "--output-format", "text",
                                "--model", "claude-sonnet-5", *FB, *ISO, "--tools", ""])

    def test_arg_last_prompt_still_last(self):
        argv = llm.build_argv("P", prompt_via="arg_last", cfg=_cfg())
        self.assertEqual(argv, ["claude", "-p", "--output-format", "text", *MODEL, *FB, *ISO, "P"])

    def test_stdin_shape(self):
        self.assertEqual(llm.build_argv("P", prompt_via="stdin", cfg=_cfg()),
                         ["claude", "-p", "--output-format", "text", *MODEL, *FB, *ISO])

    def test_json_output_format_keeps_the_order(self):
        argv = llm.build_argv("P", output_format="json", cfg=_cfg())
        self.assertEqual(argv[3:], ["--output-format", "json", *MODEL, *FB, *ISO])

    def test_off_drops_exactly_the_pair(self):
        off = _cfg(models_fallback="off")
        self.assertEqual(llm.build_argv("P", cfg=off),
                         ["claude", "-p", "P", "--output-format", "text", *MODEL, *ISO])
        self.assertEqual(llm.build_argv("P", prompt_via="arg_last", cfg=off,
                                        extra_argv=["--max-turns", "1"]),
                         ["claude", "-p", "--output-format", "text", *MODEL, *ISO,
                          "--max-turns", "1", "P"])
        self.assertNotIn("--fallback-model", llm.build_argv("P", cfg=off))

    def test_explicit_fallback_id(self):
        argv = llm.build_argv("P", cfg=_cfg(models_fallback="claude-sonnet-5"))
        i = argv.index("--fallback-model")
        self.assertEqual(argv[i:i + 2], ["--fallback-model", "claude-sonnet-5"])
        self.assertEqual(argv[i + 2:], ISO)

    def test_a_fallback_equal_to_the_primary_is_left_off(self):
        argv = llm.build_argv("P", cfg=_cfg(models_pipeline="claude-sonnet-5",
                                            models_fallback="claude-sonnet-5"))
        self.assertNotIn("--fallback-model", argv)

    def test_garbage_knob_never_reaches_argv(self):
        argv = llm.build_argv("P", cfg=_cfg(models_fallback="bad id here"))
        self.assertEqual(argv.count("--fallback-model"), 1)
        self.assertEqual(argv[argv.index("--fallback-model") + 1], DEFAULT)
        self.assertNotIn("bad id here", argv)

    def test_run_hands_the_same_argv_to_the_runner(self):
        rec = []

        def runner(argv, **kw):
            rec.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        with mock.patch("act.llm.runner_env", return_value={}):
            llm.run("P", runner=runner, timeout=1, cfg=_cfg())
        self.assertEqual(rec[0], ["claude", "-p", "P", "--output-format", "text", *MODEL, *FB, *ISO])


class ProbeArgvTestCase(_Sandbox):
    def test_doctor_probe_carries_no_fallback(self):
        # the probe asks whether THIS id answers; a fallback would mask the outage
        argv = llm.probe_argv(OPUS, _cfg())
        self.assertNotIn("--fallback-model", argv)
        self.assertEqual(argv, ["claude", "-p", "ok", "--model", OPUS,
                                "--output-format", "text", "--max-turns", "1", *ISO])


if __name__ == "__main__":
    unittest.main()
