from __future__ import annotations

import asyncio
import os
import random

try:
    from playwright.async_api import Browser, Playwright, async_playwright
except ImportError:
    Browser = Playwright = object  # type: ignore[assignment]
    async_playwright = None


_pool_instance: BrowserPool | None = None


class BrowserPool:
    def __init__(self, min_browsers: int = 2) -> None:
        if min_browsers < 1:
            raise ValueError("Browser pool size must be at least 1")
        self._min_browsers = min_browsers
        self._playwright: Playwright | None = None
        self._browsers: list[Browser] = []
        self._lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        if async_playwright is None:
            raise RuntimeError("Playwright is not installed")
        async with self._lock:
            if self._started:
                return
            self._playwright = await async_playwright().start()
            for _ in range(self._min_browsers):
                browser = await self._playwright.chromium.launch(headless=True)
                self._browsers.append(browser)
            self._started = True

    async def get_instance(self) -> tuple[Playwright, Browser]:
        async with self._lock:
            if not self._started or self._playwright is None or not self._browsers:
                raise RuntimeError("Browser pool is not running")
            return self._playwright, random.choice(self._browsers)

    async def shutdown(self) -> None:
        async with self._lock:
            browsers, self._browsers = self._browsers, []
            playwright, self._playwright = self._playwright, None
            self._started = False
            first_error: Exception | None = None
            for browser in browsers:
                try:
                    await browser.close()
                except Exception as exc:
                    first_error = first_error or exc
            if playwright is not None:
                try:
                    await playwright.stop()
                except Exception as exc:
                    first_error = first_error or exc
            if first_error is not None:
                raise first_error


async def init_browser_pool() -> BrowserPool:
    global _pool_instance
    if _pool_instance is not None:
        return _pool_instance
    min_browsers = int(os.getenv("LUMON_BROWSER_POOL_SIZE", "2"))
    pool = BrowserPool(min_browsers=min_browsers)
    await pool.start()
    _pool_instance = pool
    return pool


def get_browser_pool() -> BrowserPool | None:
    return _pool_instance


async def shutdown_browser_pool() -> None:
    global _pool_instance
    if _pool_instance is not None:
        await _pool_instance.shutdown()
        _pool_instance = None
