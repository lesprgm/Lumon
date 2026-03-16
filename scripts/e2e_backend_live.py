from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.main import app
from session_bootstrap_utils import build_ws_url


def main() -> None:
    os.environ.setdefault("LUMON_HEADLESS", "1")
    with TestClient(app) as client:
        bootstrap = client.get("/api/bootstrap", headers={"Origin": "http://127.0.0.1:5173"})
        bootstrap.raise_for_status()
        payload = bootstrap.json()
        with client.websocket_connect(
            build_ws_url("/ws/session", payload["session_id"], payload["ws_token"]),
            headers={"origin": "http://127.0.0.1:5173"},
        ) as websocket:
            websocket.receive_json()
            websocket.send_json(
                {
                    "type": "start_task",
                    "payload": {
                        "task_text": "Find a hotel in NYC next weekend under $250",
                        "demo_mode": True,
                        "adapter_id": "playwright_native",
                    },
                }
            )
            saw_takeover = False
            saw_fresh_checkpoint = False
            saw_result = False
            first_checkpoint = None
            deadline = time.monotonic() + 30

            while time.monotonic() < deadline:
                message = websocket.receive_json()
                if message["type"] == "frame":
                    continue
                if message["type"] == "approval_required" and not saw_takeover:
                    first_checkpoint = message["payload"]["checkpoint_id"]
                    websocket.send_json({"type": "start_takeover", "payload": {}})
                elif message["type"] == "session_state" and message["payload"]["state"] == "takeover":
                    saw_takeover = True
                    websocket.send_json({"type": "end_takeover", "payload": {}})
                elif message["type"] == "session_state" and message["payload"]["state"] == "paused" and saw_takeover:
                    websocket.send_json({"type": "resume", "payload": {}})
                elif message["type"] == "approval_required" and saw_takeover:
                    if first_checkpoint is not None and message["payload"]["checkpoint_id"] == first_checkpoint:
                        raise SystemExit("Checkpoint was not re-issued after takeover")
                    saw_fresh_checkpoint = True
                    websocket.send_json(
                        {
                            "type": "approve",
                            "payload": {"checkpoint_id": message["payload"]["checkpoint_id"]},
                        }
                    )
                elif message["type"] == "task_result":
                    saw_result = True
                    if message["payload"]["status"] != "completed":
                        raise SystemExit(f"Unexpected task result: {message['payload']}")
                    break

            websocket.close()

    if not (saw_takeover and saw_fresh_checkpoint and saw_result):
        raise SystemExit(
            f"Live backend E2E failed: takeover={saw_takeover}, fresh_checkpoint={saw_fresh_checkpoint}, result={saw_result}"
        )


if __name__ == "__main__":
    main()
