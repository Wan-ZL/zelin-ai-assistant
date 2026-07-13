<#
One-click installer for Zelin's AI Assistant on WINDOWS (v1 beta).

The Windows mirror of install.sh / install-linux.sh. Windows v1 ships the
headless core + Task Scheduler tasks + the local web dashboard (the Windows UI)
+ Slack self-DM capture + native toast notifications. See docs/WINDOWS.md for
exactly what works, what is DEFERRED (the Mac SwiftUI app; the screenpipe
screen-ingest chain), and what still needs a real Windows machine to validate.

What it does:
  1. dependency checks (python + PyYAML required; claude required for
     dispatch/extraction; gh optional)
  2. config.example.yaml -> config.yaml + config\runtime.json + secrets dir
     (best-effort NTFS ACL lockdown; NTFS has no chmod 0600)
  3. create state\ and state\inbox\ + seed state\dashboard.json
  4. render act\tasksched\*.xml (via `python -m act.lib.taskscheduler`) into a
     staging dir, then Register-ScheduledTask each under \ZelinAIAssistant\ and
     start the resident tasks (actd + webui)
  5. run the post-install diagnostics (python -m act.doctor)

Run from anywhere (it locates the repo root via its own path):
    powershell -ExecutionPolicy Bypass -File install.ps1
    powershell -ExecutionPolicy Bypass -File install.ps1 --check

--check: run the doctor (python -m act.doctor) and exit with the number of
  failing checks. Installs/changes nothing.

NOTE: this script is real and runnable on a Windows box but has NOT been
executed on Windows here (built + statically checked on macOS). The pure-string
RENDER it drives (python -m act.lib.taskscheduler) is unit-tested in CI; the
Register-ScheduledTask / Start-ScheduledTask calls need a real machine.
#>

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
$TaskFolder = '\ZelinAIAssistant\'

