@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Run-Dashboard.ps1"
if errorlevel 1 (
  echo.
  echo Dashboard launcher exited with an error.
  pause
)
endlocal
