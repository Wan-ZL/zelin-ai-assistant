"""act/llm.py — the single LLM boundary (CONTRACT §59, D22; 防腐十条 #3).

Pins, per call site, that routing through ``llm.run`` / ``llm.dispatch_argv``
changed NOTHING while both knobs follow: the argv every headless site hands to
``subprocess.run`` is byte-identical to its pre-§59 literal (binary resolution
patched to the bare ``"claude"`` so the pin reads like the old code), and so
are the kwargs (timeout / env / neutral cwd / stdin piping). Then flips the
pipeline knob through ``state/settings_overrides.json`` (the web's write path)
and asserts every site — separate-process sites read config fresh — gains
exactly ``--model <id>`` right behind ``--output-format text`` and nothing
else moves. The dispatch knob rides ``executor._bg_base_cmd`` the same way.

``subprocess.run`` is faked (recorder) — no claude is ever spawned (the
tests/__init__.py guard would refuse anyway). Sandbox AIASSISTANT_HOME.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act import

from act import (actd, analyze, ask, executor, golden_eval, llm, merge_review,
                 radar, radar_gmail, radar_slack, voice_gen, weekly_digest)
from act.lib import config, quick_capture

STATE = str(config.STATE_DIR)
OPUS = "claude-opus-5"


class _Recorder:
    def __init__(self, stdout="[]"):
        self.calls = []
        self.stdout = stdout

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        return subprocess.CompletedProcess(argv, 0, stdout=self.stdout, stderr="")


def _write_overrides(doc):
    config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.SETTINGS_OVERRIDES_PATH.write_text(json.dumps(doc), encoding="utf-8")


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self.addCleanup(lambda: config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True))
        self.addCleanup(lambda: config.CONFIG_PATH.unlink(missing_ok=True))
        config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True)
        config.CONFIG_PATH.unlink(missing_ok=True)
        # bare "claude" so the per-site pins read exactly like the pre-§59 code
        patcher = mock.patch("act.lib.config.resolve_claude_bin", return_value="claude")
        patcher.start()
        self.addCleanup(patcher.stop)
        env_patch = mock.patch("act.llm.runner_env", return_value={"ENV": "x"})
        env_patch.start()
        self.addCleanup(env_patch.stop)


# --------------------------------------------------------------------------- #
# knob resolution + argv builders
# --------------------------------------------------------------------------- #
class ModelForTestCase(_Sandbox):
    def test_follow_is_none_for_both_knobs(self):
        cfg = config.Config()
        self.assertIsNone(llm.model_for("dispatch", cfg))
        self.assertIsNone(llm.model_for("pipeline", cfg))

    def test_explicit_id_comes_back_verbatim(self):
        cfg = config.Config()
        cfg.models_dispatch = OPUS
        self.assertEqual(llm.model_for("dispatch", cfg), OPUS)
        self.assertIsNone(llm.model_for("pipeline", cfg))

    def test_garbage_degrades_to_follow_never_into_argv(self):
        cfg = config.Config()
        cfg.models_pipeline = "has space\nnewline"
        self.assertIsNone(llm.model_for("pipeline", cfg))
        self.assertNotIn("--model", llm.build_argv("p", cfg=cfg))

    def test_unknown_mode_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            llm.model_for("brain", config.Config())

    def test_cfg_none_reads_fresh_config(self):
        _write_overrides({"models_pipeline": OPUS})
        self.assertEqual(llm.model_for("pipeline"), OPUS)
        self.assertIsNone(llm.model_for("dispatch"))


class BuildArgvTestCase(_Sandbox):
    def test_arg_shape_follow(self):
        self.assertEqual(llm.build_argv("P", cfg=config.Config()),
                         ["claude", "-p", "P", "--output-format", "text"])

    def test_arg_shape_with_model_sits_behind_output_format(self):
        cfg = config.Config()
        cfg.models_pipeline = OPUS
        self.assertEqual(
            llm.build_argv("P", cfg=cfg, extra_argv=["--allowedTools", "X"]),
            ["claude", "-p", "P", "--output-format", "text", "--model", OPUS,
             "--allowedTools", "X"])

    def test_arg_last_keeps_prompt_at_the_end(self):
        cfg = config.Config()
        cfg.models_pipeline = OPUS
        self.assertEqual(llm.build_argv("P", prompt_via="arg_last", cfg=cfg),
                         ["claude", "-p", "--output-format", "text", "--model", OPUS, "P"])

    def test_stdin_shape_has_no_prompt_in_argv(self):
        self.assertEqual(llm.build_argv("P", prompt_via="stdin", cfg=config.Config()),
                         ["claude", "-p", "--output-format", "text"])

    def test_unknown_prompt_via_rejected(self):
        with self.assertRaises(ValueError):
            llm.build_argv("P", prompt_via="magic", cfg=config.Config())

    def test_dispatch_argv_follow_and_explicit(self):
        cfg = config.Config()
        self.assertEqual(llm.dispatch_argv(cfg),
                         ["claude", "--bg", "--dangerously-skip-permissions"])
        cfg.models_dispatch = OPUS
        self.assertEqual(llm.dispatch_argv(cfg),
                         ["claude", "--bg", "--dangerously-skip-permissions", "--model", OPUS])
        cfg.skip_permissions = False
        self.assertEqual(llm.dispatch_argv(cfg), ["claude", "--bg", "--model", OPUS])

    def test_pipeline_knob_never_leaks_into_dispatch(self):
        cfg = config.Config()
        cfg.models_pipeline = OPUS
        self.assertNotIn("--model", llm.dispatch_argv(cfg))

    def test_probe_argv(self):
        self.assertEqual(llm.probe_argv(OPUS, config.Config()),
                         ["claude", "-p", "ok", "--model", OPUS,
                          "--output-format", "text", "--max-turns", "1"])


class RunSeamTestCase(_Sandbox):
    def test_runner_receives_argv_and_legacy_kwargs(self):
        rec = _Recorder()
        proc = llm.run("P", runner=rec, timeout=42, cwd="/tmp/x", cfg=config.Config())
        self.assertEqual(proc.returncode, 0)
        argv, kw = rec.calls[0]
        self.assertEqual(argv, ["claude", "-p", "P", "--output-format", "text"])
        self.assertEqual(kw, {"capture_output": True, "text": True, "timeout": 42,
                              "env": {"ENV": "x"}, "cwd": "/tmp/x"})

    def test_stdin_mode_pipes_the_scrubbed_prompt(self):
        rec = _Recorder()
        llm.run("key sk-ant-api03-abcdefghijklmnop here", runner=rec, timeout=1,
                prompt_via="stdin", cfg=config.Config())
        argv, kw = rec.calls[0]
        self.assertEqual(argv, ["claude", "-p", "--output-format", "text"])
        self.assertNotIn("sk-ant-api03", kw["input"])
        self.assertIn("[脱敏]", kw["input"])

    def test_default_runner_is_subprocess_run_looked_up_at_call_time(self):
        rec = _Recorder()
        with mock.patch("subprocess.run", rec):
            llm.run("P", timeout=1, cfg=config.Config())
        self.assertEqual(len(rec.calls), 1)


# --------------------------------------------------------------------------- #
# per-site pins — argv byte-identical to the pre-§59 literals while following
# --------------------------------------------------------------------------- #
class PerSiteUnchangedArgvTestCase(_Sandbox):
    """Each entry: (site runner, expected argv, expected kwargs besides env)."""

    def _sites(self):
        return [
            ("analyze", analyze._default_runner,
             ["claude", "-p", "P", "--output-format", "text",
              "--allowedTools", analyze._EXPAND_ALLOWED_TOOLS],
             {"timeout": 420}),
            ("radar_slack.extractor", radar_slack._default_extractor,
             ["claude", "-p", "--output-format", "text"],
             {"timeout": 180, "cwd": STATE, "input": "P"}),
            ("radar_slack.mcp", radar_slack._default_mcp_runner,
             ["claude", "-p", "P", "--output-format", "text",
              "--allowedTools", radar_slack._MCP_ALLOWED_TOOLS],
             {"timeout": 300}),
            ("radar_gmail", radar_gmail._default_extractor,
             ["claude", "-p", "--output-format", "text"],
             {"timeout": 180, "cwd": STATE, "input": "P"}),
            ("golden_eval", golden_eval._default_extractor,
             ["claude", "-p", "--output-format", "text"],
             {"timeout": 180, "cwd": STATE, "input": "P"}),
            ("voice_gen", voice_gen._default_runner,
             ["claude", "-p", "P", "--output-format", "text",
              "--allowedTools", voice_gen._MCP_ALLOWED_TOOLS],
             {"timeout": voice_gen.TIMEOUT_S}),
            ("ask", ask._default_runner,
             ["claude", "-p", "P", "--output-format", "text"],
             {"timeout": ask.ASK_TIMEOUT, "cwd": STATE}),
            ("merge_review", merge_review._default_runner,
             ["claude", "-p", "P", "--output-format", "text"],
             {"timeout": merge_review.CLAUDE_TIMEOUT, "cwd": STATE}),
            ("weekly_digest", weekly_digest._run_claude,
             ["claude", "-p", "--output-format", "text", "P"],
             {"timeout": 420, "cwd": STATE}),
            ("quick_capture", quick_capture._default_extractor,
             ["claude", "-p", "--output-format", "text", "P"],
             {"timeout": 300, "cwd": STATE}),
        ]

    def test_every_site_argv_and_kwargs_unchanged_while_following(self):
        for name, runner, argv, extra in self._sites():
            with self.subTest(site=name):
                rec = _Recorder()
                with mock.patch("subprocess.run", rec):
                    runner("P")
                self.assertEqual(len(rec.calls), 1)
                got_argv, kw = rec.calls[0]
                self.assertEqual(got_argv, argv)
                expected = {"capture_output": True, "text": True, "env": {"ENV": "x"}}
                expected.update(extra)
                self.assertEqual(kw, expected)

    def test_radar_extract_argv_unchanged_prompt_last(self):
        rec = _Recorder()
        with mock.patch("subprocess.run", rec):
            radar._run_extract("note body")
        argv, kw = rec.calls[0]
        self.assertEqual(argv[:4], ["claude", "-p", "--output-format", "text"])
        self.assertEqual(len(argv), 5)
        self.assertIn("note body", argv[-1])          # prompt stays LAST (radar_scrub pin)
        self.assertEqual(kw["timeout"], 600)
        self.assertEqual(kw["cwd"], STATE)

    def test_every_site_gains_only_model_flag_when_pipeline_knob_is_explicit(self):
        _write_overrides({"models_pipeline": OPUS})
        for name, runner, argv, _extra in self._sites():
            with self.subTest(site=name):
                rec = _Recorder()
                with mock.patch("subprocess.run", rec):
                    runner("P")
                got, _kw = rec.calls[0]
                i = got.index("--output-format")
                self.assertEqual(got[i:i + 4], ["--output-format", "text", "--model", OPUS])
                without = got[:i + 2] + got[i + 4:]
                self.assertEqual(without, argv, "only --model may differ")

    def test_dispatch_knob_does_not_touch_pipeline_sites(self):
        _write_overrides({"models_dispatch": OPUS})
        rec = _Recorder()
        with mock.patch("subprocess.run", rec):
            ask._default_runner("P")
        self.assertNotIn("--model", rec.calls[0][0])


class ExecutorDispatchArgvTestCase(_Sandbox):
    def _launch_argv(self, cfg):
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = list(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="backgrounded · abc123ff", stderr="")

        with mock.patch("subprocess.run", fake_run):
            executor._default_runner("prompt text", Path("/tmp"), name="R-1 · t", cfg=cfg)
        return captured["cmd"]

    def test_follow_argv_is_the_pre_57_literal(self):
        self.assertEqual(self._launch_argv(config.Config()),
                         ["claude", "--bg", "--dangerously-skip-permissions",
                          "--name", "R-1 · t", "prompt text"])

    def test_explicit_dispatch_knob_adds_model_before_name(self):
        cfg = config.Config()
        cfg.models_dispatch = OPUS
        self.assertEqual(self._launch_argv(cfg),
                         ["claude", "--bg", "--dangerously-skip-permissions",
                          "--model", OPUS, "--name", "R-1 · t", "prompt text"])

    def test_bg_base_cmd_is_the_boundary(self):
        cfg = config.Config()
        cfg.models_dispatch = OPUS
        self.assertEqual(executor._bg_base_cmd(cfg), llm.dispatch_argv(cfg))


# --------------------------------------------------------------------------- #
# live pickup: actd refreshes the two knobs on its frozen cfg every pass
# --------------------------------------------------------------------------- #
class ActdKnobRefreshTestCase(_Sandbox):
    def test_refresh_pulls_overrides_onto_frozen_cfg(self):
        frozen = config.Config()
        self.assertEqual(frozen.models_dispatch, "follow")
        _write_overrides({"models_dispatch": OPUS, "models_pipeline": "claude-sonnet-5"})
        actd._refresh_model_knobs(frozen)
        self.assertEqual(frozen.models_dispatch, OPUS)
        self.assertEqual(frozen.models_pipeline, "claude-sonnet-5")
        # and back to follow once the override key is gone (diff-write delete)
        _write_overrides({})
        actd._refresh_model_knobs(frozen)
        self.assertEqual((frozen.models_dispatch, frozen.models_pipeline), ("follow", "follow"))

    def test_refresh_survives_a_broken_config_loader(self):
        frozen = config.Config()
        frozen.models_dispatch = OPUS
        with mock.patch("act.lib.config.load_config", side_effect=RuntimeError("boom")):
            actd._refresh_model_knobs(frozen)
        self.assertEqual(frozen.models_dispatch, OPUS)


# --------------------------------------------------------------------------- #
# Claude Code global default reader (what follow inherits)
# --------------------------------------------------------------------------- #
class ClaudeCodeDefaultReaderTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="llm-cc-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "settings.json"

    def test_missing_file(self):
        self.assertEqual(llm.read_claude_code_default_model(self.path),
                         {"model": None, "exists": False, "parseable": False})

    def test_unparsable_file(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(llm.read_claude_code_default_model(self.path),
                         {"model": None, "exists": True, "parseable": False})

    def test_model_key_read_and_stripped(self):
        self.path.write_text(json.dumps({"model": " claude-fable-5-1[1m] ", "theme": "dark"}),
                             encoding="utf-8")
        self.assertEqual(llm.read_claude_code_default_model(self.path),
                         {"model": "claude-fable-5-1[1m]", "exists": True, "parseable": True})

    def test_no_model_key_is_none_but_parseable(self):
        self.path.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
        self.assertEqual(llm.read_claude_code_default_model(self.path),
                         {"model": None, "exists": True, "parseable": True})

    def test_path_lives_under_home(self):
        self.assertEqual(llm.claude_code_settings_path(),
                         Path.home() / ".claude" / "settings.json")


# --------------------------------------------------------------------------- #
# runner_env — the one env every claude subprocess inherits (§19 + §55 第五幕)
# --------------------------------------------------------------------------- #
class RunnerEnvTestCase(unittest.TestCase):
    def test_headless_claude_never_self_updates(self):
        # daemons run the stable daemon copy; install.sh — not Claude Code's own
        # updater — decides when that file changes (§55 第五幕). Pinned even over
        # an inherited opt-in, and on a COPY of the environment.
        with mock.patch.dict(os.environ, {"DISABLE_AUTOUPDATER": "0"}):
            env = llm.runner_env()
            self.assertEqual(os.environ["DISABLE_AUTOUPDATER"], "0", "our own environ is untouched")
        self.assertEqual(env["DISABLE_AUTOUPDATER"], "1")

    def test_run_hands_it_to_the_runner(self):
        # llm.run and the executor's --bg sites all take env=runner_env(); pin
        # the seam they share rather than each site
        rec = _Recorder()
        with mock.patch("act.lib.config.resolve_claude_bin", return_value="claude"):
            llm.run("hi", runner=rec, timeout=1)
        self.assertEqual(rec.calls[0][1]["env"].get("DISABLE_AUTOUPDATER"), "1")


if __name__ == "__main__":
    unittest.main()
