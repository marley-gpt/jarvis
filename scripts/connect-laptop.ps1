# One-time: connect this laptop to JARVIS so the PC can finish and maintain the setup by itself.
# Run by double-clicking CONNECT-LAPTOP.cmd (it asks for admin once).
$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $env:USERPROFILE "connect-laptop-log.txt"
Start-Transcript -Path $log -Force | Out-Null

Write-Host "1/5 Updating the JARVIS code in C:\jarvis"
if (Test-Path C:\jarvis\.git) {
  Push-Location C:\jarvis
  git stash push -u -m "auto-stash before connect" | Out-Null
  git fetch -q origin
  git checkout -q jarvis
  git pull -q --ff-only origin jarvis
  git log --oneline -1
  Pop-Location
}

Write-Host "2/5 Running the standard laptop setup (idempotent)"
& (Join-Path $here "laptop-setup.ps1") -Branch jarvis

Write-Host "3/5 Installing Tailscale so the PC can reach this laptop"
if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
  winget install --id Tailscale.Tailscale -e --accept-source-agreements --accept-package-agreements
}

Write-Host "4/5 Turning on the SSH server (Tailscale network only) so JARVIS can set things up remotely"
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
Set-Service sshd -StartupType Automatic
Start-Service sshd
New-NetFirewallRule -Name "JARVIS-SSH-Tailscale" -DisplayName "JARVIS SSH (Tailscale only)" -Protocol TCP -LocalPort 22 -RemoteAddress 100.64.0.0/10 -Action Allow -ErrorAction SilentlyContinue | Out-Null
$keyFile = Join-Path $here "pc-ssh-key.pub"
if (Test-Path $keyFile) {
  $ak = "C:\ProgramData\ssh\administrators_authorized_keys"
  Get-Content $keyFile | Add-Content $ak
  icacls $ak /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F" | Out-Null
}

Write-Host "5/5 Starting JARVIS and the sync"
Push-Location C:\jarvis
python bin\jarvis sync pull --force
python bin\jarvis fleet
Start-Process pythonw -ArgumentList "core\jarvis_app.pyw"
Pop-Location

Write-Host ""
Write-Host "Done. If Tailscale opened a sign-in window, sign in with the same account as the PC (bakermarley125)."
Stop-Transcript | Out-Null
Read-Host "Press Enter to close"
