<!-- What does this PR do, and WHY? One logical change per PR. -->

Closes #

## The three questions (constitution check — answer in the description above)

Read [`CLAUDE.md`](https://github.com/Wan-ZL/zelin-ai-assistant/blob/main/CLAUDE.md) and [`docs/CONTRACT.md` §0](https://github.com/Wan-ZL/zelin-ai-assistant/blob/main/docs/CONTRACT.md) first.

1. Which CONTRACT §§ does this change touch? (New behavior ⇒ which section is added/amended in this same PR — or state "none, because…")
2. Which §0 constitution articles does it relate to? Does it break one? (Breaking one ⇒ amend the constitution explicitly in this PR, or change the approach.)
3. Does an existing mechanism already do something similar? (Search the contract before inventing a parallel one.)

## The four gates

CI runs these on every PR (the Swift gates on the macOS runner only when a Swift-related path changed — CONTRACT §56.8); please run them locally first.

- [ ] `python3 -m compileall -q act server ingest` passes
- [ ] `AIASSISTANT_HOME=$(mktemp -d) python3 -m unittest discover -s tests` passes (tests use a tempdir HOME — never a real `state/`)
- [ ] `bash shell/build.sh` builds cleanly and `bash shell/tests/run.sh` passes (when `shell/` changed)
- [ ] `cd web && npm run typecheck && npm run build && npx vitest run` passes (when `web/` changed)

## Project rules

See [CONTRIBUTING.md](https://github.com/Wan-ZL/zelin-ai-assistant/blob/main/CONTRIBUTING.md). Tick what applies; strike through what doesn't (e.g. docs-only).

- [ ] **Contract first** — a cross-component field (`/api/*` payloads, `state/` files, `config.yaml` keys) changes only if `docs/CONTRACT.md` is updated in the same PR, and the field is add-only (nothing renamed or removed); a retired feature leaves a one-line tombstone
- [ ] **Isolation** (constitution art.13, CONTRACT §78) — no code reads or writes `~/.claude` / `~/.claude.json`; every headless `claude -p` goes through `act/llm.py` or `ingest/claude-env.sh`; no new LaunchAgent, cron line or login item outside the closed list
- [ ] **Install / upgrade scripts** — `install.sh`, `uninstall.sh`, `scripts/upgrade.sh` changes are tested with fake `launchctl` / `crontab` and a throwaway `$HOME`, never against a live login session; the recording engine is never signalled
- [ ] **No version, no shared-ledger edits** (CONTRACT §56.1 / §56.7; the required check **Version pins untouched**) — no version pin or `__version__` edit, nothing written into `CHANGELOG.md` `## [Unreleased]`
- [ ] **Release note as a fragment** — `changelog.d/<kebab-slug>.md` (first line `type: added|changed|deprecated|removed|fixed|security`, then `- ` bullets; [shape](https://github.com/Wan-ZL/zelin-ai-assistant/blob/main/changelog.d/README.md)), validated by `python3 scripts/ci/changelog_fragments.py check`
- [ ] **Bilingual strings** — every new user-visible string comes in both languages
- [ ] New behavior is covered by a test where practical
- [ ] Commit messages are conventional commits, in English, and explain *why*
