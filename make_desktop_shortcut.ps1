# Create a desktop shortcut "ELN 云端" -> eln_cloud.vbs (ensure service up, open public URL).
# ASCII-only source. WScript.Shell's Save() mangles non-codepage chars, so we save to a
# temp ASCII name and then rename via .NET (Unicode-safe). Re-running overwrites the old one.
$root    = Split-Path -Parent $MyInvocation.MyCommand.Path
$vbs     = Join-Path $root "eln_cloud.vbs"
$desktop = [Environment]::GetFolderPath("Desktop")
$tmpPath = Join-Path $desktop "ELN_cloud_tmp.lnk"

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($tmpPath)
$sc.TargetPath       = "$env:SystemRoot\System32\wscript.exe"
$sc.Arguments        = "`"$vbs`""
$sc.WorkingDirectory = $root
$sc.IconLocation     = "$env:SystemRoot\System32\SHELL32.dll,13"
$sc.Description       = "Start ELN service and open eln.yanchang-online.com"
$sc.WindowStyle      = 7
$sc.Save()

# rename to the Chinese name "ELN 云端.lnk" using .NET (handles Unicode filenames)
$finalPath = Join-Path $desktop ("ELN " + [char]0x4E91 + [char]0x7AEF + ".lnk")
if (Test-Path -LiteralPath $finalPath) { Remove-Item -LiteralPath $finalPath -Force }
[System.IO.File]::Move($tmpPath, $finalPath)

if (Test-Path -LiteralPath $finalPath) { Write-Host "OK: created desktop shortcut ELN 云端" }
else { Write-Host "Failed to create shortcut" }
