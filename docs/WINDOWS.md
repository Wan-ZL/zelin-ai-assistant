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
| **UI** | **Web dashboard** (`act/webui.py`, shipped v0.24.0) — this IS the Windows UI | `\ZelinAIAssistant\webui` task, `http://127.0.0.1:<port>` in a browser |
| **Obsidian radar** | **Works** — scans notes already in the vault (no TCC on Windows, so it runs as a normal user task) | `\ZelinAIAssistant\obsidian-radar` (repeat 30 min) |
| **Gmail radar** | **Works** — IMAP, read-only | `\ZelinAIAssistant\gmail-radar` (repeat 5 min) |
| **Slack radar + self-DM quick capture** | **Works** — network only; self-DM is the phone / always-on channel | `\ZelinAIAssistant\slack-radar` (repeat 3 min) |
| **Weekly digest** | **Works** | `\ZelinAIAssistant\weekly-digest` (hourly wake, module gates to Mon 09:xx) |
| **Desktop notifications** | **Works** via a native WinRT toast fired through PowerShell — no pip dependency; falls back to False (Slack self-DM + the web dashboard badge cover it) if WinRT is unavailable | `act/lib/platform.notify_user()` |
| **Approval** | **Works** — web dashboard writes `state\inbox\<uuid>.json`, or write the inbox JSON directly (CONTRACT §3/§10) | — |

## Install

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

XML templates carry `@PYTHON@` / `@REPO_ROOT@` / `@CLAUDE_BIN_DIR@` placeholders,
rendered by `python -m act.lib.taskscheduler` (the single source of truth for the
substitution — unit-tested in `tests/test_taskscheduler_render.py`, no drift
between what install registers and what CI validated), then registered under the
`\ZelinAIAssistant\` folder with `Register-ScheduledTask`.

```
\ZelinAIAssistant\actd            resident daemon, LogonTrigger + RestartOnFailure
\ZelinAIAssistant\webui           resident web dashboard (the UI), same shape
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

## Deferred on v1

- **Screen ingest (screenpipe glue).** The `ingest/screenpipe-export.sh` chain
  is bash + cron + macOS-shaped; on Windows it needs a full **`.ps1` rewrite**
  plus DXGI screen capture. This is **out of scope for Windows v1**. The
  Obsidian radar still processes notes that already exist in the vault; only the
  "turn screen captures into vault notes" step is missing.
- **Full Settings write-through** and the update-check UI surfaces.
- **The macOS SwiftUI menu-bar app** — platform-exclusive; the web dashboard is
  the Windows UI by design.
- **`os.startfile` reveal** (`platform.open_path`) is wired but unproven here.

## Needs a real Windows machine (friend test + PR)

Everything below was written to spec and is CI-validated only for its
pure-Python / XML-rendering parts. The runtime behavior has **not** been
exercised on Windows here — please test and PR fixes:

- **`Register-ScheduledTask` actually loading the XML**, and the `LogonTrigger`
  firing `actd`/`webui` at logon, the `Repetition` interval firing the radars.
- **`RestartOnFailure` really restarting `actd`/`webui` after a crash.** Task
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
- **The web dashboard** in a real browser (the API layer + token/Origin defense
  are unit-tested; the end-to-end click-through is not).
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
  XML, resident tasks carry `RestartOnFailure` + no repetition, periodic tasks
  carry `Repetition` at the right interval, `MultipleInstancesPolicy=IgnoreNew`
  everywhere, claude dir first on the task PATH
  (`tests/test_taskscheduler_render.py`).
- `act.doctor`'s schtasks branch parsing a `schtasks /query` fixture, and the
  macOS/Linux-only checks (launchd/cron/systemd/screenpipe/npx) being
  conditioned out on Windows
  (`tests/test_doctor.py::WindowsScheduledTasksDoctorTestCase`).
- **Pure-Python Windows bugs fixed** so the `windows-latest` job goes greener:
  `import fcntl` (POSIX-only) is now guarded in `act/radar.py` and
  `act/lib/health.py` (it crashed the import on Windows); the doctor key-mode
  check no longer false-WARNs on NTFS.
