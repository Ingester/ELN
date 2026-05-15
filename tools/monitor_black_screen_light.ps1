param(
    [int]$DurationSeconds = 900,
    [string]$LogPath = "C:\Users\Ingester\Documents\Codex\2026-05-12\files-mentioned-by-the-user-eln\eln_app_export\logs\black_screen_monitor_live2.log"
)

$ErrorActionPreference = "SilentlyContinue"
$logDir = Split-Path -Parent $LogPath
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Remove-Item -LiteralPath $LogPath -Force

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    Add-Content -LiteralPath $LogPath -Value "[$ts] $Message" -Encoding UTF8
}

function ProcSummary {
    param([string]$Name)
    $items = @(Get-Process -Name $Name -ErrorAction SilentlyContinue)
    $count = $items.Count
    $ws = ($items | Measure-Object -Property WorkingSet64 -Sum).Sum
    $cpu = ($items | Measure-Object -Property CPU -Sum).Sum
    if ($null -eq $ws) { $ws = 0 }
    if ($null -eq $cpu) { $cpu = 0 }
    return "$Name count=$count cpu=$([math]::Round([double]$cpu,2)) ws=$ws"
}

function Write-EventsSince {
    param([datetime]$Since)
    $providers = "Display|Kernel-Power|Power-Troubleshooter|Microsoft-Windows-DxgKrnl|Microsoft-Windows-Kernel-PnP|nvlddmkm|amdkmdag|igfx|WHEA|Winlogon|UserModePowerService"
    $messagePattern = "display|monitor|video|graphics|TDR|driver|power|sleep|resume|lock|unlock|显示|监视器|睡眠|恢复|驱动|黑屏"
    $events = @(Get-WinEvent -FilterHashtable @{LogName="System"; StartTime=$Since} |
        Where-Object { $_.ProviderName -match $providers -or $_.Message -match $messagePattern } |
        Select-Object -First 10)
    foreach ($e in $events) {
        $msg = ($e.Message -replace "\s+", " ").Trim()
        if ($msg.Length -gt 260) { $msg = $msg.Substring(0, 260) }
        Write-Log "EVENT System Provider=$($e.ProviderName) Id=$($e.Id) Level=$($e.LevelDisplayName) Time=$($e.TimeCreated) Message=$msg"
    }
}

Write-Log "LIGHT_MONITOR START DurationSeconds=$DurationSeconds"
Write-Log "Computer=$env:COMPUTERNAME User=$env:USERNAME"
$start = Get-Date
$lastEventCheck = $start.AddSeconds(-5)
$end = $start.AddSeconds($DurationSeconds)
$tick = 0

while ((Get-Date) -lt $end) {
    $tick++
    Write-Log "TICK $tick $(ProcSummary python) | $(ProcSummary flet) | $(ProcSummary msedgewebview2)"
    Write-EventsSince -Since $lastEventCheck
    $lastEventCheck = Get-Date
    Start-Sleep -Seconds 1
}

Write-EventsSince -Since $start
Write-Log "LIGHT_MONITOR END"
