# Security Policy

This project records your screen, reads your Slack and Gmail, stores API keys on
disk, and dispatches autonomous agents that bypass permission prompts. We take
reports about any of that seriously. For the full data-flow and trust model
(what leaves your machine, when, and how to turn it off), see
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
  `config/secrets/` (token files, permissions, logging leaks); see
  `act/lib/secrets.py` and CONTRACT §19.
- **Egress masking** — bypasses of the built-in secret-pattern masking or the
  opt-in term redaction in `act/lib/sanitize.py` on a covered prompt boundary.
- **Executor boundary violations** — ways a dispatched agent escapes the
  documented model beyond what `docs/PRIVACY.md` already discloses (e.g. the
  `execution.skip_permissions: false` mode not actually restoring permission
  prompts, or worktree isolation writing outside the target repo).
- **Prompt-injection paths with concrete impact** — third-party content (a
  received email, a Slack message, screen content) reliably escalating into
  unintended tool actions or data exfiltration in the ingest/radar/executor
  chain.
- **Undisclosed data egress** — any network flow of user data not listed in
  `docs/PRIVACY.md`.
- **Install-time integrity** — `install.sh` / launchd / cron manipulation that
  a local unprivileged attacker could exploit.

**Out of scope:**

- Vulnerabilities in third-party components themselves — the screenpipe engine,
  the `claude` CLI, Node/npx, macOS. Report those upstream.
- Attacks requiring an already-compromised local user account or physical
  access (the threat model assumes your Mac and account are yours).
- The **documented** design trade-offs in `docs/PRIVACY.md` — e.g. that screen
  capture is app-agnostic, or that `--dangerously-skip-permissions` is the
  default dispatch mode. Reports that *bypass a documented control* (a feature
  flag, redaction, `skip_permissions: false`) are in scope; reports that the
  trade-off exists are not.
- Social engineering, spam, or denial-of-service against your own machine.

## Hardening tips for users

Before deploying on a machine you care about, read
[`docs/PRIVACY.md`](docs/PRIVACY.md) — in particular the "执行权限 / Execution
permissions" section (approval is the security boundary) and the available
off-switches (recording mode, per-radar feature flags,
`execution.create_github_repo`, redaction).
