#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$ROOT/backend/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"
PIP_BIN="$VENV_DIR/bin/pip"

python3 -m venv --system-site-packages "$VENV_DIR"
"$PIP_BIN" install --no-build-isolation --no-deps -e "$ROOT/backend[dev]"
"$PYTHON_BIN" - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright():
    print("PLAYWRIGHT_OK")
PY
