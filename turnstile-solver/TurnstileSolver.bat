@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv || exit /b 1
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || exit /b 1
)
REM Camoufox pip package != browser binary. Always ensure official/stable is fetched.
".venv\Scripts\python.exe" -c "import subprocess,sys; from pathlib import Path; r=subprocess.run([sys.executable,'-m','camoufox','active'],capture_output=True,text=True); t=(r.stdout or '')+(r.stderr or ''); sys.exit(0 if 'not fetched' not in t.lower() and r.returncode==0 else 1)" >nul 2>&1
if errorlevel 1 (
  echo [Solver] Camoufox browser missing, running: python -m camoufox fetch
  ".venv\Scripts\python.exe" -m camoufox fetch || exit /b 1
)
if not exist "logs" mkdir logs
if not exist "keys" mkdir keys
".venv\Scripts\python.exe" api_solver.py --browser_type camoufox --thread 2 --debug --host 127.0.0.1 --port 5072