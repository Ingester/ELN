param(
    [int]$DurationSeconds = 120,
    [string]$LogPath = "C:\Users\Ingester\Documents\Codex\2026-05-12\files-mentioned-by-the-user-eln\eln_app_export\logs\black_screen_monitor.log"
)

$ErrorActionPreference = "SilentlyContinue"

$logDir = Split-Path -Parent $LogPath
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    Add-Content -LiteralPath $LogPath -Value "[$ts] $Message" -Encoding UTF8
}

function Write-RecentEvents {
    param([datetime]$Since)
    $providers = "Display|Kernel-Power|Power-Troubleshooter|Microsoft-Windows-DxgKrnl|Microsoft-Windows-Kernel-PnP|nvlddmkm|amdkmdag|igfx|WHEA|Winlogon|UserModePowerService"
    $messagePattern = "display|monitor|video|graphics|TDR|driver|power|sleep|resume|lock|unlock|显示|监视器|睡眠|恢复|驱动|黑屏"
    $events = Get-WinEvent -FilterHashtable @{LogName="System"; StartTime=$Since} |
        Where-Object { $_.ProviderName -match $providers -or $_.Message -match $messagePattern } |
        Select-Object -First 30
    foreach ($e in $events) {
        $msg = ($e.Message -replace "\s+", " ").Trim()
        if ($msg.Length -gt 400) { $msg = $msg.Substring(0, 400) }
        Write-Log "EVENT System Provider=$($e.ProviderName) Id=$($e.Id) Level=$($e.LevelDisplayName) Time=$($e.TimeCreated) Message=$msg"
    }

    $appEvents = Get-WinEvent -FilterHashtable @{LogName="Application"; StartTime=$Since} |
        Where-Object { $_.ProviderName -match "Application Error|Windows Error Reporting|\.NET Runtime" -or $_.Message -match "python|flet|flutter|ELN|WebView|crash|fault|hang" } |
        Select-Object -First 30
    foreach ($e in $appEvents) {
        $msg = ($e.Message -replace "\s+", " ").Trim()
        if ($msg.Length -gt 400) { $msg = $msg.Substring(0, 400) }
        Write-Log "EVENT Application Provider=$($e.ProviderName) Id=$($e.Id) Level=$($e.LevelDisplayName) Time=$($e.TimeCreated) Message=$msg"
    }
}

Remove-Item -LiteralPath $LogPath -Force
Write-Log "MONITOR START DurationSeconds=$DurationSeconds"
Write-Log "Computer=$env:COMPUTERNAME User=$env:USERNAME"
Write-Log "Initial python processes:"
Get-Process python -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Log "PROC python Id=$($_.Id) Path=$($_.Path) Start=$($_.StartTime) Responding=$($_.Responding)"
}

$start = Get-Date
$lastEventCheck = $start.AddSeconds(-10)
$end = $start.AddSeconds($DurationSeconds)
$tick = 0

while ((Get-Date) -lt $end) {
    $tick++
    Write-Log "TICK $tick"
    Get-Process python -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Log "PROC python Id=$($_.Id) CPU=$($_.CPU) WS=$($_.WorkingSet64) Responding=$($_.Responding) MainWindowTitle=$($_.MainWindowTitle)"
    }
    Get-Process -Name flet,flutter,msedge,chrome,msedgewebview2 -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Log "PROC $($_.ProcessName) Id=$($_.Id) CPU=$($_.CPU) WS=$($_.WorkingSet64) Responding=$($_.Responding)"
    }
    Write-RecentEvents -Since $lastEventCheck
    $lastEventCheck = Get-Date
    Start-Sleep -Seconds 1
}

Write-RecentEvents -Since $start
Write-Log "MONITOR END"
