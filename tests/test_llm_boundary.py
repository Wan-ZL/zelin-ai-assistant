"""act/llm.py — the single LLM boundary (CONTRACT §59, D22; §78 isolation; 防腐十条 #3).

v2.0.0: one headless site is left (the meeting recap, act/recap.py), and every
argv the boundary builds carries the §78 isolation literally:

- ``build_argv`` = ``[claude, -p, <prompt>, --output-format, <fmt>, --model, <id>,
  (--fallback-model, <id>)?, *ISOLATION_ARGV, *extra_argv]`` — ``--model`` is
  always explicit (the "follow the Claude Code global default" mode retired with
  its reader of the owner's settings), the D53 pair sits behind it, and the
  isolation tuple sits ahead of the variadic tail;
- ``runner_env`` always sets ``CLAUDE_CONFIG_DIR`` to the app's own claude-config
  (overriding an inherited value), injects ``ANTHROPIC_API_KEY`` from
  config/secrets or ~/.config/anthropic-key.txt, sets ``DISABLE_AUTOUPDATER=1``;
- ``run`` hands the runner ``cwd = config.headless_cwd()`` unless the caller names
  one, scrubs the prompt, pipes it for ``prompt_via="stdin"``;
- the knob flips through ``state/settings_overrides.json`` (the web's write path)
  reach the recap site's argv and nothing else moves.

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

from act import llm, recap
from act.lib import config, secrets

OPUS = config.DEFAULT_MODEL_PIPELINE
FB = ["--fallback-model", config.DEFAULT_MODEL_FALLBACK]
# §78, spelled literally on purpose (a change here is a law change, not a refactor)
ISOLATION = ["--bare", "--no-session-persistence", "--setting-sources", "",
             "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']


class _Recorder:
    def __init__(self, stdout="ok"):
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
        patcher = mock.patch("act.lib.config.resolve_claude_bin", return_value="claude")
        patcher.start()
        self.addCleanup(patcher.stop)


class IsolationConstantsTestCase(unittest.TestCase):
    def test_the_tuple_is_the_law(self):
        self.assertEqual(llm.ISOLATION_ARGV, tuple(ISOLATION))
        self.assertEqual(llm.ISOLATION_FLAGS, ("--bare", "--no-session-persistence",
                                               "--setting-sources", "--strict-mcp-config",
                                               "--mcp-config"))
        self.assertEqual(json.loads(llm.ISOLATION_ARGV[-1]), {"mcpServers": {}})
        self.assertEqual(llm.CONFIG_DIR_ENV, "CLAUDE_CONFIG_DIR")

    def test_the_owner_settings_readers_are_gone(self):
        for gone in ("dispatch_argv", "NO_MCP_ARGV", "claude_code_settings_path",
                     "read_claude_code_default_model", "MODE_DISPATCH", "MODEL_FOLLOW"):
            self.assertFalse(hasattr(llm, gone), gone)
        self.assertEqual(llm.MODES, ("pipeline",))


class ModelForTestCase(_Sandbox):
    def test_default_is_explicit(self):
        self.assertEqual(llm.model_for("pipeline", config.Config()), OPUS)

    def test_explicit_and_legacy_follow_and_garbage(self):
        self.assertEqual(llm.model_for("pipeline", _cfg(models_pipeline="claude-sonnet-5")),
                         "claude-sonnet-5")
        self.assertEqual(llm.model_for("pipeline", _cfg(models_pipeline="follow")), OPUS)
        self.assertEqual(llm.model_for("pipeline", _cfg(models_pipeline="bad id")), OPUS)

    def test_unknown_mode_raises(self):
        for mode in ("dispatch", "fallback", ""):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                llm.model_for(mode, config.Config())

    def test_cfg_none_reads_fresh_config(self):
        _write_overrides({"models_pipeline": "claude-haiku-4-5-20251001"})
        self.assertEqual(llm.model_for("pipeline"), "claude-haiku-4-5-20251001")


class BuildArgvTestCase(_Sandbox):
    def test_shape(self):
        self.assertEqual(llm.build_argv("P", cfg=config.Config()),
                         ["claude", "-p", "P", "--output-format", "text", "--model", OPUS,
                          *FB, *ISOLATION])

    def test_extra_tail_rides_behind_the_isolation(self):
        argv = llm.build_argv("P", extra_argv=("--tools", ""), cfg=config.Config())
        self.assertEqual(argv[-2:], ["--tools", ""])
        self.assertEqual(argv[-2 - len(ISOLATION):-2], ISOLATION)

    def test_prompt_placements(self):
        cfg = config.Config()
        last = llm.build_argv("P", prompt_via="arg_last", extra_argv=("--x",), cfg=cfg)
        self.assertEqual((last[:2], last[-2:]), (["claude", "-p"], ["--x", "P"]))
        stdin = llm.build_argv("P", prompt_via="stdin", cfg=cfg)
        self.assertNotIn("P", stdin)
        self.assertEqual(stdin[:3], ["claude", "-p", "--output-format"])
        with self.assertRaises(ValueError):
            llm.build_argv("P", prompt_via="file", cfg=cfg)

    def test_no_personal_setup_flag_ever_appears(self):
        argv = llm.build_argv("P", cfg=config.Config())
        for banned in ("--bg", "--resume", "--dangerously-skip-permissions", "--allowedTools",
                       "--add-dir", "--settings"):
            self.assertNotIn(banned, argv)

    def test_probe_argvs_carry_the_isolation(self):
        self.assertEqual(llm.probe_argv("claude-sonnet-5", config.Config()),
                         ["claude", "-p", "ok", "--model", "claude-sonnet-5", "--output-format",
                          "text", "--max-turns", "1", *ISOLATION])
        self.assertEqual(llm.auth_probe_argv(config.Config()),
                         ["claude", "-p", llm.AUTH_PROBE_PROMPT, "--max-turns", "1", *ISOLATION])


class RunnerEnvTestCase(_Sandbox):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        self.secrets = Path(tempfile.mkdtemp(prefix="zai-llm-secrets-"))
        mock.patch.object(secrets, "SECRETS_DIR", self.secrets).start()
        mock.patch.object(secrets, "LEGACY_ANTHROPIC_KEY_PATH",
                          str(self.secrets / "absent-legacy.txt")).start()
        self.addCleanup(mock.patch.stopall)

    def test_claude_config_dir_is_always_the_apps_own(self):
        os.environ["CLAUDE_CONFIG_DIR"] = "/Users/someone/.claude"   # inherited from a CC session
        env = llm.runner_env(config.Config())
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(config.claude_config_dir()))
        self.assertNotIn(".claude", Path(env["CLAUDE_CONFIG_DIR"]).name)
        self.assertEqual(env["DISABLE_AUTOUPDATER"], "1")

    def test_api_key_from_the_secrets_file(self):
        (self.secrets / "anthropic-api-key.txt").write_text("sk-ant-file\n", encoding="utf-8")
        self.assertEqual(llm.runner_env(config.Config())["ANTHROPIC_API_KEY"], "sk-ant-file")

    def test_an_exported_key_wins_and_no_key_is_no_key(self):
        self.assertNotIn("ANTHROPIC_API_KEY", llm.runner_env(config.Config()))
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-env"
        (self.secrets / "anthropic-api-key.txt").write_text("sk-ant-file\n", encoding="utf-8")
        self.assertEqual(llm.runner_env(config.Config())["ANTHROPIC_API_KEY"], "sk-ant-env")


class RunTestCase(_Sandbox):
    def test_kwargs_and_neutral_cwd(self):
        rec = _Recorder()
        with mock.patch("act.llm.runner_env", return_value={"ENV": "x"}):
            llm.run("hello", runner=rec, timeout=7, cfg=config.Config())
        argv, kwargs = rec.calls[0]
        self.assertEqual(argv[:3], ["claude", "-p", "hello"])
        self.assertEqual(kwargs, {"capture_output": True, "text": True, "timeout": 7,
                                  "env": {"ENV": "x"}, "cwd": config.headless_cwd()})
        self.assertTrue(Path(kwargs["cwd"]).is_dir())

    def test_explicit_cwd_and_stdin_piping(self):
        rec = _Recorder()
        llm.run("hello", runner=rec, timeout=1, prompt_via="stdin", cwd="/tmp", cfg=config.Config())
        argv, kwargs = rec.calls[0]
        self.assertEqual((kwargs["cwd"], kwargs["input"]), ("/tmp", "hello"))
        self.assertNotIn("hello", argv)

    def test_the_prompt_is_scrubbed_before_it_leaves(self):
        rec = _Recorder()
        llm.run("key sk-ant-api03-abcdefghijklmnop", runner=rec, timeout=1, cfg=config.Config())
        self.assertNotIn("sk-ant-api03-abcdefghijklmnop", rec.calls[0][0][2])

    def test_default_runner_is_subprocess_run_looked_up_at_call_time(self):
        rec = _Recorder()
        with mock.patch("subprocess.run", rec):
            llm.run("hi", timeout=1, cfg=config.Config())
        self.assertEqual(len(rec.calls), 1)


class RecapSiteTestCase(_Sandbox):
    """The one headless site left: act/recap._call_model through the boundary."""

    def _call(self, cfg=None):
        from act.lib import recap_text
        rec = _Recorder()
        recap._call_model("PROMPT", rec, cfg if cfg is not None else config.load_config(),
                          recap_text.NO_EGRESS_ARGV, 30)
        return rec.calls[0]

    def test_default_argv_and_kwargs(self):
        argv, kwargs = self._call()
        self.assertEqual(argv, ["claude", "-p", "PROMPT", "--output-format", "text",
                                "--model", OPUS, *FB, *ISOLATION,
                                "--tools", "", "--strict-mcp-config", "--mcp-config",
                                '{"mcpServers":{}}'])
        self.assertEqual(kwargs["cwd"], config.headless_cwd())
        self.assertEqual(kwargs["env"]["CLAUDE_CONFIG_DIR"], str(config.claude_config_dir()))

    def test_the_pipeline_knob_flip_moves_only_the_model_id(self):
        before, _ = self._call()
        _write_overrides({"models_pipeline": "claude-sonnet-5"})
        after, _ = self._call()
        self.assertEqual([a for a in after if a != "claude-sonnet-5"],
                         [a for a in before if a != OPUS])
        self.assertIn("claude-sonnet-5", after)


def _cfg(**over):
    cfg = config.Config()
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


if __name__ == "__main__":
    unittest.main()
