@echo off
cd /d "%~dp0turnstile-solver"
call TurnstileSolver.bat
if errorlevel 1 pause