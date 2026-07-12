# Porting to Windows / Linux

This is the map for anyone who wants to run the assistant off macOS. It is
written from a real audit of every OS-specific call in the tree (July 2026) —
not from hope. Honest summary up front: **the headless core is already
portable and its test suite runs green on ubuntu CI on every push**; what a
port must build is the service wiring (launchd → systemd/Task Scheduler), an
ingest chain, and eventually a UI. (The iMessage transport was removed in
v0.21 — nothing macOS-exclusive remains in the capture layer.)

Want to help? Comment on the pinned "Windows/Linux port — help wanted" issue.

## Component matrix

| Component | Where | Status off macOS |
|---|---|---|
| act core: `actd` daemon, executor, registry, dashboard projection, inbox, config, failures, health, analytics | `act/`, `act/lib/` | **Portable as-is** — pure Python 3.9+ + PyYAML; subprocess calls are `claude` / `git` / `gh`, all cross-platform. CI runs the full suite on ubuntu (Python 3.9 and current). |
| Obsidian radar | `act/radar.py` | **Portable as-is** (filesystem + `claude`) |
| Gmail radar | `act/radar_gmail.py` | **Portable as-is** (network + state/) |
| Slack radar (DMs/mentions + self-DM quick capture) | `act/radar_slack.py` | **Portable as-is** (network + state/) |
| Claude-sessions radar | `act/radar_claude_sessions.py` | **Portable as-is** (reads `~/.claude` projects) |
| Weekly digest / merge review / analyze | `act/weekly_digest.py`, `act/merge_review.py`, `act/analyze.py` | **Portable as-is** |
| Doctor | `act/doctor.py` | **Mostly portable** — service checks read `platform.service_list_text()` (empty off macOS → agents honestly report unregistered); cron/FDA checks are macOS-shaped and need per-OS equivalents |
| OS seam | `act/lib/platform.py` | **The porting surface** — see the interface below |
| Ingest chain (screenpipe → Obsidian) | `ingest/*.sh` | **Needs platform impl** — bash + cron + macOS TCC assumptions. [screenpipe](https://github.com/mediar-ai/screenpipe) itself runs on Windows/Linux, so this is a rewrite of the glue (export/process/cleanup on a timer), not of the engine. |
| Service wiring (install/uninstall) | `install.sh`, `uninstall.sh`, `act/launchd/*.plist` | **Needs platform impl** — plist templates + `launchctl` + `crontab`. See the equivalents table. |
| Menu-bar app | `mac/Sources/*.swift` | **Platform-exclusive (darwin)** — SwiftUI. A port needs its own thin UI later; the product works headless first (dashboard.json is the UI contract, `docs/CONTRACT.md`). |
| `.pkg` packaging | `mac/package.sh`, release workflow | **Platform-exclusive (darwin)** |

## The OS seam — `act/lib/platform.py`

Every *generic* OS effect in the python tree goes through this module. This
is the contract a port implements; everything else in `act/` is plain Python.

| Function | Semantics | darwin impl | linux today | windows today |
|---|---|---|---|---|
| `is_darwin()` / `is_windows()` | platform check for OS-exclusive features | `sys.platform == "darwin"` | — | `sys.platform.startswith("win")` |
| `notify_user(title, body, subtitle=None)` | native user notification; best-effort, never raises | `osascript` `display notification` | `notify-send` (desktop) | PowerShell WinRT toast (wired; no pip dep — see WINDOWS.md) |
| `open_path(path)` | open a path with the system handler / file manager; never raises | `open`(1) | `xdg-open` | `os.startfile` (wired) |
| `service_list_text()` | raw user-service table for `act.doctor` | `launchctl list` | `systemctl --user list-units --type=service,timer` (wired; doctor parses it — see LINUX.md) | `schtasks /query /fo LIST /v` (wired; doctor parses it — see WINDOWS.md) |

Rules for touching the seam:
- keep it thin — no classes, no plugin registry, one function per concern;
- every function is best-effort and never raises (a failed notification must
  not kill the daemon loop);
- darwin-only-by-nature features do **not** get seam functions — they guard
  with `is_darwin()` and skip with a classified reason.

## Service-manager equivalents

The launchd agents are rendered from `act/launchd/*.plist` by `install.sh`
step 5 (placeholder substitution). A port reproduces each agent:

| launchd (macOS, today) | systemd user units (Linux) | Task Scheduler (Windows) |
|---|---|---|
| `act/launchd/com.zelin.aiassistant.actd.plist` (`KeepAlive` daemon) | `~/.config/systemd/user/aiassistant-actd.service` with `Restart=always` | `schtasks /Create ... /SC ONLOGON` or a service wrapper |
| periodic radar agents (`StartInterval`) | `.timer` + `.service` pairs (`OnUnitActiveSec=180`) | `/SC MINUTE /MO 3` tasks |
| `launchctl bootstrap gui/$UID` / legacy `load` | `systemctl --user enable --now` | `schtasks /Create` |
| `launchctl bootout` / `unload` | `systemctl --user disable --now` | `schtasks /Delete` |
| `launchctl kickstart gui/$UID/<label>` | `systemctl --user restart <unit>` | `schtasks /Run` |
| `launchctl list` (doctor probe) | `systemctl --user list-units --type=service` | `schtasks /Query` |
| cron ingest chain (`crontab` lines, `install.sh` step 6) | systemd timers (preferred) or user crontab | Task Scheduler |
| `state/<agent>.launchd.log` | journald (`journalctl --user -u ...`) or the same log files via `StandardOutput=append:` | log files |

Env parity matters: agents run with `AIASSISTANT_HOME` set and the pinned
interpreter from `config/runtime.json` (CONTRACT §19) — keep both in unit files.

## What a port must NOT touch

- **`docs/CONTRACT.md` semantics** — sections are add-only and never
  renumbered; every state file schema lives there. A port that needs a new
  field follows the same contract-first rule as everyone else.
- **State schema / paths** — `state/dashboard.json`, `state/inbox/*.json`,
  `act/registry/*`, `state/radar_health.json`, markers and outboxes. These are
  the product; the OS wiring around them is the only thing that changes.
- **Failure ids** (`act/lib/failures.py`) — the Swift app mirrors them
  (drift-guarded by tests); add new ids, never rename.
- **Bilingual copy rules** — user-facing python text goes through
  `failures.pick(zh, en)`.
- **Secrets layout** — `config/secrets/*` files with `0600` modes (§19).

## Linux v1 (beta)

A first cut of this milestone now ships: systemd user units
(`act/systemd/*.service|*.timer`), `install-linux.sh`, the `platform` +
`doctor` systemd branches, and the web dashboard as the Linux UI. See
**[LINUX.md](LINUX.md)** for exactly what runs, what is deferred (screen
ingest), and what still needs a real Linux machine to validate.

## Windows v1 (beta)

The analogous Windows cut now ships too: Task Scheduler XML templates
(`act/tasksched/*.xml`) rendered by `act/lib/taskscheduler.py`, `install.ps1`,
the `platform` (PowerShell WinRT toast + `schtasks`) + `doctor` (schtasks
branch) Windows seams, and the same web dashboard as the Windows UI. See
**[WINDOWS.md](WINDOWS.md)** for exactly what runs, what is deferred (screen
ingest — a `.ps1` + DXGI rewrite), and what still needs a real Windows machine
to validate (Task Scheduler load/restart, toast firing, the claude PATH guard).

## Suggested first milestone: headless core on Linux

Smallest honest useful port — no UI, no screen capture:

1. `git clone` + `python3` + PyYAML + `claude` CLI on Linux; run
   `AIASSISTANT_HOME=$(mktemp -d) python3 -m unittest discover -s tests`
   (already green on ubuntu CI — your baseline).
2. Write systemd user units for `actd` + the Obsidian/Gmail/Slack radars
   (table above), a `install-linux.sh` that renders them the way `install.sh`
   step 5 renders plists, and pin the interpreter in `config/runtime.json`.
3. Wire `platform.service_list_text()` to `systemctl --user` and teach
   `act.doctor`'s parser about it — doctor is the acceptance test:
   `python3 -m act.doctor` should read all-green on a healthy Linux install.
4. Mobile capture = Slack self-DM quick capture (works today); notifications
   via `notify-send` (works today on desktop). Approval is via inbox files
   today (CONTRACT §3/§10; the Mac app is the reference approver — a port
   supplies its own UI or writes inbox JSON directly).
5. Skip: ingest chain, the app. Cards flow end to end from radar → approval
   (inbox decision) → executor → draft PR. That is the demo that proves the
   port.

CI will hold you honest: the ubuntu jobs run the full suite on every PR.
