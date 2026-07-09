"""Repo inventory — lets the LLM pick the right target_repo for a task.

Scans ~/Projects (top level) for git repos and returns {name, path, hint} where
hint = the first README heading/line. Fed into analyze/radar prompts so target
selection is a JUDGMENT, not a hard-coded default.

Routing rules (also embedded in the prompt):
- Task belongs to an existing project -> that repo.
- Paperwork / research / compliance / comms drafts -> the neutral workbench
  (cfg.default_target_repo, e.g. ~/Projects/your-workbench).
- Brand-new product -> propose a NEW path under ~/Projects (executor will
  git-init it and optionally create a private GitHub remote).
- When unsure, prefer the workbench over guessing an unrelated project repo
  (curated repos must stay free of unrelated context).
"""
from __future__ import annotations

from pathlib import Path

PROJECTS_ROOT = Path("~/Projects").expanduser()
_SKIP = {"data", "zelin-ai-assistant"}  # data corpus + the assistant itself


def _readme_hint(repo: Path) -> str:
    for name in ("README.md", "README.rst", "README.txt", "README"):
        p = repo / name
        if p.exists():
            try:
                for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip().lstrip("#").strip()
                    if line:
                        return line[:90]
            except OSError:
                pass
    return ""


def inventory(root: Path = PROJECTS_ROOT, limit: int = 40) -> list[dict]:
    """Top-level git repos under ``root`` with a one-line hint. Never raises."""
    out: list[dict] = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return out
    for p in entries:
        if len(out) >= limit:
            break
        if not p.is_dir() or p.name.startswith(".") or p.name in _SKIP:
            continue
        if not (p / ".git").exists():
            continue
        out.append({"name": p.name, "path": str(p), "hint": _readme_hint(p)})
    return out


def inventory_text(root: Path = PROJECTS_ROOT) -> str:
    lines = []
    for r in inventory(root):
        hint = f" — {r['hint']}" if r["hint"] else ""
        lines.append(f"- {r['path']}{hint}")
    return "\n".join(lines) if lines else "(no repos found)"
