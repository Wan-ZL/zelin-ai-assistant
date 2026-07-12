# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Bumping the version

`__version__` in [`act/__init__.py`](act/__init__.py) is the **single source of
truth** for the project version. `mac/build.sh` stamps it into the app bundle's
`Info.plist` at build time, and `mac/package.sh` reads it for the `.pkg` — no
other file needs editing. To cut a release:

0. Pick the bump: **patch** for bug fixes / small UX corrections / docs,
   **minor** for new user-visible features (pre-1.0, breaking changes also
   ride a minor with a `!` commit marker and a prominent changelog callout).
   See CONTRIBUTING.md "Versioning".
1. Bump `__version__` in `act/__init__.py`.
2. Rename the `[Unreleased]` section below to `[X.Y.Z] - YYYY-MM-DD` and add a
   fresh empty `[Unreleased]` heading above it; update the compare links at the
   bottom.
3. Commit (`chore: bump version to X.Y.Z`) and tag `vX.Y.Z`; pushing the tag
   triggers the release workflow.

## [Unreleased]

(nothing yet)

## [0.22.0] - 2026-07-12

### Added

- **Multi-card merge selection now works across all board lanes.** The
  merge-selection affordance is no longer limited to a single lane — you can
  select cards in 储备 / 提案 / 运行中 / 待验收 / 已验收 and request a merge
  proposal across them (legality of cross-status merges stays with the backend
  `merge_review`).
- **Running cards now stop into review instead of vanishing.** The single
  「停止」 on a running card opens a 退回提案 / 去待验收 choice:
  `stop_to_review` stops the agent but **keeps what it produced** and lands the
  card in 待验收 for you to check, instead of discarding the run (退回提案 /
  `abort_execution`) or skipping review entirely.

### Changed

- **Backlog lane renamed 备选 → 储备** (and its proposal defer button
  存备选 → 入库) — a display rename of the former debt/backlog lane; the
  underlying `defer` action and `detected` status are unchanged.
- **Proposal decision buttons are back to one compact row** (批准 · 拒绝 ·
  修改 · 入库), with 展开 demoted to a right-aligned disclosure link rather than
  competing as a fifth button.

### Known limitation

- Stopping an agent **externally** inside Claude Code can still trigger
  auto-resume; use the in-app 「停止」 button to reliably land the card in
  待验收 (follow-up tracked).

## [0.21.0] - 2026-07-12

### Added

- **Settings redesign — collapsible sections + fuzzy search.** Settings is now
  organized into collapsible sections (**default collapsed**) with a search box
  that fuzzy-matches setting names and reveals the matching sections, so the
  growing list of integrations stays scannable.

### Removed

- **⚠️ iMessage transport removed, and Slack's phone-approval commands /
  reactions removed** — a user-visible capability removal. Dropped: the iMessage
  radar (`act/radar_imessage.py` + its launchd agent), the `phone_channel` /
  `imessage_self_handle` config, all outbound notification mirroring to the
  Slack self-DM, the `批准/拒绝/打回/验收 R-xxx` phone command surface, and the
  ✅-reaction approval poll. Upgrades auto-unload the stale `imessageradar`
  launchd agent.
  - **Mobile approval now happens in the Mac app** — it is the sole approval
    surface (a dedicated **iOS app is planned**). Migration: approve/accept
    cards in the Mac app.
  - **Slack self-DM QUICK-CAPTURE is KEPT** — only the phone-approval commands
    were removed. DM yourself a one-liner (or a photo/video) and it still
    triages into a card; self-DM is now a one-way capture inbox (the assistant
    no longer posts replies or notifications back into it) and remains the
    mobile-capture path until the iOS app ships. Slack ingest (DMs / group DMs /
    @mentions + MCP fallback) is unchanged.

### Changed

- **Permissions: Full Disk Access row repurposed for scheduled jobs.** With the
  iMessage radar gone, the FDA capability row no longer references Messages; it
  now explains that Full Disk Access is for scheduled background jobs
  (cron/launchd) reading protected data while the app isn't open.

