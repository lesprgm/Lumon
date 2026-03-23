from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
FRONTEND_ROOT = REPO_ROOT / "frontend"
BACKEND_VENV = BACKEND_ROOT / ".venv"
BACKEND_PYTHON = BACKEND_VENV / "bin" / "python"
PLUGIN_PATH = REPO_ROOT / ".opencode" / "plugins" / "lumon.js"
PLUGIN_TOOL_API_PATH = REPO_ROOT / ".opencode" / "node_modules" / "@opencode-ai" / "plugin" / "dist" / "tool.d.ts"
FRONTEND_BUILD_INDEX = FRONTEND_ROOT / "dist" / "index.html"


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str
    remedy: str | None = None


def _path_exists(path: Path) -> bool:
    return path.exists()


def _command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def _playwright_browser_installed() -> bool:
    cache_root = Path.home() / "Library" / "Caches" / "ms-playwright"
    if not cache_root.exists():
        return False
    return any(entry.is_dir() and "chromium" in entry.name for entry in cache_root.iterdir())


def _backend_imports_ready() -> bool:
    if not BACKEND_PYTHON.exists():
        return False
    command = [
        str(BACKEND_PYTHON),
        "-c",
        "import fastapi, playwright, uvicorn, pydantic; import app.main",
    ]
    result = subprocess.run(
        command,
        cwd=BACKEND_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def collect_doctor_checks() -> list[DoctorCheck]:
    checks = [
        DoctorCheck(
            name="backend venv",
            ok=_path_exists(BACKEND_PYTHON),
            detail=str(BACKEND_PYTHON),
            remedy="Run `./lumon setup` to create the backend environment.",
        ),
        DoctorCheck(
            name="backend imports",
            ok=_backend_imports_ready(),
            detail="fastapi, uvicorn, playwright, pydantic, and app.main import cleanly",
            remedy="Run `./lumon setup` again to finish the backend install.",
        ),
        DoctorCheck(
            name="frontend install",
            ok=_path_exists(FRONTEND_ROOT / "node_modules"),
            detail=str(FRONTEND_ROOT / "node_modules"),
            remedy="Run `./lumon setup` to install the frontend dependencies.",
        ),
        DoctorCheck(
            name="frontend build",
            ok=_path_exists(FRONTEND_BUILD_INDEX),
            detail=str(FRONTEND_BUILD_INDEX),
            remedy="Run `./lumon setup` to build the shipped frontend bundle.",
        ),
        DoctorCheck(
            name="project plugin",
            ok=_path_exists(PLUGIN_PATH),
            detail=str(PLUGIN_PATH),
            remedy="Restore the project plugin at `.opencode/plugins/lumon.js`.",
        ),
        DoctorCheck(
            name="opencode plugin tool api",
            ok=_path_exists(PLUGIN_TOOL_API_PATH),
            detail="Custom tool registration runtime is installed for `.opencode`",
            remedy="Run `./lumon setup` to install the OpenCode plugin tool dependencies.",
        ),
        DoctorCheck(
            name="playwright browser",
            ok=_playwright_browser_installed(),
            detail="Chromium installed in the local Playwright cache",
            remedy="Run `./lumon setup` to install the Playwright browser.",
        ),
        DoctorCheck(
            name="opencode cli",
            ok=_command_exists("opencode"),
            detail="`opencode` is available on PATH",
            remedy="Install OpenCode or add it to PATH before using Lumon.",
        ),
        DoctorCheck(
            name="npm",
            ok=_command_exists("npm"),
            detail="`npm` is available on PATH",
            remedy="Install Node.js/npm before using Lumon.",
        ),
    ]
    return checks


def render_report(checks: list[DoctorCheck]) -> int:
    print("Lumon doctor", flush=True)
    print(f"Repo: {REPO_ROOT}", flush=True)
    print("", flush=True)

    failures: list[DoctorCheck] = []
    for check in checks:
        status = "ok" if check.ok else "missing"
        print(f"[{status}] {check.name}: {check.detail}", flush=True)
        if not check.ok:
            failures.append(check)

    if not failures:
        print("\nLumon looks ready.", flush=True)
        print("Next step: run plain `opencode .` from the repo root.", flush=True)
        print("If the backend/frontend drift after local changes, run `./lumon restart`.", flush=True)
        return 0

    print("\nWhat to fix", flush=True)
    for check in failures:
        if check.remedy:
            print(f"- {check.remedy}", flush=True)

    if any(
        check.name in {"backend venv", "backend imports", "frontend install", "frontend build", "playwright browser"}
        for check in failures
    ):
        print("- The normal recovery path is `./lumon setup`.", flush=True)

    return 1


def run() -> int:
    return render_report(collect_doctor_checks())


if __name__ == "__main__":
    raise SystemExit(run())
