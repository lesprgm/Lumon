from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "output" / "manual_checks"
OPENCODE_LOG_DIR = Path.home() / ".local" / "share" / "opencode" / "log"
LUMON_LOG_PATHS = [
    Path("/tmp/lumon-plugin-debug.log"),
    Path("/tmp/lumon-backend.log"),
    Path("/tmp/lumon-frontend.log"),
]


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def run_command(command: list[str], cwd: Path | None = None, timeout_seconds: int = 20) -> CommandResult:
    process = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    return CommandResult(
        command=command,
        returncode=process.returncode,
        stdout=strip_ansi(process.stdout).strip(),
        stderr=strip_ansi(process.stderr).strip(),
    )


def format_command_result(result: CommandResult) -> str:
    command_text = " ".join(result.command)
    lines = [f"$ {command_text}", f"exit_code: {result.returncode}"]
    if result.stdout:
        lines.append("stdout:")
        lines.append(result.stdout)
    if result.stderr:
        lines.append("stderr:")
        lines.append(result.stderr)
    return "\n".join(lines)


def tail_text_file(path: Path, line_count: int) -> str:
    if not path.exists():
        return f"(missing) {path}"
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = content[-line_count:]
    return "\n".join(tail) if tail else "(file exists but is empty)"


def newest_log_file(log_dir: Path) -> Path | None:
    if not log_dir.exists():
        return None
    candidates = [entry for entry in log_dir.iterdir() if entry.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda entry: entry.stat().st_mtime)


def build_report(tail_lines: int) -> str:
    sections: list[str] = []
    timestamp = dt.datetime.now().isoformat(timespec="seconds")

    sections.append("# Lumon CLI Triage Report")
    sections.append(f"Generated: {timestamp}")
    sections.append(f"Repo: {REPO_ROOT}")
    sections.append("")

    opencode_path = shutil.which("opencode")
    sections.append("## OpenCode CLI availability")
    sections.append(f"opencode_path: {opencode_path or '(not found on PATH)'}")
    sections.append("")

    command_sets: list[list[str]] = []
    if opencode_path:
        command_sets.extend(
            [
                ["opencode", "--version"],
                ["opencode", "--help"],
                ["opencode", "mcp", "--help"],
                ["opencode", "mcp", "list"],
            ]
        )

    command_sets.append(["python3", str(REPO_ROOT / "scripts" / "lumon_doctor.py")])

    sections.append("## Command outputs")
    for command in command_sets:
        result = run_command(command, cwd=REPO_ROOT)
        sections.append(format_command_result(result))
        sections.append("")

    sections.append("## OpenCode logs")
    newest_log = newest_log_file(OPENCODE_LOG_DIR)
    if newest_log is None:
        sections.append(f"No log files found in {OPENCODE_LOG_DIR}")
    else:
        sections.append(f"Newest log: {newest_log}")
        sections.append("```text")
        sections.append(tail_text_file(newest_log, tail_lines))
        sections.append("```")
    sections.append("")

    sections.append("## Lumon local logs")
    for log_path in LUMON_LOG_PATHS:
        sections.append(f"### {log_path}")
        sections.append("```text")
        sections.append(tail_text_file(log_path, tail_lines))
        sections.append("```")
        sections.append("")

    sections.append("## Recommended escalation payload")
    sections.append("- Include this report file.")
    sections.append("- Include exact prompt that triggered the failure.")
    sections.append("- Include whether OpenCode was fully restarted after plugin changes.")
    sections.append("- OpenCode issues: https://github.com/anomalyco/opencode/issues")
    sections.append("- OpenCode Discord: https://opencode.ai/discord")

    return "\n".join(sections).strip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect CLI-first OpenCode + Lumon triage evidence for plugin/runtime failures."
    )
    parser.add_argument(
        "--tail-lines",
        type=int,
        default=120,
        help="Number of lines to include from each log tail.",
    )
    parser.add_argument(
        "--no-bundle",
        action="store_true",
        help="Print report only and skip writing a report file.",
    )
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    report = build_report(tail_lines=max(args.tail_lines, 1))

    if args.no_bundle:
        print(report, flush=True)
        return 0

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = OUTPUT_DIR / f"opencode_cli_triage_{stamp}.md"
    report_path.write_text(report, encoding="utf-8")

    print(f"Wrote triage report: {report_path}", flush=True)
    print("Share this file when opening an OpenCode issue or asking in Discord.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
