#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
if [[ ! -d frontend/node_modules ]]; then
  npm --prefix frontend install
fi
if [[ ! -f frontend/dist/index.html ]]; then
  npm --prefix frontend run build
fi
exec .venv/bin/python -m app.main