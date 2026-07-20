# ELN — silent background starter (idempotent).
# Used by the desktop shortcut and the logon autostart entry.
#   -OpenBrowser : after the server is up, open the local UI in the browser.
# Keeps footprint small: trims log files over 2 MB before starting.

param(
    [switch]$OpenBrowser
)

$ErrorActionPreference = "SilentlyContinue"

$repo   = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = "C:\Users\Ingester\mambaforge\envs\py310\python.exe"
$url    = "http://127.0.0.1:8600/run"
$logDir = Join-Path $repo "logs"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# ── keep disk usage low: truncate any log over 2 MB ──
foreach ($f in Get-ChildItem -Path $logDir, $repo -Filter *.log -File -ErrorAction SilentlyContinue) {
    if ($f.Length -gt 2MB) { Clear-Content -Path $f.FullName -ErrorAction SilentlyContinue }
}

function Test-PortListening([int]$port) {
    try {
        $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop |
            Select-Object -First 1
        return $null -ne $c
    } catch { return $false }
}

if (-not (Test-PortListening 8600)) {
    $env:ELN_API_HOST   = "0.0.0.0"   # LAN access for the phone
    $env:ELN_API_PORT   = "8600"
    $env:ELN_NATIVE_ONLY = "1"        # native 8600 pages only; no Flet on 8550
    Start-Process -FilePath $python -ArgumentList "run_web.py" `
        -WorkingDirectory $repo -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "eln_bg_out.log") `
        -RedirectStandardError  (Join-Path $logDir "eln_bg_err.log")

    for ($i = 0; $i -lt 25; $i++) {
        Start-Sleep -Seconds 1
        if (Test-PortListening 8600) { break }
    }
}

if ($OpenBrowser) {
    Start-Process $url
}
