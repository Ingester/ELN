' ELN — 开机自启目标：只在后台静默启动服务，不开浏览器、无任何窗口。
Dim sh, root
Set sh = CreateObject("WScript.Shell")
root = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -File """ & root & "start_eln_background.ps1""", 0, False
