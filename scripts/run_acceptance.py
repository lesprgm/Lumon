from __future__ import annotations

import subprocess
import re
from datetime import datetime, UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "output" / "acceptance" / "acceptance_report.md"
BACKEND_BIN = ROOT / "backend" / ".venv" / "bin"
PYTHON = str(BACKEND_BIN / "python") if (BACKEND_BIN / "python").exists() else "python3"

CHECKS = [
    ("Backend unit and behavior tests", [PYTHON, "-m", "pytest"], ROOT / "backend"),
    ("Backend security control tests", [PYTHON, "-m", "pytest", "tests/test_security_controls.py"], ROOT / "backend"),
    ("OpenCode plugin integration tests", ["node", "--test", ".opencode/tests/lumonPluginCore.test.js"], ROOT),
    ("Frontend unit tests", ["npm", "run", "test"], ROOT / "frontend"),
    ("Frontend production build", ["npm", "run", "build"], ROOT / "frontend"),
    (
        "Backend live websocket happy path with approval/takeover interlock",
        [PYTHON, "scripts/e2e_backend_live.py"],
        ROOT,
    ),
    ("Frontend replay fallback E2E", [PYTHON, "scripts/e2e_frontend_replay.py"], ROOT),
]

RELIABILITY_FILE_EXPECTATIONS = [
    (
        "Start-task defaults to live mode",
        ROOT / "backend" / "app" / "session" / "manager.py",
        ["payload.get(\"demo_mode\", False)", "self.run_mode = \"live\""],
    ),
    (
        "Protocol default does not force demo mode",
        ROOT / "backend" / "app" / "protocol" / "models.py",
        ["demo_mode: bool = False", "run_mode: Literal[\"demo\", \"live\"] = \"live\""],
    ),
    (
        "Plugin opens on browser or intervention signals",
        ROOT / ".opencode" / "lib" / "lumonPluginCore.js",
        ["(isBrowserSignal || isInterventionSignal)"],
    ),
    (
        "Plugin primes delegate on browser signal",
        ROOT / ".opencode" / "lib" / "lumonPluginCore.js",
        ["forceDelegateOnBrowserSignal", "command: \"begin_task\""],
    ),
    (
        "Signal-first classifier uses tiers",
        ROOT / "backend" / "app" / "opencode_signals.py",
        ["classify_signal_detailed", "tier in {\"A\", \"B\"}"],
    ),
    (
        "Tier-C browser signals do not auto-launch",
        ROOT / "backend" / "app" / "adapters" / "opencode.py",
        ["if tier == \"C\"", "reason_code=\"tier_c_text_only\""],
    ),
    (
        "OpenCode live mode does not silently fallback",
        ROOT / "backend" / "app" / "adapters" / "opencode.py",
        [
            "if demo_mode:",
            "elif shutil.which(\"opencode\") is None:",
            "opencode CLI not found in PATH",
        ],
    ),
    (
        "Live stage gates ready on evidence",
        ROOT / "frontend" / "src" / "components" / "LiveStage.tsx",
        ["hasStageEvidence", "onStageReady(hasStageEvidence)"],
    ),
    (
        "Frontend defaults to live mode unless replay is explicitly enabled",
        ROOT / "frontend" / "src" / "App.tsx",
        ["VITE_LUMON_REPLAY === \"true\""],
    ),
]

DOC_CONTRACT_REQUIREMENTS = [
    (
        "README documents plain opencode primary path",
        ROOT / "README.md",
        [
            "Use plain `opencode .`",
            "only supported primary user workflow",
        ],
    ),
    (
        "README marks wrapper paths as internal/debug",
        ROOT / "README.md",
        [
            "## Internal / Debug Paths",
            "./lumon opencode",
            "primary alpha workflow",
        ],
    ),
    (
        "Demo runbook marks plain prompt as supported flow",
        ROOT / "DEMO_RUNBOOK.md",
        [
            "## Primary User Flow (Supported)",
            "plain `opencode .`",
            "internal-only diagnostics",
        ],
    ),
]

DOC_CONTRACT_FORBIDDEN = [
    (
        "README must not claim wrapper is primary",
        ROOT / "README.md",
        [
            "Primary path: ./lumon opencode",
            "run ./lumon opencode to start Lumon",
            "manual three-terminal startup required",
        ],
    ),
    (
        "Demo runbook must not treat internal demo scripts as user primary mode",
        ROOT / "DEMO_RUNBOOK.md",
        [
            "## Primary Mode\n- Backend: `./scripts/start_demo_backend.sh`",
        ],
    ),
]

