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

The P0 wave of the open-source readiness review: make a fresh install work on a
clean Mac, default to privacy-safe behavior, and give first-time visitors
English docs plus privacy/security policies.

### Added

- `docs/PRIVACY.md`: full data-egress inventory — every channel that sends data
  off the machine (ingest cron chain, radars, quick capture, executor, telemetry)
  with trigger, frequency, payload, and off-switch, plus local retention and an
  execution-permissions section explaining `--dangerously-skip-permissions` and
  the `execution.skip_permissions` config
  ([`f72f7dd`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/f72f7dd))
- `SECURITY.md`: supported versions, private vulnerability reporting via GitHub
  advisories, response window, and explicit scope
  ([`f72f7dd`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/f72f7dd))
- First-launch recording consent: a fresh install no longer auto-starts screen
  capture; a one-time bilingual sheet explains what is captured and where it
  goes, with Screen Only / Screen + Audio / Not Now choices. Existing installs
  keep their stored mode
  ([`aed97a3`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/aed97a3))
- English `README.md` (badges, how-it-works, quickstart, features, license
  summary) with the Chinese original moved to `README.zh-CN.md`; hero
  screenshots and demo video under `docs/assets/`
  ([`c906f9d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/c906f9d),
  [`ac1d78e`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/ac1d78e))
- `docs/INSTALL.md` as the single authoritative install guide: prerequisites
  table with versions, numbered steps with expected-state checkpoints, exact TCC
  grant paths, and a "first card in 5 minutes" path; pitfalls collected into a
  symptom-first `docs/TROUBLESHOOTING.md`
  ([`c906f9d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/c906f9d))
- `.github/release.yml` release-notes category template
  ([`c906f9d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/c906f9d))

### Changed

- UI language now defaults to the system locale (`zh-*` → Chinese, otherwise
  English) instead of hardcoded Chinese; an explicit language override still
  wins ([`aed97a3`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/aed97a3))
- The app's Dependencies page checks Node/npx and the recording engine
  (npx-pinned canonical path) instead of looking for a Screenpipe.app that the
  pipeline never uses
  ([`13e3700`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/13e3700))
- `PUBLISHING.md` slimmed into provenance-only `docs/SANITIZATION.md`
  ([`c906f9d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/c906f9d))

### Fixed

- Fresh installs actually start: `install.sh` renders the launchd plist
  placeholders (python path, repo root, log paths) before loading and verifies
  each agent really spawned, instead of copying template plists verbatim and
  failing silently
  ([`13e3700`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/13e3700))
- Clones outside `~/Projects/zelin-ai-assistant` no longer split-brain against
  the GUI app: `install.sh` persists the repo root to a pointer file the app
  resolves (env var → pointer → legacy default)
  ([`13e3700`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/13e3700))
- `install.sh` detects the Node/npx hard dependency of the capture engine
  ([`13e3700`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/13e3700))

### Security

- Built-in secret-pattern masking (`sk-ant-`/`xox*`/`AKIA`/`gh*_`/PEM) is now
  **default-on** and controlled by its own `redaction_mask_secrets` switch,
  independent of the opt-in user term redaction. Previously `scrub()` returned
  early when `redaction_enabled` was false (the default), so on-screen API keys
  could leave the machine inside outbound prompts
  ([`c73f7c2`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/c73f7c2))

## [0.11.0] - 2026-07-09

### Added

- Opt-in Supabase telemetry sync: batched uploader tails
  `state/analytics/events.jsonl` and POSTs to a user-owned Supabase project;
  default off, local JSONL stays the source of truth (`docs/TELEMETRY.md`)
  ([`8fd3b33`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/8fd3b33))
- macOS `.pkg` installer for the full suite: app to `/Applications` plus a
  pipeline master copy with a postinstall that syncs it into the user's home
  and runs `install.sh --pkg-postinstall`; built by `mac/package.sh` and
  published as a release asset
  ([`b77ee3e`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/b77ee3e))
- Demo-data seeder `scripts/demo_seed.py`: fully fictional `dashboard.json`
  covering all card types and scenes for screenshots and demo video, no real
  data or API key needed (`docs/DEMO.md`)
  ([`d39581b`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/d39581b))

### Fixed

- Ingest falls back to claude CLI credentials when no API key file exists
  ([`9cf87bb`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/9cf87bb))
- CI runs on `macos-latest` with the newest installed Xcode selected
  ([`e05dc72`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/e05dc72))

## [0.10.3] - 2026-07-09

Initial public snapshot: sanitized export of the personal AI assistant — the
ingest pipeline (screenpipe → headless claude → Obsidian), the act pipeline
(radars → registry → approval cards → autonomous execution → review), and the
SwiftUI menu-bar app — plus the FSL-1.1-MIT license, `CONTRIBUTING.md`, CI and
release workflows
([`88a2141`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/88a2141)).

[Unreleased]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.11.0...HEAD
[0.11.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.10.3...v0.11.0
[0.10.3]: https://github.com/Wan-ZL/zelin-ai-assistant/releases/tag/v0.10.3
