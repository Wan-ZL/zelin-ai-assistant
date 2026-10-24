"""act/llm.py — the single LLM boundary (CONTRACT §59 / §78; 防腐十条 #3).

Every ``claude`` invocation this package makes with a prompt is built here and
only here:

- the headless ``claude -p`` site (``act/recap.py``'s sealed recap call) goes
  through :func:`run`;
- the doctor's live probes (claude auth, model liveness) take their argv from
  :func:`auth_probe_argv` / :func:`probe_argv` and their env from
  :func:`runner_env`.

(The ingest chain's ``claude -p`` lives in ``ingest/process-screenpipe.sh`` and
sources the same constants from ``ingest/claude-env.sh``; §78 pins the two
copies equal.)

What is centralised (and nothing else): argv construction, the claude binary
resolution (``config.resolve_claude_bin``: execution.claude_bin pin → the stable
copy (§55 第五幕) → PATH → ~/.local/bin), the outbound ``sanitize.scrub`` of the
prompt, the subprocess env (:func:`runner_env`), **the one place ``--model`` is
appended** (``cfg.models_pipeline``) and **the one place ``--fallback-model`` is
appended** (D53, ``cfg.models_fallback``).

§78 isolation (v2.0.0, constitution art.13): every argv carries
:data:`ISOLATION_ARGV` and every env carries ``CLAUDE_CONFIG_DIR`` pointed at
``config.claude_config_dir()`` — the subprocess never reads or writes the
owner's own Claude Code home (settings, hooks, plugins, skills, MCP servers, session
transcripts), never discovers a CLAUDE.md, and never touches the keychain
(``--bare``: credentials come from ``ANTHROPIC_API_KEY`` only). The cwd is
``config.headless_cwd()``, an empty directory outside the repo tree. The
ingredients:

- ``--bare`` — no hooks, plugin sync, keychain/OAuth, CLAUDE.md auto-discovery;
- ``--no-session-persistence`` — no transcript under ``projects/``;
- ``--setting-sources ""`` — no user / project / local settings files at all
  (a project settings file on the ancestor chain would otherwise still apply);
- ``--strict-mcp-config --mcp-config '{"mcpServers":{}}'`` — the MCP server set is
  exactly the empty set given here.

Retired with the Act pipeline (v2.0.0; full text in tag ``full``): the
executor's ``claude --bg`` builder ``dispatch_argv`` and its ``NO_MCP_ARGV``
(subsumed by ISOLATION_ARGV), the ``models.dispatch`` knob, the "follow the
Claude Code global default" mode and its reader of the owner's Claude Code settings.

Argv order: ``-p <prompt>`` → ``--output-format`` → ``--model`` →
``--fallback-model`` → ISOLATION_ARGV → the variable tail (``extra_argv``, e.g.
the recap's ``NO_EGRESS_ARGV``). ``--mcp-config`` / ``--setting-sources`` take a
value, so they sit in the fixed head and nothing variadic precedes them.

Injection: every function takes ``runner=`` / ``cfg=`` seams; the default
runner is ``subprocess.run`` looked up at call time so the suite's global fake
still intercepts. **Module-global runner seams are banned here.**
"""
from __future__ import annotations

import os
import subprocess
from typing import Callable, Optional, Sequence

from act.lib import config, sanitize

MODE_PIPELINE = "pipeline"
MODES = config.MODEL_MODES
CANONICAL_MODELS = config.CANONICAL_MODELS
DEFAULT_MODEL = config.DEFAULT_MODEL_PIPELINE
# D53: the --fallback-model id; "off" = no flag (CLI's own fallback).
FALLBACK_OFF = config.MODEL_FALLBACK_OFF
DEFAULT_FALLBACK = config.DEFAULT_MODEL_FALLBACK
# The Claude Code env var that decides what its `opus` alias resolves to.
OPUS_ALIAS_ENV = "ANTHROPIC_DEFAULT_OPUS_MODEL"
_OPUS_PREFIX = "claude-opus"
# §78 the env var that relocates Claude Code's whole config home.
CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"

