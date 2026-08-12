from __future__ import annotations

import asyncio
import sys

USAGE = """Usage:
  ./lumon setup
  ./lumon doctor
  ./lumon triage [--tail-lines N] [--no-bundle]
  ./lumon restart [--force]
  ./lumon app

Internal / debug:
  ./lumon opencode [--wrapper-options ... -- opencode-args ...]
  ./lumon fast-open [--wrapper-options ... -- opencode-args ...]
"""


def _run_with_argv(runner, argv: list[str]) -> int:
    original_argv = sys.argv[:]
    sys.argv = [original_argv[0], *argv]
    try:
        return runner()
    finally:
        sys.argv = original_argv


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(USAGE, file=sys.stderr)
        return 1

    command, *rest = args
    if command == "setup":
        from .setup import run

        return _run_with_argv(run, rest)
    if command == "doctor":
        from .doctor import run

        return _run_with_argv(run, rest)
    if command == "triage":
        from .triage import run

        return _run_with_argv(run, rest)
    if command == "restart":
        from .restart import run

        return _run_with_argv(run, rest)
    if command == "app":
        from .app_cmd import run

        return _run_with_argv(run, rest)
    if command == "opencode":
        from .opencode import run

        return asyncio.run(_run_with_argv(run, rest))
    if command == "fast-open":
        from .fast_open import run

        return _run_with_argv(run, rest)
    if command == "internal-start-backend":
        from .runtime import run_backend

        return run_backend()
    if command == "internal-start-frontend":
        from .runtime import run_frontend

        return run_frontend()

    print(f"Unknown subcommand: {command}", file=sys.stderr)
    return 1
