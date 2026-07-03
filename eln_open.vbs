' ELN — 桌面快捷方式目标：静默确保服务在跑，然后打开浏览器。全程无黑窗。
Dim sh, root
Set sh = CreateObject("WScript.Shell")
root = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -File """ & root & "start_eln_background.ps1"" -OpenBrowser", 0, False
