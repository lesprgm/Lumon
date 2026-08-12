from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

from .common import BACKEND_ROOT, FRONTEND_ROOT, REPO_ROOT, load_env_file


def _port_is_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _listener_pids(port: int) -> list[int]:
    result = subprocess.run(
        ["lsof", "-t", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return pids


def _preflight_port(port: int) -> int:
    if not _port_is_listening(port):
        return 0
    pids = _listener_pids(port)
    print(f"Port {port} is in use by PID(s): {' '.join(str(pid) for pid in pids) or '(unknown)'}.", file=sys.stderr, flush=True)
    if os.getenv("LUMON_BACKEND_KILL_PORT_OWNER", "0") != "1":
        return 10
    for pid in pids:
        try:
            os.kill(pid, 15)
        except OSError:
            continue
    return 0 if not _port_is_listening(port) else 1


def _choose_backend_port(requested_port: int) -> int:
    max_probe = int(os.getenv("LUMON_BACKEND_PORT_MAX_PROBE", "10"))
    if not _port_is_listening(requested_port):
        return requested_port
    if os.getenv("LUMON_BACKEND_PORT_STRICT", "0") == "1":
        raise SystemExit(f"Port {requested_port} is already in use and strict mode is enabled.")
    for probe_count in range(max_probe):
        candidate = requested_port + probe_count + 1
        if not _port_is_listening(candidate):
            return candidate
    raise SystemExit(f"No free backend port found from {requested_port} to {requested_port + max_probe}.")


def _runtime_env_file() -> Path:
    return REPO_ROOT / "output" / "runtime" / "lumon_backend.env"


def _latest_source_mtime() -> float:
    candidates: list[Path] = []
    for relative in ("src", "public"):
        root = FRONTEND_ROOT / relative
        if root.exists():
            candidates.extend(path for path in root.rglob("*") if path.is_file())
    for name in ("index.html", "package.json", "vite.config.ts"):
        path = FRONTEND_ROOT / name
        if path.exists():
            candidates.append(path)
    candidates.extend(FRONTEND_ROOT.glob("tsconfig*.json"))
    if not candidates:
        return 0
    return max(path.stat().st_mtime for path in candidates)


def run_backend() -> int:
    load_env_file(REPO_ROOT / ".env")

    requested_port = int(os.getenv("LUMON_BACKEND_PORT", "8000"))
    preflight_status = _preflight_port(requested_port)
    if preflight_status not in {0, 10}:
        return preflight_status

    selected_port = _choose_backend_port(requested_port)
    if selected_port != requested_port:
        print(f"Port {requested_port} is busy; starting backend on {selected_port}.", file=sys.stderr, flush=True)

    runtime_env_file = _runtime_env_file()
    runtime_env_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_env_file.write_text(
        "\n".join(
            [
                f"LUMON_BACKEND_PORT={selected_port}",
                f"VITE_LUMON_BACKEND_ORIGIN=http://127.0.0.1:{selected_port}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    os.environ["LUMON_BACKEND_PORT"] = str(selected_port)
    os.environ["VITE_LUMON_BACKEND_ORIGIN"] = f"http://127.0.0.1:{selected_port}"

    python_bin = BACKEND_ROOT / ".venv" / "bin" / "python"
    executable = str(python_bin) if python_bin.exists() else "python3"
    args = [
        executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(selected_port),
        "--loop",
        "uvloop",
        "--limit-concurrency",
        "100",
        "--backlog",
        "2048",
        "--ws-ping-interval",
        "5",
        "--ws-max-size",
        "1048576",
    ]
    if os.getenv("LUMON_BACKEND_RELOAD", "0") == "1":
        args.append("--reload")
    os.chdir(BACKEND_ROOT)
    os.execvp(executable, args)


def run_frontend() -> int:
    load_env_file(_runtime_env_file())

    os.chdir(FRONTEND_ROOT)
    os.environ["VITE_LUMON_REPLAY"] = "false"
    os.environ["CI"] = "1"

    runtime_mode = os.getenv("LUMON_FRONTEND_RUNTIME_MODE", "preview")
    if runtime_mode == "preview":
        dist_index = FRONTEND_ROOT / "dist" / "index.html"
        needs_build = not dist_index.exists() or _latest_source_mtime() > dist_index.stat().st_mtime
        if needs_build:
            subprocess.run(["npm", "run", "build"], check=True)
        os.execvp(
            "npm",
            ["npm", "run", "preview", "--", "--host", "127.0.0.1", "--port", "5173", "--strictPort"],
        )

    os.execvp(
        "npm",
        ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173", "--strictPort", "--clearScreen", "false"],
    )
