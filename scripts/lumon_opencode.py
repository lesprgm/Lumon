from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from websockets.asyncio.client import connect as ws_connect

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.opencode_observer import DEFAULT_OPENCODE_DB_PATH, OpenCodeSQLiteObserver  # noqa: E402
from session_bootstrap_utils import bootstrap_session, build_ws_url  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Internal/debug wrapper that launches OpenCode TUI with Lumon attached as an observer.",
    )
    parser.add_argument(
        "--backend-origin",
        default="http://127.0.0.1:8000",
        help="Lumon backend origin.",
    )
    parser.add_argument(
        "--frontend-origin",
        default="http://127.0.0.1:8000",
        help="Lumon frontend origin.",
    )
    parser.add_argument(
        "--web-mode",
        choices=("observe_only", "delegate_playwright"),
        default="observe_only",
        help="How Lumon should handle web-capable OpenCode work.",
    )
    parser.add_argument(
        "--web-bridge",
        choices=("playwright_native", "off"),
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_OPENCODE_DB_PATH),
        help="OpenCode sqlite database path.",
    )
    parser.add_argument(
        "--auto-delegate",
        action="store_true",
        help="Automatically launch the delegated browser bridge when browser-capable OpenCode work is detected.",
    )
    parser.add_argument(
        "--session-timeout",
        type=float,
        default=20.0,
        help="Seconds to wait for a new OpenCode session to appear in sqlite.",
    )
    parser.add_argument(
        "--discover-poll-interval",
        type=float,
        default=0.08,
        help="Seconds between sqlite polls while discovering the active OpenCode session.",
    )
    parser.add_argument(
        "--idle-poll-min",
        type=float,
        default=0.1,
        help="Minimum seconds between sqlite polls after the OpenCode session is locked.",
    )
    parser.add_argument(
        "--idle-poll-max",
        type=float,
        default=0.6,
        help="Maximum seconds between sqlite polls after the OpenCode session is locked.",
    )
    parser.add_argument(
        "--observed-session-id",
        default=None,
        help="Existing OpenCode session id to follow instead of discovering a new one.",
    )
    parser.add_argument(
        "opencode_args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to `opencode` after `--`.",
    )
    args = parser.parse_args()
    if args.web_bridge == "playwright_native":
        args.web_mode = "delegate_playwright"
    return args


def forwarded_opencode_args(raw_args: list[str]) -> list[str]:
    args = list(raw_args)
    if args[:1] == ["--"]:
        args = args[1:]
    if args and args[0] in {"run", "serve", "attach", "session", "export"}:
        raise SystemExit("lumon_opencode.py only wraps interactive `opencode [project]` sessions, not opencode subcommands.")
    return args


def infer_project_directory(opencode_args: list[str]) -> str:
    for value in opencode_args:
        if value.startswith("-"):
            continue
        return str(Path(value).expanduser().resolve())
    return str(Path.cwd().resolve())


def build_frontend_session_url(frontend_origin: str, bootstrap: dict[str, str]) -> str:
    return f"{frontend_origin}/?{urlencode({'session_id': bootstrap['session_id'], 'ws_token': bootstrap['ws_token'], 'ws_path': bootstrap['ws_path'], 'protocol_version': bootstrap['protocol_version']})}"


async def drain_messages(websocket) -> None:
    try:
        while True:
            await websocket.recv()
    except Exception:
        return


def bridge_for_web_mode(web_mode: str) -> str | None:
    if web_mode == "delegate_playwright":
        return "playwright_native"
    return None


async def wait_for_session(
    observer: OpenCodeSQLiteObserver,
    directory: str,
    *,
    since_ms: int,
    exclude_session_ids: set[str],
    preferred_session_id: str | None,
    timeout_seconds: float,
    poll_interval: float,
):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        session = observer.find_session(
            directory,
            since_ms=since_ms,
            exclude_session_ids=exclude_session_ids,
            preferred_session_id=preferred_session_id,
        )
        if session is not None:
            return session
        await asyncio.sleep(poll_interval)
    return None