# §78 the isolation flags every argv carries (tests/test_llm_boundary.py and
# tests/test_recap_no_egress.py pin the tuple; ingest/claude-env.sh spells the
# same literals for the shell side).
ISOLATION_ARGV: tuple = (
    "--bare",
    "--no-session-persistence",
    "--setting-sources", "",
    "--strict-mcp-config",
    "--mcp-config", '{"mcpServers":{}}',
)
# The flags `claude --help` must list for the isolation to hold (doctor row
# `claude bare supported`).
ISOLATION_FLAGS: tuple = tuple(a for a in ISOLATION_ARGV if a.startswith("--"))

PROMPT_VIA = ("arg", "arg_last", "stdin")

# The prompts the doctor spends on its live probes.
PROBE_PROMPT = "ok"
AUTH_PROBE_PROMPT = "Reply with exactly: ok"


# --------------------------------------------------------------------------- #
# resolution helpers
# --------------------------------------------------------------------------- #
def claude_bin(cfg: Optional[config.Config] = None) -> str:
    """The claude CLI for every subprocess site (``config.resolve_claude_bin``)."""
    return config.resolve_claude_bin(cfg)


def runner_env(cfg: Optional[config.Config] = None) -> dict:
    """The env every claude subprocess gets: §78 isolation + credentials + no
    self-update + the D53 Opus-alias pin.

    ``CLAUDE_CONFIG_DIR`` is **always set**, overriding whatever the parent had
    (a server or cron started from inside the owner's own Claude Code session
    inherits that session's value).

    Credentials (CONTRACT §19): ``ANTHROPIC_API_KEY`` when already in the
    environment, else config/secrets/anthropic-api-key.txt, else
    ~/.config/anthropic-key.txt. ``--bare`` never reads OAuth or the keychain,
    so with no key the call fails loudly (doctor's ``anthropic key`` row).

    ``DISABLE_AUTOUPDATER=1`` (§55 第五幕): install.sh — not Claude Code's own
    updater — decides when the stable copy changes.

    ``ANTHROPIC_DEFAULT_OPUS_MODEL=<fallback>`` (§59 D53): set when the fallback
    knob is on **and** its id is an Opus id, so every Opus the CLI reaches for
    inside the session is the same Opus. ``cfg`` None = fresh ``load_config()``.
    """
    env = dict(os.environ)
    env[CONFIG_DIR_ENV] = str(config.claude_config_dir())
    if not env.get("ANTHROPIC_API_KEY"):
        from act.lib import secrets
        key = secrets.resolve_anthropic_key()
        if key:
            env["ANTHROPIC_API_KEY"] = key
    env["DISABLE_AUTOUPDATER"] = "1"
    fallback = fallback_model(cfg)
    if fallback and fallback.startswith(_OPUS_PREFIX):
        env[OPUS_ALIAS_ENV] = fallback
    return env


def model_for(mode: str = MODE_PIPELINE, cfg: Optional[config.Config] = None) -> str:
    """The ``--model`` id for ``mode`` (always explicit since v2.0.0).

    ``cfg`` None = fresh ``load_config()``. Anything that is not a well-formed
    id degrades to the product default — argv never carries garbage.
    """
    if mode not in MODES:
        raise ValueError(f"unknown llm mode: {mode!r}")
    if cfg is None:
        cfg = config.load_config()
    raw = getattr(cfg, f"models_{mode}", DEFAULT_MODEL)
    try:
        return config.coerce_model(raw)
    except (TypeError, ValueError):
        return DEFAULT_MODEL


def fallback_model(cfg: Optional[config.Config] = None) -> Optional[str]:
    """The ``--fallback-model`` id, or None when the knob is ``off`` (D53).

    A malformed value degrades to the **default**, not to None: garbage never
    reaches argv, and a typo must not quietly hand the session back to the
    CLI's own fallback.
    """
    if cfg is None:
        cfg = config.load_config()
    raw = getattr(cfg, "models_fallback", DEFAULT_FALLBACK)
    try:
        value = config.coerce_fallback_model(raw)
    except (TypeError, ValueError):
        value = DEFAULT_FALLBACK
    return None if value == FALLBACK_OFF else value


def is_canonical(model: Optional[str]) -> bool:
    return config.model_is_canonical(model)


def fallback_is_canonical(fallback: Optional[str]) -> bool:
    """D53: the product default ``claude-opus-5[1m]`` or a canonical id —
    ``server/settings.py::fallback_warning`` hand-copies the same rule."""
    return fallback == DEFAULT_FALLBACK or is_canonical(fallback)


