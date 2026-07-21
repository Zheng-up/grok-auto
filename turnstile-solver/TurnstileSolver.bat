@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv || exit /b 1
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || exit /b 1
  ".venv\Scripts\python.exe" -m camoufox fetch
)
if not exist "logs" mkdir logs
if not exist "keys" mkdir keys
".venv\Scripts\python.exe" api_solver.py --browser_type camoufox --thread 2 --debug --host 127.0.0.1 --port 5072