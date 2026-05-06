#!/usr/bin/env bash
# Start the FastAPI + static frontend (uvicorn). Run from the Pi after venv + deps are installed.
#
# Usage:
#   ./deploy/start-server.sh
#   HOST=127.0.0.1 PORT=8080 ./deploy/start-server.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND="$PROJECT_ROOT/backend"
VENV_PY="$BACKEND/.venv/bin/python"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing venv at ${BACKEND}/.venv — create it and: pip install -r requirements-pi.txt" >&2
  exit 1
fi

cd "$BACKEND"
exec "$VENV_PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
