<#
  JARVIS laptop setup (Windows 10/11). One click, safe to run more than once, no admin where possible.

  Model (T105, decision recorded in the brain): the home PC is the PRIMARY JARVIS (Telegram bridge,
  routines, calls). The laptop is the STANDBY: full install, restored brain copy, same shortcuts, but the
  machine role file (%LOCALAPPDATA%\JARVIS\role = standby) keeps its Telegram bridge, routines tick and
  call server switched off, so the owner never gets two answers. Takeover: `bin\jarvis machine primary`.

  Run in a normal PowerShell window:
      powershell -ExecutionPolicy Bypass -File .\laptop-setup.ps1
  A copy sits in OneDrive\JARVIS-laptop-transfer next to the brain bundle: right-click > Run with PowerShell.
  Options:
      -RepoDir C:\jarvis      where to clone (default C:\jarvis, OUTSIDE OneDrive)
      -Branch  jarvis         branch to check out
      -Bundle  <zip>          brain zip to restore (default: the PC's lean push in OneDrive\JARVIS-laptop-transfer)
      -NoVoice                skip the Kokoro + Whisper venv (voice, calls)
      -NoMedia                skip the reel/video venv
      -NoShortcuts            skip the desktop/Startup shortcuts and the routines task
#>
param(
  [string]$RepoDir = "C:\jarvis",
  [string]$Branch = "jarvis",
  [string]$RepoUrl = "https://github.com/marley-gpt/jarvis.git",
  [string]$Bundle = "",
  [switch]$NoVoice,
  [switch]$NoMedia,
  [switch]$NoShortcuts
)
$ErrorActionPreference = "Stop"
$log = Join-Path $env:TEMP "jarvis-laptop-setup.log"
Start-Transcript -Path $log -Append | Out-Null
function Step($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "  OK  $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  !!  $t" -ForegroundColor Yellow; $script:todo += $t }
$todo = @()

function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
              [Environment]::GetEnvironmentVariable("Path", "User")
}

function Ensure-Winget($id, $check, [switch]$User) {
  if (& $check) { Ok "$id already installed"; return }
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Warn "winget missing: install 'App Installer' from the Microsoft Store, then rerun"; return
  }
  Write-Host "  installing $id ..."
  $scope = if ($User) { @("--scope", "user") } else { @() }
  winget install --id $id -e --accept-source-agreements --accept-package-agreements --silent @scope | Out-Host
  Refresh-Path
  if (& $check) { Ok "$id installed" } else { Warn "$id did not install (or needs a new PowerShell window): rerun this script" }
}

function Has-Py($v) { [bool]((& py -0 2>$null) -match [regex]::Escape($v)) }

if ($env:OS -ne "Windows_NT") { throw "This script is for Windows." }

# --- 1. Prerequisites -----------------------------------------------------------
Step "Prerequisites"
Ensure-Winget "Git.Git"             { [bool](Get-Command git -ErrorAction SilentlyContinue) }
# JARVIS core runs on the newest Python (3.15 on the PC); the voice venv needs 3.12 (kokoro-onnx wheels).
Ensure-Winget "Python.Python.3.12"  { Has-Py "3.12" } -User
try { Ensure-Winget "Python.Python.3.15" { Has-Py "3.15" } -User } catch { }
if (-not (Has-Py "3.15")) { Ensure-Winget "Python.Python.3.13" { (Has-Py "3.13") -or (Has-Py "3.15") } -User }
Ensure-Winget "OpenJS.NodeJS.LTS"   { [bool](Get-Command node -ErrorAction SilentlyContinue) }
Ensure-Winget "Gyan.FFmpeg"         { [bool](Get-Command ffmpeg -ErrorAction SilentlyContinue) } -User
Ensure-Winget "Google.Chrome"       { (Test-Path "$env:ProgramFiles\Google\Chrome\Application\chrome.exe") -or (Test-Path "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe") -or (Test-Path "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe") }
Ensure-Winget "tailscale.tailscale" { [bool](Get-Command tailscale -ErrorAction SilentlyContinue) -or (Test-Path "$env:ProgramFiles\Tailscale\tailscale.exe") }
Ensure-Winget "Google.ChromeRemoteDesktopHost" { [bool](Get-Service chromoting -ErrorAction SilentlyContinue) }

