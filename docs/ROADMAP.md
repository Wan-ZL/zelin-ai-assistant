# Roadmap

Single source of truth for where this project is going. Extracted from [HANDOFF.md](../HANDOFF.md) §6
(which now just points here). Statuses reflect July 2026; issue links will be added as items get filed.
Before picking anything up, check the in-progress list — several items already have work in flight.

## In progress

- **`.pkg` installer + release automation** — one installer that lays down the app and the whole
  pipeline (launchd agents + cron chain); the `.pkg` itself has landed, release automation is being
  finished. *In flight — don't duplicate.*
- **Demo assets** — hero screenshots, flow GIF and demo video per the conventions in
  [docs/assets/README.md](assets/README.md); the hero set has landed, remaining screenshot slots
  (`t2-card`, `review-final-draft`, `trash`) are in flight.
- **Telemetry v2 — research loop** — v1 (Supabase sync of local analytics events; default-on with a one-click opt-out) has landed;
  v2 closes the loop: after ≥14 days of data the Monday digest proposes self-improvement cards
  ("this feature is unused", "this step keeps failing") — the new-install guard is already written.
- **iMessage channel** — a fourth capture/notification channel alongside Obsidian, Slack and Gmail;
  v1 has landed (text-only, `phone_channel: imessage`, see [docs/IMESSAGE_SETUP.md](IMESSAGE_SETUP.md)) —
  image/video capture still routes through the Slack path.
- **Swift strict-concurrency migration** — move the Mac app onto Swift 6 strict concurrency before
  the toolchain default flips make it urgent.
- **Developer ID signing & notarization** — a stable signing identity so upgrades stop resetting TCC
  permissions (today's ad-hoc signature changes every build; users right-click → Open).

## Next

- **iOS remote approver** (`ios/` is a placeholder) — approve and accept from the phone, with push
  notifications; the app stays a pure remote control over the same dashboard/inbox contract.
- **Structured deliverable manifests** — agents end a run by writing a `{branch, files[]}` JSON
  manifest so review cards can link artifacts instead of describing them in prose.

## Later

- **Radar confidence-routing parameters** — "hard but no deadline → debt parking lot" is a fixed
  rule today; make the thresholds tunable once there is enough data to justify settings.
- **Stable TCC helper binary** — split the TCC-sensitive capture path into a rarely-changing helper
  so main-app updates don't touch permissions; fallback plan if Developer ID signing doesn't happen.
