from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "lumon_app.py"

spec = importlib.util.spec_from_file_location("lumon_app", MODULE_PATH)
assert spec and spec.loader
lumon_app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lumon_app
spec.loader.exec_module(lumon_app)


class FakeProcess:
    def __init__(self, poll_values: list[int | None]) -> None:
        self._poll_values = list(poll_values)
        self.returncode = None

    def poll(self) -> int | None:
        if self._poll_values:
            self.returncode = self._poll_values.pop(0)
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9


def test_parse_args_defaults_to_plugin_first_flow(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["lumon_app.py"])
    args = lumon_app.parse_args()

    assert args.backend_origin == "http://127.0.0.1:8000"
    assert args.frontend_origin == "http://127.0.0.1:5173"


def test_run_keeps_backend_alive_if_frontend_exits(monkeypatch, capsys) -> None:
    backend = FakeProcess([None, 0])
    frontend = FakeProcess([1])
    spawned: list[list[str]] = []
    terminated: list[list[FakeProcess]] = []

    monkeypatch.setattr(
        lumon_app,
        "parse_args",
        lambda: SimpleNamespace(
            backend_origin="http://127.0.0.1:8000",
            frontend_origin="http://127.0.0.1:5173",
        ),
    )
    monkeypatch.setattr(lumon_app, "wait_for_backend", lambda origin: None)
    monkeypatch.setattr(lumon_app, "wait_for_frontend", lambda origin: None)
    monkeypatch.setattr(lumon_app.signal, "signal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lumon_app.time, "sleep", lambda _seconds: None)

    def fake_spawn(command: list[str]) -> FakeProcess:
        spawned.append(command)
        return backend if len(spawned) == 1 else frontend

    monkeypatch.setattr(lumon_app, "spawn", fake_spawn)
    monkeypatch.setattr(lumon_app, "terminate_all", lambda processes: terminated.append(list(processes)))

    assert lumon_app.run() == 0
    assert len(spawned) == 2
    assert "Lumon frontend exited; backend will keep running for browser commands." in capsys.readouterr().err
    assert terminated
