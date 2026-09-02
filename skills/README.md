# skills/ — the in-repo skill store (vnext2-plan D13, R2.7)

Skills live here so they version with the code and sync between machines with `git pull`. Agents and Claude Code read skills from `~/.claude/skills/<name>`; enabling a repo skill is a symlink to its directory.

## Conventions (R2.7.1)

| Rule | Detail |
|---|---|
| Layout | `skills/<name>/SKILL.md` (frontmatter + ≤150 lines, tables over prose) · `references/*.md` loaded on demand · `scripts/*.py` stdlib-only, Python 3.9 floor, every function CC ≤ 6, negative-control tests under `tests/test_skill_<name>_*.py` |
| Frontmatter | `name`, `description` (the trigger phrases), `version` (semver of this skill), `upstream` + `upstream_version` when adapted from elsewhere (attribution also goes into `NOTICE`) |
| Default policy | **default OFF**: nothing in `skills/` is active until enabled per machine. Whether a skill is on for the owner is a per-machine choice; this README states the policy, not the state |
| Enable | `ln -s "$(pwd)/skills/<name>" ~/.claude/skills/<name>` from the repo root (Claude Code and dispatched agents pick it up at their next session). Disable = remove the symlink |
| Sync | another machine: `git pull` then create the same symlink once; later pulls update in place |
| Default vs custom (R2.7.3) | the repo copy is the default. A locally edited *copy* (not a symlink) is a custom skill: keep its `version` and note "custom, based on <name> v<x.y.z>" in the frontmatter; upgrades never overwrite it, they only tell you the repo version moved |
| Worktrees (R2.7.4) | a dispatched agent in a worktree sees the skill through the same `~/.claude/skills` symlink; the symlink points at the live checkout, so keep skills self-contained (no imports from `act/`) |
| Settings UI (R2.7.2) | the web settings page for enable/disable is P4 work; until then the symlink command above is the switch |

## Skills

| name | version | default | what it does | invoke |
|---|---|---|---|---|
| `board-agent` | — (predates the store; add `version` on its next touch) | off | scoped agent channel to the card board via `boardctl` (read / capture / comment only; CONTRACT §52) | agent reads it when working a card |
| `test-code` 测试代码 | 0.2.1 | off (owner may enable per machine) | five-tier (档 1 静态门 … 档 5 通宵/通几天), trigger-aware, core/extended-circle testing ladder for any repo: detect → ask tier + multi-select once → run → `report.md` + `report.json` with fix-first ranking, core-skip accounting and structural blind spots (R2.8, D14) | `/test-code` in Claude Code, or `python3 skills/test-code/scripts/detect.py --repo . --out /tmp/d.json && python3 skills/test-code/scripts/run_ladder.py --repo . --detect /tmp/d.json --tier 2 --chosen-by user` |
