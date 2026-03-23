from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal


StreamProfileName = Literal["demo_local"]
ScreenshotFormat = Literal["jpeg", "png"]


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return value if value > 0 else default


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return value if value > 0 else default


@dataclass(frozen=True)
class ScreencastPreset:
    format: ScreenshotFormat
    every_nth_frame: int = 1
    quality: int | None = None


@dataclass(frozen=True)
class StreamProfileConfig:
    name: StreamProfileName | None
    device_scale_factor: float | None
    webrtc_frame_queue_size: int
    webrtc_target_fps: float
    webrtc_video_width: int | None
    webrtc_video_height: int | None
    bitrate_as_kbps: int
    min_bitrate_kbps: int
    max_bitrate_kbps: int
    preserve_source_dimensions: bool
    cdp_emit_queue_size: int
    cdp_min_fps: float
    cdp_presets: tuple[ScreencastPreset, ...]
    allow_quality_degrade: bool
    screenshot_interval_seconds: float
    screenshot_format: ScreenshotFormat
    screenshot_quality: int | None


def default_stream_profile() -> StreamProfileConfig:
    return StreamProfileConfig(
        name=None,
        device_scale_factor=None,
        webrtc_frame_queue_size=_env_int("LUMON_WEBRTC_FRAME_QUEUE_SIZE", 5),
        webrtc_target_fps=_env_float("LUMON_WEBRTC_TARGET_FPS", 30.0),
        webrtc_video_width=_env_int("LUMON_WEBRTC_VIDEO_WIDTH", 1920),
        webrtc_video_height=_env_int("LUMON_WEBRTC_VIDEO_HEIGHT", 1080),
        bitrate_as_kbps=4000,
        min_bitrate_kbps=2000,
        max_bitrate_kbps=6000,
        preserve_source_dimensions=False,
        cdp_emit_queue_size=_env_int("LUMON_CDP_EMIT_QUEUE_SIZE", 5),
        cdp_min_fps=_env_float("LUMON_CDP_MIN_FPS", 12.0),
        cdp_presets=(
            ScreencastPreset(format="jpeg", quality=80, every_nth_frame=1),
            ScreencastPreset(format="jpeg", quality=70, every_nth_frame=1),
            ScreencastPreset(format="jpeg", quality=60, every_nth_frame=1),
        ),
        allow_quality_degrade=True,
        screenshot_interval_seconds=_env_float("LUMON_POLL_INTERVAL_SECONDS", 0.1),
        screenshot_format="jpeg",
        screenshot_quality=100,
    )


def demo_local_stream_profile() -> StreamProfileConfig:
    return StreamProfileConfig(
        name="demo_local",
        device_scale_factor=2.0,
        webrtc_frame_queue_size=1,
        webrtc_target_fps=10.0,
        webrtc_video_width=1280,
        webrtc_video_height=800,
        bitrate_as_kbps=12000,
        min_bitrate_kbps=6000,
        max_bitrate_kbps=16000,
        preserve_source_dimensions=True,
        cdp_emit_queue_size=1,
        cdp_min_fps=5.0,
        cdp_presets=(ScreencastPreset(format="png", every_nth_frame=1),),
        allow_quality_degrade=False,
        screenshot_interval_seconds=0.1,
        screenshot_format="png",
        screenshot_quality=None,
    )


def resolve_stream_profile(
    name: StreamProfileName | str | None,
) -> StreamProfileConfig:
    if name == "demo_local":
        return demo_local_stream_profile()
    return default_stream_profile()
