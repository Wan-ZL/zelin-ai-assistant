# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Bumping the version

`__version__` in [`act/__init__.py`](act/__init__.py) is the **single source of
truth** for the project version. `mac/build.sh` stamps it into the app bundle's
`Info.plist` at build time, and `mac/package.sh` reads it for the `.pkg` — no
other file needs editing. To cut a release:

1. Bump `__version__` in `act/__init__.py`.
2. Rename the `[Unreleased]` section below to `[X.Y.Z] - YYYY-MM-DD` and add a
   fresh empty `[Unreleased]` heading above it; update the compare links at the
   bottom.
3. Commit (`chore: bump version to X.Y.Z`) and tag `vX.Y.Z`; pushing the tag
   triggers the release workflow.

## [Unreleased]

(nothing yet)

## [0.12.0] - 2026-07-09

The P0 + P1 waves of the open-source readiness review: make a fresh install
work on a clean Mac, default to privacy-safe behavior, give first-time visitors
English docs plus privacy/security policies, and harden the pipeline
(security fencing, diagnostics, state-machine test coverage, an iMessage phone
channel, and sensitive-app capture exclusion).

### Added

- iMessage phone channel: approve/reject/rework/accept cards, quick capture,
  and 👍-tapback approvals from the iMessage "message yourself" thread
  (`phone_channel: imessage`); Slack remains available; see
  `docs/IMESSAGE_SETUP.md`
  ([`fec102f`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/fec102f))
- Sensitive-app screen-capture exclusion (`recording.ignored_apps`): password
  managers, Keychain Access, and private-browsing windows are excluded at the
  engine level by default, with a matching SQL filter on export for frames
  recorded earlier
  ([`1b3fc29`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/1b3fc29))
- `python3 -m act.doctor` (also `install.sh --check`): 14 post-install
  diagnostics with symptom-first output and per-check fixes; an "auth model"
  section in `docs/INSTALL.md` explains API key vs subscription auth
  ([`b693541`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/b693541))
- Pipeline health banner in the app distinguishing slow vs broken (stale/dead
  tiers with recovery actions), first-launch dependencies walkthrough, TCC
  status rows, and instant Anthropic-key validation in Settings
  ([`9adb246`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/9adb246))
- CI: shellcheck + ruff lint gates and a Python 3.9 floor job; releases now
  ship SHA-256 checksums and build-provenance attestations; third-party actions
  pinned to commit SHAs with Dependabot updates
  ([`848aaf6`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/848aaf6))
- 94 new state-machine and radar tests (reconcile/resume/transitions/registry
  merge), plus a shared agent-state vocabulary module ending the
  actd/dashboard drift
  ([`825eb30`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/825eb30))
- Community files: contributor quickstart without the full stack, issue forms,
  PR template, Code of Conduct, and a plain-language license FAQ
  ([`8a5b505`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/8a5b505))
- Mermaid architecture diagram with the trust boundary in both READMEs,
  English orientation headers for HANDOFF/CONTRACT, and `docs/ROADMAP.md`
  ([`9cc1a28`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/9cc1a28))
- `docs/PRIVACY.md`: full data-egress inventory — every channel that sends data
  off the machine (ingest cron chain, radars, quick capture, executor, telemetry)
  with trigger, frequency, payload, and off-switch, plus local retention and an
  execution-permissions section explaining `--dangerously-skip-permissions` and
  the `execution.skip_permissions` config
  ([`1a6e45b`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/1a6e45b))
- `SECURITY.md`: supported versions, private vulnerability reporting via GitHub
  advisories, response window, and explicit scope
  ([`1a6e45b`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/1a6e45b))
- First-launch recording consent: a fresh install no longer auto-starts screen
  capture; a one-time bilingual sheet explains what is captured and where it
  goes, with Screen Only / Screen + Audio / Not Now choices. Existing installs
  keep their stored mode
  ([`8949f7e`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/8949f7e))
- English `README.md` (badges, how-it-works, quickstart, features, license
  summary) with the Chinese original moved to `README.zh-CN.md`; hero
  screenshots and demo video under `docs/assets/`
  ([`d8f9083`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/d8f9083),
  [`2399e3b`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/2399e3b))
- `docs/INSTALL.md` as the single authoritative install guide: prerequisites
  table with versions, numbered steps with expected-state checkpoints, exact TCC
  grant paths, and a "first card in 5 minutes" path; pitfalls collected into a
  symptom-first `docs/TROUBLESHOOTING.md`
  ([`d8f9083`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/d8f9083))
- `.github/release.yml` release-notes category template
  ([`d8f9083`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/d8f9083))

### Changed

