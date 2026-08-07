@echo off
setlocal EnableExtensions
cd /d "%~dp0turnstile-solver"
if not exist "TurnstileSolver.bat" (
  echo [error] turnstile-solver\TurnstileSolver.bat not found
  pause
  exit /b 1
)
call "TurnstileSolver.bat"
set ERR=%ERRORLEVEL%
if not "%ERR%"=="0" (
  echo [error] solver exited with code %ERR%
  pause
  exit /b %ERR%
)
exit /b 0
