@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0start-solver.bat" %*
exit /b %ERRORLEVEL%
