# Lets the owner's phone reach JARVIS's phone line (core/jarvis/sip.py) through Windows Firewall.
# Only from the Tailscale network (100.64.0.0/10), only UDP: SIP on 5060 and the call audio on 40000-40100.
# Run once: right-click > Run with PowerShell (it asks for administrator itself).

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

$rules = @(
    @{ Name = "JARVIS phone line (SIP)"; Port = "5060" },
    @{ Name = "JARVIS phone line (call audio)"; Port = "40000-40100" }
)
foreach ($r in $rules) {
    Get-NetFirewallRule -DisplayName $r.Name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $r.Name -Direction Inbound -Action Allow -Protocol UDP `
        -LocalPort $r.Port -RemoteAddress "100.64.0.0/10" -Profile Any | Out-Null
    Write-Host "Allowed: $($r.Name), UDP $($r.Port), from Tailscale only"
}
Write-Host "Done. You can close this window."
Start-Sleep -Seconds 5
