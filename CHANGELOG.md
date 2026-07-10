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

## [0.15.0] - 2026-07-10

Voice, speed, and a milestone: the first external contribution.

### Added

- **Voice profile two-level fallback** ([docs/VOICE.md](docs/VOICE.md)):
  drafts written in the owner's name follow `state/voice-profile.md`
  (private, gitignored) when present, else the neutral
  anti-assistant-register starter template that now ships at
  `config/voice-profile.default.md`; the template is nobody's voice
  (empty example buckets, fingerprint-guard test) and the prompt injection
  no longer hardcodes a personal name
  ([`7329157`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/7329157))
- **Double-click to run in your terminal**: a card's copyable
  `claude attach` / `--resume` command now runs on double-click in a new
  window of your terminal of choice (Ghostty via its AppleScript
  dictionary, Terminal, iTerm2 when installed — pick in Settings →
  General); single click still copies. First run asks for the standard
  macOS Automation consent
  ([`a2c99ac`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/a2c99ac))

### Fixed

- `dispatch_failed` analytics event now fires exactly once per launch
  failure (and backoff-window passes no longer emit noise events);
  unexpected crashes are tagged `dispatch_crashed` — the project's first
  external contribution, thanks @tapheret2!
  ([#24](https://github.com/Wan-ZL/zelin-ai-assistant/pull/24), closes
  [#12](https://github.com/Wan-ZL/zelin-ai-assistant/issues/12))

## [0.14.0] - 2026-07-10

The novice-friendliness release: a guided setup wizard, one-click repair on
every failure, in-app Q&A, fully in-app Slack/Gmail/iMessage setup, and a
`.pkg` that ends with a *live* product — plus the first self-improvement
loops (weekly digest, daily usage insights) and Windows/Linux porting
groundwork. Standard: the happy path never requires YAML, Terminal, or docs.

### Added

- Six-step first-run **setup wizard**: language, AI-engine detection with
  paste-and-verify API key, permissions, screen-only recording consent,
  Obsidian vault picker (reads the Obsidian registry), and a live
  health-check finale with fix buttons and a menu-bar "I live here" bubble;
  re-runnable anytime from Settings
  ([`128d400`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/128d400))
- **AI Doctor** (`docs/CONTRACT.md` §25): a failure-classification catalog
  shared by python and the app, one-click in-app repair buttons on every
  banner that used to print raw `launchctl` commands, auto-run diagnostics,
  a real cron Full-Disk-Access probe, and a **Fix with AI** button that opens
  Terminal on an interactive claude session pre-loaded with a scrubbed
  diagnostic bundle
  ([`0527f4d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/0527f4d))
- **In-app Q&A "问问助手 / Ask"** (§27): ask anything about the product;
  answers are grounded in the docs and this Mac's real state via one
  tool-less headless claude call, with history, feedback, and honest
  disclosure of what is sent where
  ([`d522624`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/d522624))
- **Claude Code session import** (§22): scan recent local sessions, preview
  with waiting-on-you badges, and import selected work as proposal cards —
  no more empty board on day one
  ([`c52bcc4`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/c52bcc4))
- **In-app update check** (§26): daily ETag-cached GitHub releases query, a
  low-key menu line and About-page download row, Settings toggle
  ([`0154430`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/0154430))
- **Slack & Gmail fully in-app** (Settings): copy-manifest button, token
  paste verified via `auth.test` with identity autofill, channel and people
  pickers; Gmail guided app-password card with in-UI address field —
  `config.yaml` is gone from both happy paths
  ([`202d1f5`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/202d1f5))
- **Weekly digest** (§24): a "what you worked on this week" recap card plus
  2-3 automation-suggestion proposals mined from the week's ingest
  ([`2193ced`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/2193ced))
- **Usage-insights loop**: a GitHub Action aggregates telemetry into one
  pinned issue (aggregates only), with optional Claude analysis; runs daily
  and skips no-change days
  ([`82c79e9`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/82c79e9),
  [`aee705c`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/aee705c))
- **Install lifecycle**: the `.pkg` now ends with a *live* product — launchd
  agents loaded, the app launched, `state/install_report.json` written
  (§23); launch-at-login defaults on; a real `uninstall.sh` (with
  `--dry-run` and `--purge`) plus an About-page uninstall entry; the release
  notes spell out the unsigned-pkg right-click-Open steps
  ([`785979b`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/785979b),
  [`e02cd1f`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/e02cd1f),
  [`501adc5`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/501adc5),
  [`ac539d1`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/ac539d1))
- **Recording robustness**: the engine restarts itself the moment Screen
  Recording permission lands, engine death is diagnosed in plain language
  (including the "npm is downloading screenpipe, 1-3 minutes" first-run
  state), and a lost TCC grant after a macOS update is detected with a calm
  re-grant flow
  ([`a6a3b06`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/a6a3b06))
- **Windows/Linux porting groundwork**: an OS seam for service control and
  notifications, `docs/PORTING.md` with a component-by-component map, an
  ubuntu CI lane keeping the core genuinely portable, and a README platform
  matrix
  ([`f4c346d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/f4c346d),
  [`f31cb2b`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/f31cb2b))

### Changed

- Kanban lane renamed: 待审批 → **提案** / "Needs approval" → "Proposals"
  ([`6dfe56f`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/6dfe56f))
- Settings now **persist on change** with diff-writes (unrelated saves can no
  longer clobber `config.yaml` values), credentials are **verified on save**
  (Slack `auth.test`, Gmail IMAP probe, spaces stripped), and the task
  working folder has a picker with auto-create instead of a dead placeholder
  ([`6dfe56f`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/6dfe56f))
- One **"Obsidian Vault 位置"** field replaces the four pipeline-directory
  fields (they derive automatically; `config.yaml` overrides remain for
  experts), and the global-hotkey Settings group is gone
  ([`fa92120`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/fa92120))
- System notifications now display under the **"Zelin's AI Assistant"
  identity** (§28): relayed through `state/notify_queue/` and posted by the
  app; the osascript / Script Editor path is gone entirely, so native
  notifications require the running app (it auto-starts at login; phone
  mirrors unaffected); bursts cap at 5 with a "+N more" summary, backlog
  older than 10 minutes is dropped, clicking opens the main window
  ([`591705f`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/591705f),
  [`76bae6f`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/76bae6f))
- Every headless-claude call site resolves the **claude binary the login
  shell uses** (install-time PATH pinning, runtime `execution.claude_bin`
  pin, doctor check for duplicate/outdated installations, and a classified
  plain-language dispatch-failure reason) — fixes dispatches failing with
  `unknown option '--bg'` when an old npm-global claude shadowed the real one
  ([`997485c`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/997485c))
- Telemetry quality: the version stamp is applied at the **writer level** on
  both sides, action events carry ok/fail outcomes, and a `merge_apply`
  outcome event makes failed merge applies visible
  ([`dd7ca03`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/dd7ca03));
  consent and collection-level copy now state exactly what is sent where,
  and the privacy egress inventory covers every channel including the weekly
  digest, Ask, and Fix-with-AI
  ([`151661a`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/151661a))

### Fixed

- Meeting action-items **backfill storm**: an unconfigured install could
  back-process months of historical notes in one evening into a placeholder
  directory with one notification each; fixed with a placeholder-path guard,
  notification coalescing, and a whole-pass radar reentry lock
  ([`aa8e8d1`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/aa8e8d1),
  [`026d83f`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/026d83f))
- Linux CI lane healed: shellcheck SC2086, uninstall dry-run portability,
  swiftc type-check timeout
  ([`93de626`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/93de626))

### Removed

- The **manager pack** meeting action-items feature: unconfigurable in
  practice (a placeholder degenerated into matching nearly every note) and
  too narrow to be universal; the concept returns as a per-person
  commitments ledger
  ([#23](https://github.com/Wan-ZL/zelin-ai-assistant/issues/23)).
  `features.manager_pack` is now ignored
  ([`b26f188`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/b26f188),
  [`e7a5816`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/e7a5816))

## [0.13.0] - 2026-07-09

Card workflow power-ups (merge review, done-outside, voice profiles) plus the
first big novice-friendliness wave: a first-run permissions page, screen-only
recording by default, fully in-app iMessage setup — and anonymous usage
telemetry that now defaults to on behind an explicit first-run consent surface.

### Added

- Multi-select merge review: select two or more cards, an AI pass suggests
  merge / link-improvement / keep-separate / close-secondary with reasoning,
  and the human verdict is applied deterministically; new terminal `merged`
  registry state that still absorbs restatements (`docs/CONTRACT.md` §21)
  ([`5e00555`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/5e00555))
- "Done outside" exit for approved and executing tasks: harvest the
  transcript best-effort, stop the lingering session, deliver — no more cards
  stranded behind a blocked agent
  ([`892da54`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/892da54))
- Optional voice profile: when `state/voice-profile.md` exists, dispatched
  agents are told to match the owner's writing style for drafts in their name
  — and to treat the file strictly as style guidance
  ([`b321061`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/b321061),
  [`e922df2`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/e922df2))
- First-run permissions & setup page: live status rows for Screen Recording,
  Notifications, and Full Disk Access (marked iPhone-channel-only) with
  one-click grants and plain-language explanations; reopenable anytime from
  the App menu, the status-item menu, or Settings
  ([`ba6d58d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/ba6d58d))
- Fully in-app iMessage (iPhone) setup in Settings: an enable toggle that
  writes config and loads/unloads the launchd radar, handle validation, live
  health rows with plain-language skip reasons, guided Full Disk Access steps
  with a copyable python path, and one-click test rounds / test messages
  ([`d6eebbd`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/d6eebbd))
- Anonymous usage telemetry with `basic` / `detailed` collection levels
  (`detailed` is opt-in and adds short instruction summaries), a Settings
  "product improvement program" section, and an anon INSERT-only Supabase
  policy so the shipped key can write but never read
  ([`e24e5cd`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/e24e5cd))
- Supabase keepalive workflow so the maintainer's free-tier telemetry project
  is never paused for inactivity
  ([`5f4738e`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/5f4738e))

### Changed

- **Recording defaults to screen-only.** First run asks a single on/off
  consent; audio capture moved to an explicit opt-in in Settings
  ([`ba6d58d`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/ba6d58d))
- **Telemetry now defaults to on** — behind a consent door: nothing uploads
  until a consent surface has been shown (or telemetry is explicitly
  configured), and opting out is one click on first run or in Settings; see
  `docs/TELEMETRY.md`
  ([`e24e5cd`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/e24e5cd),
  [`5854726`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/5854726))

### Fixed

- Merge review persists the primary card's absorbed data before marking each
  secondary as merged, so a mid-merge crash can no longer lose it
  ([`d32d491`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/d32d491))
- Repaired CHANGELOG prose corrupted by the history-rewrite text replacement,
  including the inverted `create_github_repo` migration note
  ([`13b64a9`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/13b64a9))

### Removed

- Dependabot version-update PRs; actions stay SHA-pinned and manually reviewed
  ([`5f4738e`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/5f4738e))

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

[Unreleased]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.15.0...HEAD
[0.15.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.10.3...v0.11.0
[0.10.3]: https://github.com/Wan-ZL/zelin-ai-assistant/releases/tag/v0.10.3
