$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = "C:\Users\Ingester\mambaforge\envs\py310\python.exe"
$url = "http://127.0.0.1:8550/"
$logDir = Join-Path $repo "logs"
$outLog = Join-Path $logDir "launcher_web_out.log"
$errLog = Join-Path $logDir "launcher_web_err.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (-not (Test-Path $python)) {
    Write-Host "Python not found: $python"
    Read-Host "Press Enter to exit"
    exit 1
}

function Test-PortListening([int]$port) {
    try {
        $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop |
            Where-Object { $_.LocalAddress -in @("0.0.0.0", "127.0.0.1", "::") } |
            Select-Object -First 1
        return $null -ne $conn
    } catch {
        return $false
    }
}

$webRunning = Test-PortListening 8550
$apiRunning = Test-PortListening 8000

if (-not ($webRunning -and $apiRunning)) {
    Write-Host "Starting ELN service..."
    $env:ELN_WEB_HOST = "0.0.0.0"
    $env:ELN_WEB_PORT = "8550"
    $env:ELN_WEB_OPEN = "0"
    Start-Process -FilePath $python -ArgumentList "run_web.py" -WorkingDirectory $repo -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog

    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        if ((Test-PortListening 8550) -and (Test-PortListening 8000)) {
            break
        }
    }

    if (-not ((Test-PortListening 8550) -and (Test-PortListening 8000))) {
        Write-Host "ELN service did not start within 20 seconds."
        Write-Host "Error log: $errLog"
        if (Test-Path $errLog) {
            Get-Content $errLog -Tail 20
        }
        exit 1
    }
} else {
    Write-Host "ELN service is already running."
}

Start-Process $url
