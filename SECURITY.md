# Security Policy

This project (lite 2.0) records your screen and audio, turns the recordings
into Obsidian notes and meeting recaps with headless `claude -p` runs, stores an
Anthropic API key on disk and serves a local web UI on 127.0.0.1. We take reports
about any of that seriously. For the full data-flow and trust model (what leaves
your machine, when, and how to turn it off), see
[`docs/PRIVACY.md`](docs/PRIVACY.md) — this file only covers how to report
vulnerabilities and what is in scope.

## Supported versions

Only the **latest minor release line** receives security fixes. Older minors do
not get backports — please update to the latest release before reporting.

| Version | Supported |
| ------- | --------- |
| latest minor (see [Releases](https://github.com/Wan-ZL/zelin-ai-assistant/releases)) | ✅ |
| anything older | ❌ |

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

1. **Preferred:** GitHub private vulnerability reporting — go to the repo's
   [Security tab → "Report a vulnerability"](https://github.com/Wan-ZL/zelin-ai-assistant/security/advisories/new).
2. **Fallback:** email **wanzelin007@gmail.com** with subject
   `[SECURITY] zelin-ai-assistant: <short summary>`.

Include what you can: affected file/component, reproduction steps, impact, and
the version or commit you tested against.

**Response window:** you will get an initial response within **7 days**. We will
keep you updated as the report is triaged and fixed, and credit you in the fix's
release notes unless you prefer otherwise.

## Scope

**In scope** (examples, not exhaustive):

- **Secrets handling** — anything that exposes the contents of
  `config/secrets/anthropic-api-key.txt` (permissions, logging leaks, the key
  copy in `install.sh`); see `act/lib/secrets.py` and CONTRACT §19.
- **Isolation of the headless runs** (CONTRACT §78) — any way a headless
  `claude -p` started by this app loads or writes the owner's Claude Code
  configuration (`~/.claude`, `~/.claude.json`, hooks, plugins, MCP servers,
  project settings), runs outside `claude-config/cwd`, or runs without the
  isolation flags; any way the app itself reads or writes `~/.claude` beyond
  the two documented exceptions (install.sh unlinking its own 1.x skills
  links, the doctor listing them).
- **Resident footprint** — a LaunchAgent, cron line or login item installed
  outside the closed list in CONTRACT §78, or one that `uninstall.sh` leaves
  behind.
- **Local web surface** — bypasses of the server's loopback-only binding,
  token authentication or request limits (CONTRACT §49).
- **Egress masking** — bypasses of the built-in secret-pattern masking or the
  opt-in term redaction in `act/lib/sanitize.py` on a covered prompt boundary.
- **Prompt-injection paths with concrete impact** — screen or audio content
  reliably escalating into unintended tool actions or data exfiltration in the
  ingest or recap chain (the recap run has no tools at all; the ingest run has
  file tools scoped to the vault).
- **Undisclosed data egress** — any network flow of user data not listed in
  `docs/PRIVACY.md`.
- **Install / upgrade integrity** — `install.sh`, `scripts/upgrade.sh`,
  launchd or cron manipulation that a local unprivileged attacker could exploit
  (for example making the upgrader fast-forward to an untagged commit).

**Out of scope:**

- Vulnerabilities in third-party components themselves — the screenpipe engine,
  the `claude` CLI, Node/npx, macOS. Report those upstream.
- Attacks requiring an already-compromised local user account or physical
  access (the threat model assumes your Mac and account are yours).
- The **documented** design trade-offs in `docs/PRIVACY.md` — e.g. that screen
  capture is app-agnostic, or that the ingest agent can write into the vault.
  Reports that *bypass a documented control* (the ignored-apps list, redaction,
  the isolation flags) are in scope; reports that the trade-off exists are not.
- Social engineering, spam, or denial-of-service against your own machine.

## Hardening tips for users

Before deploying on a machine you care about, read
[`docs/PRIVACY.md`](docs/PRIVACY.md) and keep recording off until the
ignored-apps list covers the apps you consider sensitive. `bash install.sh
--check` reports the isolation footprint and whether the installed `claude`
supports the isolation flags.
