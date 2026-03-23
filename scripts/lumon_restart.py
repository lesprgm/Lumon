from __future__ import annotations

import argparse
import datetime as dt
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_LOG = Path("/tmp/lumon-backend.log")
FRONTEND_LOG = Path("/tmp/lumon-frontend.log")
PLUGIN_LOG = Path("/tmp/lumon-plugin-debug.log")
LOG_ARCHIVE_DIR = REPO_ROOT / "output" / "runtime" / "logs"

BACKEND_COMMAND = ["/bin/zsh", str(REPO_ROOT / "scripts" / "start_demo_backend.sh")]
FRONTEND_COMMAND = ["/bin/zsh", str(REPO_ROOT / "scripts" / "start_demo_frontend.sh")]

CONTROL_SCRIPT_MARKERS = (
    str(REPO_ROOT / "scripts" / "lumon_app.py"),
    str(REPO_ROOT / "scripts" / "start_demo_backend.sh"),
    str(REPO_ROOT / "scripts" / "start_demo_frontend.sh"),
)
BACKEND_MARKERS = (
    "uvicorn app.main:app",
    "app.main:app --host 127.0.0.1 --port 8000",
    str(REPO_ROOT / "scripts" / "start_demo_backend.sh"),
)
FRONTEND_MARKERS = (
    "vite --host 127.0.0.1 --port 5173",
    "vite.js --host 127.0.0.1 --port 5173",
    "npm run dev -- --host 127.0.0.1 --port 5173 --strictPort",
    "vite preview --host 127.0.0.1 --port 5173",
    "vite.js preview --host 127.0.0.1 --port 5173",
    "npm run preview -- --host 127.0.0.1 --port 5173 --strictPort",
    str(REPO_ROOT / "scripts" / "start_demo_frontend.sh"),
)


@dataclass(frozen=True)
class StopTarget:
    pid: int
    kind: str
    reason: str
    command: str


@dataclass(frozen=True)
class ForeignOccupant:
    pid: int
    port: int
    command: str


class RestartError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Restart Lumon backend/frontend cleanly for the plugin-first local workflow.")
    parser.add_argument("--backend-origin", default="http://127.0.0.1:8000")
    parser.add_argument("--frontend-origin", default="http://127.0.0.1:8000")
    parser.add_argument("--force", action="store_true", help="Also kill unrelated processes occupying Lumon ports.")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    return parser.parse_args()


def origin_port(origin: str) -> int:
    return int(origin.rsplit(":", 1)[-1])


def same_origin(left: str, right: str) -> bool:
    return left.rstrip("/") == right.rstrip("/")


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)


