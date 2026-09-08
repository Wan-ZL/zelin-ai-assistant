"""``--fallback-model`` placement in every argv act/llm.py builds (CONTRACT §59
D53; §4 / §65 ordering).

Fixed head of every launch: ``--output-format <fmt>`` → ``--model`` →
``--fallback-model`` → (``--bg`` only) ``NO_MCP_ARGV`` → the variable tail
(``extra_argv`` / ``--name`` / ``--resume`` / prompt). ``off`` restores the
pre-D53 argv byte for byte; garbage on the knob degrades to the default id —
never to a bare flag, never to argv junk. The doctor's probe stays fallback-free
on purpose. actd's per-pass refresh picks the third knob up live.

No claude is spawned: subprocess.run is faked (tests/__init__.py guard).
"""
import json
import subprocess
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act import

from act import actd, executor, llm
from act.lib import config

DEFAULT = config.DEFAULT_MODEL_FALLBACK
FB = ["--fallback-model", DEFAULT]
NO_MCP = list(llm.NO_MCP_ARGV)
OPUS = "claude-opus-5"


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
    """``claude -p`` sites — the pair sits behind --output-format / --model,
    ahead of extra_argv and the arg_last prompt."""

    def test_arg_prompt_first(self):
        self.assertEqual(llm.build_argv("P", cfg=_cfg()),
                         ["claude", "-p", "P", "--output-format", "text", *FB])

    def test_behind_model_ahead_of_variadic_tail(self):
        argv = llm.build_argv("P", cfg=_cfg(models_pipeline=OPUS),
                              extra_argv=["--allowedTools", "A,B"])
        self.assertEqual(argv, ["claude", "-p", "P", "--output-format", "text",
                                "--model", OPUS, *FB, "--allowedTools", "A,B"])

    def test_arg_last_prompt_still_last(self):
        argv = llm.build_argv("P", prompt_via="arg_last", cfg=_cfg())
        self.assertEqual(argv, ["claude", "-p", "--output-format", "text", *FB, "P"])
        self.assertEqual(argv[-1], "P")

    def test_stdin_shape(self):
        self.assertEqual(llm.build_argv("P", prompt_via="stdin", cfg=_cfg()),
                         ["claude", "-p", "--output-format", "text", *FB])

    def test_json_output_format_keeps_the_order(self):
        argv = llm.build_argv("P", output_format="json", cfg=_cfg())
        self.assertEqual(argv[3:], ["--output-format", "json", *FB])

    def test_off_is_the_pre_d53_argv_byte_for_byte(self):
        off = _cfg(models_fallback="off")
        self.assertEqual(llm.build_argv("P", cfg=off),
                         ["claude", "-p", "P", "--output-format", "text"])
        self.assertEqual(llm.build_argv("P", prompt_via="arg_last", cfg=off,
                                        extra_argv=["--max-turns", "1"]),
                         ["claude", "-p", "--output-format", "text", "--max-turns", "1", "P"])
        self.assertNotIn("--fallback-model", llm.build_argv("P", cfg=off))

    def test_explicit_fallback_id(self):
        argv = llm.build_argv("P", cfg=_cfg(models_fallback="claude-sonnet-5"))
        self.assertEqual(argv[-2:], ["--fallback-model", "claude-sonnet-5"])

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
        self.assertEqual(rec[0], ["claude", "-p", "P", "--output-format", "text", *FB])


class BgArgvTestCase(_Sandbox):
    """``claude --bg`` base argv — the pair sits behind the dispatch model flag
    and ahead of NO_MCP_ARGV; the caller's --name / --resume / prompt follow."""

    def test_default(self):
        self.assertEqual(llm.dispatch_argv(_cfg()),
                         ["claude", "--bg", "--dangerously-skip-permissions", *FB])

    def test_behind_model_flag(self):
        self.assertEqual(llm.dispatch_argv(_cfg(models_dispatch=OPUS)),
                         ["claude", "--bg", "--dangerously-skip-permissions", "--model", OPUS, *FB])

    def test_ahead_of_no_mcp(self):
        self.assertEqual(llm.dispatch_argv(_cfg(), no_mcp=True),
                         ["claude", "--bg", "--dangerously-skip-permissions", *FB, *NO_MCP])
        self.assertEqual(llm.dispatch_argv(_cfg(models_dispatch=OPUS), no_mcp=True),
                         ["claude", "--bg", "--dangerously-skip-permissions",
                          "--model", OPUS, *FB, *NO_MCP])

    def test_without_no_mcp_ends_with_the_pair(self):
        self.assertEqual(llm.dispatch_argv(_cfg(models_dispatch=OPUS), no_mcp=False)[-2:], FB)

    def test_skip_permissions_off(self):
        self.assertEqual(llm.dispatch_argv(_cfg(skip_permissions=False)), ["claude", "--bg", *FB])
        self.assertEqual(llm.dispatch_argv(_cfg(skip_permissions=False), no_mcp=True),
                         ["claude", "--bg", *FB, *NO_MCP])

    def test_off_is_the_pre_d53_argv_byte_for_byte(self):
        off = _cfg(models_fallback="off")
        self.assertEqual(llm.dispatch_argv(off), ["claude", "--bg", "--dangerously-skip-permissions"])
        self.assertEqual(llm.dispatch_argv(off, no_mcp=True),
                         ["claude", "--bg", "--dangerously-skip-permissions", *NO_MCP])
        off.models_dispatch = OPUS
        self.assertEqual(llm.dispatch_argv(off, no_mcp=True),
                         ["claude", "--bg", "--dangerously-skip-permissions", "--model", OPUS, *NO_MCP])

    def test_executor_launch_site_keeps_name_and_prompt_last(self):
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = list(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="backgrounded · abc123ff", stderr="")

        with mock.patch("subprocess.run", fake_run), mock.patch("act.llm.runner_env", return_value={}):
            executor._default_runner("prompt text", config.STATE_DIR, name="R-1 · t",
                                     cfg=_cfg(models_dispatch=OPUS))
        self.assertEqual(captured["cmd"],
                         ["claude", "--bg", "--dangerously-skip-permissions", "--model", OPUS, *FB,
                          "--name", "R-1 · t", "prompt text"])

    def test_bg_base_cmd_is_still_the_boundary(self):
        cfg = _cfg(models_fallback="claude-sonnet-5")
        self.assertEqual(executor._bg_base_cmd(cfg), llm.dispatch_argv(cfg))


class ProbeArgvTestCase(_Sandbox):
    def test_doctor_probe_carries_no_fallback(self):
        # the probe asks whether THIS id answers; a fallback would mask the outage
        argv = llm.probe_argv(OPUS, _cfg())
        self.assertNotIn("--fallback-model", argv)
        self.assertEqual(argv, ["claude", "-p", "ok", "--model", OPUS,
                                "--output-format", "text", "--max-turns", "1"])


class ActdRefreshTestCase(_Sandbox):
    def test_refresh_pulls_the_third_knob_live(self):
        frozen = config.Config()
        self.assertEqual(frozen.models_fallback, DEFAULT)
        _write_overrides({"models_fallback": "off"})
        actd._refresh_model_knobs(frozen)
        self.assertEqual(frozen.models_fallback, "off")
        self.assertEqual(llm.dispatch_argv(frozen), ["claude", "--bg", "--dangerously-skip-permissions"])
        _write_overrides({})
        actd._refresh_model_knobs(frozen)
        self.assertEqual(frozen.models_fallback, DEFAULT)
        self.assertEqual(llm.dispatch_argv(frozen)[-2:], FB)


if __name__ == "__main__":
    unittest.main()
