# skills/ — the in-repo skill store (CONTRACT §65; vnext2-plan D13, R2.7)

Skills live here so they version with the code and sync between machines with `git pull`. Claude Code and dispatched agents read skills from `~/.claude/skills/<name>`; **enabling a repo skill is a symlink from there to its directory here**. The manifest `skills/index.yaml` is the catalog; `act/lib/skills.py` is the only thing that ever writes `~/.claude/skills` links or `state/skills.json`; git is the only writer of `skills/` itself (防腐 #8).

## Conventions (R2.7.1)

| Rule | Detail |
|---|---|
| Layout | `skills/<name>/SKILL.md` (frontmatter + ≤150 lines, tables over prose) · `references/*.md` loaded on demand · `scripts/*.py` stdlib-only, Python 3.9 floor, every function CC ≤ 6, negative-control tests under `tests/test_skill_<name>_*.py` (real-subprocess ones under `tests/integration/`) |
| Frontmatter | `name` (= directory name), `description` (the trigger phrases), `version` (dotted integers, **must equal the manifest entry** — `tests/test_skills_manifest_repo.py` pins it), `upstream` + `upstream_version` when adapted from elsewhere (attribution also goes into `NOTICE`). `version` may also sit under `metadata:` (Agent Skills spec placement); the store reads both |
| Manifest | `skills/index.yaml`: one entry per skill — `name`, `version`, `upstream`, `upstream_version`, `default_enabled`, `description`. Every `skills/*/SKILL.md` must be listed and every entry must have its directory |
| Default policy | A skill that changes the assistant's **voice or behaviour** ships `default_enabled: false` — nobody gets a new voice from `git pull`. Infrastructure skills the product itself relies on (`board-agent`, `test-code`) ship `default_enabled: true`: `install.sh` links them on a fresh machine and they are project-visible (below) |
| Enable / disable | Settings page → **Skills** (web board, `?page=settings`), or `python3 -m act.lib.skills enable <name>` / `disable <name>`. Enable = symlink `~/.claude/skills/<name>` → `skills/<name>` (copy fallback where the filesystem refuses symlinks; the copy's hash is recorded so it is never mistaken for a custom copy). Each decision is recorded in `state/skills.json` per machine |
| Sync | another machine: `bash scripts/skills_sync.sh --pull` (= `git pull --ff-only` + refresh). `install.sh` runs the same script (step `skills`) on every install/deploy: re-points links that still aim at another checkout, refreshes unmodified copies, applies `default_enabled` **only where no decision is recorded** — a skill you switched off stays off |
| Default vs custom (R2.7.3) | the repo copy is the default. A real directory at `~/.claude/skills/<name>` whose content differs from every version the store knows is a **custom** copy: the store never overwrites or deletes it, the toggle is locked, and the row shows `自定义 · 落后/领先 N 版` (its frontmatter `version` vs the manifest; N = the first differing semver component). To go back to the repo version, move the directory away and enable |
| Project-visible (R2.7.4) | the `default_enabled` skills are also tracked as **relative symlinks** `.claude/skills/<name>` → `../../skills/<name>`. Claude Code loads project skills from `.claude/skills/` of the working directory and its parents, and follows symlinks — so every checkout **and every git worktree** (including the `<repo>/.claude/worktrees/<name>` a `claude --bg` agent isolates into) sees them without any per-machine step. Personal (`~/.claude/skills`) wins over project on a name clash, per Claude Code's documented precedence; same-target links are loaded once |
| Never | a skill never sends anything anywhere; no personal data except first-party owner skills; no imports from `act/` (skills must work from a bare checkout and through the symlink) |

## Skills

| name | version | default | what it does | invoke |
|---|---|---|---|---|
| `board-agent` | 1.0.0 | **on** | scoped agent channel to the card board via `boardctl` (read / capture / comment only; CONTRACT §52) | agent reads it when working a card |
| `test-code` 测试代码 | 0.2.1 | **on** | five-tier (档 1 静态门 … 档 5 通宵/通几天), trigger-aware, core/extended-circle testing ladder for any repo: detect → ask tier + multi-select once → run → `report.md` + `report.json` with fix-first ranking, core-skip accounting and structural blind spots (R2.8, D14) | `/test-code` in Claude Code, or `python3 skills/test-code/scripts/detect.py --repo . --out /tmp/d.json && python3 skills/test-code/scripts/run_ladder.py --repo . --detect /tmp/d.json --tier 2 --chosen-by user` |
| `write-better` | 1.0.0 | off | precision-first formal English (emails, papers, articles, READMEs, reports, CVs) modeled on Munindar P. Singh's published editorial standards, quoted with attribution; `scripts/style_check.py` is a deterministic, sentence-aware linter (ERROR/WARN, exit 0/1/2/3) with its own 62-case suite `scripts/test_style_check.py`. Changes the assistant's voice — off by default (absorbed from PR #102) | `/write-better` after enabling; `python3 skills/write-better/scripts/style_check.py <file>` |

## Contributing a skill (from PR #102)

New skills arrive as pull requests adding **one directory** under `skills/` plus its `index.yaml` entry (`default_enabled: false` unless the product itself depends on it). Rules: attributed quotes only, no personal data, testable scripts, `version` in frontmatter and manifest agree. The store needs no code change to list it — `GET /api/skills`, the Settings page and `skills_sync.sh` all read the manifest.
