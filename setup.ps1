$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

Write-Host ""
Write-Host "JARVIS Windows setup" -ForegroundColor Cyan
Write-Host "====================" -ForegroundColor DarkCyan

if ($env:OS -ne "Windows_NT") {
  throw "JARVIS computer control currently requires Windows."
}

$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
  Write-Host ""
  Write-Host "Node.js 20 or newer is required." -ForegroundColor Yellow
  Write-Host "Install it with:" -ForegroundColor Yellow
  Write-Host "  winget install --id OpenJS.NodeJS.LTS -e" -ForegroundColor White
  Write-Host "Then close and reopen PowerShell and run .\setup.ps1 again."
  exit 1
}

$nodeMajor = [int]((node --version).TrimStart("v").Split(".")[0])
if ($nodeMajor -lt 20) {
  throw "Node.js 20 or newer is required. Current version: $(node --version)"
}

$workspace = Join-Path $env:USERPROFILE "Documents\JarvisWorkspace"
New-Item -ItemType Directory -Path $workspace -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $workspace "Inbox") -Force | Out-Null

if (-not (Test-Path -LiteralPath ".env")) {
  Copy-Item -LiteralPath ".env.example" -Destination ".env"
  Write-Host "Created .env with restricted default access." -ForegroundColor Green
}

Write-Host ""
Write-Host "Installing JARVIS packages..." -ForegroundColor Cyan
npm install --legacy-peer-deps --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw "npm install failed." }

Write-Host ""
Write-Host "Installing Playwright Chromium..." -ForegroundColor Cyan
npx playwright install chromium
if ($LASTEXITCODE -ne 0) { throw "Playwright browser installation failed." }

Write-Host ""
Write-Host "Setup complete. Starting JARVIS..." -ForegroundColor Green
npm run dev