function Write-Ok   { param($m) Write-Host "  [ ok ] $m" }
function Write-Warn2 { param($m) Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Write-Info { param($m) Write-Host "  [info] $m" }
function Write-Err2 { param($m) Write-Host "  [ERR ] $m" -ForegroundColor Red }

# --- interpreter discovery -------------------------------------------------
# Prefer the Windows launcher (`py -3`), then python / python3 on PATH. Return
# the interpreter's OWN sys.executable (absolute) so the tasks + doctor + the
# runtime.json pin all agree on one interpreter.
function Resolve-Python {
    $cands = @(
        @{ exe = 'py';      pre = @('-3') },
        @{ exe = 'python';  pre = @() },
        @{ exe = 'python3'; pre = @() }
    )
    foreach ($c in $cands) {
        if (Get-Command $c.exe -ErrorAction SilentlyContinue) {
            try {
                $full = & $c.exe @($c.pre) -c 'import sys; print(sys.executable)' 2>$null
                if ($LASTEXITCODE -eq 0 -and $full) { return ($full | Select-Object -First 1).Trim() }
            } catch { }
        }
    }
    return $null
}

# --check: delegate to act/doctor.py (its schtasks branch validates the tasks)
if (($args -contains '--check') -or ($args -contains '-Check') -or ($args -contains '/check')) {
    $py = Resolve-Python
    if (-not $py) { Write-Err2 'python not found'; exit 1 }
    $env:AIASSISTANT_HOME = $RepoRoot
    Set-Location $RepoRoot
    & $py -m act.doctor
    exit $LASTEXITCODE
}

if (-not ($env:OS -eq 'Windows_NT')) {
    Write-Err2 'install.ps1 targets Windows. On macOS run: bash install.sh ; on Linux: bash install-linux.sh'
    exit 1
}

# ---------------------------------------------------------------------------
Write-Host '==> 1. dependencies'
$PY = Resolve-Python
if (-not $PY) {
    Write-Err2 'python not found - install Python 3.9+ (https://www.python.org/downloads/windows/ or: winget install Python.Python.3.12)'
    exit 1
}
Write-Ok "python: $PY"

$Claude = Get-Command claude -ErrorAction SilentlyContinue
if ($Claude) { Write-Ok "claude: $($Claude.Source)" }
else { Write-Warn2 'claude not found - dispatch and radar extraction need it (https://code.claude.com/docs/en/setup)' }

if (Get-Command gh -ErrorAction SilentlyContinue) { Write-Ok 'gh found (optional - draft-PR delivery)' }
else { Write-Warn2 'gh not found (optional - cards deliver as local branches without it)' }

# The dir of the login-shell claude goes FIRST on the task PATH (the "outdated
# claude shadowed the new one" guard). Task Scheduler runs a minimal env, so the
# tasks prepend it explicitly (see act\tasksched\*.xml).
if ($Claude) {
    $ClaudeBinDir = Split-Path -Parent $Claude.Source
    Write-Ok "daemon claude dir: $ClaudeBinDir (first on the task PATH)"
} else {
    $ClaudeBinDir = Join-Path $env:USERPROFILE '.local\bin'
    Write-Warn2 "claude not resolvable - task PATH falls back to $ClaudeBinDir first"
}

# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '==> 2. config.yaml + config\runtime.json + secrets'
$ConfigYaml = Join-Path $RepoRoot 'config.yaml'
if (Test-Path $ConfigYaml) {
    Write-Ok 'config.yaml already exists (left untouched)'
} else {
    Copy-Item (Join-Path $RepoRoot 'config.example.yaml') $ConfigYaml
    Write-Ok 'created config.yaml from config.example.yaml - review it before first run'
}

$RedTerms = Join-Path $RepoRoot 'config\redaction_terms.txt'
$RedExample = Join-Path $RepoRoot 'config\redaction_terms.example.txt'
if ((-not (Test-Path $RedTerms)) -and (Test-Path $RedExample)) {
    Copy-Item $RedExample $RedTerms
    Write-Ok 'created config\redaction_terms.txt from template (gitignored)'
}

# runtime python pointer (CONTRACT §19): $env:AIASSISTANT_PYTHON override, else
# the interpreter found above. ConvertTo-Json escapes the backslashes for us.
$ConfigDir = Join-Path $RepoRoot 'config'
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
$RuntimePy = $PY
if ($env:AIASSISTANT_PYTHON -and (Test-Path $env:AIASSISTANT_PYTHON)) { $RuntimePy = $env:AIASSISTANT_PYTHON }
(@{ python = $RuntimePy } | ConvertTo-Json -Compress) | Set-Content -Path (Join-Path $ConfigDir 'runtime.json') -Encoding UTF8
Write-Ok "config\runtime.json -> $RuntimePy"

# secrets dir. NTFS has no chmod 0600, so lock it down with an ACL instead
# (best-effort): break inheritance and grant only the current user. A Task
# Scheduler session has no Keychain, so the Anthropic key MUST be a file.
$SecretsDir = Join-Path $ConfigDir 'secrets'
New-Item -ItemType Directory -Force -Path $SecretsDir | Out-Null
try {
    & icacls $SecretsDir /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" | Out-Null
    Write-Ok 'config\secrets locked to current user (NTFS ACL; no POSIX 0600)'
} catch {
    Write-Info 'could not tighten config\secrets ACL (non-fatal); keep this folder private'
}
$KeyFile = Join-Path $SecretsDir 'anthropic-api-key.txt'
if ((Test-Path $KeyFile) -and ((Get-Item $KeyFile).Length -gt 0)) {
    Write-Ok 'anthropic key present (config\secrets\anthropic-api-key.txt)'
} else {
    Write-Warn2 'no Anthropic API key - write it to config\secrets\anthropic-api-key.txt. A Task Scheduler session cannot read subscription-auth credentials.'
}

# verify PyYAML against the DAEMON interpreter (what the tasks + doctor run).
& $RuntimePy -c 'import yaml' 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Warn2 "PyYAML missing for $RuntimePy; attempting install"
    & $RuntimePy -m pip install --user pyyaml 2>$null
    & $RuntimePy -c 'import yaml' 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Err2 "PyYAML unavailable for the daemon python: $RuntimePy"
        Write-Info "fix: `"$RuntimePy`" -m pip install pyyaml   (then re-run)"
        exit 1
    }
    Write-Ok "PyYAML installed for $RuntimePy"
} else {
    Write-Ok "PyYAML importable for $RuntimePy"
}

# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '==> 3. state directories'
New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot 'state\inbox') | Out-Null
Write-Ok 'state\ and state\inbox\ ready'
$Dashboard = Join-Path $RepoRoot 'state\dashboard.json'
if (-not (Test-Path $Dashboard)) {
    $env:AIASSISTANT_HOME = $RepoRoot
    Push-Location $RepoRoot
    & $RuntimePy -m act.lib.dashboard 2>$null | Out-Null
    Pop-Location
    if (Test-Path $Dashboard) {
        Write-Ok 'generated state\dashboard.json from registry'
    } elseif (Test-Path (Join-Path $RepoRoot 'state\dashboard.seed.json')) {
        Copy-Item (Join-Path $RepoRoot 'state\dashboard.seed.json') $Dashboard
        Write-Ok 'seeded state\dashboard.json from dashboard.seed.json'
    } else {
        Write-Warn2 "could not generate dashboard.json (run: `"$RuntimePy`" -m act.lib.dashboard)"
    }
}

# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '==> 4. Task Scheduler tasks (actd + web dashboard + radar/digest)'
$Staging = Join-Path $env:TEMP 'zelin-tasksched'
New-Item -ItemType Directory -Force -Path $Staging | Out-Null
$env:AIASSISTANT_HOME = $RepoRoot
Push-Location $RepoRoot
# act.lib.taskscheduler is the single source of truth for the @TOKEN@
# substitution (unit-tested in CI), so there is no drift between "what install
# registers" and "what CI validated".
& $RuntimePy -m act.lib.taskscheduler --python $RuntimePy --repo-root $RepoRoot --claude-bin-dir $ClaudeBinDir --out $Staging | Out-Null
$rendered = $LASTEXITCODE
Pop-Location
if ($rendered -ne 0) {
    Write-Err2 'failed to render Task Scheduler XML (python -m act.lib.taskscheduler)'
    exit 1
}
Write-Ok "rendered task XML into $Staging"

$ResidentLeaves = @('actd', 'webui')
$RegisterFailed = 0
foreach ($xml in Get-ChildItem -Path $Staging -Filter '*.xml' | Sort-Object Name) {
    $leaf = $xml.BaseName -replace '^zelin-', ''
    try {
        $xmlText = Get-Content -Raw -Path $xml.FullName -Encoding UTF8
        Register-ScheduledTask -Force -TaskName $leaf -TaskPath $TaskFolder -Xml $xmlText -ErrorAction Stop | Out-Null
        Write-Ok "registered $TaskFolder$leaf"
    } catch {
        Write-Warn2 "could not register $leaf : $($_.Exception.Message)"
        $RegisterFailed++
        continue
    }
    if ($ResidentLeaves -contains $leaf) {
        try {
            Start-ScheduledTask -TaskPath $TaskFolder -TaskName $leaf -ErrorAction Stop
            Write-Ok "started $TaskFolder$leaf"
        } catch {
            Write-Info "registered but could not start $leaf now (it will start at next logon)"
        }
    }
}
if ($RegisterFailed -eq 0) {
    Write-Ok 'web dashboard: the webui task prints its http://127.0.0.1:<port> URL to its stdout log'
}

# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '==> 5. post-install diagnostics (python -m act.doctor)'
$env:AIASSISTANT_HOME = $RepoRoot
Push-Location $RepoRoot
& $RuntimePy -m act.doctor
$doctorRc = $LASTEXITCODE
Pop-Location
if ($doctorRc -ne 0) {
    Write-Warn2 'doctor reported problems above - fix them, then re-check: powershell -File install.ps1 --check'
}

# ---------------------------------------------------------------------------
Write-Host @"

==============================================
 Windows install complete (v1 beta). Next steps:
==============================================
 1. Edit config.yaml (Slack IDs, watched people, source paths).
 2. Anthropic API key -> config\secrets\anthropic-api-key.txt.
    A Task Scheduler session has no Keychain, so a file-form key is required.
 3. Open the web dashboard (the Windows UI): the webui task logs its
      http://127.0.0.1:<port>
    URL; open that in a browser on this machine. It reads state\dashboard.json
    and writes approvals to state\inbox\ (CONTRACT §3/§10).
 4. Phone / always-on channel = Slack self-DM quick capture (works today).
 5. Manage the tasks:
      Get-ScheduledTask -TaskPath '\ZelinAIAssistant\'
      Start-ScheduledTask -TaskPath '\ZelinAIAssistant\' -TaskName actd
      schtasks /Query /TN \ZelinAIAssistant\actd /V /FO LIST
 6. Anything off later? Re-run diagnostics anytime:
      powershell -File install.ps1 --check

 DEFERRED on Windows v1 (see docs\WINDOWS.md): the screenpipe screen-ingest
 chain (needs a .ps1 rewrite + DXGI capture) and the macOS SwiftUI app. The
 Obsidian radar still scans notes already in your vault.
==============================================
"@
