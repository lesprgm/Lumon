from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "lumon_doctor.py"

spec = importlib.util.spec_from_file_location("lumon_doctor", MODULE_PATH)
assert spec and spec.loader
lumon_doctor = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lumon_doctor
spec.loader.exec_module(lumon_doctor)


def test_render_report_returns_zero_when_all_checks_pass(capsys) -> None:
    checks = [
        lumon_doctor.DoctorCheck(name="backend venv", ok=True, detail="ok"),
        lumon_doctor.DoctorCheck(name="frontend install", ok=True, detail="ok"),
    ]

    assert lumon_doctor.render_report(checks) == 0
    assert "Lumon looks ready." in capsys.readouterr().out


def test_render_report_returns_nonzero_and_prints_remedies(capsys) -> None:
    checks = [
        lumon_doctor.DoctorCheck(name="backend venv", ok=False, detail="missing", remedy="Run `./lumon setup`."),
        lumon_doctor.DoctorCheck(name="opencode cli", ok=False, detail="missing", remedy="Install OpenCode."),
    ]

    assert lumon_doctor.render_report(checks) == 1
    output = capsys.readouterr().out
    assert "What to fix" in output
    assert "Run `./lumon setup`." in output
    assert "Install OpenCode." in output


def test_collect_doctor_checks_includes_expected_prerequisites() -> None:
    check_names = {check.name for check in lumon_doctor.collect_doctor_checks()}

    assert {
        "backend venv",
        "backend imports",
        "frontend install",
        "project plugin",
        "opencode plugin tool api",
        "playwright browser",
        "opencode cli",
        "npm",
    }.issubset(check_names)