## [0.20.1] - 2026-07-12

### Fixed

- The first-run finale coach-mark ("我在这里 👆") now points at the menu-bar
  icon instead of floating in the middle of the screen over the Settings
  window. It fires from a delayed dispatch after the wizard window closes, so
  the menu-bar-only app was no longer active and the transient popover failed
  to attach to the status item — the app is now re-activated before the bubble
  is shown so it anchors under the menu-bar icon where the assistant lives.
- Radar scan analytics now count re-raised cards: the `new_cards` field of the
  `radar_scan` event undercounted passes where LLM triage re-raised an
  already-accepted card (kind `"reraised"`, added in v0.20.0) back into
  proposals. Gmail, Slack and Obsidian radars all include it now.

## [0.20.0] - 2026-07-11

Card lifecycle: thread-level matching + an `archived` sealed state + re-raise
of already-accepted cards. A thing you already accepted no longer silently
spawns a duplicate backlog card when related info arrives — it comes back to
your proposals; only after you archive it does later info open a fresh card.

### Added

- **Re-raise (prior acceptance = ownership)**: when new *actionable* info
  matches an un-archived completed (`delivered`/`merged`) card, the original
  card flips back to a proposal (`card_sent`) instead of a new backlog card —
  source folded, `repeated_mentions` bumped, `execution.reraised_at`/
  `reraised_note` stamped, summary appended with "· 新增:…". A hit on the same
  email/Slack thread but a *different* task opens a distinct follow-up child
  (inheriting the thread lineage) without polluting the old card. Both the
  deterministic (`merge_or_new`) and LLM (`apply_triage`/self-DM quick capture)
  paths share `registry.reraise_or_followup`. Pure restatements / `needs_action=
  false` only bump the count — they never flip (Q3: flip on new actionable
  content only). Re-raised proposals carry a `reraised` flag + note in the
  dashboard (app shows an amber "↩︎ Returned" badge) and notify via
  `notify.msg_reraised`.
- **Thread-level matching**: cards gain `thread_id` (grouping anchor, reuses
  the `R-` namespace) and `thread_key` (a strong deterministic bucket from an
  external thread ref only — `gmail:<X-GM-THRID>` / `slack:<thread_ts>`, else
  None, never fuzzy; `registry.derive_thread_key`). `merge_or_new` prefers a
  `thread_key` match, then the legacy title heuristic. The triage/capture LLM
  inventory is capped but HARD-PINS all non-archived delivered/merged cards so
  re-raise recall can't silently fail.
