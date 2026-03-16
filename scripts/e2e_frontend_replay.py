from __future__ import annotations

import asyncio
import contextlib
import http.server
import os
import socketserver
import threading
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "frontend" / "dist"


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

    with serve_dist() as (url, _server):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle")
            await page.get_by_role("button", name="Show activity").click()
            timeout_ms = 20000
            await page.wait_for_selector(".timeline-panel", timeout=timeout_ms)
            await page.get_by_role("heading", name="Activity").wait_for(timeout=timeout_ms)
            await page.wait_for_selector("text=What's happening", timeout=timeout_ms)
            await page.screenshot(path=str(ROOT / "output" / "playwright" / "frontend_replay.png"))
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
