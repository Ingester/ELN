# ELN 云端启动器 —— 确保本地服务在跑（顺带确保 Cloudflare 隧道服务在跑），
# 然后打开公网地址 https://eln.yanchang-online.com/ 。
# 用法：双击桌面的「ELN 云端」快捷方式即可（它调用 eln_cloud.vbs 静默运行本脚本）。

$ErrorActionPreference = "SilentlyContinue"
$root      = Split-Path -Parent $MyInvocation.MyCommand.Path
$publicUrl = "https://eln.yanchang-online.com/"

function Test-PortListening([int]$port) {
    try {
        return $null -ne (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop |
            Select-Object -First 1)
    } catch { return $false }
}

# 1) 隧道：cloudflared 是 Windows 服务，通常开机自启；万一停了，尽力拉起来。
$svc = Get-Service -Name "Cloudflared" -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -ne "Running") {
    Start-Service -Name "Cloudflared" -ErrorAction SilentlyContinue
}

# 2) 本地服务：没在跑就用后台启动脚本拉起（幂等）。
if (-not (Test-PortListening 8600)) {
    & (Join-Path $root "start_eln_background.ps1")
}

# 3) 等本地 API 就绪（最多约 25 秒）。
for ($i = 0; $i -lt 25; $i++) {
    if (Test-PortListening 8600) { break }
    Start-Sleep -Seconds 1
}

# 4) 打开公网地址（走隧道，会要求输入密码——这是对外保护，属正常）。
Start-Process $publicUrl
