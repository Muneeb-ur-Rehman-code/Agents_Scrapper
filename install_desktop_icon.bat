@echo off
REM One-time / anytime: create proper Desktop LeadOps app icon, then launch
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\create_desktop_shortcut.ps1"
echo.
echo Desktop app icon ready: LeadOps
echo Opening LeadOps...
wscript //nologo "%~dp0LeadOps.vbs"
