from __future__ import annotations

import asyncio
import base64

import pytest

from app.browser.screencast import CDPScreencastStreamer, ScreenshotPollStreamer

REAL_SLEEP = asyncio.sleep


class FakeCDPSession:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.sent: list[tuple[str, dict | None]] = []

    def on(self, event_name: str, handler: object) -> None:
        self.handlers[event_name] = handler

    async def send(self, command: str, params: dict | None = None) -> None:
        self.sent.append((command, params))


class FakePage:
    def __init__(self) -> None:
        self.calls = 0

    async def screenshot(self, *, type: str, quality: int) -> bytes:  # noqa: A002
        self.calls += 1
        return f"{type}:{quality}:{self.calls}".encode("utf-8")


@pytest.mark.asyncio
async def test_cdp_screencast_acks_and_emits_frame() -> None:
    cdp = FakeCDPSession()
    frames: list[dict] = []
    streamer = CDPScreencastStreamer(cdp, lambda payload: _append(frames, payload))

    await streamer.start()
    await streamer._ack_and_emit({"data": "abc123", "sessionId": 77})
    await streamer.stop()

    assert ("Page.enable", None) in cdp.sent
    assert any(command == "Page.startScreencast" for command, _params in cdp.sent)
    assert frames == [
        {"mime_type": "image/jpeg", "data_base64": "abc123", "frame_seq": 1}
    ]
    assert ("Page.screencastFrameAck", {"sessionId": 77}) in cdp.sent


@pytest.mark.asyncio
async def test_cdp_screencast_requests_fallback_after_exhausted_degrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cdp = FakeCDPSession()
    streamer = CDPScreencastStreamer(cdp, lambda payload: _append([], payload))
    streamer._running = True
    streamer._preset_index = len(streamer.PRESETS) - 1

    async def single_tick_sleep(_seconds: float) -> None:
        streamer._running = False

    monkeypatch.setattr("app.browser.screencast.asyncio.sleep", single_tick_sleep)
    monkeypatch.setattr(streamer.monitor, "no_frames_for", lambda: 3.1)
    monkeypatch.setattr(streamer.monitor, "restart_count_within", lambda _seconds: 0)
    monkeypatch.setattr(streamer.monitor, "effective_fps", lambda _seconds=10.0: 10.0)

    task = asyncio.create_task(streamer._monitor_health())
    await task

    assert streamer.fallback_requested.is_set()


@pytest.mark.asyncio
async def test_screenshot_poll_streamer_base64_encodes_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage()
    frames: list[dict] = []
    streamer = ScreenshotPollStreamer(
        page, lambda payload: _append(frames, payload), interval_seconds=0.01
    )

    async def stop_after_first_sleep(_seconds: float) -> None:
        streamer._running = False

    monkeypatch.setattr("app.browser.screencast.asyncio.sleep", stop_after_first_sleep)

    await streamer.start()
    while streamer._task and not streamer._task.done():
        await REAL_SLEEP(0)

    assert page.calls == 1
    assert frames[0]["frame_seq"] == 1
    assert base64.b64decode(frames[0]["data_base64"]).startswith(b"jpeg:80:")


async def _append(collection: list[dict], payload: dict) -> None:
    collection.append(payload)