- **BREAKING**: approving a card no longer auto-creates a private GitHub repo
  for new targets — `execution.create_github_repo` now defaults to `false`;
  set it to `true` explicitly to restore the old behavior
  ([`f5feeb2`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/f5feeb2))
- The dashboard's `completed` list is capped at the 50 most recent items
  (counts stay exact); resolving credentials through the legacy
  `~/Desktop/Keys/` path now logs a deprecation warning
  ([`f5feeb2`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/f5feeb2))
- Kanban/popover task rows take an explicit lane (retiring the accent-color
  hack), error messages are copyable with full text, and timeout notices
  appear in the lane where the action happened
  ([`00b3e2c`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/00b3e2c))

- UI language now defaults to the system locale (`zh-*` → Chinese, otherwise
  English) instead of hardcoded Chinese; an explicit language override still
  wins ([`8949f7e`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/8949f7e))
- The app's Dependencies page checks Node/npx and the recording engine
  (npx-pinned canonical path) instead of looking for a Screenpipe.app that the
  pipeline never uses
  ([`24aca9d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/24aca9d))
- `PUBLISHING.md` slimmed into provenance-only `docs/SANITIZATION.md`
  ([`d8f9083`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/d8f9083))

### Fixed

- Obsidian radar prompts now pass through the same redaction scrub as every
  other outbound channel, and all radar/executor prompts fence untrusted
  source material as data-not-instructions
  ([`1e99ce6`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/1e99ce6))
- The Obsidian radar marker is now a watermark: a note that fails extraction
  no longer silently advances the marker (the failure class behind the
  2026-07-08 incident); recovery rescans are idempotent
  ([`825eb30`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/825eb30))
- Ingest scripts resolve the Obsidian vault path through the config layer
  instead of a hardcoded location, with a cron-safe fallback
  ([`3afae87`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/3afae87))
- launchd plists render real paths at install time instead of shipping
  placeholders (fresh installs used to silently never start the daemon), and
  `install.sh` verifies each agent actually spawned
  ([`24aca9d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/24aca9d))
- Fresh installs actually start: `install.sh` renders the launchd plist
  placeholders (python path, repo root, log paths) before loading and verifies
  each agent really spawned, instead of copying template plists verbatim and
  failing silently
  ([`24aca9d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/24aca9d))
- Clones outside `~/Projects/zelin-ai-assistant` no longer split-brain against
  the GUI app: `install.sh` persists the repo root to a pointer file the app
  resolves (env var → pointer → legacy default)
  ([`24aca9d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/24aca9d))
- `install.sh` detects the Node/npx hard dependency of the capture engine
  ([`24aca9d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/24aca9d))

### Security

- Built-in secret-pattern masking (`sk-ant-`/`xox*`/`AKIA`/`gh*_`/PEM) is now
  **default-on** and controlled by its own `redaction_mask_secrets` switch,
  independent of the opt-in user term redaction. Previously `scrub()` returned
  early when `redaction_enabled` was false (the default), so on-screen API keys
  could leave the machine inside outbound prompts
  ([`2a84adf`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/2a84adf))

## [0.11.0] - 2026-07-09

### Added

- Opt-in Supabase telemetry sync: batched uploader tails
  `state/analytics/events.jsonl` and POSTs to a user-owned Supabase project;
  default off, local JSONL stays the source of truth (`docs/TELEMETRY.md`)
  ([`c896a84`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/c896a84))
- macOS `.pkg` installer for the full suite: app to `/Applications` plus a
  pipeline master copy with a postinstall that syncs it into the user's home
  and runs `install.sh --pkg-postinstall`; built by `mac/package.sh` and
  published as a release asset
  ([`6046f68`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/6046f68))
- Demo-data seeder `scripts/demo_seed.py`: fully fictional `dashboard.json`
  covering all card types and scenes for screenshots and demo video, no real
  data or API key needed (`docs/DEMO.md`)
  ([`e1f42ea`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/e1f42ea))

### Fixed

- Ingest falls back to claude CLI credentials when no API key file exists
  ([`72128fe`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/72128fe))
- CI runs on `macos-latest` with the newest installed Xcode selected
  ([`d8e9480`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/d8e9480))

## [0.10.3] - 2026-07-09

Initial public snapshot: sanitized export of the personal AI assistant — the
ingest pipeline (screenpipe → headless claude → Obsidian), the act pipeline
(radars → registry → approval cards → autonomous execution → review), and the
SwiftUI menu-bar app — plus the FSL-1.1-MIT license, `CONTRIBUTING.md`, CI and
release workflows
([`ef421de`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/ef421de)).

[Unreleased]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.12.0...HEAD
[0.12.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.10.3...v0.11.0
[0.10.3]: https://github.com/Wan-ZL/zelin-ai-assistant/releases/tag/v0.10.3
