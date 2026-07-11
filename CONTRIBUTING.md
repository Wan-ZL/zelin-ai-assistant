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

## The three gates

Every change batch must pass all three before merging — CI runs exactly these on every PR:

1. `python3 -m compileall act ingest`
2. `AIASSISTANT_HOME=$(mktemp -d) python3 -m unittest discover -s tests`
3. `bash mac/build.sh`

They are cheap; run them locally before pushing.

## Project rules

- **Contract first.** Any change to a `dashboard.json` or `state/inbox/` field lands in [docs/CONTRACT.md](docs/CONTRACT.md) *before* the code. Fields are **add-only** — never renamed or removed — and the Swift side decodes every new field with `decodeIfPresent` for backward compatibility. CONTRACT.md's section numbers are referenced from code and docs; never renumber them.
- **Bilingual strings.** Every user-visible string goes through `L("中文", "English")` — both languages, always; the UI switches at runtime.
- **Tests use a tempdir `AIASSISTANT_HOME`** — never a real `state/` or registry.
- **Commit messages**: conventional commits, English, and say *why*, not just what.

Recommended reading before a non-trivial change: `HANDOFF.md` (architecture map, the reasoning behind every "weird" design, and a pitfall list paid for in real debugging time), then `docs/CONTRACT.md`.

## External contributors

- Fork the repo, branch from `main`, open a pull request against `main`.
- CI runs the three gates automatically on every PR, including PRs from forks.
- Definition of done: **green CI + the checklist in the PR template**. The worktree / fast-forward-merge convention below is a maintainer concern — it does not apply to your fork.
- One logical change per PR. If the PR resolves an issue, include `Closes #XX` in the body.

## Maintainer notes

- **Always work in a git worktree and fast-forward-merge back to `main`.** The main working tree can be a live daemon runtime — actd and cron execute files straight from it, and half-edited files have caused real breakage (HANDOFF §4).
- After merging, verify HEAD actually moved (`git log -1`) — a failed `--ff-only` merge prints "Aborting" to stderr, which pipelines can swallow.
- External PRs can't be ff-merged as-is; rebase them onto `main` (keeping the linear history) and run the three gates before the merge lands.

## Versioning

Releases follow [Semantic Versioning](https://semver.org), applied like this
while the project is pre-1.0:

- **PATCH** (`x.y.Z`) — bug fixes, small UX corrections, docs. "小修小补."
- **MINOR** (`x.Y.0`) — new user-visible features. Pre-1.0, breaking changes
  also ride a minor bump: the commit must carry the conventional `!` marker
  and the changelog entry must call the break out prominently.
- **MAJOR** — reserved for 1.0 and post-1.0 breaking changes.

Merging a PR does **not** produce an installer: packages are built only when
a maintainer cuts a release (version bump + changelog + `vX.Y.Z` tag — see
the procedure at the top of [CHANGELOG.md](CHANGELOG.md)). Contributors never
need to touch the version.

## License of contributions

This project is licensed under the [Functional Source License 1.1, MIT Future License (FSL-1.1-MIT)](LICENSE.md). By submitting a contribution (pull request, patch, or suggestion incorporated into the code), you agree that:

1. Your contribution is licensed to the project under the same FSL-1.1-MIT terms (including the future MIT grant), and
2. You grant the project maintainer (Zelin Wan) a perpetual, worldwide, irrevocable right to use, modify, sublicense, and relicense your contribution as part of this project, including under commercial terms.

This keeps the project's licensing options unified in one place. If you're not comfortable with that, open an issue instead of a PR — suggestions are just as valuable.

Plain-language license Q&A — including *why* this grant exists — lives in [docs/LICENSE-FAQ.md](docs/LICENSE-FAQ.md).
