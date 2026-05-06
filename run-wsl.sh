#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"

if [[ ! -d "${BACKEND_DIR}" ]]; then
  echo "ERROR: backend/ folder not found next to this script."
  exit 1
fi

cd "${BACKEND_DIR}"

if [[ ! -d ".venv" ]]; then
  echo "Creating venv at backend/.venv ..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if [[ "${1:-}" == "--install" ]]; then
  echo "Installing backend dependencies ..."
  python -m pip install -r requirements.txt
  echo ""
fi

echo ""
echo "Starting AI Light Canvas backend:"
echo "- URL: http://localhost:8000/"
echo "- Stop: CTRL+C"
echo ""

exec uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

