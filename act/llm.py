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
credentials + ``DISABLE_AUTOUPDATER=1``), and **the one place ``--model`` is
appended** —
from ``cfg.models_dispatch`` / ``cfg.models_pipeline`` (D22: two knobs, "手"
vs "脑"). ``follow`` (the default) appends nothing, so every site's argv is
byte-identical to the pre-§59 shape (tests/test_llm_boundary.py pins each
site's argv); an explicit id appends ``--model <id>`` right after
``--output-format <fmt>``.

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

The model knob is read from ``cfg`` when given, else from a fresh
``config.load_config()`` — the separate-process sites (radars, ask, merge
review, digest) are therefore live by construction; actd refreshes the two
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


def runner_env() -> dict:
    """The env every claude subprocess gets: credentials + no self-update.

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


def is_canonical(model: Optional[str]) -> bool:
    return config.model_is_canonical(model)


# --------------------------------------------------------------------------- #
# argv builders — the only place `--model` is spelled
# --------------------------------------------------------------------------- #
def _model_flags(mode: str, cfg: Optional[config.Config]) -> list:
    model = model_for(mode, cfg)
    return ["--model", model] if model else []


def build_argv(prompt: Optional[str], *, mode: str = MODE_PIPELINE,
               output_format: str = "text", prompt_via: str = "arg",
               extra_argv: Sequence[str] = (),
               cfg: Optional[config.Config] = None) -> list:
    """Headless ``claude -p`` argv.

    Shape (``prompt_via="arg"``, the default)::

        [<claude>, "-p", <prompt>, "--output-format", <fmt>,
         ("--model", <id>)?, *extra_argv]

    ``"arg_last"`` moves the prompt to the very end (radar / weekly_digest /
    quick_capture legacy order); ``"stdin"`` leaves it out (the caller pipes
    it — :func:`run` does). ``extra_argv`` is appended verbatim after the
    model flag (``--allowedTools`` lists must trail the prompt, see module
    docstring).
    """
    if prompt_via not in PROMPT_VIA:
        raise ValueError(f"unknown prompt_via: {prompt_via!r}")
    argv = [claude_bin(cfg), "-p"]
    if prompt_via == "arg":
        argv.append(prompt if prompt is not None else "")
    argv += ["--output-format", output_format]
    argv += _model_flags(mode, cfg)
    argv += [str(a) for a in extra_argv]
    if prompt_via == "arg_last":
        argv.append(prompt if prompt is not None else "")
    return argv


def dispatch_argv(cfg: Optional[config.Config] = None) -> list:
    """Base ``claude --bg`` argv shared by the executor's launch sites
    (dispatch / resume / rework / brief). ``--dangerously-skip-permissions``
    is included only while ``execution.skip_permissions`` is on (default;
    P0-10) — off means the agent runs under claude's normal permission
    model. The dispatch model knob rides right behind it; the caller appends
    ``--name`` / ``--resume`` / the prompt.
    """
    cmd = [claude_bin(cfg), "--bg"]
    if cfg is None or getattr(cfg, "skip_permissions", True):
        cmd.append("--dangerously-skip-permissions")
    cmd += _model_flags(MODE_DISPATCH, cfg)
    return cmd


def probe_argv(model: str, cfg: Optional[config.Config] = None) -> list:
    """The doctor's minimal live call for an explicit knob (§59):
    ``claude -p ok --model <id> --output-format text --max-turns 1``."""
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
                    "env": runner_env()}
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
    try:
        doc = json.loads(text)
    except ValueError:
        return out
    if not isinstance(doc, dict):
        return out
    out["parseable"] = True
    model = doc.get("model")
    if isinstance(model, str) and model.strip():
        out["model"] = model.strip()
    return out
