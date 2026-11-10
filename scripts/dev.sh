#!/usr/bin/env bash
# Start CyberLab for development with one command: installs what's missing, builds the web
# app if needed, and runs the server on http://127.0.0.1:8000/app/
#
#   ./scripts/dev.sh            # start
#   ./scripts/dev.sh --rebuild  # rebuild the web app first
#
# Development mode has NO sign-in: anyone who can reach the server is the owner. That's why
# it only listens on 127.0.0.1 (this machine). In a Codespace, the port is forwarded to you
# privately through your GitHub login.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8000}"
REBUILD=0
[[ "${1:-}" == "--rebuild" ]] && REBUILD=1

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }

if ! command -v cyberlab >/dev/null 2>&1; then
  say "Installing the CyberLab backend (Python)"
  python3 -m pip install --quiet -e "$ROOT/backend"
fi

if [[ $REBUILD == 1 || ! -f "$ROOT/frontend/dist/index.html" ]]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm is needed to build the web app (Node.js 20+). The API will still run." >&2
  else
    say "Building the web app"
    (cd "$ROOT/frontend" && npm ci --no-audit --no-fund && npm run build)
  fi
fi

if docker info >/dev/null 2>&1; then
  PROVIDER=docker
else
  PROVIDER=none
  say "Docker isn't reachable: environments will show NOT CONNECTED (everything else works)"
fi

mkdir -p "$ROOT/cyberlab-data"
export CYBERLAB_ENVIRONMENT="${CYBERLAB_ENVIRONMENT:-development}"
export CYBERLAB_AUTH_MODE="${CYBERLAB_AUTH_MODE:-dev}"
export CYBERLAB_EXECUTION_PROVIDER="${CYBERLAB_EXECUTION_PROVIDER:-$PROVIDER}"
export CYBERLAB_DATABASE_URL="${CYBERLAB_DATABASE_URL:-sqlite:///$ROOT/cyberlab-data/cyberlab.db}"
export CYBERLAB_SNAPSHOT_DIR="${CYBERLAB_SNAPSHOT_DIR:-$ROOT/cyberlab-data/snapshots}"

say "CyberLab is starting: open http://127.0.0.1:$PORT/app/  (Ctrl+C to stop)"
cd "$ROOT/backend"
exec python3 -m uvicorn --factory cyberlab.main:app_factory --host 127.0.0.1 --port "$PORT"