- **`archived` state + `archive`/`unarchive` inbox actions**: seal a completed
  card from 已验收 (`delivered`) or 备选 (`detected`) (Q2). Archived cards
  RELOCATE to `act/registry/archive/` (out of the hot scan, #10), are excluded
  from matching, hidden from the LLM, and NEVER purged; `unarchive` restores the
  prior status and moves the file back. New dashboard partition `archived[]`
  (+ `counts.archived`); archived cards enter no kanban lane.
- **`archive_stale` auto-archive of cold delivered matters (#10) — DEFAULT OFF**
  (`archive_after_days=0`). When enabled it runs at most once per 24h and skips
  cards with a future deadline or a live sibling in their cluster — so a
  long-silent immigration/EB-1A matter is never auto-sealed (which would let new
  mail re-open a duplicate).

### Fixed

- **id-collision data-loss guard**: `next_id()` and `load()` now scan the
  archive subdir, so a freshly allocated id can never overwrite an archived card
  (the highest-risk failure of the relocate model).

### Changed

- **MERGED / delivered card behavior (visible change)**: a restatement carrying
  a new actionable ask on a `delivered`/`merged` card now RE-RAISES the original
  card back to a proposal, rather than silently absorbing it (previously merged
  duplicates just bumped the count). Pure restatements are unchanged (bump only).

## [0.19.2] - 2026-07-11

The first stably-self-signed release: release builds now carry a constant
code-signing identity, so macOS keeps its permission grants across updates.

### Changed

- **Release builds are now signed with a stable self-signed code-signing
  identity, so macOS Screen Recording (and other TCC) permissions persist
  across app updates** instead of re-prompting on every version. A one-time,
  idempotent maintainer script (`mac/scripts/make-signing-cert.sh`) creates a
  free (non-notarized) `Zelin AI Engineer Dev` identity and wires it into CI
  via two secrets (`MACOS_SIGN_CERT_P12`, `MACOS_SIGN_CERT_PASSWORD`); the
  release workflow imports it into a throwaway keychain before building
  (guarded — absent secret ⇒ ad-hoc fallback, still builds) and fails loudly on
  a misconfigured secret. With signing configured the app's Designated
  Requirement stays constant across versions, so the grants no longer reset.
  One-time transition: the first stably-signed update re-prompts for Screen
  Recording once, then never again. Gatekeeper first-open is unchanged —
  self-signed is not notarized.

### Fixed

- **Detect the untrusted self-signed identity correctly** (`mac/build.sh`,
  `.github/workflows/release.yml`, `mac/scripts/make-signing-cert.sh`): the
  `Zelin AI Engineer Dev` cert is self-signed and therefore untrusted
  (`CSSMERR_TP_NOT_TRUSTED`), so `security find-identity -v` (valid/trusted-only)
  would hide it even though it signs fine and yields a stable cert-based
  Designated Requirement. Dropping `-v` from the identity probes makes the build,
  the CI import verification, and the cert script's idempotency guard all detect
  the untrusted-but-usable cert (the guard would otherwise miss an existing cert
  and create a duplicate CN). `make-signing-cert.sh` also supports
  non-interactive setup, using `$KEYCHAIN_PW` for the key partition list instead
  of prompting when it is set.

## [0.19.1] - 2026-07-11

Patch release: a voice-profile cleanup plus two follow-ups from the v0.19.0
review — no new user-visible features.

### Changed

- **Voice-profile default drops the vulgar example**
  (`config/voice-profile.default.md`): the shipped author's-voice layer keeps
  all of its rules and register, but the illustrative Chinese chitchat line no
  longer uses a crude interjection — softened to a clean casual exclamation
  that makes the same point.
- **Usage-insights abandonment table excludes once-per-install milestones**
  (`scripts/insights_report.py`): the "used exactly once" / abandonment view no
  longer counts the milestone / first-reach events (`milestone_first_card`,
  `milestone_first_approval`, `milestone_first_delivery`, `feature_first_reach`)
  that are used exactly once *by construction* and were drowning out the real
  tried-then-dropped signal.
- **De-duplicated the insights `**Totals:**` line**: it was emitted both in the
  main body and again inside the `<details>` appendix — now emitted once, in the
  main body (the no-change gate still greps it via `head -n1`).
- **Hardened the Slack MCP probe** (`act/radar_slack.py`): `_probe_slack_mcp`
  now wraps its imports and `_claude_bin()`/`_runner_env()` arg-eval inside the
  guard too, so any exception (not only `OSError`/`SubprocessError`) degrades to
  "not present / `mcp_not_configured`" instead of escaping into the radar scan.

## [0.19.0] - 2026-07-11

Diagnose-and-fix, then measure: the board now surfaces the ingest paths that
are silently failing with a one-tap fix, and a lifecycle funnel replaces raw
event counts in the usage insights report.

### Added

- **Board diagnostic cards** (`mac/Sources/Diagnostics.swift`): when an ingest
  path you've configured is silently failing, the task board / popover now
  synthesizes a plain-language diagnostic card — one sentence naming the
  problem, one primary button that jumps straight to the fix. Cards only show
  for paths you've actually set up (never noise), are dismissable, and vanish
  on their own once the path recovers. Composed Swift-side from
  `state/radar_health.json`; no new `dashboard.json` partition.
- **Obsidian radar health tracking**: the Obsidian radar now writes an
  `obsidian` entry into `state/radar_health.json` (same shape as gmail/slack),
  but **only** from the cron ingest chain (`AIASSISTANT_CRON=1`) — gated by
  `radar._owns_health()` so a TCC-blocked launchd context or a manual run can
  never stomp cron's good health with a fake-empty vault. Entries carry an
  optional `last_cards` count and a `skip_reason` vocabulary (`disabled` /
  `vault_missing` / `vault_empty` / `no_api_key` / `extract_failed`).
- **Slack `mcp_not_configured` diagnosis** (B4): Slack radar health tells the
  actionable "fallback is on but there's no token and the claude CLI has no
  Slack MCP" case apart from a transient `mcp_failed:` error, via a
  `claude mcp list` pre-check cached in `state/slack_mcp_present.marker`.
- **Lifecycle / activation-funnel telemetry milestones** (metadata only,
  at most one per install; docs/TELEMETRY.md): `feature_first_reach` for
  `app_launch` (first launch) and `ingest_configured` (first ingest source
  live) on the app side; daemon-side `milestone_first_card`,
  `milestone_first_approval`, and `milestone_first_delivery` fired once each
  through a single choke point (`registry.save`, actd approve, executor
  dispatch). Behavior fields only (`req` id / counts) — no card titles, links,
  or summaries — reusing the existing `analytics.content_gate` privacy
  boundary with no schema migration.
- **Rewritten Usage Insights report** (`scripts/insights_report.py`): instead
  of raw event counts it now reports an activation funnel
  (launch → ingest configured → first card → first approval → first delivery),
  reliability, abandonment, and retention (by `client_ts`) — aggregate
  counts / ratios only, device ids never leak, anonymous devices merged across
  all installs.

## [0.18.1] - 2026-07-11

Patch release: bug fixes, one cleanup, and an honesty correction to the config
docs — no new user-visible features.

### Fixed

- **Proposal card action row no longer truncates**: the 存备选 (defer) button
  was being clipped on narrower cards; the action row is restructured into a
  primary/secondary hierarchy so every button stays reachable
  ([`3428413`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/3428413))
- **Gmail radar now goes through the unified triage gate**
  (`act/radar_gmail.py`): it was the only radar still filing every extracted
  item straight into the 提案 lane as a `card_sent` proposal, bypassing the
  shared `quick_capture.triage` / `apply_triage` gate the Slack and Obsidian
  radars use. Gmail candidates now get the same three-way decision — new
  proposal (提案, or 备选 when the ask is real but not urgent), fold into a
  related open card, or ignore pure-FYI mail — gaining ignore / relates_to /
  improvement_of lineage. The existing UID-marker dedup is untouched.

### Removed

- **Retired the redundant Obsidian radar launchd agent**
  (`act/launchd/com.zelin.aiassistant.radar.plist`): it was TCC-blocked from
  `~/Documents` and only ever saw an empty vault. The Obsidian radar already
  runs from the crontab ingest chain (`python3 -m act.radar --once`, every
  30 min); `install.sh` now unloads and removes any previously-installed copy
  on upgrade, and the next-steps output + `docs/PRIVACY.md` no longer reference
  the launchd agent.

### Changed

- **Honest `watch_people` docs** (`config.example.yaml`): the comment used to
  promise these people's messages/meetings "trigger extraction", but no radar
  reads the list to filter what gets extracted — it only derives the
  tracked-requester display name (first entry, `config.requester_display`) and
  drives the Settings people picker. The comment now says so plainly and notes
  it is **not** a hard filter, so no one's messages are dropped for being off
  the list.

## [0.18.0] - 2026-07-11

Board redesign, a defer verdict, and — the big one — richer, honest-by-default
telemetry.

### Added

- **「存备选」— defer a proposal to the backlog** (`docs/CONTRACT.md` §10):
  a fourth on-card button sends a proposal to the backlog instead of
  approving or rejecting it; unlike reject (which trashes and kills
  dedup-matching), defer keeps the card matchable so the radar can merge
  future mentions. One click, undo via the backlog's 研究并提议
  ([#34](https://github.com/Wan-ZL/zelin-ai-assistant/pull/34))
- **Lane definitions**: every board lane header gets a `?` icon — click for
  a popover explaining what the lane means, hover for a tooltip — plus
  empty-state copy that teaches the lane when it has no cards
  ([#33](https://github.com/Wan-ZL/zelin-ai-assistant/pull/33))
- **Richer behavior telemetry (metadata only, default-on)**: new events
  `mw_section_dwell` (per-page dwell), `mw_setting_change` (which settings
  key changed — never the value), `board_search` (query length only),
  `feature_first_reach` (once-per-install feature reach); new metadata
  fields `dispatch.wait_s`, `review_promoted.exec_s`, `rework_launch.round`,
  `radar_scan.secs`, comment/typed-length counters. Full list in
  docs/TELEMETRY.md.
- **`telemetry.capture_input` (default ON, together with the new default
  `level: detailed`)**: telemetry now includes the text you type into the
  app — captures, Ask questions, card comments / rework feedback, board
  search terms — each clipped to 500 chars. The first-run disclosure and all
  docs say so plainly; a dedicated Settings toggle ("上传我输入的文本 /
  Upload the text I type") turns just the text off while keeping anonymous
  behavior stats. Hard scope boundary at any setting: never the AI's
  answers, screen-recording content, email or Slack/iMessage message bodies,
  or secrets — radar-extracted third-party content never enters telemetry.
  The double gate (capture_input AND detailed) is enforced emit-side in both
  Python and Swift and locked by tests (tests/test_telemetry_level.py,
  including an honesty drift-guard on the disclosure copy).
- Capacity budget section in docs/TELEMETRY.md (Supabase free-tier headroom
  + archival guidance).
- Adversarial-review hardening of the content pipeline: dispatch.instruction
  is provenance-gated (user-capture-origin cards only, title only — radar
  cards summarizing third-party mail/messages/screen send no instruction at
  all); a v2 consent marker gates content for upgraded installs (behavior
  telemetry keeps the old marker, typed text waits for the new disclosure
  to render or an explicit capture_input); every content field passes an
  unconditional secret masker (mirrored in Swift, drift-guarded) before
  hitting the local log; media quick-captures record only the typed words,
  never the synthetic image prompt or local file paths.

### Changed

- **Board lane order is now backlog-first**: 备选 | 提案 | 运行中 | 待验收 |
  已验收 (the backlog pool sits upstream, left of proposals, with a quieted
  header so proposals still draw the eye), and the 已验收 lane's English
  label is now "Done"
  ([#33](https://github.com/Wan-ZL/zelin-ai-assistant/pull/33))
- **`level: detailed` no longer attaches any content by itself** (previously
  ≤200-char instruction/delivery/question summaries) — content is controlled
  by the separate `capture_input` switch; level only sets behavior-event
  granularity (and basic also switches text capture off).
- First-run telemetry consent is now a one-line honest disclosure — it
  states that typed text is included by default — with a "Details & opt-out
  in Settings" link (the toggles live in Settings → Product improvement
  program, same override key; the `telemetry_consent` event retired with
  the old checkbox).

### Fixed

- Attaching to a review-lane card's session (the v0.17.1 double-click
  `claude attach`) is no longer misreported as a rework round: the card now
  stays in the review lane with a calm "会话有新活动 / Session active" badge
  (new optional `review[].session_active` field, CONTRACT §30) instead of
  jumping to the running lane as「验收后返工中」— no 打回 verdict ever
  happened. Genuine rework rounds are untouched, and the periodic re-harvest
  of deliverables from attach conversations is kept

## [0.17.1] - 2026-07-11

### Fixed

- Double-click terminal launch opens a new **tab** of the existing Ghostty
  window instead of a separate window, and `--install` swaps the running
  app seamlessly
  ([#32](https://github.com/Wan-ZL/zelin-ai-assistant/pull/32))
- Clicking outside a text field (board search, proposals composer, Ask,
  popover capture…) or pressing Esc now dismisses the caret — drafts are
  never lost, and Esc on a non-empty search clears the filter first
  ([`e790f7a`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/e790f7a))

### Changed

- CONTRIBUTING/CHANGELOG now codify the versioning rule: patch = fixes and
  small UX corrections, minor = new user-visible features; merging a PR does
  not build an installer — cutting a release does
  ([`789e7c6`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/789e7c6))

## [0.17.0] - 2026-07-10

### Added

- **Unified radar triage gate** — every radar candidate (Slack native, Slack
  MCP sweep, Obsidian notes, self-DM quick capture) now passes one three-way
  gate before filing: act-now proposal, lineage follow-up on a
  delivered/merged ancestor (one per cluster, deduped across passes and
  sources), fold-into-open-card note, backlog demotion for real-but-not-urgent
  items, or ignore for pure-FYI. The proposals lane now strictly means "needs
  the owner's action or decision now"
  ([`807f90a`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/807f90a))
- **Voice-first default** — the author's sanitized voice profile ships as the
  repo default for any text drafted in the owner's name, plus a Settings
  「语气档案 / Voice profile」 group: live status row, master switch
  (`voice.enabled`), open-profile button, and one-click generation of your own
  private profile from your sent Slack messages (`python -m act.voice_gen`;
  read-only MCP tools, automatic backup, never overwrites on failure)
  ([`6cac752`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/6cac752))
- **Board search (⌘F)** — local keyword filter across all lanes; the 备选 lane
  is now labelled **备选 · Backlog**
  ([`807f90a`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/807f90a))
- **Card feedback channel** — pick cards on the board, describe what looked
  wrong, and the report is saved under `state/feedback/` (and uploaded to your
  own Supabase when configured — see `docs/PRIVACY.md`)
  ([`807f90a`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/807f90a))

### Fixed

- **Session binding** — cards only carry a `claude --resume` command when the
  transcript really belongs to them: sessionId is validated against the
  transcript itself, and an empty session id no longer globs the whole
  transcript dir and grabs the alphabetically-first session. Double-clicking a
  card's terminal command now bootstraps PATH so `claude` resolves under a
  fresh shell ([`807f90a`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/807f90a))
- The shipped `R-000-example.yaml` is documentation and never loads as a real
  card (it used to surface in the backlog lane on every fresh install)
  ([`6cac752`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/6cac752))

## [0.16.0] - 2026-07-10

### Added

- **Always-visible update row** in About: current version, an honest status
  line ("已是最新（上次检查：X 分钟前）", failure and disabled states
  included), and a **立即检查 / Check now** button that bypasses the daily
  budget (`python3 -m act.lib.update_check --force`); the privacy switch
  still wins — when auto-check is off, the button never fires a request
  ([`0ff44a8`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/0ff44a8))
- **Contributors wall** in both READMEs with a good-first-issue pointer —
  celebrating the project's first external contributor
  ([`a59c7ba`](https://github.com/Wan-ZL/zelin-ai-assistant/commit/a59c7ba))

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

[Unreleased]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.22.0...HEAD
[0.22.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.21.0...v0.22.0
[0.21.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.20.1...v0.21.0
[0.20.1]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.20.0...v0.20.1
[0.20.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.19.2...v0.20.0
[0.19.2]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.19.1...v0.19.2
[0.19.1]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.19.0...v0.19.1
[0.19.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.18.1...v0.19.0
[0.18.1]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.18.0...v0.18.1
[0.18.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.17.1...v0.18.0
[0.17.1]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.17.0...v0.17.1
[0.17.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/Wan-ZL/zelin-ai-assistant/compare/v0.10.3...v0.11.0
[0.10.3]: https://github.com/Wan-ZL/zelin-ai-assistant/releases/tag/v0.10.3
