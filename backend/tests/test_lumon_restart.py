from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "lumon_restart.py"

spec = importlib.util.spec_from_file_location("lumon_restart", MODULE_PATH)
assert spec and spec.loader
lumon_restart = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lumon_restart
spec.loader.exec_module(lumon_restart)


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid


def test_collect_restart_targets_separates_lumon_from_foreign_ports(monkeypatch) -> None:
    def fake_listeners(port: int) -> list[int]:
        if port == 8000:
            return [101]
        if port == 5173:
            return [202, 303]
        return []

    commands = {
        101: "/Users/leslie/Documents/Lumon/backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000",
        202: "/opt/homebrew/bin/node /Users/leslie/Documents/Lumon/frontend/node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173 --strictPort",
        303: "/usr/local/bin/python -m http.server 5173",
    }

    monkeypatch.setattr(lumon_restart, "list_listener_pids", fake_listeners)
    monkeypatch.setattr(lumon_restart, "command_for_pid", lambda pid: commands[pid])
    monkeypatch.setattr(
        lumon_restart,
        "process_table",
        lambda: [(404, f"python3 {REPO_ROOT / 'scripts' / 'lumon_app.py'}")],
    )

    targets, foreign = lumon_restart.collect_restart_targets(8000, 5173)

    assert [target.pid for target in targets] == [101, 404, 202]
    assert [occupant.pid for occupant in foreign] == [303]


def test_restart_services_refuses_foreign_port_occupants_without_force(monkeypatch) -> None:
    monkeypatch.setattr(
        lumon_restart,
        "collect_restart_targets",
        lambda _backend_port, _frontend_port: ([], [lumon_restart.ForeignOccupant(pid=22, port=5173, command="python -m http.server")]),
    )
    args = SimpleNamespace(
        backend_origin="http://127.0.0.1:8000",
        frontend_origin="http://127.0.0.1:5173",
        force=False,
        timeout_seconds=20.0,
    )

    assert lumon_restart.restart_services(args) == 1


def test_restart_services_stops_targets_and_starts_backend_frontend(monkeypatch) -> None:
    stopped: list[int] = []
    spawned: list[tuple[list[str], Path]] = []
    waited: list[str] = []
    rotated: list[str] = []

    monkeypatch.setattr(
        lumon_restart,
        "collect_restart_targets",
        lambda _backend_port, _frontend_port: (
            [
                lumon_restart.StopTarget(pid=11, kind="backend", reason="listening on 8000", command="uvicorn ..."),
                lumon_restart.StopTarget(pid=12, kind="control", reason="repo control process", command="lumon_app.py"),
            ],
            [],
        ),
    )
    monkeypatch.setattr(lumon_restart, "terminate_pid", lambda pid, grace_seconds=6.0: stopped.append(pid))
    monkeypatch.setattr(lumon_restart, "wait_for_http", lambda url, timeout_seconds: waited.append(url))
    monkeypatch.setattr(lumon_restart, "rotate_runtime_logs", lambda: rotated.append("rotated"))

    next_pid = iter((401, 402, 403))
    monkeypatch.setattr(
        lumon_restart,
        "spawn",
        lambda command, log_path: spawned.append((command, log_path)) or FakeProcess(next(next_pid)),
    )

    args = SimpleNamespace(
        backend_origin="http://127.0.0.1:8000",
        frontend_origin="http://127.0.0.1:5173",
        force=False,
        timeout_seconds=20.0,
    )

    assert lumon_restart.restart_services(args) == 0
    assert stopped == [11, 12]
    assert [command for command, _log_path in spawned] == [
        lumon_restart.BACKEND_COMMAND,
        lumon_restart.FRONTEND_COMMAND,
    ]
    assert waited == ["http://127.0.0.1:8000/healthz", "http://127.0.0.1:5173"]
    assert rotated == ["rotated"]


def test_rotate_runtime_logs_archives_existing_content(tmp_path, monkeypatch) -> None:
    backend_log = tmp_path / "lumon-backend.log"
    frontend_log = tmp_path / "lumon-frontend.log"
    plugin_log = tmp_path / "lumon-plugin-debug.log"
    archive_dir = tmp_path / "archive"

    backend_log.write_text("backend line\n", encoding="utf-8")
    frontend_log.write_text("", encoding="utf-8")
    plugin_log.write_text("plugin line\n", encoding="utf-8")

    monkeypatch.setattr(lumon_restart, "BACKEND_LOG", backend_log)
    monkeypatch.setattr(lumon_restart, "FRONTEND_LOG", frontend_log)
    monkeypatch.setattr(lumon_restart, "PLUGIN_LOG", plugin_log)
    monkeypatch.setattr(lumon_restart, "LOG_ARCHIVE_DIR", archive_dir)

    lumon_restart.rotate_runtime_logs()

    archived = sorted(archive_dir.glob("*.log"))
    assert [path.name.startswith("lumon-backend.log.") or path.name.startswith("lumon-plugin-debug.log.") for path in archived] == [True, True]
    assert backend_log.read_text(encoding="utf-8") == ""
    assert frontend_log.read_text(encoding="utf-8") == ""
    assert plugin_log.read_text(encoding="utf-8") == ""