# --------------------------------------------------------------------------- #
# argv builders — the only place `--model` / `--fallback-model` is spelled
# --------------------------------------------------------------------------- #
def _fallback_flags(model: str, cfg: Optional[config.Config]) -> list:
    fallback = fallback_model(cfg)
    if not fallback or fallback == model:
        return []   # the CLI refuses a fallback equal to the primary
    return ["--fallback-model", fallback]


def build_argv(prompt: Optional[str], *, mode: str = MODE_PIPELINE,
               output_format: str = "text", prompt_via: str = "arg",
               extra_argv: Sequence[str] = (),
               cfg: Optional[config.Config] = None) -> list:
    """Headless ``claude -p`` argv.

    Shape (``prompt_via="arg"``, the default)::

        [<claude>, "-p", <prompt>, "--output-format", <fmt>,
         "--model", <id>, ("--fallback-model", <id>)?, *ISOLATION_ARGV, *extra_argv]

    ``"arg_last"`` moves the prompt to the very end; ``"stdin"`` leaves it out
    (the caller pipes it — :func:`run` does).
    """
    if prompt_via not in PROMPT_VIA:
        raise ValueError(f"unknown prompt_via: {prompt_via!r}")
    argv = [claude_bin(cfg), "-p"]
    if prompt_via == "arg":
        argv.append(prompt if prompt is not None else "")
    model = model_for(mode, cfg)
    argv += ["--output-format", output_format, "--model", model]
    argv += _fallback_flags(model, cfg)
    argv += list(ISOLATION_ARGV)
    argv += [str(a) for a in extra_argv]
    if prompt_via == "arg_last":
        argv.append(prompt if prompt is not None else "")
    return argv


def probe_argv(model: str, cfg: Optional[config.Config] = None) -> list:
    """The doctor's minimal live call for the pipeline model (§59):
    ``claude -p ok --model <id> --output-format text --max-turns 1`` + the §78
    isolation flags. Deliberately **without** ``--fallback-model`` (D53): the
    probe asks whether *this* id answers."""
    return ([claude_bin(cfg), "-p", PROBE_PROMPT, "--model", str(model),
             "--output-format", "text", "--max-turns", "1"] + list(ISOLATION_ARGV))


def auth_probe_argv(cfg: Optional[config.Config] = None) -> list:
    """The doctor's one cheap credential check, with exactly the isolation every
    real run has (§78) — so a green row means the recap call will authenticate."""
    return ([claude_bin(cfg), "-p", AUTH_PROBE_PROMPT, "--max-turns", "1"]
            + list(ISOLATION_ARGV))


# --------------------------------------------------------------------------- #
# run — the boundary every headless site crosses
# --------------------------------------------------------------------------- #
def _default_runner(argv: list, **kwargs) -> subprocess.CompletedProcess:
    # looked up at call time on purpose: the suite patches subprocess.run
    return subprocess.run(argv, **kwargs)


def run(prompt: str, *, mode: str = MODE_PIPELINE,
        runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
        timeout: Optional[float], output_format: str = "text",
        prompt_via: str = "arg", extra_argv: Sequence[str] = (),
        cwd: Optional[str] = None,
        cfg: Optional[config.Config] = None) -> subprocess.CompletedProcess:
    """Scrub the prompt, build the argv, run claude headless.

    ``runner(argv, **kwargs)`` is the injection seam (default
    ``subprocess.run``); it receives ``capture_output=True, text=True,
    timeout=..., env=runner_env(), cwd=<cwd or config.headless_cwd()>`` plus
    ``input=<prompt>`` for ``prompt_via="stdin"``. The cwd is never left to
    inheritance (§78): a caller that passes none gets the neutral directory.
    Returns the CompletedProcess untouched.
    """
    scrubbed, _ = sanitize.scrub(prompt)
    argv = build_argv(scrubbed, mode=mode, output_format=output_format,
                      prompt_via=prompt_via, extra_argv=extra_argv, cfg=cfg)
    kwargs: dict = {"capture_output": True, "text": True, "timeout": timeout,
                    "env": runner_env(cfg),
                    "cwd": cwd if cwd is not None else config.headless_cwd()}
    if prompt_via == "stdin":
        kwargs["input"] = scrubbed
    return (runner or _default_runner)(argv, **kwargs)
