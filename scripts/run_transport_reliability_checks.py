from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import InvalidStatus

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output" / "manual_checks"
LOG_PATHS = {
    "plugin": Path("/tmp/lumon-plugin-debug.log"),
    "backend": Path("/tmp/lumon-backend.log"),
    "frontend": Path("/tmp/lumon-frontend.log"),
}


def load_runtime_origins() -> tuple[str, str, str]:
    backend_url = "http://127.0.0.1:8000"
    backend_env = ROOT / "output" / "runtime" / "lumon_backend.env"
    if backend_env.exists():
        for line in backend_env.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "VITE_LUMON_BACKEND_ORIGIN" and value.strip():
                backend_url = value.strip()
                break
    parsed = urlparse(backend_url)
    ws_base_url = f"{'wss' if parsed.scheme == 'https' else 'ws'}://{parsed.netloc}"
    return backend_url.rstrip("/"), "http://127.0.0.1:5173", ws_base_url


BACKEND_URL, FRONTEND_URL, WS_BASE_URL = load_runtime_origins()
LOCAL_TRACE_URL = f"{BACKEND_URL}/__lumon_harness__/search"
LOCAL_APPROVAL_URL = f"{BACKEND_URL}/__lumon_harness__/approval"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run transport-focused Lumon reliability checks.")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--output-dir", default=None)
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
    global BACKEND_URL, FRONTEND_URL, WS_BASE_URL, LOCAL_TRACE_URL, LOCAL_APPROVAL_URL
    subprocess.run(["./lumon", "restart"], cwd=ROOT, check=True)
    BACKEND_URL, FRONTEND_URL, WS_BASE_URL = load_runtime_origins()
    LOCAL_TRACE_URL = f"{BACKEND_URL}/__lumon_harness__/search"
    LOCAL_APPROVAL_URL = f"{BACKEND_URL}/__lumon_harness__/approval"
    wait_for_http(f"{BACKEND_URL}/healthz")
    wait_for_http(FRONTEND_URL)


def ensure_services_ready(*, restart: bool) -> None:
    if restart:
        maybe_restart_services()
        return
    try:
        wait_for_http(f"{BACKEND_URL}/healthz", timeout_seconds=12.0)
        wait_for_http(FRONTEND_URL, timeout_seconds=12.0)
    except RuntimeError:
        maybe_restart_services()


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


async def collect_messages(session: dict[str, str], *, duration_seconds: float = 1.2) -> list[dict[str, Any]]:
    ws_url = f"{WS_BASE_URL}{session['ws_path']}?session_id={session['session_id']}&token={session['token']}"
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


async def reconnect_messages(session: dict[str, str]) -> list[dict[str, Any]]:
    _ = await collect_messages(session, duration_seconds=0.3)
    return await collect_messages(session, duration_seconds=0.6)


async def stale_credential_result(session: dict[str, str]) -> dict[str, Any]:
    bad_url = (
        f"{WS_BASE_URL}{session['ws_path']}?session_id={session['session_id']}&token={session['token']}_stale"
    )
    try:
        async with ws_connect(bad_url, origin=FRONTEND_URL):
            return {"result": "unexpected_success"}
    except InvalidStatus as exc:
        return {"result": "invalid_status", "status_code": exc.response.status_code}
    except Exception as exc:  # pragma: no cover - diagnostic fallback
        return {"result": "exception", "detail": str(exc)}


def approve_checkpoint(session_id: str, checkpoint_id: str) -> dict[str, Any]:
    return request_json(
        f"{BACKEND_URL}/api/local/session/{session_id}/approve",
        {"checkpoint_id": checkpoint_id},
    )


def run_command_with_retries(base_payload: dict[str, Any], spec: dict[str, Any], *, attempts: int = 40, delay_seconds: float = 0.25) -> dict[str, Any]:
    response: dict[str, Any] = {}
    for _ in range(attempts):
        response = request_json(f"{BACKEND_URL}/api/local/opencode/browser/command", {**base_payload, **spec})
        if not (response.get("status") == "blocked" and response.get("reason") == "busy"):
            return response
        time.sleep(delay_seconds)
    return response


