from __future__ import annotations

import asyncio
import base64
import time
import contextlib
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from app.streaming.stream_profile import (
    ScreencastPreset,
    StreamProfileConfig,
    default_stream_profile,
)


FrameEmitter = Callable[[dict[str, Any]], Awaitable[None]]


class ScreencastMonitor:
    def __init__(self) -> None:
        self.last_frame_at = 0.0
        self.restart_times: deque[float] = deque(maxlen=4)
        self.frame_times: deque[float] = deque(maxlen=120)

    def mark_frame(self) -> None:
        now = time.monotonic()
        self.last_frame_at = now
        self.frame_times.append(now)

    def mark_restart(self) -> None:
        self.restart_times.append(time.monotonic())

    def no_frames_for(self) -> float:
        if self.last_frame_at == 0.0:
            return float("inf")
        return time.monotonic() - self.last_frame_at

    def restart_count_within(self, seconds: float) -> int:
        now = time.monotonic()
        return sum(1 for item in self.restart_times if now - item <= seconds)

    def effective_fps(self, window_seconds: float = 10.0) -> float:
        now = time.monotonic()
        samples = [item for item in self.frame_times if now - item <= window_seconds]
        if len(samples) < 2:
            return 0.0
        duration = samples[-1] - samples[0]
        if duration <= 0:
            return 0.0
        return (len(samples) - 1) / duration


class CDPScreencastStreamer:
    PRESETS = default_stream_profile().cdp_presets

    def __init__(
        self,
        cdp_session: Any,
        emit_frame: FrameEmitter,
        *,
        profile_config: StreamProfileConfig | None = None,
    ) -> None:
        self.cdp_session = cdp_session
        self.emit_frame = emit_frame
        self.profile_config = profile_config or default_stream_profile()
        self.monitor = ScreencastMonitor()
        self._frame_seq = 0
        self._running = False
        self._preset_index = 0
        self._health_task: asyncio.Task[None] | None = None
        self._emit_task: asyncio.Task[None] | None = None
        self._emit_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=self.profile_config.cdp_emit_queue_size
        )
        self._needs_degrade = asyncio.Event()
        self._fallback_requested = asyncio.Event()
        self._frame_emitted_event = asyncio.Event()
        self._min_fps = self.profile_config.cdp_min_fps

    @property
    def presets(self) -> tuple[ScreencastPreset, ...]:
        return self.profile_config.cdp_presets

    @property
    def fallback_requested(self) -> asyncio.Event:
        return self._fallback_requested

    @property
    def frame_emitted_event(self) -> asyncio.Event:
        return self._frame_emitted_event

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.cdp_session.on("Page.screencastFrame", self._on_screencast_frame)
        await self.cdp_session.send("Page.enable")
        await self._start_screencast()
        self._emit_task = asyncio.create_task(self._emit_loop())
        self._health_task = asyncio.create_task(self._monitor_health())

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        with contextlib.suppress(asyncio.QueueFull):
            self._emit_queue.put_nowait(None)
        if self._emit_task:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._emit_task, timeout=1.0)
        if self._emit_task:
            self._emit_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._emit_task
        if self._health_task:
            self._health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_task
        with contextlib.suppress(Exception):
            await self.cdp_session.send("Page.stopScreencast")

    async def _start_screencast(self) -> None:
        self.monitor.mark_restart()
        preset = self.presets[self._preset_index]
        params = {
            "format": preset.format,
            "everyNthFrame": preset.every_nth_frame,
        }
        if preset.quality is not None:
            params["quality"] = preset.quality
        await self.cdp_session.send("Page.startScreencast", params)

    async def _restart_screencast(self) -> None:
        with contextlib.suppress(Exception):
            await self.cdp_session.send("Page.stopScreencast")
        await self._start_screencast()

    def request_degrade(self) -> bool:
        if not self.profile_config.allow_quality_degrade:
            return False
        if self._preset_index + 1 >= len(self.presets):
            return False
        self._preset_index += 1
        self._needs_degrade.set()
        return True

    async def _monitor_health(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(1)
                if self._needs_degrade.is_set():
                    self._needs_degrade.clear()
                    await self._restart_screencast()
                    continue

                if self.monitor.no_frames_for() >= 2.0:
                    degraded = self.request_degrade()
                    if not degraded:
                        self._fallback_requested.set()
                    continue

                if self.monitor.restart_count_within(60) >= 4:
                    self._fallback_requested.set()
                    continue

                if (
                    self.monitor.effective_fps(5) < self._min_fps
                    and self.monitor.no_frames_for() > 0
                ):
                    degraded = self.request_degrade()
                    if not degraded:
                        self._fallback_requested.set()
        except asyncio.CancelledError:
            raise

    def _on_screencast_frame(self, params: dict[str, Any]) -> None:
        asyncio.create_task(self._ack_and_emit(params))

    async def _ack_and_emit(self, params: dict[str, Any]) -> None:
        self.monitor.mark_frame()
        self._frame_seq += 1
        await self.cdp_session.send(
            "Page.screencastFrameAck", {"sessionId": params["sessionId"]}
        )
        payload = {
            "mime_type": f"image/{self.presets[self._preset_index].format}",
            "data_base64": params["data"],
            "frame_seq": self._frame_seq,
        }
        if self._emit_queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                _ = self._emit_queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            self._emit_queue.put_nowait(payload)

    async def _emit_loop(self) -> None:
        while self._running or not self._emit_queue.empty():
            payload = await self._emit_queue.get()
            if payload is None:
                continue
            self._frame_emitted_event.set()
            self._frame_emitted_event = asyncio.Event()
            await self.emit_frame(payload)


class ScreenshotPollStreamer:
    def __init__(
        self,
        page: Any,
        emit_frame: FrameEmitter,
        *,
        profile_config: StreamProfileConfig | None = None,
        interval_seconds: float | None = None,
    ) -> None:
        self.page = page
        self.emit_frame = emit_frame
        self.profile_config = profile_config or default_stream_profile()
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None and interval_seconds > 0
            else self.profile_config.screenshot_interval_seconds
        )
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._frame_seq = 0

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._frame_emitted_event = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    @property
    def frame_emitted_event(self) -> asyncio.Event:
        return self._frame_emitted_event

    async def _run(self) -> None:
        while self._running:
            self._frame_seq += 1
            screenshot_kwargs: dict[str, Any] = {
                "type": self.profile_config.screenshot_format,
            }
            if self.profile_config.screenshot_quality is not None:
                screenshot_kwargs["quality"] = self.profile_config.screenshot_quality
            data = await self.page.screenshot(**screenshot_kwargs)
            if isinstance(data, str):
                encoded = data
            else:
                encoded = base64.b64encode(data).decode("ascii")
            self._frame_emitted_event.set()
            self._frame_emitted_event = asyncio.Event()
            await self.emit_frame(
                {
                    "mime_type": f"image/{self.profile_config.screenshot_format}",
                    "data_base64": encoded,
                    "frame_seq": self._frame_seq,
                }
            )
            await asyncio.sleep(self.interval_seconds)
