# Linux support — v1 (beta)

This is the concrete "what actually runs on Linux today" doc. The general
port map lives in [PORTING.md](PORTING.md); this page is the honest v1 scope
for a Linux box, what is deferred, and exactly which parts still need a real
Linux machine to validate (friends test + PR — this was built and CI-checked
on macOS, so the items under **Needs a real Linux machine** are unproven here).

## What v1 Linux does

A headless-first port of the Act pipeline. No Mac app, no screen capture — but
the full card lifecycle works end to end: radar → approve → executor → draft PR.

| Piece | On Linux v1 | How |
|---|---|---|
| **Core pipeline** (actd, executor, registry, dashboard, inbox, failures, health, analytics) | **Works** — pure Python 3.9+ + PyYAML; already green on ubuntu CI | `zelin-actd.service` (`Restart=always`) |
| **UI** | **Web dashboard** (`act/webui.py`, shipped v0.24.0) — this IS the Linux UI | `zelin-webui.service`, `http://127.0.0.1:<port>` in a browser |
| **Obsidian radar** | **Works** — scans notes already in the vault (no TCC on Linux, so it runs as a normal user timer, unlike the macOS crontab story) | `zelin-obsidian-radar.timer` (30 min) |
| **Gmail radar** | **Works** — IMAP, read-only | `zelin-gmail-radar.timer` (5 min) |
| **Slack radar + self-DM quick capture** | **Works** — network only; self-DM is the phone / always-on channel | `zelin-slack-radar.timer` (3 min) |
| **Weekly digest** | **Works** | `zelin-weekly-digest.timer` (hourly wake, module gates to Mon 09:xx) |
| **Desktop notifications** | **Works on a desktop session** via `notify-send`; no-op on a headless box (Slack self-DM covers that) | `act/lib/platform.notify_user()` |
| **Approval** | **Works** — web dashboard writes `state/inbox/<uuid>.json`, or write the inbox JSON directly (CONTRACT §3/§10) | — |

## Install

**Option A — download the release bundle (no git).** On the
[latest Release](https://github.com/Wan-ZL/zelin-ai-assistant/releases/latest),
grab `ZelinAIAssistant-<tag>-linux.tar.gz`, unpack it, and run the installer
from the extracted tree:

```bash
tar -xzf ZelinAIAssistant-*-linux.tar.gz
cd ZelinAIAssistant-*/
bash install-linux.sh          # renders + enables the systemd user units, runs the doctor
bash install-linux.sh --check  # re-run diagnostics anytime (python -m act.doctor)
```

**Option B — clone the repo** (for contributors / to track `main`):

```bash
git clone https://github.com/Wan-ZL/zelin-ai-assistant
cd zelin-ai-assistant
bash install-linux.sh          # renders + enables the systemd user units, runs the doctor
bash install-linux.sh --check  # re-run diagnostics anytime (python -m act.doctor)
```

`install-linux.sh` mirrors `install.sh`: it creates `config.yaml` +
`config/runtime.json`, sets up `config/secrets/` (0700, key files 0600),
renders the unit templates, and enables them. A systemd `--user` session has no
Keychain, so the Anthropic key must be a file:
`config/secrets/anthropic-api-key.txt` (chmod 600).

The Anthropic key file is required (same as launchd on macOS — a daemon session
cannot read a subscription-auth token).

### The systemd unit set (`act/systemd/`)

Templates carry `@PYTHON@` / `@REPO_ROOT@` / `@CLAUDE_BIN_DIR@` placeholders,
rendered by `python3 -m act.lib.systemd` (the single source of truth for the
substitution — unit-tested in `tests/test_systemd_render.py`, no sed/install
drift). The login-shell `claude` dir is kept FIRST on every unit `PATH` (the
2026-07-08 "outdated claude shadowed the new one" guard the plists carry);
`systemd --user` does not source `~/.profile`, so the interpreter and PATH are
pinned in the units.

```
zelin-actd.service          resident daemon, Restart=always (KeepAlive equivalent)
zelin-webui.service         resident web dashboard, Restart=always (the UI)
zelin-obsidian-radar.{service,timer}   act.radar --once, every 30 min
zelin-gmail-radar.{service,timer}      act.radar_gmail --once, every 5 min
zelin-slack-radar.{service,timer}      act.radar_slack --once, every 3 min
zelin-weekly-digest.{service,timer}    act.weekly_digest, hourly wake
```

Manage them:

```bash
systemctl --user status zelin-actd.service zelin-webui.service
systemctl --user list-timers 'zelin-*'
journalctl --user -u zelin-actd -f
journalctl --user -u zelin-webui        # prints the dashboard URL
```

On a headless server, enable lingering so the `--user` units run without an
active login: `sudo loginctl enable-linger "$USER"` (install-linux.sh attempts
this).

## Deferred on v1

- **Screen ingest (screenpipe glue).** The `ingest/screenpipe-export.sh` chain
  uses BSD `date -j -f` (needs a GNU `date -d` rewrite) and, more fundamentally,
  needs a display to capture (X11 / PipeWire) — a headless Linux server has
  none. This is **out of scope for v1**. The Obsidian radar still processes
  notes that already exist in the vault; only the "turn screen captures into
  vault notes" step is missing.
- **Full Settings write-through** and the update-check UI surfaces.
- **The macOS SwiftUI menu-bar app** — platform-exclusive; the web dashboard is
  the Linux UI by design.

## Needs a real Linux machine (friend test + PR)

Everything below was written to spec and is CI-validated only for its
pure-Python / string-rendering parts. The runtime behavior has **not** been
exercised on Linux here — please test and PR fixes:

- **`systemctl --user enable --now` actually loading the units**, and
  `Restart=always` really restarting `actd`/`webui` after a crash.
- **`notify-send` really firing** on a desktop session.
- The **daemon PATH/env** actually resolving `claude` under `systemd --user`
  (this class of bug bit macOS twice — see the PATH comments in the units).
- The **web dashboard** in a real browser (the API layer + token/Origin defense
  are unit-tested; the end-to-end click-through is not).
- `xdg-open` behavior for `open_path()`.

## What is CI-validated (no Linux machine needed)

- The pure-Python suite on **ubuntu** (Python 3.9 + current) and **windows**
  (`.github/workflows/ci.yml`) — POSIX-only checks (file modes, launchd/cron,
  shell-shim resolution) guard themselves with `sys.platform` / `os.name`.
- `act/lib/platform.py` seam: `service_list_text()` builds the
  `systemctl --user list-units` argv; `notify_user()` builds the `notify-send`
  argv (`tests/test_platform_seam.py`).
- Unit-template rendering — every placeholder substituted, `Restart=always` on
  the resident units, timers point at their service (`tests/test_systemd_render.py`).
- `act.doctor`'s systemd branch parsing a `systemctl` fixture, and the
  macOS-only checks (crontab/FDA/screenpipe/npx) being conditioned out
  off-macOS (`tests/test_doctor.py::SystemdDoctorTestCase`).

## Windows

Windows v1 (beta) now ships the analogous port: Task Scheduler tasks
(`act/tasksched/*.xml`), `install.ps1`, a PowerShell WinRT toast, and the
`schtasks` doctor branch — with the same web dashboard as the UI. See
**[WINDOWS.md](WINDOWS.md)**. `windows-latest` runs the pure-Python suite in CI.
