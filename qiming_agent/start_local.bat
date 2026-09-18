@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
  echo Python is not on PATH. Install Python 3.11 or newer first.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto failed
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Open http://127.0.0.1:8765 in your browser.
echo Default configuration is local demo mode. Press Ctrl+C to stop.
.venv\Scripts\python.exe -m qiming serve --port 8765
exit /b %errorlevel%
:failed
echo Setup failed. Check Python version, network and package permissions.
pause
exit /b 1