async def stream_observed_parts(
    websocket,
    observer: OpenCodeSQLiteObserver,
    session_id: str,
    *,
    min_poll_interval: float,
    max_poll_interval: float,
    process: asyncio.subprocess.Process,
) -> None:
    last_rowid = 0
    quiet_polls_after_exit = 0
    current_interval = min_poll_interval
    while True:
        parts = observer.load_parts(session_id, after_rowid=last_rowid)
        if parts:
            quiet_polls_after_exit = 0
            current_interval = min_poll_interval
            last_rowid = parts[-1].rowid
            for observed_part in parts:
                payload = observer.part_to_observer_event(observed_part)
                if payload is None:
                    continue
                await websocket.send(json.dumps({"type": "observer_event", "payload": payload}))
            continue

        if process.returncode is None:
            await asyncio.sleep(current_interval)
            current_interval = min(max_poll_interval, max(min_poll_interval, current_interval * 1.5))
            continue

        quiet_polls_after_exit += 1
        if quiet_polls_after_exit >= 3:
            return
        await asyncio.sleep(current_interval)
        current_interval = min(max_poll_interval, max(min_poll_interval, current_interval * 1.5))


async def run() -> int:
    args = parse_args()
    opencode_args = forwarded_opencode_args(args.opencode_args)
    if shutil.which("opencode") is None:
        raise SystemExit("OpenCode CLI is not installed or not on PATH.")

    print("[lumon_opencode] Internal debug wrapper. Primary user flow is plain: opencode .", flush=True)

    observer = OpenCodeSQLiteObserver(args.db_path)
    project_directory = infer_project_directory(opencode_args)
    session_bootstrap = bootstrap_session(args.backend_origin, args.frontend_origin)
    frontend_url = build_frontend_session_url(args.frontend_origin, session_bootstrap)

    print(f"Lumon session URL: {frontend_url}", flush=True)
    print("Open this URL in your browser before or during the OpenCode session.", flush=True)

    web_bridge = bridge_for_web_mode(args.web_mode)
    baseline_session_ids = observer.baseline_session_ids(project_directory)
    launch_started_ms = int(time.time() * 1000)

    ws_base_url = f"{args.backend_origin.replace('http://', 'ws://').replace('https://', 'wss://')}{session_bootstrap['ws_path']}"
    async with ws_connect(
        build_ws_url(ws_base_url, session_bootstrap["session_id"], session_bootstrap["ws_token"]),
        origin=args.frontend_origin,
    ) as websocket:
        await websocket.recv()
        await websocket.send(
            json.dumps(
                {
                    "type": "attach_observer",
                    "payload": {
                        "task_text": "OpenCode interactive session",
                        "adapter_id": "opencode",
                        "web_mode": args.web_mode,
                        "web_bridge": web_bridge,
                        "auto_delegate": args.auto_delegate,
                        "observed_session_id": args.observed_session_id,
                    },
                }
            )
        )

        drain_task = asyncio.create_task(drain_messages(websocket))
        process = await asyncio.create_subprocess_exec("opencode", *opencode_args)

        try:
            session = await wait_for_session(
                observer,
                project_directory,
                since_ms=launch_started_ms,
                exclude_session_ids=baseline_session_ids,
                preferred_session_id=args.observed_session_id,
                timeout_seconds=args.session_timeout,
                poll_interval=args.discover_poll_interval,
            )
            if session is None:
                await websocket.send(
                    json.dumps(
                        {
                            "type": "observer_complete",
                            "payload": {
                                "status": "failed",
                                "summary_text": "Lumon could not detect the OpenCode session in sqlite.",
                            },
                        }
                    )
                )
                return 1

            await websocket.send(
                json.dumps(
                    {
                        "type": "observer_event",
                        "payload": {
                            "source_event_id": f"attach_{session.session_id}",
                            "event_type": "tool_start",
                            "state": "thinking",
                            "summary_text": "Lumon attached to the OpenCode TUI session",
                            "intent": session.title or "Observe the active OpenCode session",
                            "task_text": session.title or "OpenCode interactive session",
                            "meta": {
                                "part_type": "session_attach",
                                "observed_session_id": session.session_id,
                                "observed_directory": session.directory,
                            },
                        },
                    }
                )
            )

            await stream_observed_parts(
                websocket,
                observer,
                session.session_id,
                min_poll_interval=args.idle_poll_min,
                max_poll_interval=args.idle_poll_max,
                process=process,
            )
            return_code = await process.wait()
            status = "completed" if return_code == 0 else "failed"
            summary = "OpenCode interactive session completed" if status == "completed" else f"OpenCode interactive session exited with code {return_code}"
            await websocket.send(json.dumps({"type": "observer_complete", "payload": {"status": status, "summary_text": summary}}))
            return 0 if status == "completed" else 1
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()
            drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await drain_task


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