def list_listener_pids(port: int) -> list[int]:
    result = run_command(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
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


def command_for_pid(pid: int) -> str:
    result = run_command(["ps", "-p", str(pid), "-o", "command="])
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def process_table() -> list[tuple[int, str]]:
    result = run_command(["ps", "-axo", "pid=,command="])
    if result.returncode != 0:
        return []
    rows: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        raw = line.strip()
        if not raw:
            continue
        parts = raw.split(maxsplit=1)
        if len(parts) != 2:
            continue
        try:
            rows.append((int(parts[0]), parts[1]))
        except ValueError:
            continue
    return rows


def is_lumon_backend_command(command: str) -> bool:
    if str(REPO_ROOT / "backend") in command and "app.main:app" in command:
        return True
    return any(marker in command for marker in BACKEND_MARKERS)


def is_lumon_frontend_command(command: str) -> bool:
    if str(REPO_ROOT / "frontend") in command and "--port 5173" in command:
        return True
    return any(marker in command for marker in FRONTEND_MARKERS)


def is_lumon_control_command(command: str) -> bool:
    return any(marker in command for marker in CONTROL_SCRIPT_MARKERS)


def collect_restart_targets(backend_port: int, frontend_port: int | None) -> tuple[list[StopTarget], list[ForeignOccupant]]:
    targets: dict[int, StopTarget] = {}
    foreign: list[ForeignOccupant] = []

    for pid in list_listener_pids(backend_port):
        command = command_for_pid(pid)
        if is_lumon_backend_command(command) or is_lumon_control_command(command):
            targets[pid] = StopTarget(pid=pid, kind="backend", reason=f"listening on {backend_port}", command=command)
        else:
            foreign.append(ForeignOccupant(pid=pid, port=backend_port, command=command))

    if frontend_port is not None:
        for pid in list_listener_pids(frontend_port):
            command = command_for_pid(pid)
            if is_lumon_frontend_command(command) or is_lumon_control_command(command):
                targets[pid] = StopTarget(pid=pid, kind="frontend", reason=f"listening on {frontend_port}", command=command)
            else:
                foreign.append(ForeignOccupant(pid=pid, port=frontend_port, command=command))

    for pid, command in process_table():
        if not is_lumon_control_command(command):
            continue
        targets.setdefault(pid, StopTarget(pid=pid, kind="control", reason="repo control process", command=command))

    return sorted(targets.values(), key=lambda entry: (entry.kind, entry.pid)), foreign


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def terminate_pid(pid: int, grace_seconds: float = 6.0) -> None:
    if not pid_alive(pid):
        return
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(0.15)
    if pid_alive(pid):
        os.kill(pid, signal.SIGKILL)


def wait_for_http(url: str, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:  # noqa: S310
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.35)
    raise RestartError(f"Lumon service did not become ready at {url} within {timeout_seconds:.0f}s.")


def spawn(command: list[str], log_path: Path) -> subprocess.Popen[str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        process = subprocess.Popen(  # noqa: S603
            command,
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    return process


def rotate_log(path: Path, stamp: str) -> None:
    if not path.exists():
        return
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        content = ""
    if not content.strip():
        path.write_text("", encoding="utf-8")
        return
    LOG_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archived = LOG_ARCHIVE_DIR / f"{path.name}.{stamp}.log"
    archived.write_text(content, encoding="utf-8")
    path.write_text("", encoding="utf-8")


def rotate_runtime_logs() -> None:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    for path in (PLUGIN_LOG, BACKEND_LOG, FRONTEND_LOG):
        rotate_log(path, stamp)


def restart_services(args: argparse.Namespace) -> int:
    backend_port = origin_port(args.backend_origin)
    frontend_port = None if same_origin(args.backend_origin, args.frontend_origin) else origin_port(args.frontend_origin)
    targets, foreign = collect_restart_targets(backend_port, frontend_port)

    if foreign and not args.force:
        print("Refusing to restart Lumon because unrelated processes occupy Lumon ports.", flush=True)
        for occupant in foreign:
            print(f"- port {occupant.port}: pid={occupant.pid} command={occupant.command}", flush=True)
        print("Use `./lumon restart --force` only if you intend to kill those processes too.", flush=True)
        return 1

    print("Restarting Lumon", flush=True)
    if targets:
        for target in targets:
            print(f"- stopping {target.kind} pid={target.pid} ({target.reason})", flush=True)
            terminate_pid(target.pid)
    if foreign and args.force:
        for occupant in foreign:
            print(f"- force stopping port {occupant.port} pid={occupant.pid}", flush=True)
            terminate_pid(occupant.pid)

    rotate_runtime_logs()

    backend = spawn(BACKEND_COMMAND, BACKEND_LOG)
    frontend = None
    try:
        wait_for_http(f"{args.backend_origin}/healthz", timeout_seconds=args.timeout_seconds)
        if frontend_port is not None:
            frontend = spawn(FRONTEND_COMMAND, FRONTEND_LOG)
        wait_for_http(args.frontend_origin, timeout_seconds=max(args.timeout_seconds, 45.0))
    except Exception:
        terminate_pid(backend.pid)
        if frontend is not None:
            terminate_pid(frontend.pid)
        raise

    print(f"Backend:  {args.backend_origin} (pid {backend.pid})", flush=True)
    if frontend is not None:
        print(f"Frontend: {args.frontend_origin} (pid {frontend.pid})", flush=True)
    else:
        print(f"Frontend: {args.frontend_origin} (served by backend)", flush=True)
    print(f"Logs: {BACKEND_LOG}, {FRONTEND_LOG}", flush=True)
    return 0


def run() -> int:
    args = parse_args()
    try:
        return restart_services(args)
    except RestartError as error:
        print(str(error), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
