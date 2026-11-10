# Set the GitHub key the Claude GitHub plugin uses (GITHUB_PERSONAL_ACCESS_TOKEN),
# on whichever machine this runs: the PC or the laptop.
#
# The owner pastes the key here; it is checked against GitHub first and only saved
# if GitHub accepts it. JARVIS never sees or types the key.
#
# Run it by double-clicking set-github-key.cmd from File Explorer. Started from inside
# the Claude app the save can silently not stick (packaged app), so use Explorer.

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$name = 'GITHUB_PERSONAL_ACCESS_TOKEN'

function Test-Key([string]$key) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri 'https://api.github.com/user' `
            -Headers @{ Authorization = "Bearer $key"; 'User-Agent' = 'jarvis-github-key' }
        return ($r.Content | ConvertFrom-Json).login
    } catch { return $null }
}

Write-Host ''
Write-Host 'JARVIS - GitHub key for the Claude GitHub plugin' -ForegroundColor Cyan
Write-Host ''

$current = [Environment]::GetEnvironmentVariable($name, 'User')
if ($current) {
    $who = Test-Key $current
    if ($who) {
        Write-Host "The saved key already works (GitHub account: $who). Nothing to do." -ForegroundColor Green
        Write-Host 'If the plugin still fails, fully quit Claude (tray icon > Quit) and reopen it.'
        Read-Host 'Press Enter to close'
        exit 0
    }
    Write-Host 'The saved key is rejected by GitHub (expired or revoked). Let us replace it.' -ForegroundColor Yellow
} else {
    Write-Host 'No GitHub key is saved on this machine yet.' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'Opening GitHub in your browser to make a new key:'
Write-Host '  1. Note: type "JARVIS"  (pick an expiry, e.g. 1 year)'
Write-Host '  2. The boxes repo, read:org and workflow are already ticked'
Write-Host '  3. Click "Generate token" at the bottom, then copy the ghp_... key'
Write-Host '  Make a NEW key - do not press "Regenerate" on the laptop key, that would break the laptop.'
Start-Process 'https://github.com/settings/tokens/new?scopes=repo,read:org,workflow&description=JARVIS'
Write-Host ''

for ($try = 1; $try -le 3; $try++) {
    $secure = Read-Host 'Paste the key here and press Enter (it stays hidden)' -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $key = ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)).Trim() }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    if (-not $key) { Write-Host 'Nothing pasted. Right-click pastes in this window.' -ForegroundColor Yellow; continue }

    $who = Test-Key $key
    if ($who) {
        [Environment]::SetEnvironmentVariable($name, $key, 'User')
        $key = $null
        $check = [Environment]::GetEnvironmentVariable($name, 'User')
        if (-not $check -or -not (Test-Key $check)) {
            Write-Host 'GitHub accepted the key but Windows did not save it. Run this from File Explorer, not from inside Claude.' -ForegroundColor Red
            Read-Host 'Press Enter to close'; exit 1
        }
        Write-Host ''
        Write-Host "Saved. GitHub account: $who" -ForegroundColor Green
        Write-Host 'Last step: fully quit Claude (tray icon by the clock > Quit) and open it again.'
        Write-Host 'The GitHub plugin connects on the next start.'
        Read-Host 'Press Enter to close'
        exit 0
    }
    $key = $null
    Write-Host 'GitHub rejected that key. Copy it again (the whole ghp_... line) and paste.' -ForegroundColor Red
}
Write-Host 'Three tries failed; nothing was changed.' -ForegroundColor Red
Read-Host 'Press Enter to close'
exit 1
