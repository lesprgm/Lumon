#!/bin/zsh
set -euo pipefail
set -a
source "$(dirname "$0")/../.env" 2>/dev/null || true
set +a
cd "$(dirname "$0")/../backend"
export LUMON_DEMO_VARIANT=backup
PYTHON_BIN="./.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi
exec "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
