"""act/llm.py — the single LLM boundary (CONTRACT §59; 防腐十条 #3).

Every ``claude`` invocation that carries a prompt is built here and only here:

- the ~10 headless ``claude -p`` sites (analyze / radar / radar_slack /
  radar_gmail / quick_capture / merge_review / ask / golden_eval / voice_gen /
  weekly_digest) call :func:`run`;
- the executor's ``claude --bg`` launch sites (dispatch / resume / rework /
  brief) take their base argv from :func:`dispatch_argv`;
- the doctor's model-liveness probe takes its argv from :func:`probe_argv`.

What is centralised (and nothing else): argv construction, the claude binary
resolution (``config.resolve_claude_bin``: execution.claude_bin pin → the
stable daemon copy (§55 第五幕) → PATH → ~/.local/bin), the outbound
``sanitize.scrub`` of the prompt, the subprocess env (:func:`runner_env`:
credentials + ``DISABLE_AUTOUPDATER=1`` + the D53 ``ANTHROPIC_DEFAULT_OPUS_MODEL``
pin), **the one place ``--model`` is appended** — from ``cfg.models_dispatch``
/ ``cfg.models_pipeline`` (D22: two knobs, "手" vs "脑") — and **the one place
``--fallback-model`` is appended** (D53, third knob ``cfg.models_fallback``,
default ``claude-opus-5[1m]``). ``follow`` (the default) appends no
``--model``; an explicit id appends ``--model <id>`` right after
``--output-format <fmt>``. The fallback rides right behind the model flag on
every ``-p`` and ``--bg`` site (``off`` appends nothing and restores the
pre-D53 argv byte for byte; tests/test_llm_boundary.py pins each site's
argv). Ordering rule for the fixed part of argv: ``--output-format`` →
``--model`` → ``--fallback-model`` → (``--bg`` only) :data:`NO_MCP_ARGV` →
the variable tail (``extra_argv`` / ``--name`` / ``--resume`` / prompt) —
the tail may start with a variadic option, so nothing fixed goes after it.

Why the fallback is ours to spell (D53): when the primary model — the
owner's Claude Code global default ``claude-fable-5-1[1m]`` or a knob — is
not available, Claude Code silently switches to *its* fallback, which is the
CLI's built-in Opus alias (Opus 4.8 at the time of writing). The owner's
words: 「fable 5.1 用不了的使用 claude code 默认使用了 opus 4.8 这个老模型。
能否去掉这个 4.8 这个老模型」. ``--fallback-model`` takes a comma-separated
list tried in order and works for interactive, ``-p`` and ``--bg`` alike
(truth = Claude Code CHANGELOG: 2.1.152 — a not-found primary switches to
the configured fallback for the rest of the session; 2.1.166 — the flag is
honoured in interactive sessions too, which is what the ``--bg`` sites lean
on, plus the ``fallbackModel`` setting). The daemon spells the fallback itself
so a fresh install behaves the same as the owner's machine (whose
``~/.claude/settings.json`` already pins ``fallbackModel``) instead of
relying on personal settings.

Per-site behaviour that must stay put stays at the site: timeouts, the
prompt's position in argv (``prompt_via``: ``"arg"`` right after ``-p`` —
the safe default because ``--allowedTools`` is variadic and swallows a
trailing positional; ``"arg_last"`` for the two legacy prompt-last sites;
``"stdin"`` for the three extractors that pipe the prompt), the neutral
``cwd`` (``config.headless_cwd``, tests/test_headless_cwd.py), the
``--allowedTools`` lists, and the fold/fence logic upstream of the prompt.

Injection: every function takes ``runner=`` / ``cfg=`` seams; the default
runner is ``subprocess.run`` looked up at call time so the suite's global
fake (tests/__init__.py guard + ``mock.patch("subprocess.run")``) still
intercepts. **Module-global runner seams are banned here** (the
``silent_merge.JUDGE_RUNNER`` precedent is the reason this file exists).

The model knobs are read from ``cfg`` when given, else from a fresh
``config.load_config()`` — the separate-process sites (radars, ask, merge
review, digest) are therefore live by construction; actd refreshes the three
fields on its startup-frozen cfg every pass (act/actd.py, auto_resume
precedent) so a Settings change applies to the next dispatch without a
restart.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Callable, Optional, Sequence

from act.lib import config, sanitize

MODE_DISPATCH = "dispatch"
MODE_PIPELINE = "pipeline"
MODES = config.MODEL_MODES
FOLLOW = config.MODEL_FOLLOW
CANONICAL_MODELS = config.CANONICAL_MODELS
# D53 third knob: the --fallback-model id; "off" = no flag (CLI's own fallback).
FALLBACK_OFF = config.MODEL_FALLBACK_OFF
DEFAULT_FALLBACK = config.DEFAULT_MODEL_FALLBACK
# The Claude Code env var that decides what its `opus` alias resolves to.
OPUS_ALIAS_ENV = "ANTHROPIC_DEFAULT_OPUS_MODEL"
_OPUS_PREFIX = "claude-opus"

PROMPT_VIA = ("arg", "arg_last", "stdin")

# The one prompt the doctor spends on an explicit knob (§59 model liveness).
PROBE_PROMPT = "ok"


# --------------------------------------------------------------------------- #
# resolution helpers
# --------------------------------------------------------------------------- #
def claude_bin(cfg: Optional[config.Config] = None) -> str:
    """The claude CLI for every subprocess site — pin → stable daemon copy →
    PATH → ~/.local/bin (``config.resolve_claude_bin``)."""
    return config.resolve_claude_bin(cfg)


def runner_env(cfg: Optional[config.Config] = None) -> dict:
    """The env every claude subprocess gets: credentials + no self-update +
    the D53 Opus-alias pin.

    actd runs under a launchd agent; when spawned outside the Aqua login session
    it cannot read the Keychain OAuth token, so fall back to the API key file
    (same pattern the screenpipe ingest cron uses). Resolution (CONTRACT §19):
    config/secrets/anthropic-api-key.txt (App 设置窗口保存) -> legacy
    ~/.config/anthropic-key.txt. If the key is already in the environment or no
    file exists, leave things untouched and let claude use its own auth.

    ``DISABLE_AUTOUPDATER=1`` (§55 第五幕): headless workers run the stable
    daemon copy, whose whole point is that install.sh — not Claude Code's own
    updater — decides when it changes. A worker that self-updated would either
    rewrite that file underneath the Full Disk Access grant's code requirement
    or, more likely, download into ~/.local/share/claude/versions/ for nothing.
    Always set, never merely defaulted: no site of ours wants a self-updating
    background claude.

    ``ANTHROPIC_DEFAULT_OPUS_MODEL=<fallback>`` (§59 D53): set when the fallback
    knob is on **and** its id is an Opus id (``claude-opus…``). ``--fallback-model``
    only covers the switch we spell on argv; inside the session the CLI still
    resolves its own ``opus`` alias (sub-agents, ``opusplan``, a ``/model opus``
    typed into a resumed session, the CLI's own unknown-model fallback) — and
    that alias is the very Opus 4.8 the owner asked to retire. Pinning the
    alias to the same id makes every Opus the CLI reaches for the same Opus.
    A non-Opus fallback (say Sonnet) leaves the variable alone: pinning
    ``opus`` to a Sonnet would be a lie. ``off`` leaves it alone too. ``cfg``
    None = fresh ``load_config()`` (same liveness as the knobs; add-only
    parameter, every pre-D53 caller still works).
    """
    env = dict(os.environ)
    if not env.get("ANTHROPIC_API_KEY"):
        from act.lib import secrets
        key = secrets.resolve_credential(
            secrets.ANTHROPIC_API_KEY_FILE,
            None,
            "~/.config/anthropic-key.txt",
        )
        if key:
            env["ANTHROPIC_API_KEY"] = key
    env["DISABLE_AUTOUPDATER"] = "1"
    fallback = fallback_model(cfg)
    if fallback and fallback.startswith(_OPUS_PREFIX):
        env[OPUS_ALIAS_ENV] = fallback
    return env


def model_for(mode: str, cfg: Optional[config.Config] = None) -> Optional[str]:
    """The explicit model id for ``mode`` or None when the knob is "follow".

    ``cfg`` None = fresh ``load_config()`` (the knob is live for every
    separate-process site). Anything that is not a well-formed id degrades to
    None — argv never carries garbage.
    """
    if mode not in MODES:
        raise ValueError(f"unknown llm mode: {mode!r}")
    if cfg is None:
        cfg = config.load_config()
    raw = getattr(cfg, f"models_{mode}", FOLLOW)
    try:
        value = config.coerce_model(raw)
    except (TypeError, ValueError):
        return None
    return None if value == FOLLOW else value


def fallback_model(cfg: Optional[config.Config] = None) -> Optional[str]:
    """The ``--fallback-model`` id, or None when the knob is ``off`` (D53).

    ``cfg`` None = fresh ``load_config()``. A malformed value degrades to the
    **default** (``claude-opus-5[1m]``), not to None: garbage never reaches
    argv, and a typo must not quietly hand the session back to the CLI's own
    fallback (the Opus 4.8 the owner asked to retire).
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
    """D53: does the fallback count as a real safety net — the product default
    ``claude-opus-5[1m]`` (the D53 decision itself; warning about it is noise)
    or a canonical id. The doctor softens the alias-retirement WARN only when
    this holds; ``server/settings.py::fallback_warning`` hand-copies the same
    rule (``tests/test_server_settings_fallback.py::MirrorTestCase``)."""
    return fallback == DEFAULT_FALLBACK or is_canonical(fallback)


# --------------------------------------------------------------------------- #
# argv builders — the only place `--model` / `--fallback-model` is spelled
# --------------------------------------------------------------------------- #
def _model_flags(mode: str, cfg: Optional[config.Config]) -> list:
    model = model_for(mode, cfg)
    return ["--model", model] if model else []


def _fallback_flags(cfg: Optional[config.Config]) -> list:
    fallback = fallback_model(cfg)
    return ["--fallback-model", fallback] if fallback else []


def build_argv(prompt: Optional[str], *, mode: str = MODE_PIPELINE,
               output_format: str = "text", prompt_via: str = "arg",
               extra_argv: Sequence[str] = (),
               cfg: Optional[config.Config] = None) -> list:
    """Headless ``claude -p`` argv.

    Shape (``prompt_via="arg"``, the default)::

        [<claude>, "-p", <prompt>, "--output-format", <fmt>,
         ("--model", <id>)?, ("--fallback-model", <id>)?, *extra_argv]

    ``"arg_last"`` moves the prompt to the very end (radar / weekly_digest /
    quick_capture legacy order); ``"stdin"`` leaves it out (the caller pipes
    it — :func:`run` does). ``extra_argv`` is appended verbatim after the
    model / fallback flags (``--allowedTools`` lists must trail the prompt,
    see module docstring). The fallback (D53) is part of the fixed head so
    the variadic tail never swallows it.
    """
    if prompt_via not in PROMPT_VIA:
        raise ValueError(f"unknown prompt_via: {prompt_via!r}")
    argv = [claude_bin(cfg), "-p"]
    if prompt_via == "arg":
        argv.append(prompt if prompt is not None else "")
    argv += ["--output-format", output_format]
    argv += _model_flags(mode, cfg)
    argv += _fallback_flags(cfg)
    argv += [str(a) for a in extra_argv]
    if prompt_via == "arg_last":
        argv.append(prompt if prompt is not None else "")
    return argv


# §65 出网封锁（self_improve lane 会话的 MCP 面归零）：``--strict-mcp-config``
# 让 claude 只认 ``--mcp-config`` 给的服务器集合，而这个集合是空的——用户级
# Slack/Gmail MCP 对该会话不存在。三个 token 顺序固定，紧跟模型旗标（D53 起
# 是 ``--model`` 再 ``--fallback-model``）、在 ``--name`` 之前（``--mcp-config``
# 是变参，后面必须是一个选项而不是裸 prompt——所以任何固定旗标都排在它前面）。
NO_MCP_ARGV: tuple = ("--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}')


def dispatch_argv(cfg: Optional[config.Config] = None, *,
                  no_mcp: bool = False) -> list:
    """Base ``claude --bg`` argv shared by the executor's launch sites
    (dispatch / resume / rework / brief). ``--dangerously-skip-permissions``
    is included only while ``execution.skip_permissions`` is on (default;
    P0-10) — off means the agent runs under claude's normal permission
    model. The dispatch model knob rides right behind it, then the D53
    fallback (``--fallback-model <id>``, nothing when ``off``); ``no_mcp``
    (§65, add-only kwarg, default off = byte-identical argv) appends
    :data:`NO_MCP_ARGV` after both; the caller appends ``--name`` /
    ``--resume`` / the prompt.
    """
    cmd = [claude_bin(cfg), "--bg"]
    if cfg is None or getattr(cfg, "skip_permissions", True):
        cmd.append("--dangerously-skip-permissions")
    cmd += _model_flags(MODE_DISPATCH, cfg)
    cmd += _fallback_flags(cfg)
    if no_mcp:
        cmd += list(NO_MCP_ARGV)
    return cmd


def probe_argv(model: str, cfg: Optional[config.Config] = None) -> list:
    """The doctor's minimal live call for an explicit knob (§59):
    ``claude -p ok --model <id> --output-format text --max-turns 1``.
    Deliberately **without** ``--fallback-model`` (D53): the probe asks
    whether *this* id answers; a fallback would mask the very outage the
    FAIL row exists to report."""
    return [claude_bin(cfg), "-p", PROBE_PROMPT, "--model", str(model),
            "--output-format", "text", "--max-turns", "1"]


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
    ``subprocess.run``); it receives exactly the kwargs the legacy sites
    passed: ``capture_output=True, text=True, timeout=..., env=runner_env()``
    plus ``cwd`` when the site pins the neutral cwd and ``input=<prompt>``
    for ``prompt_via="stdin"``. Returns the CompletedProcess untouched —
    every site keeps its own returncode / stdout interpretation.
    """
    scrubbed, _ = sanitize.scrub(prompt)
    argv = build_argv(scrubbed, mode=mode, output_format=output_format,
                      prompt_via=prompt_via, extra_argv=extra_argv, cfg=cfg)
    kwargs: dict = {"capture_output": True, "text": True, "timeout": timeout,
                    "env": runner_env(cfg)}
    if prompt_via == "stdin":
        kwargs["input"] = scrubbed
    if cwd is not None:
        kwargs["cwd"] = cwd
    return (runner or _default_runner)(argv, **kwargs)


# --------------------------------------------------------------------------- #
# Claude Code global default (what "follow" inherits) — read-only on this side
# --------------------------------------------------------------------------- #
def claude_code_settings_path() -> Path:
    """``~/.claude/settings.json`` — Claude Code's user settings; its ``model``
    key is what every follow-mode call inherits. The web's one-click
    「设为 …」writes it via server/settings.py (never from here — the pipeline
    only reads)."""
    return Path.home() / ".claude" / "settings.json"


def read_claude_code_default_model(path: Optional[Path] = None) -> dict:
    """``{"model": str|None, "exists": bool, "parseable": bool}`` — never
    raises. ``model`` is None when the file/key is absent (Claude Code then
    uses its own built-in default) or the file is not a JSON object."""
    p = path or claude_code_settings_path()
    out = {"model": None, "exists": False, "parseable": False}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return out
    out["exists"] = True
    doc = _json_object(text)
    if doc is None:
        return out
    out["parseable"] = True
    out["model"] = _model_key(doc)
    return out


def _json_object(text: str) -> Optional[dict]:
    """JSON text → dict; anything else (bad JSON, non-object) → None."""
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def _model_key(doc: dict) -> Optional[str]:
    """The ``model`` key when it is a non-blank string (stripped), else None."""
    model = doc.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return None
