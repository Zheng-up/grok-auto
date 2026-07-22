#!/usr/bin/env bash
# Rebuild and run grok-auto + turnstile solver with safe defaults.
# - solver network alias: solver
# - console bind: 127.0.0.1:18080
# - data: /opt/grok-auto/data
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

IMAGE_CONSOLE="${IMAGE_CONSOLE:-grok-auto-console:local}"
IMAGE_SOLVER="${IMAGE_SOLVER:-grok-auto-solver:local}"
NETWORK="${NETWORK:-zradx-app}"
SOLVER_URL="${SOLVER_URL:-http://solver:5072}"

echo "[1/5] ensure networks"
docker network inspect zradx-app >/dev/null 2>&1 || docker network create \
  --label zradx.role=app \
  --label zradx.description="Shared app network for loopback-proxied services" \
  zradx-app
NETWORK="${NETWORK:-zradx-app}"

echo "[2/5] build images"
docker build -t "$IMAGE_SOLVER" "$ROOT/turnstile-solver"
docker build -t "$IMAGE_CONSOLE" "$ROOT"

echo "[3/5] recreate solver (alias=solver)"
docker stop grok-auto-solver 2>/dev/null || true
docker rm grok-auto-solver 2>/dev/null || true
docker run -d --name grok-auto-solver --restart unless-stopped \
  --network "$NETWORK" --network-alias solver \
  --shm-size=2g \
  -e TURNSTILE_HOST=0.0.0.0 \
  -e TURNSTILE_PORT=5072 \
  -e TURNSTILE_THREAD="${TURNSTILE_THREAD:-1}" \
  -e TURNSTILE_BROWSER_TYPE="${TURNSTILE_BROWSER_TYPE:-camoufox}" \
  -e TURNSTILE_PREFETCH_TTL="${TURNSTILE_PREFETCH_TTL:-75}" \
  "$IMAGE_SOLVER"

echo "[4/5] recreate console (127.0.0.1:18080)"
mkdir -p "$ROOT/data"
docker stop grok-auto 2>/dev/null || true
docker rm grok-auto 2>/dev/null || true

ENV_FILE_ARGS=()
if [[ -f "$ROOT/.env" ]]; then
  ENV_FILE_ARGS+=(--env-file "$ROOT/.env")
fi

docker run -d --name grok-auto --restart unless-stopped \
  --network "$NETWORK" \
  "${ENV_FILE_ARGS[@]}" \
  -e REG_CONSOLE_COOKIE_SECURE="${REG_CONSOLE_COOKIE_SECURE:-1}" \
  -e REG_CONSOLE_SESSION_HOURS="${REG_CONSOLE_SESSION_HOURS:-24}" \
  -e REG_CONSOLE_MAX_CONCURRENCY="${REG_CONSOLE_MAX_CONCURRENCY:-4}" \
  -e REG_CONSOLE_LOCAL_CONCURRENCY="${REG_CONSOLE_LOCAL_CONCURRENCY:-2}" \
  -e REG_CONSOLE_SOLVER_URL="$SOLVER_URL" \
  -p 127.0.0.1:18080:18080 \
  -v /opt/grok-auto/data:/app/data \
  "$IMAGE_CONSOLE"

echo "[5/5] health"
sleep 3
docker ps --filter name='grok-auto' --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
echo -n "console: "; curl -fsS http://127.0.0.1:18080/health || true; echo
echo -n "solver : "; docker exec grok-auto python -c "import urllib.request; print(urllib.request.urlopen('$SOLVER_URL/health', timeout=5).read().decode())" 2>/dev/null || \
  curl -fsS http://127.0.0.1:5072/health 2>/dev/null || echo '(solver internal-only OK if console can resolve alias)'
echo
echo "Done. Public entry: https://grokcli.zradx.com (OpenResty -> 127.0.0.1:18080)"
