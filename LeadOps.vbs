' LeadOps — silent desktop launcher (shows custom icon via .lnk)
Option Explicit
Dim sh, fso, root, ps1
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = root & "\scripts\launch_leadops.ps1"
' 0 = hide PowerShell window — feels like a real desktop app
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1 & """", 0, False
