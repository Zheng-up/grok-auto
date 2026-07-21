param(
    [switch]$Dev
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    py -3 -m venv .venv
}
& '.venv\Scripts\python.exe' -m pip install -r requirements.txt

if (-not (Test-Path 'frontend\node_modules')) {
    npm --prefix frontend install
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
    Start-Process -FilePath 'npm.cmd' -ArgumentList '--prefix','frontend','run','dev' -WorkingDirectory $Root
} elseif ($NeedsFrontendBuild) {
    Write-Host '[build] 检测到前端源码更新，正在生成最新页面...'
    npm --prefix frontend run build
}

try {
    Invoke-RestMethod -Uri 'http://127.0.0.1:5072/health' -TimeoutSec 3 | Out-Null
    Write-Host '[ready] Turnstile Solver: http://127.0.0.1:5072'
} catch {
    Write-Warning '本地 Turnstile Solver 未就绪；如使用本地验证，请先运行 启动Solver.bat。'
}

& '.venv\Scripts\python.exe' -m app.main