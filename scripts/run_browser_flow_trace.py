from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.main import app  # noqa: E402


BACKEND_URL = "http://127.0.0.1:8000"
FRONTEND_URL = "http://127.0.0.1:5173"
WS_BASE_URL = "ws://127.0.0.1:8000/ws/session"
OUTPUT_DIR = ROOT / "output" / "manual_checks"
LOG_PATHS = {
    "plugin": Path("/tmp/lumon-plugin-debug.log"),
    "backend": Path("/tmp/lumon-backend.log"),
    "frontend": Path("/tmp/lumon-frontend.log"),
}
INPROCESS_TRACE_URL = (
    "data:text/html;charset=utf-8,"
    "<html><body><input id='search' aria-label='Search Wikipedia' placeholder='Search Wikipedia' />"
    "<button id='go'>Search</button></body></html>"
)


@dataclass
class CommandTrace:
    command: str
    command_id: str
    duration_ms: int
    response: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a deterministic Lumon browser-command flow trace and capture telemetry.")
    parser.add_argument("--mode", choices=("stack", "inprocess"), default="stack")
    parser.add_argument(
        "--prompt",
        default='Open https://www.wikipedia.org, click the search box, type "OpenAI", and stop before submitting.',
    )
    parser.add_argument("--open-url", default="https://www.wikipedia.org")
    parser.add_argument("--restart", action="store_true", help="Restart backend/frontend first in stack mode.")
    parser.add_argument("--tail-lines", type=int, default=120)
    return parser.parse_args()


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def wait_for_http(url: str, timeout_seconds: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:  # noqa: S310
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for {url}")


def maybe_restart_services() -> None:
    subprocess.run(["./lumon", "restart"], cwd=ROOT, check=True)
    wait_for_http(f"{BACKEND_URL}/healthz")
    wait_for_http(FRONTEND_URL)


def request_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=45) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def parse_open_url(open_url: str) -> dict[str, str]:
    parsed = urlparse(open_url)
    params = parse_qs(parsed.query)
    return {
        "session_id": params["session_id"][0],
        "token": params["ws_token"][0],
        "ws_path": params.get("ws_path", ["/ws/session"])[0],
    }


def tail_file(path: Path, line_count: int) -> str:
    if not path.exists():
        return f"(missing) {path}"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-line_count:]) if lines else "(empty)"


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


async def collect_stack_ws_messages(session: dict[str, str], *, duration_seconds: float) -> list[dict[str, Any]]:
    from websockets.asyncio.client import connect as ws_connect

    ws_url = f"{WS_BASE_URL}?session_id={session['session_id']}&token={session['token']}"
    messages: list[dict[str, Any]] = []
    async with ws_connect(ws_url, origin=FRONTEND_URL) as websocket:
        messages.append(json.loads(await asyncio.wait_for(websocket.recv(), timeout=5)))
        await websocket.send(json.dumps({"type": "ui_ready", "payload": {"ready": True}}))
        deadline = asyncio.get_running_loop().time() + duration_seconds
        while asyncio.get_running_loop().time() < deadline:
            try:
                message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=0.25))
            except asyncio.TimeoutError:
                continue
            messages.append(message)
    return messages


def command_sequence(prompt: str, *, open_url: str) -> list[dict[str, Any]]:
    return [
        {"command_id": "trace_begin", "command": "begin_task", "task_text": prompt},
        {"command_id": "trace_status_1", "command": "status"},
        {"command_id": "trace_open_1", "command": "open", "url": open_url},
    ]


