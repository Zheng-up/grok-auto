@echo off
setlocal EnableExtensions
cd /d "%~dp0"
where powershell >nul 2>&1
if errorlevel 1 (
  echo [error] powershell not found.
  pause
  exit /b 1
)
echo.
echo Open this URL after start:
echo   http://127.0.0.1:18080
echo (http only, port required; 5072 is Solver not the Web UI)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-web.ps1" %*
set ERR=%ERRORLEVEL%
if not "%ERR%"=="0" (
  echo.
  echo [error] start-web.ps1 exited with code %ERR%
  pause
  exit /b %ERR%
)
exit /b 0
