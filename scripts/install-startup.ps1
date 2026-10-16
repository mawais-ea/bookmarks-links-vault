# Make the LinkVault server start when Windows starts (before anyone logs in).
#
#   Right-click > "Run with PowerShell", or:  powershell -ExecutionPolicy Bypass -File scripts\install-startup.ps1
#
# Registers the scheduled task "LinkVault Server": runs at boot as SYSTEM, restarts it every
# minute if it stops, never times out. Asks for administrator rights (UAC) by itself.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$taskName = "LinkVault Server"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "Python environment not found at $pythonw (run the setup in README first)." }

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$root\start_server.pyw`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = "PT30S"  # let the network and Tailscale come up first
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Description "LinkVault API + vault web page on port 8000" `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

# Start it now too (stopping a copy that may already be running, e.g. started by hand).
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 10

if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "LinkVault Server is installed and running. It will start by itself whenever Windows starts." -ForegroundColor Green
} else {
    Write-Host "Task installed, but the server didn't start. Check backend\server.log." -ForegroundColor Yellow
}
Read-Host "Press Enter to close"
