@echo off
cd /d "%~dp0.."
title LeadOps Server
REM Prefer `python` (3.9+) when present — many client PCs have no `py` launcher
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if not errorlevel 1 (
  python frontend_server.py
  goto :eof
)
where py >nul 2>&1 && (
  py -3.12 -c "import sys" >nul 2>&1 && (
    py -3.12 frontend_server.py
    goto :eof
  )
  py -3 frontend_server.py
  goto :eof
)
echo ERROR: Need Python 3.9+. Install Python 3.12 and Add to PATH.
pause
