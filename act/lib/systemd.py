"""Render the systemd user-unit templates (Linux service wiring).

The templates in ``act/systemd/*.service`` / ``*.timer`` are the Linux mirror
of ``act/launchd/*.plist``: they carry a small set of ``@TOKEN@`` placeholders
that a real install must fill with this machine's paths, exactly the way
``install.sh`` renders the ``/Users/YOURUSERNAME`` placeholders in the plists.

Substitution is a pure string operation with a single source of truth (this
module), so ``install-linux.sh`` and the test suite render identically — no
drift between "what install writes" and "what CI validated". ``install-linux.sh``
calls ``python3 -m act.lib.systemd`` to emit the rendered units into
``~/.config/systemd/user`` on a real Linux box; the render itself needs nothing
but stdlib and is fully unit-tested here on macOS/CI.

Placeholders (all three are absolute, filled from the same values install.sh
already computes for the plists):

  @PYTHON@          the daemon interpreter (config/runtime.json "python")
  @REPO_ROOT@       the checkout root  (AIASSISTANT_HOME / WorkingDirectory)
  @CLAUDE_BIN_DIR@  dir of the login-shell ``claude`` — kept FIRST on the unit
                    PATH, the same 2026-07-08 "outdated claude shadowed the new
                    one" guard the plists carry.

``%h`` (systemd's own $HOME specifier) is used directly inside the templates
for the rest of the PATH, so home never needs a placeholder.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List

# Repo location of the templates (act/systemd/), resolved from this file so it
# works regardless of cwd.
UNIT_DIR = Path(__file__).resolve().parent.parent / "systemd"

# The placeholder tokens, in one place so template + renderer + tests agree.
TOKEN_PYTHON = "@PYTHON@"  # nosec B105 - template placeholder, not a secret
TOKEN_REPO_ROOT = "@REPO_ROOT@"  # nosec B105 - template placeholder, not a secret
TOKEN_CLAUDE_BIN_DIR = "@CLAUDE_BIN_DIR@"  # nosec B105 - template placeholder, not a secret

_TOKENS = (TOKEN_PYTHON, TOKEN_REPO_ROOT, TOKEN_CLAUDE_BIN_DIR)


def render(template_text: str, python: str, repo_root: str,
           claude_bin_dir: str) -> str:
    """Substitute the @TOKEN@ placeholders. Pure string op, no I/O.

    Every token is replaced; the result must contain no leftover ``@...@``
    placeholder from our set (the tests assert this).
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


def unit_templates(unit_dir: Path = UNIT_DIR) -> List[Path]:
    """The template files to render (both .service and .timer), sorted."""
    return sorted(
        p for p in unit_dir.iterdir()
        if p.suffix in (".service", ".timer")
    )


def render_all(python: str, repo_root: str, claude_bin_dir: str,
               unit_dir: Path = UNIT_DIR) -> Dict[str, str]:
    """Map unit filename -> rendered text for every template on disk."""
    return {
        p.name: render(p.read_text(encoding="utf-8"),
                       python, repo_root, claude_bin_dir)
        for p in unit_templates(unit_dir)
    }


def main(argv: List[str] = None) -> int:
    """CLI used by install-linux.sh: render units into an output directory."""
    ap = argparse.ArgumentParser(
        prog="python -m act.lib.systemd",
        description="Render systemd user units from act/systemd templates.")
    ap.add_argument("--python", required=True, help="daemon interpreter path")
    ap.add_argument("--repo-root", required=True, help="checkout root")
    ap.add_argument("--claude-bin-dir", required=True,
                    help="dir of the login-shell claude (first on unit PATH)")
    ap.add_argument("--out", required=True,
                    help="output dir (e.g. ~/.config/systemd/user)")
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
