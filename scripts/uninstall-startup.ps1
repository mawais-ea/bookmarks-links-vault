# Stop the LinkVault server from starting with Windows, and stop it now.
#
#   Right-click > "Run with PowerShell", or:  powershell -ExecutionPolicy Bypass -File scripts\uninstall-startup.ps1
#
# Your bookmarks, screenshots and settings are NOT deleted. Asks for administrator rights (UAC) by itself.

$taskName = "LinkVault Server"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed the startup task." -ForegroundColor Green
} else {
    Write-Host "No startup task was installed."
}

Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force; Write-Host "Stopped the running server." }

Write-Host "Public access through Tailscale is still configured (it just has nothing to show)."
Write-Host "To turn that off too:  tailscale funnel --https=443 off"
Read-Host "Press Enter to close"