$bash = @("$env:ProgramFiles\Git\bin\bash.exe", "$env:ProgramFiles\Git\usr\bin\bash.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($bash) { Ok "Git Bash found (the Claude Code hooks need it)" } else { Warn "Git Bash not found: the JARVIS hooks will not run" }

if (Get-Command claude -ErrorAction SilentlyContinue) { Ok "Claude Code CLI found" }
else {
  Write-Host "  installing Claude Code CLI ..."
  try { Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression; Refresh-Path } catch { Warn "Claude Code install failed: $($_.Exception.Message)" }
  $env:Path += ";$env:USERPROFILE\.local\bin"
  if (Get-Command claude -ErrorAction SilentlyContinue) { Ok "Claude Code CLI installed" } else { Warn "Claude Code CLI not on PATH yet: reopen PowerShell" }
}
$py = if (Has-Py "3.15") { "-3.15" } elseif (Has-Py "3.13") { "-3.13" } else { "-3.12" }

# --- 2. Repo --------------------------------------------------------------------
Step "Repository ($RepoDir, branch $Branch)"
if ($RepoDir -match "OneDrive") { throw "Clone outside OneDrive (e.g. C:\jarvis). OneDrive must never sync a live brain\jarvis.db." }
# Best source: the PC's own working copy, synced by OneDrive (has the branch and any uncommitted work).
# Copied without brain\ and node_modules, so the live brain is never touched. Fallback: GitHub.
$od = Join-Path $env:USERPROFILE "OneDrive\Documents\GitHub\jarvis"
# Or a copy of it that came on a USB stick next to this script (jarvis-code), when OneDrive hasn't synced.
$usb = Join-Path $PSScriptRoot "jarvis-code"
if (-not (Test-Path (Join-Path $od ".git")) -and (Test-Path (Join-Path $usb ".git"))) { $od = $usb }
if (-not (Test-Path (Join-Path $RepoDir ".git")) -and (Test-Path (Join-Path $od ".git"))) {
  Write-Host "  copying the PC's working copy from OneDrive (downloads on demand) ..."
  robocopy $od $RepoDir /E /XD "$od\brain" "$od\node_modules" /XF "jarvis.db*" /NFL /NDL /NJH /NJS /NP | Out-Null
  Ok "code copied from OneDrive ($(git -C $RepoDir branch --show-current))"
} elseif (Test-Path (Join-Path $RepoDir ".git")) {
  Push-Location $RepoDir
  if (git status --porcelain) { Warn "local changes in $RepoDir, pull skipped" }
  else { git fetch -q origin; git checkout -q $Branch; git pull -q --ff-only origin $Branch }
  Pop-Location
  Ok "repo up to date"
} else {
  git clone -q --branch $Branch $RepoUrl $RepoDir
  Ok "cloned"
}
Set-Location $RepoDir

# --- 3. Machine role: standby (before anything can start the bridge) ---------------
Step "Machine role"
& py $py core\jarvis_cli.py machine standby
Ok "this laptop is the STANDBY (Telegram, routines and calls stay with the PC)"

# --- 4. Brain -------------------------------------------------------------------
Step "Brain"
$brain = Join-Path $RepoDir "brain"
$drop = Join-Path $env:USERPROFILE "OneDrive\JARVIS-laptop-transfer"
# Run from a USB stick (or any copy of this folder) when OneDrive hasn't synced: use the files next to the script.
if (-not (Test-Path (Join-Path $drop "jarvis-brain-lean.zip")) -and (Test-Path (Join-Path $PSScriptRoot "jarvis-brain-lean.zip"))) {
  $drop = $PSScriptRoot; $env:JARVIS_SYNC_DIR = $PSScriptRoot
  Ok "using the brain next to this script ($drop), not OneDrive"
}
# The PC keeps one lean text-only brain here (jarvis-brain-lean.zip, ~40 MB, refreshed every 2 hours by its
# brain-sync routine). This laptop pulls it on every start and every routines tick (bin\jarvis sync pull).
if (-not $Bundle -and (Test-Path (Join-Path $drop "jarvis-brain-lean.zip"))) { $Bundle = "lean" }
if (Test-Path (Join-Path $brain "jarvis.db")) {
  & py $py core\jarvis_cli.py sync pull | Out-Host
  Ok "brain already here, refreshed from the PC's latest push"
} elseif ($Bundle -eq "lean") {
  Write-Host "  pulling the PC's lean brain from OneDrive (downloads it first if it is cloud-only) ..."
  & py $py core\jarvis_cli.py init | Out-Null
  & py $py core\jarvis_cli.py sync pull --force | Out-Host
  Ok "brain and secrets restored from the PC's latest push"
} elseif ($Bundle -and (Test-Path $Bundle)) {
  Write-Host "  restoring $Bundle ..."
  Expand-Archive -Path $Bundle -DestinationPath $brain -Force
  Ok "brain restored from bundle"
} else {
  & py $py core\jarvis_cli.py init
  Warn "no brain bundle found (OneDrive\JARVIS-laptop-transfer): empty brain created. Sign in to OneDrive and rerun."
}
Set-Content -Encoding utf8 (Join-Path $brain "STANDBY-LAPTOP.txt") "This brain is the laptop STANDBY copy, restored $(Get-Date -Format s). The home PC is the live JARVIS. Takeover steps: brain\work\general\laptop-install-guide.md."

# Private skills (Blake playbooks etc.) live in brain\skills; Claude Code reads them from ~\.claude\skills.
& py $py core\jarvis_cli.py skills backup --restore | Out-Host

# The call server binds to this machine's own Tailscale address, not the PC's.
$ts = @("$env:ProgramFiles\Tailscale\tailscale.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($ts) {
  $ip = (& $ts ip -4 2>$null | Select-Object -First 1)
  if ($ip -match "^100\.") {
    & py $py -c "import sys; sys.path.insert(0, 'core'); from jarvis import paths; paths.set_config('sip_bind', '$ip')"
    Ok "sip_bind set to this laptop's Tailscale address $ip"
  } else { Warn "Tailscale not signed in: open Tailscale and sign in with the same account as the PC (bakermarley125@)" }
}

# --- 5. Tools: cloudflared (call link tunnel), no admin -----------------------------
Step "cloudflared"
$cf = Join-Path $brain "tools\cloudflared.exe"
if (Test-Path $cf) { Ok "cloudflared present" }
else {
  New-Item -ItemType Directory -Force (Split-Path $cf) | Out-Null
  Invoke-WebRequest "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $cf -UseBasicParsing
  Ok "cloudflared downloaded to brain\tools"
}

# --- 6. Voice + speech-to-text venv (Kokoro, faster-whisper) --------------------------
if (-not $NoVoice) {
  Step "Voice venv (Kokoro + Whisper, Python 3.12)"
  $tts = Join-Path $brain "tts"
  if (-not (Test-Path "$tts\venv\Scripts\python.exe")) { & py -3.12 -m venv "$tts\venv" }
  & "$tts\venv\Scripts\python.exe" -m pip install -q --upgrade pip
  & "$tts\venv\Scripts\python.exe" -m pip install -q "kokoro-onnx==0.6.1" "faster-whisper==1.2.1" soundfile
  New-Item -ItemType Directory -Force "$tts\models" | Out-Null
  $base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
  foreach ($f in "kokoro-v1.0.onnx", "voices-v1.0.bin") {
    if (-not (Test-Path "$tts\models\$f")) { Invoke-WebRequest "$base/$f" -OutFile "$tts\models\$f" -UseBasicParsing }
  }
  Ok "Kokoro + Whisper ready (Whisper base.en downloads on the first call)"
}
if (-not $NoMedia) {
  Step "Reel/video tools (yt-dlp + faster-whisper)"
  & py $py core\jarvis_cli.py watch --setup | Out-Host
  Ok "media venv ready (%LOCALAPPDATA%\JARVIS\media-venv)"
}

# --- 7. Shortcuts + routines task (safe: standby keeps the bridge and ticks off) -----
if (-not $NoShortcuts) {
  Step "Shortcuts and routines task"
  $pyw = (& py $py -c "import sys, pathlib; print(pathlib.Path(sys.executable).with_name('pythonw.exe'))")
  & $pyw core\jarvis_app.pyw --install
  Ok "JARVIS / JARVIS Chat shortcuts + Startup entry"
  & py $py core\jarvis_cli.py routines install | Out-Host
}

# --- 8. Check ----------------------------------------------------------------------
Step "Tests"
& py $py -m unittest discover -s core/tests -t core/tests 2>&1 | Select-Object -Last 3 | Out-Host
& py $py core\jarvis_cli.py machine | Out-Host

Step "Done"
$msg = @"
Laptop is the JARVIS STANDBY. Code: $RepoDir. Log: $log
Still to do by hand (only you can sign in):
  1. Tailscale: sign in (same account as the PC) so JARVIS can reach this laptop remotely.
  2. Claude Code: run 'claude' in $RepoDir once and log in (claude.ai connectors - Shopify, Meta ads,
     Google Drive - come with the account).
  3. Chrome Remote Desktop: remotedesktop.google.com/access > Set up remote access.
Takeover if the PC dies: bin\jarvis machine primary, then restart JARVIS (Startup shortcut) and
  bin\jarvis call sip --from-linphone only if Linphone desktop is installed here.
"@
Write-Host $msg
if ($todo.Count) { Write-Host "`nWarnings:" -ForegroundColor Yellow; $todo | ForEach-Object { Write-Host "  - $_" } }
# Tell the owner's phone (works with the restored Telegram token; send is allowed on a standby).
& py $py core\jarvis_cli.py telegram send "Laptop setup finished: standby ready at $RepoDir. $($todo.Count) warnings, see $log." 2>$null | Out-Null
Stop-Transcript | Out-Null
