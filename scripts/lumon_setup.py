from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
FRONTEND_ROOT = REPO_ROOT / "frontend"
OPENCODE_ROOT = REPO_ROOT / ".opencode"
BACKEND_VENV = BACKEND_ROOT / ".venv"
BACKEND_PYTHON = BACKEND_VENV / "bin" / "python"
BACKEND_PIP = BACKEND_VENV / "bin" / "pip"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install Lumon once for the plugin-first local workflow.",
    )
    parser.add_argument("--skip-backend", action="store_true")
    parser.add_argument("--skip-frontend", action="store_true")
    parser.add_argument("--check", action="store_true", help="Only verify expected setup artifacts.")
    return parser.parse_args()


def build_setup_plan(repo_root: Path = REPO_ROOT) -> list[tuple[list[str], Path]]:
    backend_root = repo_root / "backend"
    frontend_root = repo_root / "frontend"
    opencode_root = repo_root / ".opencode"
    backend_venv = backend_root / ".venv"
    backend_python = backend_venv / "bin" / "python"
    backend_pip = backend_venv / "bin" / "pip"

    return [
        ([sys.executable, "-m", "venv", str(backend_venv)], repo_root),
        ([str(backend_pip), "install", "-e", ".[dev]"], backend_root),
        ([str(backend_python), "-m", "playwright", "install", "chromium"], backend_root),
        (["npm", "install"], frontend_root),
        (["npm", "install"], opencode_root),
    ]


def run_command(command: list[str], cwd: Path) -> None:
    printable = " ".join(shlex.quote(part) for part in command)
    print(f"[lumon setup] {printable}  (cwd={cwd})", flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def expected_setup_state() -> dict[str, bool]:
    return {
        "backend_venv": BACKEND_PYTHON.exists(),
        "frontend_modules": (FRONTEND_ROOT / "node_modules").exists(),
        "opencode_plugin_modules": (OPENCODE_ROOT / "node_modules").exists(),
    }


def verify_setup() -> int:
    state = expected_setup_state()
    if all(state.values()):
        print("Lumon setup looks ready.", flush=True)
        print("Next step: run plain `opencode .` from the repo root.", flush=True)
        return 0

    print("Lumon setup is incomplete.", flush=True)
    for key, ready in state.items():
        status = "ok" if ready else "missing"
        print(f" - {key}: {status}", flush=True)
    return 1


def run() -> int:
    args = parse_args()

    if args.check:
        return verify_setup()

    plan = build_setup_plan()
    for command, cwd in plan:
        if args.skip_backend and cwd == BACKEND_ROOT:
            continue
        if args.skip_frontend and cwd in {FRONTEND_ROOT, OPENCODE_ROOT}:
            continue
        if command[:3] == [sys.executable, "-m", "venv"] and BACKEND_PYTHON.exists():
            continue
        run_command(command, cwd)

    print("\nLumon setup complete.", flush=True)
    print("Normal use:", flush=True)
    print("  1. Stay in the repo root", flush=True)
    print("  2. Run `opencode .`", flush=True)
    print("  3. Let the Lumon plugin attach silently in the background", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
