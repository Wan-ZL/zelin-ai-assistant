"""Repo inventory — lets the LLM pick the right target_repo for a task.

契约：CONTRACT §4（派发目标 = 显式 `target_repo` 否则默认工作 repo）+ §7
（卡片 `target_repo` / `target_kind` 字段——ROUTING RULES 的候选清单由本模块产出）。

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


_README_NAMES = ("README.md", "README.rst", "README.txt", "README")


def _first_line(p: Path) -> str:
    """First non-empty line of ``p`` (markdown ``#`` stripped, clipped to 90);
    "" when the file is absent, unreadable or blank."""
    if not p.exists():
        return ""
    try:
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                return line[:90]
    except OSError:
        pass
    return ""


def _readme_hint(repo: Path) -> str:
    for name in _README_NAMES:
        hint = _first_line(repo / name)
        if hint:
            return hint
    return ""


def _is_candidate_repo(p: Path) -> bool:
    """Visible top-level directory with a .git, outside the skip list."""
    return (p.is_dir() and not p.name.startswith(".") and p.name not in _SKIP
            and (p / ".git").exists())


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
        if not _is_candidate_repo(p):
            continue
        out.append({"name": p.name, "path": str(p), "hint": _readme_hint(p)})
    return out


def inventory_text(root: Path = PROJECTS_ROOT) -> str:
    lines = []
    for r in inventory(root):
        hint = f" — {r['hint']}" if r["hint"] else ""
        lines.append(f"- {r['path']}{hint}")
    return "\n".join(lines) if lines else "(no repos found)"