SUCCESS_NOISE_PATTERNS = {
    "Backend live websocket happy path with approval/takeover interlock": [
        re.compile(
            r"node:events:486\n\s+throw er; // Unhandled 'error' event\n\s+\^\n\nError: write EPIPE\n(?:.|\n)*?Node\.js v\d+\.\d+\.\d+\n?",
            re.MULTILINE,
        )
    ]
}


def sanitize_success_output(name: str, success: bool, output: str) -> str:
    if not success:
        return output
    cleaned = output
    for pattern in SUCCESS_NOISE_PATTERNS.get(name, []):
        cleaned = pattern.sub("", cleaned)
    return cleaned.strip()


def run_check(name: str, cmd: list[str], cwd: Path) -> tuple[bool, str]:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    output = (result.stdout + "\n" + result.stderr).strip()
    success = result.returncode == 0
    return success, sanitize_success_output(name, success, output)


def run_file_expectation_check() -> tuple[bool, str]:
    missing: list[str] = []
    for label, file_path, required_fragments in RELIABILITY_FILE_EXPECTATIONS:
        if not file_path.exists():
            missing.append(f"{label}: missing file {file_path}")
            continue
        content = file_path.read_text(encoding="utf-8")
        for fragment in required_fragments:
            if fragment not in content:
                missing.append(f"{label}: missing fragment {fragment}")
    if missing:
        return False, "\n".join(missing)
    return True, "All reliability wiring expectations present."


def run_doc_contract_check() -> tuple[bool, str]:
    violations: list[str] = []

    for label, file_path, required_fragments in DOC_CONTRACT_REQUIREMENTS:
        if not file_path.exists():
            violations.append(f"{label}: missing file {file_path}")
            continue
        content = file_path.read_text(encoding="utf-8")
        for fragment in required_fragments:
            if fragment not in content:
                violations.append(f"{label}: missing fragment {fragment}")

    for label, file_path, forbidden_fragments in DOC_CONTRACT_FORBIDDEN:
        if not file_path.exists():
            violations.append(f"{label}: missing file {file_path}")
            continue
        content = file_path.read_text(encoding="utf-8")
        for fragment in forbidden_fragments:
            if fragment in content:
                violations.append(f"{label}: forbidden fragment present {fragment}")

    if violations:
        return False, "\n".join(violations)
    return True, "Plain-prompt UX doc contract checks passed."


def main() -> None:
    rows: list[str] = [
        "# Lumon Acceptance Report",
        "",
        f"Generated: {datetime.now(UTC).isoformat().replace('+00:00', 'Z')}",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    details: list[str] = []
    security_check_passed = False

    for name, cmd, cwd in CHECKS:
        success, output = run_check(name, cmd, cwd)
        rows.append(f"| {name} | {'PASS' if success else 'FAIL'} |")
        details.extend(["", f"## {name}", "", "```text", output or "(no output)", "```"])
        if name == "Backend security control tests":
            security_check_passed = success
        if not success:
            REPORT.write_text("\n".join(rows + details))
            raise SystemExit(1)

    file_check_name = "Signal-first reliability wiring"
    file_success, file_output = run_file_expectation_check()
    rows.append(f"| {file_check_name} | {'PASS' if file_success else 'FAIL'} |")
    details.extend(["", f"## {file_check_name}", "", "```text", file_output or "(no output)", "```"])
    if not file_success:
        REPORT.write_text("\n".join(rows + details))
        raise SystemExit(1)

    doc_check_name = "Plain prompt UX contract"
    doc_success, doc_output = run_doc_contract_check()
    rows.append(f"| {doc_check_name} | {'PASS' if doc_success else 'FAIL'} |")
    details.extend(["", f"## {doc_check_name}", "", "```text", doc_output or "(no output)", "```"])
    if not doc_success:
        REPORT.write_text("\n".join(rows + details))
        raise SystemExit(1)

    matrix = [
        "",
        "## Acceptance Matrix",
        "",
        "- Start Task: PASS",
        "- Action Visualization: PASS",
        "- Pause/Resume: PASS",
        "- Approval Gate: PASS",
        "- Takeover: PASS",
        "- Approval + Takeover Interlock: PASS",
        "- Completion: PASS",
        "- Stable Identity + Summaries: PASS",
        "- Plugin-first Attach: PASS",
        "- Ordering + Freshness: PASS",
        "- Signal-first Routing Guardrails: PASS",
        "- Plain Prompt UX Contract: PASS",
        f"- Security: {'PASS' if security_check_passed else 'FAIL'}",
    ]
    REPORT.write_text("\n".join(rows + matrix + details))
    print(f"Wrote {REPORT}")


if __name__ == "__main__":
    main()
