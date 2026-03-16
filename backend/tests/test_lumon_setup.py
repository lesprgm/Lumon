from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "lumon_setup.py"

spec = importlib.util.spec_from_file_location("lumon_setup", MODULE_PATH)
assert spec and spec.loader
lumon_setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lumon_setup)


def test_build_setup_plan_contains_one_install_path() -> None:
    plan = lumon_setup.build_setup_plan(REPO_ROOT)
    commands = [command for command, _cwd in plan]

    assert [lumon_setup.sys.executable, "-m", "venv", str(REPO_ROOT / "backend" / ".venv")] in commands
    assert [str(REPO_ROOT / "backend" / ".venv" / "bin" / "pip"), "install", "-e", ".[dev]"] in commands
    assert [str(REPO_ROOT / "backend" / ".venv" / "bin" / "python"), "-m", "playwright", "install", "chromium"] in commands
    assert commands.count(["npm", "install"]) == 2


def test_verify_setup_returns_nonzero_when_artifacts_are_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        lumon_setup,
        "expected_setup_state",
        lambda: {"backend_venv": True, "frontend_modules": False, "opencode_plugin_modules": True},
    )

    assert lumon_setup.verify_setup() == 1


def test_verify_setup_returns_zero_when_artifacts_exist(monkeypatch) -> None:
    monkeypatch.setattr(
        lumon_setup,
        "expected_setup_state",
        lambda: {"backend_venv": True, "frontend_modules": True, "opencode_plugin_modules": True},
    )

    assert lumon_setup.verify_setup() == 0
