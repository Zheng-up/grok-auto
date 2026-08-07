param(
    [switch]$Dev
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "[grok-auto] root = $Root"

# Local Windows defaults: bind loopback explicitly (avoids some proxy/firewall oddities).
if (-not $env:REG_CONSOLE_HOST) { $env:REG_CONSOLE_HOST = '127.0.0.1' }
if (-not $env:REG_CONSOLE_PORT) { $env:REG_CONSOLE_PORT = '18080' }
$HostAddr = $env:REG_CONSOLE_HOST
$Port = $env:REG_CONSOLE_PORT
$ConsoleUrl = "http://127.0.0.1:$Port"
if ($HostAddr -ne '0.0.0.0' -and $HostAddr -ne '::') {
    $ConsoleUrl = "http://${HostAddr}:$Port"
}

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host '[setup] creating .venv ...'
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'py -3 -m venv failed. Install Python 3 and ensure py launcher works.' }
}
& '.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw 'npm not found. Install Node.js LTS, then re-open the terminal.'
}

if (-not (Test-Path 'frontend\node_modules')) {
    Write-Host '[setup] npm install (frontend) ...'
    npm --prefix frontend install
    if ($LASTEXITCODE -ne 0) { throw 'npm install failed' }
}

$DistIndex = Join-Path $Root 'frontend\dist\index.html'
$NeedsFrontendBuild = -not (Test-Path $DistIndex)
if (-not $NeedsFrontendBuild) {
    $DistTime = (Get-Item $DistIndex).LastWriteTimeUtc
    $FrontendInputs = @(
        (Join-Path $Root 'frontend\src'),
        (Join-Path $Root 'frontend\index.html'),
        (Join-Path $Root 'frontend\package.json'),
        (Join-Path $Root 'frontend\package-lock.json'),
        (Join-Path $Root 'frontend\vite.config.ts'),
        (Join-Path $Root 'frontend\tsconfig.json'),
        (Join-Path $Root 'frontend\tsconfig.app.json')
    )
    $LatestInput = Get-ChildItem -Path $FrontendInputs -Recurse -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    $NeedsFrontendBuild = $null -ne $LatestInput -and $LatestInput.LastWriteTimeUtc -gt $DistTime
}

if ($Dev) {
    Write-Host '[dev] starting frontend vite dev server...'
    Start-Process -FilePath 'npm.cmd' -ArgumentList '--prefix','frontend','run','dev' -WorkingDirectory $Root
} elseif ($NeedsFrontendBuild) {
    Write-Host '[build] frontend dist missing/outdated, building...'
    npm --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw 'frontend build failed' }
}

if (-not (Test-Path $DistIndex)) {
    throw "frontend/dist/index.html missing after build. UI cannot start. Run: npm --prefix frontend run build"
}

try {
    Invoke-RestMethod -Uri 'http://127.0.0.1:5072/health' -TimeoutSec 3 | Out-Null
    Write-Host '[ready] Turnstile Solver: http://127.0.0.1:5072'
} catch {
    Write-Warning 'Local Turnstile Solver not ready. If you use local captcha, run start-solver.bat first.'
}

try {
    $inUse = Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction SilentlyContinue
    if ($inUse) {
        Write-Warning "Port $Port already has a LISTEN process. If page fails, close the old console window or change REG_CONSOLE_PORT."
    }
} catch {
}

Write-Host ''
Write-Host '===================================================='
Write-Host "  Open UI in browser (HTTP, include port):"
Write-Host "    $ConsoleUrl"
Write-Host "  Health check:"
Write-Host "    $ConsoleUrl/health"
Write-Host '  Do NOT use https:// and do NOT omit :port'
Write-Host "  If ERR_CONNECTION_REFUSED: disable system/browser proxy"
Write-Host "  for localhost, or try http://localhost:$Port"
Write-Host '===================================================='
Write-Host ''

$openScript = @"
`$url = '$ConsoleUrl'
for (`$i = 0; `$i -lt 60; `$i++) {
  try {
    `$r = Invoke-WebRequest -UseBasicParsing -Uri (`$url + '/health') -TimeoutSec 2
    if (`$r.StatusCode -ge 200 -and `$r.StatusCode -lt 500) {
      Start-Process `$url
      exit 0
    }
  } catch {
    Start-Sleep -Seconds 1
  }
}
"@
Start-Process -FilePath 'powershell' -WindowStyle Hidden -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $openScript
) | Out-Null

Write-Host "[start] binding $HostAddr`:$Port ..."
& '.venv\Scripts\python.exe' -m app.main
