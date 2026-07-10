"""Fix with AI — generate a Terminal repair session from the diagnostic bundle.

``python3 -m act.ai_fix [--open]`` builds a ``.command`` file in ``$TMPDIR``
that launches ``claude`` preloaded with a diagnostic bundle (doctor findings +
relevant log tails, scrubbed of secrets by act/lib/sanitize before anything is
embedded), and prints the file path. ``--open`` also opens it in Terminal.app.
The Mac app's "让 AI 修 / Fix with AI" button is a thin wrapper over this CLI.

Safety posture (also stated in the generated file's header):
  - the bundle is scrubbed locally (sanitize.scrub masks API keys / tokens /
    private keys and the user's opt-in redaction terms) BEFORE it is written;
  - the generated script only ever runs ``claude`` interactively in the repo —
    WITHOUT ``--dangerously-skip-permissions``, so claude asks for consent
    before every file edit or command it wants to run;
  - the prompt asks claude to diagnose, fix locally with the user's approval,
    verify with ``python3 -m act.doctor --fast``, and finally offer a prefilled
    GitHub new-issue URL (sanitized title + body) to report the bug upstream;
  - config.yaml ``doctor.ai_fix_enabled: false`` disables all of this (exit 2).

Never raises out of main(); every failure prints a bilingual, actionable line.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import stat
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from act import doctor
from act.lib import config, failures, platform, sanitize

ISSUES_URL = "https://github.com/Wan-ZL/zelin-ai-assistant/issues/new"

# log tails embedded in the bundle — (label, path builder). Kept short: the
# point is a starting scent for claude, not a full archive.
TAIL_LINES = 40


def _log_candidates() -> List[tuple]:
    home = config.HOME
    return [
        ("state/actd.log", home / "state" / "actd.log"),
        ("state/actd.launchd.log", home / "state" / "actd.launchd.log"),
        ("state/radar.cron.log", home / "state" / "radar.cron.log"),
        ("engine.log", Path.home() / ".screenpipe" / "engine.log"),
    ]


def _tail(path: Path, lines: int = TAIL_LINES) -> Optional[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    tail = text.splitlines()[-lines:]
    return "\n".join(tail).strip() or None


def build_bundle(results=None, extra_context: Optional[str] = None) -> str:
    """Assemble the SCRUBBED diagnostic bundle (doctor report + log tails)."""
    if results is None:
        results = doctor.run_checks(fast=True)
    parts = ["## doctor report (python3 -m act.doctor --fast)",
             doctor.render(results)]
    for label, path in _log_candidates():
        tail = _tail(path)
        if tail:
            parts.append("## tail of %s (last %d lines)" % (label, TAIL_LINES))
            parts.append(tail)
    if extra_context and extra_context.strip():
        parts.append("## context from the app (what the user was looking at)")
        parts.append(extra_context.strip())
    bundle = "\n\n".join(parts)
    # scrub AFTER assembly so every path (including extra context) is covered
    return sanitize.scrub_text(bundle)


def build_prompt(bundle: str, lang: Optional[str] = None) -> str:
    lang = lang or failures.ui_lang()
    reply_lang = "Chinese" if lang == "zh" else "English"
    return f"""You are the repair assistant for "Zelin's AI Assistant"