def run_browser_sequence() -> tuple[dict[str, str], list[dict[str, Any]], dict[str, Any]]:
    observed_session_id = f"sess_transport_{stamp()}"
    base_payload = {
        "project_directory": str(ROOT),
        "observed_session_id": observed_session_id,
        "frontend_origin": FRONTEND_URL,
    }
    traces: list[dict[str, Any]] = []
    for spec in (
        {
            "command_id": "transport_begin",
            "command": "begin_task",
            "task_text": "Open the local trace page, inspect it, and stop before submitting.",
        },
        {"command_id": "transport_open", "command": "open", "url": LOCAL_TRACE_URL},
        {"command_id": "transport_inspect", "command": "inspect"},
        {"command_id": "transport_status", "command": "status"},
        {"command_id": "transport_stop", "command": "stop"},
    ):
        started = time.monotonic()
        response = request_json(f"{BACKEND_URL}/api/local/opencode/browser/command", {**base_payload, **spec})
        traces.append(
            {
                "command": spec["command"],
                "command_id": spec["command_id"],
                "duration_ms": round((time.monotonic() - started) * 1000),
                "response": response,
            }
        )
    open_url = next(
        (
            item["response"].get("open_url")
            for item in traces
            if isinstance(item["response"], dict) and item["response"].get("open_url")
        ),
        None,
    )
    if not open_url:
        raise RuntimeError("No open_url returned from browser sequence")
    session = parse_open_url(open_url)
    with urlopen(f"{BACKEND_URL}/api/session-artifacts/{session['session_id']}", timeout=10) as response:  # noqa: S310
        artifact = json.loads(response.read().decode("utf-8"))
    return session, traces, artifact


