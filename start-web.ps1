param(
    [switch]$Dev
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "[grok-auto] root = $Root"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[setup] creating .venv ..."
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "py -3 -m venv failed. Install Python 3 and ensure py launcher works." }
}
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm not found. Install Node.js LTS, then re-open the terminal."
}

if (-not (Test-Path "frontend\node_modules")) {
    Write-Host "[setup] npm install (frontend) ..."
    npm --prefix frontend install
    if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
}

$DistIndex = Join-Path $Root "frontend\dist\index.html"
$NeedsFrontendBuild = -not (Test-Path $DistIndex)
if (-not $NeedsFrontendBuild) {
    $DistTime = (Get-Item $DistIndex).LastWriteTimeUtc
    $FrontendInputs = @(
        (Join-Path $Root "frontend\src"),
        (Join-Path $Root "frontend\index.html"),
        (Join-Path $Root "frontend\package.json"),
        (Join-Path $Root "frontend\package-lock.json"),
        (Join-Path $Root "frontend\vite.config.ts"),
        (Join-Path $Root "frontend\tsconfig.json"),
        (Join-Path $Root "frontend\tsconfig.app.json")
    )
    $LatestInput = Get-ChildItem -Path $FrontendInputs -Recurse -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    $NeedsFrontendBuild = $null -ne $LatestInput -and $LatestInput.LastWriteTimeUtc -gt $DistTime
}

if ($Dev) {
    Write-Host "[dev] starting frontend vite dev server..."
    Start-Process -FilePath "npm.cmd" -ArgumentList "--prefix","frontend","run","dev" -WorkingDirectory $Root
} elseif ($NeedsFrontendBuild) {
    Write-Host "[build] frontend source changed, building dist..."
    npm --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
}

try {
    Invoke-RestMethod -Uri "http://127.0.0.1:5072/health" -TimeoutSec 3 | Out-Null
    Write-Host "[ready] Turnstile Solver: http://127.0.0.1:5072"
} catch {
    Write-Warning "Local Turnstile Solver not ready. If you use local captcha, run start-solver.bat first."
}

Write-Host "[start] console -> http://127.0.0.1:18080"
& ".venv\Scripts\python.exe" -m app.main
