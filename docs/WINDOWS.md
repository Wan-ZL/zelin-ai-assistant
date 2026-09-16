# Windows support — v1 (beta)

This is the concrete "what actually runs on Windows today" doc, the mirror of
[LINUX.md](LINUX.md). The general port map lives in [PORTING.md](PORTING.md);
this page is the honest v1 scope for a Windows box, what is deferred, and
exactly which parts still need a real Windows machine to validate (friends test
+ PR — this was built and CI-checked on macOS, so the items under **Needs a
real Windows machine** are unproven here).

## What v1 Windows does

A headless-first port of the Act pipeline, exactly like Linux v1. No Mac app, no
screen capture — but the full card lifecycle works end to end: radar → approve →
executor → draft PR.

| Piece | On Windows v1 | How |
|---|---|---|
| **Core pipeline** (actd, executor, registry, dashboard, inbox, failures, health, analytics) | **Works** — pure Python 3.9+ + PyYAML; runs on the `windows-latest` CI job | `\ZelinAIAssistant\actd` scheduled task (LogonTrigger + RestartOnFailure) |
| **UI** | **The board** (`server/` + `web/`, CONTRACT §49) — the same React board macOS and Linux serve; this IS the Windows UI since 2026-09-14 (owner decision D67, the retired `webui` task is gone) | `\ZelinAIAssistant\server` task, `http://127.0.0.1:$ZAI_PORT/` in a browser (default 47820, `config.yaml` `server.port`) — installable as an app, see below |
| **Obsidian radar** | **Works** — scans notes already in the vault (no TCC on Windows, so it runs as a normal user task) | `\ZelinAIAssistant\obsidian-radar` (repeat 30 min) |
| **Gmail radar** | **Works** — IMAP, read-only | `\ZelinAIAssistant\gmail-radar` (repeat 5 min) |
| **Slack radar + self-DM quick capture** | **Works** — network only; self-DM is the phone / always-on channel | `\ZelinAIAssistant\slack-radar` (repeat 3 min) |
| **Weekly digest** | **Works** | `\ZelinAIAssistant\weekly-digest` (hourly wake, module gates to Mon 09:xx) |
| **Desktop notifications** | **Works** via a native WinRT toast fired through PowerShell — no pip dependency; falls back to False (Slack self-DM + the board's own badge cover it) if WinRT is unavailable | `act/lib/platform.notify_user()` |
| **Approval** | **Works** — the board writes `state\inbox\<uuid>.json`, or write the inbox JSON directly (CONTRACT §3/§10) | — |

## Install

**Option A — download the release bundle (no git).** On the
[latest Release](https://github.com/Wan-ZL/zelin-ai-assistant/releases/latest),
grab `ZelinAIAssistant-<tag>-windows.zip`, extract it (right-click → Extract
All, or `Expand-Archive`), and run the installer from the extracted folder:

```powershell
Expand-Archive ZelinAIAssistant-*-windows.zip -DestinationPath .
cd ZelinAIAssistant-*\
powershell -ExecutionPolicy Bypass -File install.ps1          # renders + registers the tasks, runs the doctor
powershell -ExecutionPolicy Bypass -File install.ps1 --check  # re-run diagnostics anytime (python -m act.doctor)
```

The bundle carries `server\` and `web\` **plus a prebuilt `web\dist`** (the
release workflow runs `npm ci && npm run build` before packaging), so the board
works on this path with no Node installed at all —
`http://127.0.0.1:<port>/`. Only a git checkout has to build it by hand.
`tests/test_portable_bundle_ships_the_board.py` pins that file set.

**Option B — clone the repo** (for contributors / to track `main`):

```powershell
git clone https://github.com/Wan-ZL/zelin-ai-assistant
cd zelin-ai-assistant
powershell -ExecutionPolicy Bypass -File install.ps1          # renders + registers the tasks, runs the doctor
powershell -ExecutionPolicy Bypass -File install.ps1 --check  # re-run diagnostics anytime (python -m act.doctor)
```

`install.ps1` mirrors `install.sh` / `install-linux.sh`: it creates
`config.yaml` + `config\runtime.json`, sets up `config\secrets\`, renders the
task templates, and registers them. A Task Scheduler session has no Keychain, so
the Anthropic key must be a file: `config\secrets\anthropic-api-key.txt`.

**Secrets on NTFS.** There is no POSIX `chmod 0600` on NTFS, so `install.ps1`
locks `config\secrets\` down with an ACL instead (`icacls /inheritance:r
/grant:r <you>`) — best-effort. `act.doctor` therefore reports the key as
"NTFS ACL; no POSIX 0600" rather than checking mode bits (which are meaningless
on NTFS). Keep the folder private.

The Anthropic key file is required (same as launchd/systemd — a daemon session
cannot read a subscription-auth token).

### The Task Scheduler task set (`act/tasksched/`)

XML templates carry `@PYTHON@` / `@REPO_ROOT@` / `@CLAUDE_BIN_DIR@` /
`@ZAI_PORT@` placeholders, rendered by `python -m act.lib.taskscheduler` (the
single source of truth for the substitution — unit-tested in
`tests/test_taskscheduler_render.py`, no drift between what install registers
and what CI validated), then registered under the `\ZelinAIAssistant\` folder
with `Register-ScheduledTask`. `@ZAI_PORT@` is `config.yaml` `server.port`
(default 47820), read by `install.ps1` and rendered into the `server` task only
— the exact mirror of `act/systemd/zelin-server.service`.

```
\ZelinAIAssistant\actd            resident daemon, LogonTrigger + RestartOnFailure
\ZelinAIAssistant\server          resident board server (the UI), same shape + ZAI_PORT
\ZelinAIAssistant\obsidian-radar  act.radar --once, repeat 30 min
\ZelinAIAssistant\gmail-radar     act.radar_gmail --once, repeat 5 min
\ZelinAIAssistant\slack-radar     act.radar_slack --once, repeat 3 min
\ZelinAIAssistant\weekly-digest   act.weekly_digest, hourly wake
```

Task Scheduler has **no per-task environment**, so each task's action is a
`powershell -Command` that sets `AIASSISTANT_HOME` and **prepends
`@CLAUDE_BIN_DIR@` to `PATH`** before invoking the pinned interpreter — so the
daemon resolves the same `claude` the login shell does (the "outdated claude
shadowed the new one" guard the launchd plists / systemd units carry). This
PATH guard is the #3 port risk and needs a real box to prove (see below).

Overlapping runs are prevented by `MultipleInstancesPolicy=IgnoreNew` on every
task — this is the Windows substitute for the radars' POSIX `fcntl` pass-lock
(`fcntl` does not exist on Windows), so a long Obsidian backfill makes the
scheduler skip the next fire rather than let two passes interleave.

Manage the tasks:

```powershell
Get-ScheduledTask -TaskPath '\ZelinAIAssistant\'
Start-ScheduledTask -TaskPath '\ZelinAIAssistant\' -TaskName actd
schtasks /Query /TN \ZelinAIAssistant\actd /V /FO LIST
```

## The board is the UI (and how to install it as an app)

`\ZelinAIAssistant\server` runs `python -m server`: the loopback board server
that reads `state\dashboard.json` and writes approvals into `state\inbox\`
(CONTRACT §49). It binds **127.0.0.1 only**, and every write passes the four
gates in `server/security.py` (Host allow-list, Origin allow-list, forced
`application/json`, per-install token in `state\server.token`).

The React board it serves is a build product. The **release bundle ships it
prebuilt** (`web\dist` is in the zip); in a **git checkout** build it once (no
npm step runs inside the installer — the same honest gap Linux carries):

```powershell
cd web
npm ci
npm run build          # writes web\dist, which the server task then serves
Start-ScheduledTask -TaskPath '\ZelinAIAssistant\' -TaskName server
```

Until `web\dist` exists, `http://127.0.0.1:<port>/` serves a placeholder page —
the `/api/*` routes work either way.

**Install it as an app.** The board ships a PWA manifest (CONTRACT §73), so
Edge and Chrome can install it as a standalone window: open
`http://127.0.0.1:<port>/`, then use the address-bar install icon (or
**Settings → Apps → Install this site as an app**). You get a window with no
address bar, a Start-menu entry and a taskbar icon — zero new processes, no
Electron, no tray icon. Uninstalling the app does not touch the tasks.

**Why the old `webui` task is gone.** Until 2026-09-14 Windows ran
`act/webui.py` as `\ZelinAIAssistant\webui` — a second, older board on a
second port with its own token file. It can only serve two static files, so it
could never serve the React board or its manifest. Owner decision **D67**
retired that task (and its Linux `zelin-webui.service` twin): one board, one
port, one token model on all three platforms. `python -m act.webui` still
exists in the tree as a hand-run fallback; nothing starts it for you any more.

Deleting the XML template is not enough on a machine that already has the task
— Task Scheduler keeps it registered with its `LogonTrigger` forever. So
`install.ps1` **unregisters** `\ZelinAIAssistant\webui` (its `$RetiredLeaves`
list), then asks `Get-ScheduledTask` again and shouts if it survived — the
`launchd_retire` discipline of CONTRACT §55, whose case history is an agent
that ran 51 days unseen. Anything else under `\ZelinAIAssistant\` with no
template in `act\tasksched\` is reported (never auto-removed) by the new
`scheduled task orphans` row in `python -m act.doctor`.

## Deferred on v1

- **Screen ingest (screenpipe glue).** The `ingest/screenpipe-export.sh` chain
  is bash + cron + macOS-shaped; on Windows it needs a full **`.ps1` rewrite**
  plus DXGI screen capture. This is **out of scope for Windows v1**. The
  Obsidian radar still processes notes that already exist in the vault; only the
  "turn screen captures into vault notes" step is missing.
- **Full Settings write-through** and the update-check UI surfaces.
- **The macOS SwiftUI menu-bar app** — platform-exclusive; the browser board is
  the Windows UI by design (installable as a PWA, above).
- **A `board server` row in `act.doctor`.** The §54 health probe
  (`GET /api/health` + "is it service-manager hosted") is macOS/Linux-only —
  `schtasks` output has no equivalent column, so on Windows the `server` task
  is checked like every other task (registered / ready / running) and
  `install.ps1` does the one-off port probe at the end of the install.
- **`os.startfile` reveal** (`platform.open_path`) is wired but unproven here.

## Needs a real Windows machine (friend test + PR)

Everything below was written to spec and is CI-validated only for its
pure-Python / XML-rendering parts. The runtime behavior has **not** been
exercised on Windows here — please test and PR fixes:

- **`Register-ScheduledTask` actually loading the XML**, and the `LogonTrigger`
  firing `actd`/`server` at logon, the `Repetition` interval firing the radars.
  The `server` task is brand new — nobody has registered it on a real box yet.
- **The board port actually answering.** `install.ps1` probes
  `http://127.0.0.1:<port>/api/health` after starting the tasks; whether the
  task really binds the port under a Task Scheduler session (and whether
  Windows Firewall stays out of the way on loopback) is unproven here.
- **Installing the board as a PWA in Edge** — the manifest and its icons are
  unit-tested (`tests/test_pwa_manifest.py`), but "the install icon appears and
  the installed window works" has never been seen on Windows.
- **`RestartOnFailure` really restarting `actd`/`server` after a crash.** Task
  Scheduler only restarts a task it considers *failed* (nonzero exit) — this is
  a **weaker guarantee** than launchd `KeepAlive` / systemd `Restart=always`. A
  resident daemon that exits 0 will NOT be restarted; validate the real crash /
  restart behavior and tune the settings if needed.
- **The toast really firing** (the WinRT `ToastNotificationManager` path via
  PowerShell), including on Windows versions where it needs a registered AppID.
- **The daemon resolving `claude` under the Task Scheduler minimal env/PATH**
  (the #3 risk — the PATH-prepend guard above). This class of bug bit macOS
  twice; the first real install may show "radar no output / dispatch failed"
  until the PATH is right.
- **The board** in a real browser (the API layer + token/Origin defense are
  unit-tested; the end-to-end click-through is not), and `npm run build`
  producing a usable `web\dist` on Windows.
- **`os.startfile`** behavior for `open_path()`.
- **`install.ps1` end to end** — it is syntactically sane and statically checked
  but has never been executed on Windows.

## What is CI-validated (no Windows machine needed)

- The pure-Python suite on **windows-latest** (Python 3.9 + current) in
  `.github/workflows/ci.yml` — POSIX-only checks (file modes, launchd/cron,
  `fcntl` flock) guard themselves with `sys.platform` / `os.name` / `try: import
  fcntl` and are skipped there, never deleted.
- `act/lib/platform.py` seam: `service_list_text()` builds the
  `schtasks /query /fo LIST /v` argv; `notify_user()` builds the `powershell`
  WinRT-toast argv with title/body escaped for a PowerShell single-quoted
  string (`tests/test_platform_seam.py`).
- Task XML rendering — every placeholder substituted, every file well-formed
  XML, resident tasks (`actd`, `server`) carry `RestartOnFailure` + no
  repetition, `server` execs `-m server` under `\ZelinAIAssistant\server` and
  is the only task carrying `ZAI_PORT`, the retired `webui` task cannot come
  back, periodic tasks carry `Repetition` at the right interval,
  `MultipleInstancesPolicy=IgnoreNew` everywhere, claude dir first on the task
  PATH (`tests/test_taskscheduler_render.py`).
- `act.doctor`'s schtasks branch parsing a `schtasks /query` fixture, and the
  macOS/Linux-only checks (launchd/cron/systemd/screenpipe/npx) being
  conditioned out on Windows
  (`tests/test_doctor.py::WindowsScheduledTasksDoctorTestCase`).
- **Pure-Python Windows bugs fixed** so the `windows-latest` job goes greener:
  `import fcntl` (POSIX-only) is now guarded in `act/radar.py` and
  `act/lib/radar_health.py` (it crashed the import on Windows); the doctor key-mode
  check no longer false-WARNs on NTFS.
