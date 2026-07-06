# Create/refresh the ELN desktop shortcuts with custom flask icons.
#   ELN 云端      -> eln_cloud.vbs   (icon eln_cloud.ico)   ensure service + open public URL
#   ELN 实验记录  -> its existing target, icon swapped to eln_local.ico
# ASCII-only source. WScript.Shell's Save() mangles non-codepage filenames, so we always
# save to a temp ASCII name and rename via .NET (Unicode-safe). Idempotent / re-runnable.
$root    = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [Environment]::GetFolderPath("Desktop")

$cloudName = "ELN " + [char]0x4E91 + [char]0x7AEF + ".lnk"                                    # 云端
$localName = "ELN " + [char]0x5B9E + [char]0x9A8C + [char]0x8BB0 + [char]0x5F55 + ".lnk"      # 实验记录

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

function Read-Lnk($path) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $shell  = New-Object -ComObject Shell.Application
    $folder = $shell.Namespace((Split-Path $path))
    $item   = $folder.ParseName((Split-Path $path -Leaf))
    if (-not $item) { return $null }
    $link = $item.GetLink
    return @{ Target = $link.Path; Args = $link.Arguments; Work = $link.WorkingDirectory }
}

# 1) ELN 云端 — we own this one fully
New-Lnk (Join-Path $desktop $cloudName) `
    "$env:SystemRoot\System32\wscript.exe" `
    ("`"" + (Join-Path $root "eln_cloud.vbs") + "`"") `
    $root `
    (Join-Path $root "eln_cloud.ico")
Write-Host "OK: ELN cloud shortcut (icon updated)"

# 2) ELN 实验记录 — keep its target, just swap the icon (if it exists)
$localPath = Join-Path $desktop $localName
$info = Read-Lnk $localPath
if ($info -and $info.Target) {
    New-Lnk $localPath $info.Target $info.Args $info.Work (Join-Path $root "eln_local.ico")
    Write-Host "OK: ELN local shortcut (icon updated)"
} else {
    Write-Host "note: ELN local shortcut not found, skipped"
}
