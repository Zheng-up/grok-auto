@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0start-web.bat" %*
exit /b %ERRORLEVEL%
