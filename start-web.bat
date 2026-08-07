@echo off
setlocal EnableExtensions
cd /d "%~dp0"
where powershell >nul 2>&1
if errorlevel 1 (
  echo [error] powershell not found.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-web.ps1" %*
set ERR=%ERRORLEVEL%
if not "%ERR%"=="0" (
  echo.
  echo [error] start-web.ps1 exited with code %ERR%
  pause
  exit /b %ERR%
)
exit /b 0
