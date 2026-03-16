from __future__ import annotations

import asyncio
import contextlib
import http.server
import os
import shutil
import socketserver
import threading
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "frontend" / "dist"
OUTPUT_DIR = ROOT / "output" / "playwright"
VIDEO_PATH = OUTPUT_DIR / "frontend_replay_recording.webm"
POSTER_PATH = OUTPUT_DIR / "frontend_replay_recording_poster.png"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


@contextlib.contextmanager
def serve_dist() -> tuple[str, socketserver.TCPServer]:
    previous = Path.cwd()
    os.chdir(DIST_DIR)
    server = socketserver.TCPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", server
    finally:
        server.shutdown()
        server.server_close()
        os.chdir(previous)


async def main() -> None:
    if not DIST_DIR.exists():
        raise SystemExit("frontend/dist does not exist. Run `cd frontend && npm run build` first.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with serve_dist() as (url, _server):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1440, "height": 960},
                record_video_dir=str(OUTPUT_DIR),
                record_video_size={"width": 1440, "height": 960},
            )
            page = await context.new_page()
            await page.goto(url, wait_until="networkidle")
            await page.wait_for_selector("text=Shortlist complete", timeout=6000)
            await page.wait_for_timeout(1500)
            await page.screenshot(path=str(POSTER_PATH))
            await page.close()
            raw_video_path = await page.video.path() if page.video else None
            await context.close()
            await browser.close()

    if not raw_video_path:
        raise SystemExit("Playwright did not produce a video file.")

    if VIDEO_PATH.exists():
        VIDEO_PATH.unlink()
    shutil.move(raw_video_path, VIDEO_PATH)
    print(f"Wrote {VIDEO_PATH}")
    print(f"Wrote {POSTER_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
