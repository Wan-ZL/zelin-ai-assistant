# Contributing

Issues, suggestions, and pull requests are all welcome — bug reports, feature ideas, and questions included. Usage and setup questions are best asked in [GitHub Discussions](https://github.com/Wan-ZL/zelin-ai-assistant/discussions); security problems go through the private channel in [SECURITY.md](SECURITY.md), never a public issue. All project spaces are covered by the [Code of Conduct](CODE_OF_CONDUCT.md).

## Hacking without the full stack

You do **not** need screenpipe, Obsidian, or an Anthropic API key to work on this project. The test suite plus a fully fictional demo runtime cover most development:

```bash
# 1. Run the whole test suite — needs only python3 (3.9+) and PyYAML.
#    150+ tests, well under a second. The tempdir HOME is mandatory:
#    tests must never touch a real state/ or registry.
AIASSISTANT_HOME=$(mktemp -d) python3 -m unittest discover -s tests

# 2. Build the menu-bar app, without installing it — needs Xcode / Swift 6.x.
bash mac/build.sh

# 3. Run the app against entirely fictional data — no keys, no recording:
python3 scripts/demo_seed.py /tmp/assistant-demo
AIASSISTANT_HOME=/tmp/assistant-demo \
  "mac/build/Zelin's AI Assistant.app/Contents/MacOS/ZelinAIEngineer"
```

The demo seeder writes a fake `state/dashboard.json` with every card type and edge state visible; [docs/DEMO.md](docs/DEMO.md) documents the `--scene` flags and the screenshot/recording workflow. Launch the binary directly as shown — `open` does not pass environment variables, so the app would silently fall back to the default home and show "dashboard missing".

What you need for what:

| You want to… | Python 3.9+ & PyYAML | Xcode / Swift 6.x | claude CLI + API key | Node.js (`npx`) | screenpipe / Obsidian |
|---|---|---|---|---|---|
| run the tests | ✅ | — | — | — | — |
| build the app | — | ✅ | — | — | — |
| demo mode (full UI, fictional data) | ✅ (stdlib only) | ✅ | — | — | — |
| run the full pipeline | ✅ | ✅ | ✅ | ✅ (engine runs via `npx screenpipe`) | ✅ (Obsidian optional but recommended) |

Note that `install.sh` is the *end-user* installer — it installs the app to /Applications, loads launchd agents, and edits your crontab. As a contributor you usually don't want any of that on your dev machine; the three commands above are enough.

## Dev environment

- **Python 3.9+ with PyYAML** — deliberately the only Python dependency (that's why there is no lockfile). If `pip install --user pyyaml` fails with "externally managed environment" (Homebrew Python, PEP 668), retry with `--break-system-packages`; CI uses the same fallback.
- **Xcode / Swift 6.x** — older toolchains fail mid-build on main-actor isolation rules (same floor as CI; see the "Select newest Xcode" comment in `.github/workflows/ci.yml`). `mac/Sources/` compiles as one module via plain `swiftc` — no SPM, no Xcode project; `bash mac/build.sh` is the whole build.
- Only needed for full-pipeline work: [Claude Code CLI](https://claude.com/claude-code) + API key, Node.js LTS, Obsidian — setup in [docs/INSTALL.md](docs/INSTALL.md).

## The four gates

Every change batch must pass all four before merging — CI runs exactly these on every PR:

1. `python3 -m compileall act ingest`
2. `AIASSISTANT_HOME=$(mktemp -d) python3 -m unittest discover -s tests`
3. `bash mac/build.sh`
4. `bash mac/LogicTests/test.sh` — Swift pure-logic unit tests. With full Xcode
   this is exactly `swift test --package-path mac/LogicTests`; the wrapper only
   adds framework search paths on Command-Line-Tools-only machines (the CLT
   ships Swift Testing but hides it — details at the top of the script).

They are cheap; run them locally before pushing. Touching `shell/` (the product
app since D3 — CONTRACT §54/§61) adds two more: `bash shell/build.sh` (must
compile; engines moved from `mac/Sources` live here) and `bash shell/tests/run.sh`
(swiftc typecheck of the whole module + the XCTest-free bridge harness). Touching
`web/` adds `cd web && npm run build && npx vitest run`.

CI additionally runs the **QA merge gates** (per-function complexity, CRAP, coverage floor, dependency direction, hygiene caps — see docs/CONTRACT.md §58) against the shrink-only baselines in `qa/`. Local equivalent: `bash scripts/qa/run_gates.sh` (needs `pip install coverage`, dev-side only). New code must pass clean; pre-existing debt is ledgered in `qa/*_baseline.txt` and may only shrink. The canonical environment for the coverage-derived numbers is the CI `qa-gates` job — on non-linux machines the two coverage-derived gates (CRAP, coverage floor) print their verdicts but never block; reconcile those ledgers from the job's `qa-report` artifact, not from a local darwin run. "Reconcile" only ever means shrink: on pull requests the `qa-gates` job also diffs `qa/` against the PR base (`scripts/qa/ledger_diff.py`) and fails on any added ledger key, raised score, lowered coverage floor, or loosened threshold in `qa/gates.toml` — new debt must be fixed in the code, never enrolled. Local equivalent: `python3 scripts/qa/ledger_diff.py --base origin/main`.

### board shell 手动检查（shell/ 没有 test target）

`shell/Sources/main.swift` 的连接序（CONTRACT §54.2）没有 Swift 测试靶，改动它时手动过一遍（每条 ≤1 分钟）：

1. **attach**：server agent 在班（`launchctl print gui/$UID/com.zelin.aiassistant.server` 退出 0、`curl -s 127.0.0.1:47820/api/health` 有答）→ `open "shell/build/Zelin AI Board.app"` → 看板直接出现；`~/Library/Logs/zelin-ai-assistant/board-shell.log` **没有**新的 `spawn` 横幅。
2. **launchd 已加载但端口没答话**：`launchctl kickstart -k gui/$UID/com.zelin.aiassistant.server` 后 1 秒内 `open` 壳 → 壳等 ≤10 s 后照常加载（log 里一行 `… is loaded in launchd — waiting, not spawning`），期间 `pgrep -fl "python3 -m server"` 只有 launchd 那一个进程。
3. **失败弹窗**：`launchctl bootout gui/$UID/com.zelin.aiassistant.server` 再把 `defaults write com.zelin.ai-board serverRepo /nonexistent` → `open` 壳 → 弹窗第一条是 `launchctl kickstart -k gui/$UID/com.zelin.aiassistant.server`，注明 label 未加载 → `bash install.sh`。完事 `defaults delete com.zelin.ai-board serverRepo && bash install.sh`。
4. **名字**：Dock、窗口标题、app 菜单都读 "Zelin's AI Assistant (Board)"；`osascript -e 'id of app "Zelin AI Board"'` 仍是 `com.zelin.ai-board`。

## Project rules

- **Contract first.** Any change to a `dashboard.json` or `state/inbox/` field lands in [docs/CONTRACT.md](docs/CONTRACT.md) *before* the code. Fields are **add-only** — never renamed or removed — and the Swift side decodes every new field with `decodeIfPresent` for backward compatibility. CONTRACT.md's section numbers are referenced from code and docs; never renumber them.
- **Bilingual strings.** Every user-visible string goes through `L("中文", "English")` — both languages, always; the UI switches at runtime.
- **Shell scripts run on bash 3.2** (the live `/bin/bash`): inside a bilingual string, always brace a variable followed by a fullwidth/CJK character — `（${_why}）` not `（$_why）` — bash 3.2 swallows the multibyte character's first byte into the variable name and aborts under `set -u` (shipped bug in PR #130, caught by its own test).
- **Tests use a tempdir `AIASSISTANT_HOME`** — never a real `state/` or registry.
- **Commit messages**: conventional commits, English, and say *why*, not just what.

Recommended reading before a non-trivial change: `HANDOFF.md` (architecture map, the reasoning behind every "weird" design, and a pitfall list paid for in real debugging time), then `docs/CONTRACT.md`.

## External contributors

- Fork the repo, branch from `main`, open a pull request against `main`.
- CI runs the four gates automatically on every PR, including PRs from forks.
- Definition of done: **green CI + the checklist in the PR template**. The worktree / fast-forward-merge convention below is a maintainer concern — it does not apply to your fork.
- One logical change per PR. If the PR resolves an issue, include `Closes #XX` in the body.

## Maintainer notes

- **Always work in a git worktree and fast-forward-merge back to `main`.** The main working tree can be a live daemon runtime — actd and cron execute files straight from it, and half-edited files have caused real breakage (HANDOFF §4).
- After merging, verify HEAD actually moved (`git log -1`) — a failed `--ff-only` merge prints "Aborting" to stderr, which pipelines can swallow.
- External PRs can't be ff-merged as-is; rebase them onto `main` (keeping the linear history) and run the four gates before the merge lands.
- After any deploy to a real machine (`bash mac/build.sh --install` / .pkg /
  Sparkle), verify it landed with `bash scripts/smoke-deploy.sh` — version
  match, binary feature markers, actd liveness, and the full doctor in one
  command (also printed as step 7 at the end of `install.sh`).

## Versioning

Releases follow [Semantic Versioning](https://semver.org), applied like this
while the project is pre-1.0:

- **PATCH** (`x.y.Z`) — bug fixes, small UX corrections, docs. "小修小补."
- **MINOR** (`x.Y.0`) — new user-visible features. Pre-1.0, breaking changes
  also ride a minor bump: the commit must carry the conventional `!` marker
  and the changelog entry must call the break out prominently.
- **MAJOR** — reserved for 1.0 and post-1.0 breaking changes.

Merging a PR **is** the release (CONTRACT §56), and **nobody bumps a version — ever**. The version's single source of truth is the git tag on `main` (§56.1): on push to `main`, `release-on-merge.yml` tags the merged commit with the highest existing tag + 1 patch and runs the release workflow, which stamps that number into the artifacts. Put a `release: minor` or `release: major` label on the PR for a bigger bump. What a PR does and does not touch:

- **Never** edit the `__version__ = "…"` fallback line in `act/__init__.py`, the `MARKETING_VERSION: "0.0.0-dev"` placeholders in `ios/project.yml` / `project.pbxproj`, or add a `## [X.Y.Z]` heading / compare link to `CHANGELOG.md`. The required CI check **Version pins untouched** rejects all of these (PRs opened before the cutover get a notice instead of a failure until they rebase).
- Write release notes under `## [Unreleased]` in `CHANGELOG.md` only; the GitHub Release body is the delta of that section since the previous tag and the file is never rewritten.
- `act.__version__` resolves at import from the generated, git-ignored `act/_version.py` (written by `install.sh`, `mac/build.sh`, `shell/build.sh`, packaging and the release job through `scripts/version_stamp.py --write`), else `git describe`, else the fallback line. `python3 scripts/version_stamp.py` prints what your checkout would be stamped with (`X.Y.Z` on a tag, `X.Y.Z+N` when ahead); `python3 -m act.doctor` has a `version` row that warns when the stamp is missing or stale.
- **Merge queue**: `gh pr merge --auto --merge <n>` enqueues the PR once it is green; the queue re-runs every required check on top of the current `main` (all workflows respond to `merge_group`) and merges by itself. Rebasing to pick up someone else's version bump is a thing of the past.

The maintainer's Mac then fast-forwards to `main` by itself (`scripts/auto-deploy.sh`) once the `ci` check-run on that exact `main` commit is green — the merge commit itself is verified before it is installed — with a doctor-gated rollback; daemons, cron and config only; the frozen legacy Mac app is never rebuilt unattended (a hand-run `bash install.sh` does that, §56.5). Changelog procedure at the top of [CHANGELOG.md](CHANGELOG.md).

## License of contributions

This project is licensed under the [Functional Source License 1.1, MIT Future License (FSL-1.1-MIT)](LICENSE.md). By submitting a contribution (pull request, patch, or suggestion incorporated into the code), you agree that:

1. Your contribution is licensed to the project under the same FSL-1.1-MIT terms (including the future MIT grant), and
2. You grant the project maintainer (Zelin Wan) a perpetual, worldwide, irrevocable right to use, modify, sublicense, and relicense your contribution as part of this project, including under commercial terms.

This keeps the project's licensing options unified in one place. If you're not comfortable with that, open an issue instead of a PR — suggestions are just as valuable.

Plain-language license Q&A — including *why* this grant exists — lives in [docs/LICENSE-FAQ.md](docs/LICENSE-FAQ.md).
