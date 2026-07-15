"""Render the Windows Task Scheduler XML templates (Windows service wiring).

The templates in ``act/tasksched/*.xml`` are the Windows mirror of
``act/systemd/*.service|*.timer`` (themselves the mirror of
``act/launchd/*.plist``): each carries the same small set of ``@TOKEN@``
placeholders that a real install must fill with this machine's paths.

Substitution is a pure string operation with a single source of truth (this
module), so ``install.ps1`` and the test suite render identically — no drift
between "what install registers" and "what CI validated". ``install.ps1`` calls
``python -m act.lib.taskscheduler`` to emit the rendered ``.xml`` into a staging
dir and then ``Register-ScheduledTask``s each into the ``\\ZelinAIAssistant\\``
folder on a real Windows box; the render itself needs nothing but stdlib and is
fully unit-tested here on macOS/CI.

Placeholders (all three are absolute, filled from the same values install.ps1
computes, exactly the way install.sh/install-linux.sh fill the plists/units):

  @PYTHON@          the daemon interpreter (config/runtime.json "python")
  @REPO_ROOT@       the checkout root  (WorkingDirectory / AIASSISTANT_HOME)
  @CLAUDE_BIN_DIR@  dir of the login-shell ``claude`` — prepended FIRST on the
                    task PATH, the same "outdated claude shadowed the new one"
                    guard the plists/units carry. Task Scheduler has no per-task
                    env, so each task runs ``powershell -Command`` that sets
                    AIASSISTANT_HOME + PATH then invokes the interpreter (this is
                    the #3 port risk — mirror of the systemd PATH guard; it needs
                    a real Windows box to validate, see docs/WINDOWS.md).

Task naming: rendered files are ``zelin-<leaf>.xml``; install.ps1 registers each
as ``\\ZelinAIAssistant\\<leaf>`` (leaf = ``actd`` / ``webui`` / ``gmail-radar``
/ ...). act.doctor's schtasks branch filters ``schtasks /query`` output to that
prefix and derives the expected set from the same template dir.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List

# Repo location of the templates (act/tasksched/), resolved from this file so it
# works regardless of cwd.
TASK_DIR = Path(__file__).resolve().parent.parent / "tasksched"

# The placeholder tokens, in one place so template + renderer + tests agree
# (identical set to act/lib/systemd.py — the port keeps one substitution model).
TOKEN_PYTHON = "@PYTHON@"  # nosec B105 - template placeholder, not a secret
TOKEN_REPO_ROOT = "@REPO_ROOT@"  # nosec B105 - template placeholder, not a secret
TOKEN_CLAUDE_BIN_DIR = "@CLAUDE_BIN_DIR@"  # nosec B105 - template placeholder, not a secret

_TOKENS = (TOKEN_PYTHON, TOKEN_REPO_ROOT, TOKEN_CLAUDE_BIN_DIR)

# Task Scheduler folder every task is registered under (install.ps1 -TaskPath,
# doctor's schtasks filter). schtasks reports full names as "\ZelinAIAssistant\<leaf>".
TASK_FOLDER = "ZelinAIAssistant"
TASK_PATH_PREFIX = "\\" + TASK_FOLDER + "\\"

# The resident tasks (AtLogOn + restart-on-failure) — the Windows mirror of the
# systemd SYSTEMD_RESIDENT set; the rest are repetition-driven radars/digest.
RESIDENT_LEAVES = ("actd", "webui")


def task_leaf(filename: str) -> str:
    """``zelin-gmail-radar.xml`` -> ``gmail-radar`` (the registered leaf name)."""
    stem = Path(filename).stem
    return stem[len("zelin-"):] if stem.startswith("zelin-") else stem


def full_task_name(filename: str) -> str:
    """``zelin-actd.xml`` -> ``\\ZelinAIAssistant\\actd`` (schtasks TaskName)."""
    return TASK_PATH_PREFIX + task_leaf(filename)


def render(template_text: str, python: str, repo_root: str,
           claude_bin_dir: str) -> str:
    """Substitute the @TOKEN@ placeholders. Pure string op, no I/O.

    Every token is replaced; the result must contain no leftover ``@...@``
    placeholder from our set (the tests assert this). The values are dropped in
    verbatim: install.ps1 renders paths that already suit a PowerShell
    single-quoted string, and none of &<>'" appear in a normal Windows path, so
    no extra XML/PS escaping is layered on here (kept identical to systemd.py).
    """
    subs: Dict[str, str] = {
        TOKEN_PYTHON: python,
        TOKEN_REPO_ROOT: repo_root,
        TOKEN_CLAUDE_BIN_DIR: claude_bin_dir,
    }
    out = template_text
    for token, value in subs.items():
        out = out.replace(token, value)
    return out


def task_templates(task_dir: Path = TASK_DIR) -> List[Path]:
    """The template files to render (*.xml), sorted."""
    return sorted(p for p in task_dir.iterdir() if p.suffix == ".xml")


def render_all(python: str, repo_root: str, claude_bin_dir: str,
               task_dir: Path = TASK_DIR) -> Dict[str, str]:
    """Map task filename -> rendered XML for every template on disk."""
    return {
        p.name: render(p.read_text(encoding="utf-8"),
                       python, repo_root, claude_bin_dir)
        for p in task_templates(task_dir)
    }


def main(argv: List[str] = None) -> int:
    """CLI used by install.ps1: render the task XML into an output directory."""
    ap = argparse.ArgumentParser(
        prog="python -m act.lib.taskscheduler",
        description="Render Windows Task Scheduler XML from act/tasksched templates.")
    ap.add_argument("--python", required=True, help="daemon interpreter path")
    ap.add_argument("--repo-root", required=True, help="checkout root")
    ap.add_argument("--claude-bin-dir", required=True,
                    help="dir of the login-shell claude (first on task PATH)")
    ap.add_argument("--out", required=True,
                    help="output dir for the rendered .xml (a staging dir)")
    args = ap.parse_args(argv)

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered = render_all(args.python, args.repo_root, args.claude_bin_dir)
    for name, text in rendered.items():
        (out_dir / name).write_text(text, encoding="utf-8")
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
