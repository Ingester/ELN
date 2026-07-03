param(
    [switch]$NoBrowser,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = "C:\Users\Ingester\mambaforge\envs\py310\python.exe"
$ConfigPath = Join-Path $Root "cloudflare\config.yml"
$LogDir = Join-Path $Root "logs"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

if (-not (Test-Path $Python)) {
    throw "Python not found: $Python"
}

if (-not $env:ELN_AUTH_PASSWORD) {
    throw "Set ELN_AUTH_PASSWORD before starting public tunnel mode. Example: setx ELN_AUTH_PASSWORD `"your-long-password`""
}

$apiListening = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($apiListening -and $Restart) {
    $ownerIds = $apiListening | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($ownerId in $ownerIds) {
        if ($ownerId) {
            Stop-Process -Id $ownerId -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Seconds 2
    $apiListening = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
}

if (-not $apiListening) {
    $env:ELN_API_HOST = "127.0.0.1"
    $env:ELN_API_PORT = "8000"
    $env:ELN_WEB_HOST = "127.0.0.1"
    $env:ELN_WEB_PORT = "8550"
    $env:ELN_WEB_OPEN = "0"
    $env:ELN_NATIVE_ONLY = "1"
    Start-Process -FilePath $Python `
        -ArgumentList "run_web.py" `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "eln_cloudflare_stdout.log") `
        -RedirectStandardError (Join-Path $LogDir "eln_cloudflare_stderr.log")
    Start-Sleep -Seconds 3
} else {
    Write-Warning "ELN API is already running on 127.0.0.1:8000. If the password was changed, rerun with: .\start_eln_cloudflare.ps1 -Restart"
}

if (Test-Path $ConfigPath) {
    $cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
    if (-not $cloudflared) {
        $cloudflared = Get-Command cloudflared.exe -ErrorAction SilentlyContinue
    }
    if ($cloudflared) {
        $tunnelRunning = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like "*cloudflared*" -and $_.CommandLine -like "*$ConfigPath*" }

        if (-not $tunnelRunning) {
            Start-Process -FilePath $cloudflared.Source `
                -ArgumentList "tunnel --config `"$ConfigPath`" run" `
                -WorkingDirectory $Root `
                -WindowStyle Hidden `
                -RedirectStandardOutput (Join-Path $LogDir "cloudflared_stdout.log") `
                -RedirectStandardError (Join-Path $LogDir "cloudflared_stderr.log")
        }
    } else {
        Write-Warning "cloudflared was not found in PATH. If you used Cloudflare Dashboard's service install command, the Windows service may still run independently."
    }
} else {
    Write-Host "No cloudflare\config.yml found. Assuming Cloudflare Dashboard remote-managed tunnel/service is used."
}

if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:8000/run"
}

Write-Host "ELN API: http://127.0.0.1:8000/run"
Write-Host "Cloudflare public hostname should point to service URL: http://localhost:8000"
