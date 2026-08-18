@echo off
REM Opens the same desktop-app launcher (creates icon if missing)
cd /d "%~dp0"
wscript //nologo "%~dp0LeadOps.vbs"
