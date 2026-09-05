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
python3 scripts/demo_seed.py /tmp/assistant-demo          # add --english for English demo data
AIASSISTANT_HOME=/tmp/assistant-demo \
  "mac/build/Zelin's AI Assistant (old).app/Contents/MacOS/ZelinAIEngineer"
```

The demo seeder writes a fake `state/dashboard.json` with every card type and edge state visible; [docs/DEMO.md](docs/DEMO.md) documents the `--scene` and `--english` flags and the screenshot/recording workflow. Launch the binary directly as shown — `open` does not pass environment variables, so the app would silently fall back to the default home and show "dashboard missing".

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
- **Xcode / Swift 6.x** — older toolchains fail mid-build on main-actor isolation rules (same floor as CI, which pins one exact Xcode for both `ci.yml` and `release.yml` — truth = `.github/xcode-version`, applied by `scripts/ci/select_xcode.sh`; a pin missing from the runner image fails loudly instead of falling back to "newest"; bump it in a PR). `mac/Sources/` compiles as one module via plain `swiftc` — no SPM, no Xcode project; `bash mac/build.sh` is the whole build.
- Only needed for full-pipeline work: [Claude Code CLI](https://claude.com/claude-code) + API key, Node.js LTS, Obsidian — setup in [docs/INSTALL.md](docs/INSTALL.md).

## The four gates

Every change batch must pass all four before merging — CI runs these on every PR (the two Swift gates on the macOS runner only when a Swift-related path changed; see "What CI runs where" below):

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
`web/` adds `cd web && npm run typecheck && npm run build && npx vitest run` (`build`
type-checks only what ships — `tsconfig.build.json` — because install.sh builds web/
from a mirror outside the repo; `typecheck` covers the tests too, CONTRACT §56.5).

### What CI runs where (CONTRACT §56.8)

The repo is public (runner minutes are free) but the owner is on the free plan, whose *concurrency* cap (~20 jobs, 5 macOS) is what dozens of parallel agent PRs actually queue on. So per-PR CI is on a diet without touching the merge gate — the seven required checks keep their names and report on every PR and every merge-queue group:

- **`ci` (macOS) and `Web tests` are path-filtered on pull requests.** A first ubuntu job (`Changed paths (per-PR filter)`) lists the PR's files; `ci` runs the Apple suite on a macOS runner only when something under `mac/`, `shell/`, `ios/`, `shared/`, any `*.swift`, the Xcode pin / selector, the version stamper the builds call, `act/lib/e2e.py` (the Swift↔Python interop gate) or `ci.yml` itself changed — otherwise it runs on ubuntu, prints "no Swift changes — Swift gates not needed" and ends green. `Web tests` does the same for `web/`. The filter list is the truth in `.github/workflows/ci.yml`; if you make something new that only the macOS job exercises, add its path to the filter in the same PR.
- **`merge_group` and every push to `main` run everything**, filter or no filter; so does a PR whose filter step failed (a broken filter must never turn into a skipped gate).
- **The Windows legs and the qlty sweep run nightly** (`ci-nightly.yml`, 10:43 UTC, or `gh workflow run ci-nightly.yml`), still informational (`continue-on-error`), never a required check.
- **AI review bots run on demand, not on every push.** The default review of a PR is the adversarial agent review in the lead's session. When you want the paid second opinions (Claude + Codex), put the **`review:ai`** label on the PR (`gh pr edit <n> --add-label review:ai`) — both bots start, the label is consumed as soon as they pick it up, so adding it again later reviews the then-current head. Alternative: `gh workflow run pr-review-claude.yml -f pr=<n>` (and `pr-review-codex.yml`). Fork PRs are skipped either way; a missing API-key secret is a green no-op.

CI also replays the fresh-machine bootstrap on a clean macOS runner (**Fresh install (macOS)**, `.github/workflows/fresh-install.yml`, CONTRACT §69): empty `$HOME`, local origin, `bash scripts/bootstrap.sh --no-launchd`, then it asserts the install report, the board server's `/api/board` / `/api/setup`, `python3 -m act.doctor --fresh-install` exiting 0 and an idempotent second run. It runs on push to main, nightly and on `gh workflow run fresh-install.yml` — not per PR (macOS runners are the scarce slot, CONTRACT §56.8), so it is informational; a red run still means the one-command install is broken for someone — treat it as a bug. Local equivalent (safe: temp HOME, `--no-launchd`, no `/Applications` write): `HOME=$(mktemp -d) AIASSISTANT_UI_APPS_DIR=$HOME/Applications ZAI_BOOTSTRAP_REPO_URL=<bare clone of your branch> bash scripts/bootstrap.sh --no-launchd --no-open`.

CI additionally runs the **QA merge gates** (per-function complexity, CRAP, coverage floor, dependency direction, hygiene caps — see docs/CONTRACT.md §58 — plus the `[ui-parity]` gate of §66, below) against the shrink-only baselines in `qa/`. Local equivalent: `bash scripts/qa/run_gates.sh` (needs `pip install coverage`, dev-side only, and node + `cd web && npm ci` for `[ui-parity]`). New code must pass clean; pre-existing debt is ledgered in `qa/*_baseline.txt` and may only shrink. The canonical environment for the coverage-derived numbers is the CI `qa-gates` job — on non-linux machines the two coverage-derived gates (CRAP, coverage floor) print their verdicts but never block; reconcile those ledgers from the job's `qa-report` artifact, not from a local darwin run. "Reconcile" only ever means shrink: on pull requests the `qa-gates` job also diffs `qa/` against the PR base (`scripts/qa/ledger_diff.py`) and fails on any added ledger key, raised score, lowered coverage floor, or loosened threshold in `qa/gates.toml` — new debt must be fixed in the code, never enrolled. Local equivalent: `python3 scripts/qa/ledger_diff.py --base origin/main`.

### UI parity (web ⟷ frozen native app, CONTRACT §66)

The retired Mac app under `mac/Sources` is the terminal UI spec; `ui/parity/native-inventory.json` is its machine extraction and `scripts/ui/parity_check.py` (the `[ui-parity]` gate inside `run_gates.sh`) checks every inventory id against the web: native controls through `web/src/parity.test.tsx` (generated from the inventory, renders the pages with demo fixtures), settings keys against `server/settings*.py`, rail / lanes / theme / layout tokens statically. Two shrink-only ledgers live next to it: `ui/parity/pending.txt` (native items the web still lacks — **every PR that adds UI must strike lines here**; a struck-but-still-missing or a newly-missing item is red) and `ui/parity/waivers.txt` (deliberately not carried over; never grows — new "not carrying this over" decisions go into the extractor's owner table with a decision reference). `ledger_diff.py` rejects any growth of either file against the PR base. Generated artifacts must stay fresh (tests fail otherwise; in particular any PR that edits `mac/Sources` — which D3 says you should not — must re-run the extractor and commit the JSON): `python3 scripts/ui/extract_native_inventory.py --write`, `python3 scripts/ui/extract_native_tokens.py --write` (also rewrites the `@generated native-tokens` block at the end of `web/src/styles/tokens.css`), `python3 scripts/ui/parity_fixture.py --write`. The human-readable state is `ui/parity/report.md`.

**Visual baselines** (`web/e2e/visual.spec.ts`, CI job "Web visual (playwright)", informational at birth): three pages × light/dark at 1440×900 against the goldens in `web/e2e/__screenshots__/`. Run locally with `cd web && npm run build && npx playwright install chromium && npm run visual` — expect it to be red on your Mac (see next paragraph); read the diff triplets in `web/test-results/` to judge whether a real layout change hides behind the glyph noise. A PR that changes the UI must update the goldens **deliberately** and say in the PR which screenshots changed and why; never update them to make a red job green without looking at the diff (CI uploads the expected/actual/diff triplets as the `web-visual` artifact).

**Goldens are runner-rendered — never regenerate them on a Mac.** Text rasterization differs between any developer / agent Mac and the `macos-latest` image the CI job runs on (PingFang / Chromium build / hinting), so a laptop-made golden makes every CJK glyph "regress" at 1–3 % of pixels and the job stays red with no UI change behind it — that is exactly what happened 2026-09-04. `npm run visual:update` therefore exists only for looking at what your change does locally; the PNGs that get committed come from the **Refresh visual goldens** workflow (`.github/workflows/visual-goldens.yml`, `workflow_dispatch`, same runner + same setup steps as the CI job; it re-captures every golden with `--update-snapshots=all`, then re-runs the comparison so the runner proves it reproduces its own output). Procedure: `gh workflow run "Refresh visual goldens" --ref main -f ref=<your branch>` (the `ref` input is what gets captured; default main) → wait for the run (`gh run list --workflow visual-goldens.yml --limit 1`) → `gh run download <run-id> -n visual-goldens -D /tmp/goldens` → copy `/tmp/goldens/visual.spec.ts/*.png` over `web/e2e/__screenshots__/visual.spec.ts/` → commit + open a PR whose body names which pages changed and why (the run's step summary lists the sha256 of every PNG — quote the run URL). The job never commits or opens PRs itself (a `GITHUB_TOKEN`-authored PR gets no CI here); the artifact is the deliverable. When the `macos-latest` label moves to a new macOS major, both jobs move together and one refresh is due — that is the only legitimate "all six changed, no UI diff" golden PR.

### board shell 手动检查（shell/ 没有 test target）

`shell/Sources/main.swift` 的连接序（CONTRACT §54.2）没有 Swift 测试靶，改动它时手动过一遍（每条 ≤1 分钟）：

1. **attach**：server agent 在班（`launchctl print gui/$UID/com.zelin.aiassistant.server` 退出 0、`curl -s 127.0.0.1:47820/api/health` 有答）→ `open "shell/build/Zelin's AI Assistant.app"` → 看板直接出现；`~/Library/Logs/zelin-ai-assistant/board-shell.log` **没有**新的 `spawn` 横幅。
2. **launchd 已加载但端口没答话**：`launchctl kickstart -k gui/$UID/com.zelin.aiassistant.server` 后 1 秒内 `open` 壳 → 壳等 ≤10 s 后照常加载（log 里一行 `… is loaded in launchd — waiting, not spawning`），期间 `pgrep -fl "python3 -m server"` 只有 launchd 那一个进程。
3. **失败弹窗**：`launchctl bootout gui/$UID/com.zelin.aiassistant.server` 再把 `defaults write com.zelin.ai-board serverRepo /nonexistent` → `open` 壳 → 弹窗第一条是 `launchctl kickstart -k gui/$UID/com.zelin.aiassistant.server`，注明 label 未加载 → `bash install.sh`。完事 `defaults delete com.zelin.ai-board serverRepo && bash install.sh`。
4. **名字**（§54 名字互换）：Dock、窗口标题、app 菜单都读 "Zelin's AI Assistant"；`osascript -e 'id of app "Zelin's AI Assistant"'` 是 `com.zelin.ai-board`（旧菜单栏 app 是 "Zelin's AI Assistant (old)" / `com.zelin.ai-engineer`）。
5. **原生残留（CONTRACT §69.13，改 `ShellSystem.swift` / `NotifyRelay.swift` 时）**：`python3 -c "from act.lib import notify; notify.notify('测试', '通知中继')"` → 5 s 内壳弹出横幅，点它前置看板窗口；`⌃⌥Space` 在任何 app 里按下 → 壳前置且提案列输入框获得焦点；看板 `?page=permissions` 三行状态与 系统设置 一致，点「授权」弹系统提示；设置 → 关于「登录时启动」翻开关后 `sfltool dumpbtm | grep -i zelin` 有 / 无记录；Dock 徽章 = 提案 + 需输入 + 待验收数。

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
- **Social preview** (issue #19): when the board UI changes materially, `bash promo/social-preview.sh`
  re-renders `docs/assets/social-preview.png` (1280 × 640) and you upload it by hand under GitHub →
  Settings → General → Social preview — GitHub has no API for this, so it is the one manual step
  left in a release. Verify it took: `gh api graphql -f query='{ repository(owner:"Wan-ZL", name:"zelin-ai-assistant") { usesCustomOpenGraphImage } }'`
  must say `true` (the field is read-only — there is no write API).

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
- **Release notes are fragments** (CONTRACT §56.7): add `changelog.d/<kebab-slug>.md` — first line `type: added|changed|deprecated|removed|fixed|security`, then top-level `- ` bullets; one type per file, shape in [`changelog.d/README.md`](changelog.d/README.md). **Never write into `## [Unreleased]`** in `CHANGELOG.md`: it is frozen (shrink-only) and the same CI check rejects any added bullet or `### ` heading there. The GitHub Release body is the delta of (fragments ∪ `[Unreleased]`) since the previous tag; nothing is rewritten or deleted by CI. Fragments a tag already shipped are pruned by the next PR that touches `changelog.d/` (`python3 scripts/ci/changelog_prune.py`; CI prints a `::notice::` while any are left).
- **Progress log rows are fragments too**: the v-next-2 round's §8 table in `docs/design/vnext2-plan.md` is frozen history; a PR adds `docs/design/progress/<YYYY-MM-DD>-<slug>.md` (header `pr:` / `phase:` / `law:`, blank line, body — see [`docs/design/progress/README.md`](docs/design/progress/README.md)). `python3 scripts/ci/progress_log.py render` prints the full table on demand; a new `| YYYY-MM-DD |` row written into the plan directly is rejected by the same CI check.
- `act.__version__` resolves at import from the generated, git-ignored `act/_version.py` (written by `install.sh`, `mac/build.sh`, `shell/build.sh`, packaging and the release job through `scripts/version_stamp.py --write`), else `git describe`, else the fallback line. `python3 scripts/version_stamp.py` prints what your checkout would be stamped with (`X.Y.Z` on a tag, `X.Y.Z+N` when ahead); `python3 -m act.doctor` has a `version` row that warns when the stamp is missing or stale.
- **No merge queue, but the same effect** (CONTRACT §56.6): GitHub's Merge Queue is unavailable on personal-account repositories, so `update-pr-branches.yml` merges every new `main` into every open PR branch instead and GitHub's auto-merge lands the PR the moment its rerun checks are green. Rebasing to pick up someone else's version bump is a thing of the past; rebasing at all is now something you do only when the bot tells you to. The lifecycle is spelled out below.

The maintainer's Mac then fast-forwards to `main` by itself (`scripts/auto-deploy.sh`) once the `ci` check-run on that exact `main` commit is green — the merge commit itself is verified before it is installed — with a doctor-gated rollback; daemons, cron and config only; the frozen legacy Mac app is never rebuilt unattended (a hand-run `bash install.sh` does that, §56.5). Changelog procedure at the top of [CHANGELOG.md](CHANGELOG.md).

### PR lifecycle (maintainers and agents)

1. **Open the PR** from a worktree branch. Never touch a version anywhere (see above), and write your release note and progress row as fragments (`changelog.d/<slug>.md`, `docs/design/progress/<date>-<slug>.md`) rather than into `CHANGELOG.md` / the plan's §8 table — the `Version pins untouched` check enforces all of it and validates the fragments' shape (`python3 scripts/ci/changelog_fragments.py check && python3 scripts/ci/progress_log.py check` locally).
2. **Arm auto-merge right away**: `gh pr merge --auto --merge <n>`. Nothing merges until the seven required checks are green (`ci`, `Lint (shellcheck + ruff)`, `Tests on ubuntu (Python 3.9)`, `Tests on ubuntu (Python 3.x)`, `Web tests (build + vitest)`, `QA gates (…)`, `Version pins untouched`); once they are, GitHub merges by itself — nobody clicks.
3. **When `main` moves** (someone else's PR merged), `update-pr-branches.yml` merges the new `main` into your branch, CI reruns on the new head, and step 2 finishes on its own. You do nothing. Exception: your branch conflicts with `main` — the bot puts the **`needs-rebase`** label on the PR and leaves one comment. Then, and only then: `git fetch origin main && git rebase origin/main`, resolve, `git push --force-with-lease`; the label comes off on the bot's next run. Put **`no-autoupdate`** on a PR you want left alone (a long-lived experiment, a PR you are actively rebasing by hand).
4. **Poll, don't watch.** Check progress with `gh pr checks <n> --required` on an interval (a minute or two between calls). **Never** `gh pr checks --watch`: it waits for *every* check, the soft contract reminder and any advisory bot review you asked for included, and a single hung one holds your agent for as long as the job's timeout (the CI jobs cap at 40 min for exactly this reason, §56.6). Expect `ci` and `Web tests` to finish in well under a minute on a PR that touches neither Swift nor `web/` — that is the path filter (§56.8), not a broken run. Stop polling when the PR reports `MERGED` (`gh pr view <n> --json state`) or a required check fails — fix, push, and the same armed auto-merge picks it up.
5. **Owner one-time setup** the bot depends on: a fine-grained PAT (this repo only; Contents read+write, Pull requests read+write) stored as the repository secret `PR_AUTOUPDATE_TOKEN`. Why not the workflow's own token: branch updates made with `GITHUB_TOKEN` do not fire `pull_request` workflows, so the PR head would move to a commit that never gets CI and auto-merge would never fire. Without the secret the workflow only labels conflicts and reports what it would have done.

## License of contributions

This project is licensed under the [Functional Source License 1.1, MIT Future License (FSL-1.1-MIT)](LICENSE.md). By submitting a contribution (pull request, patch, or suggestion incorporated into the code), you agree that:

1. Your contribution is licensed to the project under the same FSL-1.1-MIT terms (including the future MIT grant), and
2. You grant the project maintainer (Zelin Wan) a perpetual, worldwide, irrevocable right to use, modify, sublicense, and relicense your contribution as part of this project, including under commercial terms.

This keeps the project's licensing options unified in one place. If you're not comfortable with that, open an issue instead of a PR — suggestions are just as valuable.

Plain-language license Q&A — including *why* this grant exists — lives in [docs/LICENSE-FAQ.md](docs/LICENSE-FAQ.md).
