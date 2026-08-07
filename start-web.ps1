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
if (-not $env:REG_CONSOLE_PORT_PROBE_LIMIT) { $env:REG_CONSOLE_PORT_PROBE_LIMIT = '50' }
$HostAddr = $env:REG_CONSOLE_HOST
$PreferredPort = [int]$env:REG_CONSOLE_PORT
$ProbeLimit = [Math]::Max(1, [int]$env:REG_CONSOLE_PORT_PROBE_LIMIT)

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

Write-Host ''
Write-Host '===================================================='
Write-Host "  Preferred port: $PreferredPort (auto +1 if busy, up to $ProbeLimit tries)"
Write-Host '  Real URL is printed by Python as:'
Write-Host '    [start] console -> http://127.0.0.1:<actual-port>'
Write-Host '  Do NOT use https:// and do NOT omit :port'
Write-Host '  If browser fails: disable system/browser proxy for localhost'
Write-Host '===================================================='
Write-Host ''

# Poll preferred..+limit for grok /health, then open the real URL (handles port auto-advance).
$openScript = @"
`$start = $PreferredPort
`$limit = $ProbeLimit
`$hostAddr = '$HostAddr'
for (`$n = 0; `$n -lt 90; `$n++) {
  for (`$p = 0; `$p -lt `$limit; `$p++) {
    `$port = `$start + `$p
    if (`$port -gt 65535) { break }
    `$disp = if (`$hostAddr -eq '0.0.0.0' -or `$hostAddr -eq '::') { '127.0.0.1' } else { `$hostAddr }
    `$url = "http://`${disp}:`${port}"
    try {
      `$r = Invoke-WebRequest -UseBasicParsing -Uri (`$url + '/health') -TimeoutSec 1
      if (`$r.StatusCode -ge 200 -and `$r.StatusCode -lt 500) {
        `$body = ''
        try { `$body = [string]`$r.Content } catch {}
        if (`$body -match 'grok-registration-console') {
          Start-Process `$url
          exit 0
        }
      }
    } catch {}
  }
  Start-Sleep -Milliseconds 500
}
"@
Start-Process -FilePath 'powershell' -WindowStyle Hidden -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $openScript
) | Out-Null

Write-Host "[start] launching console (host=$HostAddr preferred=$PreferredPort, auto-advance on busy) ..."
& '.venv\Scripts\python.exe' -m app.main
