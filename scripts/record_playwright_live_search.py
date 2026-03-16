from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright
from websockets.asyncio.client import connect as ws_connect

from session_bootstrap_utils import bootstrap_session, build_ws_url, ensure_recording_enabled

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
OUTPUT_DIR = ROOT / "output" / "playwright"
VIDEO_PATH = OUTPUT_DIR / "playwright_live_search_recording.webm"
POSTER_PATH = OUTPUT_DIR / "playwright_live_search_poster.png"
BACKEND_LOG = OUTPUT_DIR / "playwright_live_search_backend.log"
FRONTEND_LOG = OUTPUT_DIR / "playwright_live_search_frontend.log"

BACKEND_URL = "http://127.0.0.1:8000"
FRONTEND_URL = "http://127.0.0.1:4173"
TASK_TEXT = os.getenv("LUMON_LIVE_SEARCH_TASK", "search the web for OpenAI API docs")
WS_BASE_URL = "ws://127.0.0.1:8000/ws/session"


def wait_for_http(url: str, timeout_seconds: float = 30.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310
                if response.status < 500:
                    return
        except Exception:
            time.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for {url}")


@contextlib.contextmanager
def start_backend() -> subprocess.Popen[str]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_handle = BACKEND_LOG.open("w", encoding="utf-8")
    python_bin = BACKEND_DIR / ".venv" / "bin" / "python"
    if not python_bin.exists():
        python_bin = Path("python3")
    env = os.environ.copy()
    env["LUMON_HEADLESS"] = "1"
    process = subprocess.Popen(  # noqa: S603
        [
            str(python_bin),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        cwd=BACKEND_DIR,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for_http(f"{BACKEND_URL}/healthz")
        yield process
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        log_handle.close()


@contextlib.contextmanager
def start_frontend() -> subprocess.Popen[str]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_handle = FRONTEND_LOG.open("w", encoding="utf-8")
    env = os.environ.copy()
    env["VITE_LUMON_REPLAY"] = "false"
    process = subprocess.Popen(  # noqa: S603
        [
            "npm",
            "run",
            "dev",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            "4173",
        ],
        cwd=FRONTEND_DIR,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for_http(FRONTEND_URL)
        yield process
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        log_handle.close()


async def main() -> None:
    ensure_recording_enabled()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with start_backend(), start_frontend():
        session = bootstrap_session(BACKEND_URL, FRONTEND_URL)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1440, "height": 960},
                record_video_dir=str(OUTPUT_DIR),
                record_video_size={"width": 1440, "height": 960},
            )
            page = await context.new_page()
            await page.goto(FRONTEND_URL, wait_until="networkidle")
            async with ws_connect(
                build_ws_url(WS_BASE_URL, session["session_id"], session["ws_token"]),
                origin=FRONTEND_URL,
            ) as websocket:
                await websocket.recv()
                await websocket.send(
                    json.dumps(
                        {
                            "type": "start_task",
                            "payload": {
                                "task_text": TASK_TEXT,
                                "demo_mode": False,
                                "adapter_id": "playwright_native",
                            },
                        }
                    )
                )
                while True:
                    message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=60))
                    message_type = message.get("type")
                    if message_type == "task_result":
                        break
                    if message_type == "error":
                        raise RuntimeError(message["payload"]["message"])
                    if message_type == "session_state" and message["payload"]["state"] in {"failed", "stopped"}:
                        raise RuntimeError(f'Live run ended in state {message["payload"]["state"]}')

            await page.wait_for_timeout(1800)
            await page.screenshot(path=str(POSTER_PATH))
            raw_video_path = await page.video.path() if page.video else None
            await page.close()
            await context.close()
            await browser.close()

    if not raw_video_path:
        raise SystemExit("Playwright did not produce a video file.")

    if VIDEO_PATH.exists():
        VIDEO_PATH.unlink()
    shutil.move(raw_video_path, VIDEO_PATH)
    print(f"Wrote {VIDEO_PATH}")
    print(f"Wrote {POSTER_PATH}")
    print(f"Backend log: {BACKEND_LOG}")
    print(f"Frontend log: {FRONTEND_LOG}")


if __name__ == "__main__":
    asyncio.run(main())
