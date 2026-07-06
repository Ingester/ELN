' ELN 云端 —— 桌面快捷方式目标：静默确保服务在跑，然后打开公网地址。全程无黑窗。
Dim sh, root
Set sh = CreateObject("WScript.Shell")
root = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -File """ & root & "open_eln_cloud.ps1""", 0, False
