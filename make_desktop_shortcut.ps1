# Create a single ELN desktop shortcut:
#   "ELN 实验记录" -> eln_cloud.vbs  (ensure service up, then open the public URL)
#   icon: eln.ico (light clay flask)
# Also removes the old "ELN 云端" shortcut so only one remains.
# ASCII-only source. WScript.Shell's Save() mangles non-codepage filenames, so we save to a
# temp ASCII name and rename via .NET (Unicode-safe). Idempotent / re-runnable.
$root    = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [Environment]::GetFolderPath("Desktop")

$name      = "ELN " + [char]0x5B9E + [char]0x9A8C + [char]0x8BB0 + [char]0x5F55 + ".lnk"  # 实验记录
$oldCloud  = "ELN " + [char]0x4E91 + [char]0x7AEF + ".lnk"                                 # 云端

function New-Lnk($finalPath, $target, $arguments, $workdir, $icon) {
    $tmp = Join-Path (Split-Path $finalPath) ("_lnk_tmp_" + [guid]::NewGuid().ToString('N') + ".lnk")
    $ws  = New-Object -ComObject WScript.Shell
    $sc  = $ws.CreateShortcut($tmp)
    $sc.TargetPath       = $target
    $sc.Arguments        = $arguments
    $sc.WorkingDirectory = $workdir
    if ($icon) { $sc.IconLocation = "$icon,0" }
    $sc.WindowStyle      = 7
    $sc.Save()
    if (Test-Path -LiteralPath $finalPath) { Remove-Item -LiteralPath $finalPath -Force }
    [System.IO.File]::Move($tmp, $finalPath)
}

# remove the extra "ELN 云端" shortcut if it exists
$oldPath = Join-Path $desktop $oldCloud
if (Test-Path -LiteralPath $oldPath) { Remove-Item -LiteralPath $oldPath -Force }

# the single shortcut: named 实验记录, opens the cloud URL
New-Lnk (Join-Path $desktop $name) `
    "$env:SystemRoot\System32\wscript.exe" `
    ("`"" + (Join-Path $root "eln_cloud.vbs") + "`"") `
    $root `
    (Join-Path $root "eln.ico")

if (Test-Path -LiteralPath (Join-Path $desktop $name)) { Write-Host "OK: desktop shortcut ready (ELN 实验记录 -> cloud)" }
else { Write-Host "Failed to create shortcut" }