def choose_primary_target(actionable_elements: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not actionable_elements:
        return None
    preferred_labels = ("search", "search wikipedia", "search box", "search input")
    typeable = [item for item in actionable_elements if item.get("typeable")]
    for item in typeable:
        label = str(item.get("label") or "").lower()
        if any(token in label for token in preferred_labels):
            return item
    if typeable:
        return typeable[0]
    clickable = [item for item in actionable_elements if item.get("clickable")]
    return clickable[0] if clickable else actionable_elements[0]


def append_trace(traces: list[CommandTrace], spec: dict[str, Any], response: dict[str, Any], started: float, ended: float) -> None:
    traces.append(
        CommandTrace(
            command=spec["command"],
            command_id=spec["command_id"],
            duration_ms=round((ended - started) * 1000),
            response=response,
        )
    )


def run_stack(args: argparse.Namespace) -> dict[str, Any]:
    if args.restart:
        maybe_restart_services()
    else:
        wait_for_http(f"{BACKEND_URL}/healthz")
        wait_for_http(FRONTEND_URL)

    observed_session_id = f"ses_trace_{stamp()}"
    base_payload = {
        "project_directory": str(ROOT),
        "observed_session_id": observed_session_id,
        "frontend_origin": FRONTEND_URL,
    }
    traces: list[CommandTrace] = []
    first_response: dict[str, Any] | None = None
    for spec in command_sequence(args.prompt, open_url=args.open_url):
        started = time.monotonic()
        response = request_json(f"{BACKEND_URL}/api/local/opencode/browser/command", {**base_payload, **spec})
        ended = time.monotonic()
        append_trace(traces, spec, response, started, ended)
        if first_response is None:
            first_response = response
        time.sleep(0.3)

    inspect_spec = {"command_id": "trace_inspect_1", "command": "inspect"}
    started = time.monotonic()
    inspect_response = request_json(f"{BACKEND_URL}/api/local/opencode/browser/command", {**base_payload, **inspect_spec})
    ended = time.monotonic()
    append_trace(traces, inspect_spec, inspect_response, started, ended)

    target = choose_primary_target(inspect_response.get("actionable_elements") or [])
    if target is not None:
        click_spec = {
            "command_id": "trace_click_1",
            "command": "click",
            "element_id": target["element_id"],
        }
        started = time.monotonic()
        click_response = request_json(f"{BACKEND_URL}/api/local/opencode/browser/command", {**base_payload, **click_spec})
        ended = time.monotonic()
        append_trace(traces, click_spec, click_response, started, ended)

        if target.get("typeable"):
            type_spec = {
                "command_id": "trace_type_1",
                "command": "type",
                "element_id": target["element_id"],
                "text": "OpenAI",
            }
            started = time.monotonic()
            type_response = request_json(f"{BACKEND_URL}/api/local/opencode/browser/command", {**base_payload, **type_spec})
            ended = time.monotonic()
            append_trace(traces, type_spec, type_response, started, ended)

    for spec in (
        {"command_id": "trace_status_2", "command": "status"},
        {"command_id": "trace_stop_1", "command": "stop"},
    ):
        started = time.monotonic()
        response = request_json(f"{BACKEND_URL}/api/local/opencode/browser/command", {**base_payload, **spec})
        ended = time.monotonic()
        append_trace(traces, spec, response, started, ended)
        time.sleep(0.2)

    if first_response is None or not first_response.get("open_url"):
        raise RuntimeError("No open_url returned; cannot attach websocket trace.")

    session = parse_open_url(first_response["open_url"])
    ws_messages = asyncio.run(collect_stack_ws_messages(session, duration_seconds=2.5))
    with urlopen(f"{BACKEND_URL}/api/session-artifacts/{session['session_id']}", timeout=10) as response:  # noqa: S310
        artifact = json.loads(response.read().decode("utf-8"))

    return {
        "mode": "stack",
        "observed_session_id": observed_session_id,
        "session": session,
        "commands": [asdict(trace) for trace in traces],
        "ws_messages": ws_messages,
        "artifact": artifact,
    }


def run_inprocess(args: argparse.Namespace) -> dict[str, Any]:
    observed_session_id = f"ses_trace_{stamp()}"
    base_payload = {
        "project_directory": str(ROOT),
        "observed_session_id": observed_session_id,
        "frontend_origin": FRONTEND_URL,
    }
    traces: list[CommandTrace] = []
    ws_messages: list[dict[str, Any]] = []

    with TestClient(app) as client:
        first_response: dict[str, Any] | None = None
        inprocess_prompt = "Open the local trace page, inspect it, and stop before submitting."
        for spec in command_sequence(inprocess_prompt, open_url=INPROCESS_TRACE_URL):
            started = time.monotonic()
            response = client.post("/api/local/opencode/browser/command", json={**base_payload, **spec})
            response.raise_for_status()
            payload = response.json()
            ended = time.monotonic()
            append_trace(traces, spec, payload, started, ended)
            if first_response is None:
                first_response = payload

        inspect_spec = {"command_id": "trace_inspect_1", "command": "inspect"}
        started = time.monotonic()
        inspect_response = client.post("/api/local/opencode/browser/command", json={**base_payload, **inspect_spec})
        inspect_response.raise_for_status()
        inspect_payload = inspect_response.json()
        ended = time.monotonic()
        append_trace(traces, inspect_spec, inspect_payload, started, ended)

        target = choose_primary_target(inspect_payload.get("actionable_elements") or [])
        if target is not None:
            click_spec = {
                "command_id": "trace_click_1",
                "command": "click",
                "element_id": target["element_id"],
            }
            started = time.monotonic()
            click_response = client.post("/api/local/opencode/browser/command", json={**base_payload, **click_spec})
            click_response.raise_for_status()
            click_payload = click_response.json()
            ended = time.monotonic()
            append_trace(traces, click_spec, click_payload, started, ended)

            if target.get("typeable"):
                type_spec = {
                    "command_id": "trace_type_1",
                    "command": "type",
                    "element_id": target["element_id"],
                    "text": "OpenAI",
                }
                started = time.monotonic()
                type_response = client.post("/api/local/opencode/browser/command", json={**base_payload, **type_spec})
                type_response.raise_for_status()
                type_payload = type_response.json()
                ended = time.monotonic()
                append_trace(traces, type_spec, type_payload, started, ended)

        for spec in (
            {"command_id": "trace_status_2", "command": "status"},
            {"command_id": "trace_stop_1", "command": "stop"},
        ):
            started = time.monotonic()
            response = client.post("/api/local/opencode/browser/command", json={**base_payload, **spec})
            response.raise_for_status()
            payload = response.json()
            ended = time.monotonic()
            append_trace(traces, spec, payload, started, ended)

        if first_response is None or not first_response.get("open_url"):
            raise RuntimeError("No open_url returned; cannot attach websocket trace.")

        session = parse_open_url(first_response["open_url"])
        with client.websocket_connect(
            f"/ws/session?session_id={session['session_id']}&token={session['token']}",
            headers={"origin": FRONTEND_URL},
        ) as websocket:
            ws_messages.append(websocket.receive_json())
            websocket.send_json({"type": "ui_ready", "payload": {"ready": True}})
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                try:
                    ws_messages.append(websocket.receive_json(timeout=0.2))
                except Exception:
                    break

        artifact_response = client.get(f"/api/session-artifacts/{session['session_id']}")
        artifact_response.raise_for_status()
        artifact = artifact_response.json()

    return {
        "mode": "inprocess",
        "observed_session_id": observed_session_id,
        "session": session,
        "commands": [asdict(trace) for trace in traces],
        "ws_messages": ws_messages,
        "artifact": artifact,
    }


def build_summary(trace: dict[str, Any]) -> str:
    lines = [
        "# Lumon Browser Flow Trace",
        "",
        f"- mode: `{trace['mode']}`",
        f"- observed_session_id: `{trace['observed_session_id']}`",
        f"- session_id: `{trace['session']['session_id']}`",
        "",
        "## Commands",
    ]
    for item in trace["commands"]:
        response = item["response"]
        lines.append(
            f"- `{item['command']}` `{item['command_id']}`: status={response.get('status')} reason={response.get('reason')} duration_ms={item['duration_ms']}"
        )

    counts: dict[str, int] = {}
    for message in trace["ws_messages"]:
        key = message.get("type", "unknown")
        counts[key] = counts.get(key, 0) + 1

    lines.append("")
    lines.append("## WebSocket message counts")
    for key, value in sorted(counts.items()):
        lines.append(f"- `{key}`: {value}")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    out_dir = OUTPUT_DIR / f"browser_flow_trace_{stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)

    trace = run_stack(args) if args.mode == "stack" else run_inprocess(args)

    write_json(out_dir / "trace.json", trace)
    with (out_dir / "ws_messages.ndjson").open("w", encoding="utf-8") as handle:
        for message in trace["ws_messages"]:
            handle.write(json.dumps(message) + "\n")
    (out_dir / "summary.md").write_text(build_summary(trace), encoding="utf-8")
    for label, path in LOG_PATHS.items():
        (out_dir / f"{label}.log.tail.txt").write_text(tail_file(path, args.tail_lines), encoding="utf-8")

    print(f"Wrote browser flow trace: {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