def run_approval_sequence() -> tuple[dict[str, str], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    observed_session_id = f"sess_transport_approval_{stamp()}"
    base_payload = {
        "project_directory": str(ROOT),
        "observed_session_id": observed_session_id,
        "frontend_origin": FRONTEND_URL,
    }
    traces: list[dict[str, Any]] = []
    for spec in (
        {
            "command_id": "approval_begin",
            "command": "begin_task",
            "task_text": "Open the local approval page, click submit, approve the intervention, and stop.",
        },
        {"command_id": "approval_open", "command": "open", "url": LOCAL_APPROVAL_URL},
        {"command_id": "approval_inspect", "command": "inspect"},
        {"command_id": "approval_click", "command": "click", "selector": "#submit-order"},
    ):
        started = time.monotonic()
        response = request_json(f"{BACKEND_URL}/api/local/opencode/browser/command", {**base_payload, **spec})
        traces.append(
            {
                "command": spec["command"],
                "command_id": spec["command_id"],
                "duration_ms": round((time.monotonic() - started) * 1000),
                "response": response,
            }
        )
    open_url = next(
        (
            item["response"].get("open_url")
            for item in traces
            if isinstance(item["response"], dict) and item["response"].get("open_url")
        ),
        None,
    )
    if not open_url:
        raise RuntimeError("No open_url returned from approval sequence")
    session = parse_open_url(open_url)
    click_response = traces[-1]["response"]
    approval_result: dict[str, Any] = {}
    checkpoint_id = click_response.get("checkpoint_id")
    if click_response.get("status") == "blocked" and checkpoint_id:
        approval_result = approve_checkpoint(session["session_id"], checkpoint_id)
        for spec in (
            {"command_id": "approval_status", "command": "status"},
            {"command_id": "approval_stop", "command": "stop"},
        ):
            started = time.monotonic()
            response = run_command_with_retries(base_payload, spec)
            traces.append(
                {
                    "command": spec["command"],
                    "command_id": spec["command_id"],
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "response": response,
                }
            )
    with urlopen(f"{BACKEND_URL}/api/session-artifacts/{session['session_id']}", timeout=10) as response:  # noqa: S310
        artifact = json.loads(response.read().decode("utf-8"))
    return session, traces, artifact, approval_result


def build_assertions(
    traces: list[dict[str, Any]],
    reconnect: list[dict[str, Any]],
    stale: dict[str, Any],
    artifact: dict[str, Any],
    approval: dict[str, Any],
) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []

    def check(code: str, passed: bool, detail: Any) -> None:
        assertions.append({"code": code, "pass": passed, "detail": detail})

    check(
        "direct_commands_succeed",
        all(item["response"].get("status") == "success" for item in traces if item["command"] != "begin_task"),
        traces,
    )
    check(
        "reconnect_replays_session_state",
        any(message.get("type") == "session_state" for message in reconnect),
        reconnect,
    )
    check(
        "reconnect_replays_live_state",
        any(message.get("type") in {"browser_context_update", "frame", "browser_command"} for message in reconnect),
        reconnect,
    )
    check(
        "stale_credentials_rejected",
        stale.get("result") == "invalid_status" and stale.get("status_code") in {403, 401},
        stale,
    )
    artifact_commands = [f"{item.get('command')}:{item.get('command_id')}" for item in artifact.get("commands", [])]
    check(
        "artifact_contains_transport_commands",
        all(f"{item['command']}:{item['command_id']}" in artifact_commands for item in traces),
        {"artifact_commands": artifact_commands, "trace_commands": traces},
    )
    approval_click = next((item["response"] for item in approval["commands"] if item["command"] == "click"), {})
    approval_resolutions = [
        item.get("resolution")
        for item in approval["artifact"].get("artifact", {}).get("interventions", [])
        if item.get("checkpoint_id") == approval_click.get("checkpoint_id")
    ]
    check(
        "approval_flow_blocks_then_resolves",
        approval_click.get("status") == "blocked"
        and approval["resolution"].get("artifact", {}).get("session_id") == approval["session"]["session_id"]
        and "approved" in approval_resolutions
        and any(
            item["response"].get("status") == "success"
            for item in approval["commands"]
            if item["command"] in {"status", "stop"}
        ),
        approval,
    )
    check(
        "approval_artifact_resolves_intervention",
        "approved" in approval_resolutions,
        {"resolutions": approval_resolutions, "checkpoint_id": approval_click.get("checkpoint_id")},
    )
    return assertions


def main() -> int:
    args = parse_args()
    out_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR / f"transport_reliability_{stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)

    ensure_services_ready(restart=args.restart)

    session, traces, artifact = run_browser_sequence()
    approval_session, approval_traces, approval_artifact, approval_result = run_approval_sequence()
    reconnect = asyncio.run(reconnect_messages(session))
    stale = asyncio.run(stale_credential_result(session))
    assertions = build_assertions(
        traces,
        reconnect,
        stale,
        artifact,
        {
            "session": approval_session,
            "commands": approval_traces,
            "artifact": approval_artifact,
            "resolution": approval_result,
        },
    )

    payload = {
        "generated_at": datetime.now().isoformat(),
        "session": session,
        "commands": traces,
        "reconnect_messages": reconnect,
        "stale_credentials": stale,
        "artifact": artifact,
        "approval": {
            "session": approval_session,
            "commands": approval_traces,
            "artifact": approval_artifact,
            "resolution": approval_result,
        },
        "assertions": assertions,
    }
    (out_dir / "transport.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (out_dir / "summary.md").write_text(
        "\n".join([
            "# Transport Reliability Checks",
            "",
            *[f"- {'[pass]' if item['pass'] else '[fail]'} {item['code']}" for item in assertions],
        ]) + "\n",
        encoding="utf-8",
    )
    for label, path in LOG_PATHS.items():
        (out_dir / f"{label}.log.tail.txt").write_text(tail_file(path, args.tail_lines), encoding="utf-8")

    failing = [item for item in assertions if not item["pass"]]
    if failing:
        print(json.dumps({"output_dir": str(out_dir), "failing": failing}, indent=2), flush=True)
        return 1
    print(json.dumps({"output_dir": str(out_dir), "assertion_count": len(assertions)}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
