from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
FRONTEND_ROOT = REPO_ROOT / "frontend"
OPENCODE_ROOT = REPO_ROOT / ".opencode"
BACKEND_VENV = BACKEND_ROOT / ".venv"
BACKEND_PYTHON = BACKEND_VENV / "bin" / "python"
BACKEND_PIP = BACKEND_VENV / "bin" / "pip"
DEFAULT_BACKEND_ORIGIN = "http://127.0.0.1:8000"
DEFAULT_FRONTEND_ORIGIN = "http://127.0.0.1:8000"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