(https://github.com/Wan-ZL/zelin-ai-assistant), a local personal-AI pipeline
installed in this directory. Something is broken; below is a diagnostic bundle
(doctor report + log tails), already scrubbed of secrets.

Your job, in order:
1. Read the bundle and diagnose the most likely root cause. You may run
   read-only commands (launchctl list, crontab -l, tail of logs,
   python3 -m act.doctor --fast) to confirm.
2. Explain the problem in one plain-language sentence before fixing anything.
3. Fix it locally with the smallest change possible. Ask before every
   modification (you are running WITHOUT permission bypass on purpose).
   Never delete user data (state/, act/registry/, the Obsidian vault).
4. Verify: rerun `python3 -m act.doctor --fast` and show the delta.
5. Finally, ALWAYS offer to report the issue upstream: print a prefilled
   GitHub new-issue URL of the form
   {ISSUES_URL}?title=<url-encoded short title>&body=<url-encoded body>
   with a body that contains ONLY: the failing doctor lines, your diagnosis,
   and what fixed it (or didn't). Re-check the body carries no secrets, no
   personal file contents, no message/email excerpts before printing it.

Talk to the user in {reply_lang}. Keep it short — they are tired.

{sanitize.fence_untrusted(bundle)}
"""


def build_command_file(extra_context: Optional[str] = None,
                       cfg: Optional[config.Config] = None,
                       results=None, out_dir: Optional[Path] = None) -> Path:
    """Write the .command file and return its path. Raises OSError on IO only."""
    cfg = cfg or config.load_config()
    bundle = build_bundle(results=results, extra_context=extra_context)
    prompt = build_prompt(bundle)
    # a heredoc delimiter colliding with a log line would truncate the prompt
    prompt = prompt.replace("\nZAIFIX_PROMPT_END\n", "\n ZAIFIX_PROMPT_END\n")
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = out_dir or Path(tempfile.gettempdir())
    path = out_dir / ("zelin-ai-fix-%s.command" % stamp)
    script = f"""#!/bin/bash
# Zelin's AI Assistant — AI repair session ({stamp})
#
# WHAT THIS DOES: opens an interactive `claude` session in your assistant's
# folder, preloaded with a diagnostic bundle (doctor report + log tails).
# SAFETY:
#   - the bundle below was scrubbed of API keys/tokens before being written;
#   - claude runs WITHOUT --dangerously-skip-permissions: it must ask you
#     before every file change or command;
#   - nothing is uploaded anywhere except your normal claude session;
#   - at the end claude offers a GitHub issue link — sending it is up to you.
# Close this window at any time to stop.
set -u
cd "{config.HOME}" || {{ echo "repo not found: {config.HOME}"; exit 1; }}
export AIASSISTANT_HOME="{config.HOME}"
if ! command -v claude >/dev/null 2>&1; then
  echo "claude CLI not found - install it first: https://claude.com/claude-code"
  exit 1
fi
PROMPT="$(cat <<'ZAIFIX_PROMPT_END'
{prompt}
ZAIFIX_PROMPT_END
)"
exec claude "$PROMPT"
"""
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m act.ai_fix",
        description="Generate (and optionally open) an AI repair .command file.")
    parser.add_argument("--open", action="store_true", dest="open_it",
                        help="open the generated file in Terminal.app")
    parser.add_argument("--context-file", default=None,
                        help="optional file with extra context from the app")
    args = parser.parse_args(argv)
    try:
        cfg = config.load_config()
        if not getattr(cfg, "doctor_ai_fix_enabled", True):
            print(failures.pick(
                "「让 AI 修」已在 config.yaml 里关闭（doctor.ai_fix_enabled: false）。"
                "打开它或手动运行 python3 -m act.doctor 查看诊断。",
                "\"Fix with AI\" is disabled in config.yaml (doctor.ai_fix_enabled:"
                " false). Re-enable it, or run python3 -m act.doctor manually."))
            return 2
        extra = None
        if args.context_file:
            try:
                extra = Path(args.context_file).read_text(encoding="utf-8")
            except OSError:
                extra = None
        path = build_command_file(extra_context=extra, cfg=cfg)
        print(str(path))
        if args.open_it:
            platform.open_path(path)
        return 0
    except Exception as exc:  # noqa: BLE001 - the escape hatch must not crash
        print(failures.pick(
            "生成修复会话失败：%s——直接在终端运行 claude 并描述问题也可以。" % exc,
            "Could not generate the repair session: %s — you can also just run"
            " claude in a terminal and describe the problem." % exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
