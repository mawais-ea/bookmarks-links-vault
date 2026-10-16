# Restart the LinkVault server, e.g. after changing code or backend\.env.
#
#   Right-click > "Run with PowerShell", or:  powershell -ExecutionPolicy Bypass -File scripts\restart-server.ps1
#
# Asks for administrator rights (UAC) by itself, because the server runs as a system task.

$taskName = "LinkVault Server"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -NoPause"
    exit
}

if (-not (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) {
    Write-Host "The startup task isn't installed. Run scripts\install-startup.ps1 first." -ForegroundColor Yellow
} else {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
    Start-Sleep -Seconds 2
    Start-ScheduledTask -TaskName $taskName
    Start-Sleep -Seconds 10
    if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "LinkVault Server restarted." -ForegroundColor Green
    } else {
        Write-Host "The server didn't come back. Check backend\server.log." -ForegroundColor Yellow
    }
}
if ($args -notcontains "-NoPause") { Read-Host "Press Enter to close" }
