from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from urllib.request import urlopen

from .common import DEFAULT_BACKEND_ORIGIN, DEFAULT_FRONTEND_ORIGIN, REPO_ROOT

BACKEND_COMMAND = ["/bin/zsh", "-lc", "./lumon internal-start-backend"]
FRONTEND_COMMAND = ["/bin/zsh", "-lc", "./lumon internal-start-frontend"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start Lumon backend and frontend together for the plugin-first local flow.")
    parser.add_argument("--backend-origin", default=DEFAULT_BACKEND_ORIGIN)
    parser.add_argument("--frontend-origin", default=DEFAULT_FRONTEND_ORIGIN)
    return parser.parse_args()


def wait_for_backend(backend_origin: str, timeout_seconds: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{backend_origin}/healthz", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.4)
    raise SystemExit(f"Lumon backend did not become ready at {backend_origin} within {timeout_seconds:.0f}s.")


def wait_for_frontend(frontend_origin: str, timeout_seconds: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(frontend_origin, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.4)
    raise SystemExit(f"Lumon frontend did not become ready at {frontend_origin} within {timeout_seconds:.0f}s.")


def spawn(command: list[str]) -> subprocess.Popen[str]:
    return subprocess.Popen(command, cwd=REPO_ROOT, text=True, stdin=subprocess.DEVNULL)


def same_origin(left: str, right: str) -> bool:
    return left.rstrip("/") == right.rstrip("/")


def terminate_all(processes: list[subprocess.Popen[str]]) -> None:
    for process in processes:
        if process.poll() is not None:
            continue
        process.terminate()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if all(process.poll() is not None for process in processes):
            return
        time.sleep(0.2)
    for process in processes:
        if process.poll() is None:
            process.kill()


def run() -> int:
    args = parse_args()

    backend = spawn(BACKEND_COMMAND)
    try:
        wait_for_backend(args.backend_origin)
    except Exception:
        terminate_all([backend])
        raise

    frontend = None
    try:
        if not same_origin(args.backend_origin, args.frontend_origin):
            frontend = spawn(FRONTEND_COMMAND)
        wait_for_frontend(args.frontend_origin)
    except Exception:
        terminate_all([process for process in [backend, frontend] if process is not None])
        raise
    processes = [process for process in [backend, frontend] if process is not None]
    print("Lumon app started.", flush=True)
    print(f"Backend:  {args.backend_origin}", flush=True)
    if frontend is None:
        print(f"Frontend: {args.frontend_origin} (served by backend)", flush=True)
    else:
        print(f"Frontend: {args.frontend_origin}", flush=True)
    print("Use plain OpenCode normally; the Lumon plugin will auto-attach in the background.", flush=True)

    def _handle_signal(signum: int, frame) -> None:
        terminate_all(processes)
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        while True:
            if backend.poll() is not None:
                terminate_all(processes)
                return backend.returncode or 0
            if frontend is not None and frontend.poll() is not None:
                print("Lumon frontend exited; backend will keep running for browser commands.", file=sys.stderr, flush=True)
                frontend = None
            time.sleep(0.5)
    finally:
        live_processes = [backend]
        if frontend is not None:
            live_processes.append(frontend)
        terminate_all(live_processes)
