from __future__ import annotations

import asyncio
import base64
import io
import os
from typing import Any, Callable

import av
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCIceCandidate,
    RTCConfiguration,
    RTCIceServer,
    VideoStreamTrack,
)
from aiortc.sdp import candidate_from_sdp


DEFAULT_WEBRTC_FRAME_QUEUE_SIZE = int(os.getenv("LUMON_WEBRTC_FRAME_QUEUE_SIZE", "5"))
DEFAULT_WEBRTC_TARGET_FPS = float(os.getenv("LUMON_WEBRTC_TARGET_FPS", "45"))
DEFAULT_WEBRTC_VIDEO_WIDTH = int(os.getenv("LUMON_WEBRTC_VIDEO_WIDTH", "1920"))
DEFAULT_WEBRTC_VIDEO_HEIGHT = int(os.getenv("LUMON_WEBRTC_VIDEO_HEIGHT", "1080"))


class FrameQueueVideoTrack(VideoStreamTrack):
    def __init__(
        self,
        *,
        width: int = DEFAULT_WEBRTC_VIDEO_WIDTH,
        height: int = DEFAULT_WEBRTC_VIDEO_HEIGHT,
    ) -> None:
        super().__init__()
        self._queue: asyncio.Queue[av.VideoFrame] = asyncio.Queue(
            maxsize=DEFAULT_WEBRTC_FRAME_QUEUE_SIZE
        )
        self._last_frame: av.VideoFrame | None = None
        self._target_fps = DEFAULT_WEBRTC_TARGET_FPS
        self._frame_interval = 1.0 / self._target_fps if self._target_fps > 0 else 0.0
        self._next_emit_at = 0.0
        self._width = width
        self._height = height

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def push_frame(self, mime_type: str, data_base64: str) -> None:
        try:
            data = base64.b64decode(data_base64)
            frame = _decode_image_frame(data, mime_type)
            frame = self._resize_frame(frame)
        except Exception:
            return
        self._enqueue_frame(frame)

    def push_frame_bytes(self, mime_type: str, data: bytes) -> None:
        try:
            frame = _decode_image_frame(data, mime_type)
            frame = self._resize_frame(frame)
        except Exception:
            return
        self._enqueue_frame(frame)

    def _resize_frame(self, frame: av.VideoFrame) -> av.VideoFrame:
        if frame.width != self._width or frame.height != self._height:
            try:
                resized = frame.reformat(width=self._width, height=self._height)
                if resized:
                    return resized
            except Exception:
                pass
        return frame

    def _enqueue_frame(self, frame: av.VideoFrame) -> None:
        self._last_frame = frame
        if self._queue.full():
            try:
                _ = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            pass

    async def recv(self) -> av.VideoFrame:
        now = asyncio.get_running_loop().time()
        if self._frame_interval > 0 and self._next_emit_at > now:
            await asyncio.sleep(self._next_emit_at - now)
        if self._frame_interval > 0:
            self._next_emit_at = max(
                self._next_emit_at + self._frame_interval,
                asyncio.get_running_loop().time(),
            )

        frame = None
        try:
            frame = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            frame = self._last_frame

        if frame is None:
            await asyncio.sleep(0.01)
            return await self.recv()

        pts, time_base = await self.next_timestamp()
        frame.pts = pts
        frame.time_base = time_base
        return frame


def parse_ice_servers() -> list[RTCIceServer]:
    value = os.getenv("LUMON_WEBRTC_ICE_SERVERS")
    if value is None:
        return [
            RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
        ]
    normalized = value.strip().lower()
    if normalized in {"", "none", "off", "false"}:
        return []
    servers = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if item.lower().startswith(("turn:", "turns:")):
            parts = item.split("@")
            if len(parts) == 2:
                urls = [parts[0]]
                rest = parts[1].rsplit("/", 1)
                if len(rest) == 2:
                    servers.append(
                        RTCIceServer(urls=urls, username=rest[0], credential=rest[1])
                    )
                else:
                    servers.append(
                        RTCIceServer(
                            urls=urls,
                            credential=item.split("@")[-1] if "@" in item else "",
                        )
                    )
            else:
                servers.append(RTCIceServer(urls=[item]))
        else:
            servers.append(RTCIceServer(urls=[item]))
    return servers


def _parse_fps(value: str | None, *, default: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _decode_image_frame(data: bytes, mime_type: str) -> av.VideoFrame:
    format_name = "mjpeg" if mime_type == "image/jpeg" else "png"
    container = av.open(io.BytesIO(data), format=format_name)
    for frame in container.decode(video=0):
        return frame
    raise RuntimeError("No frame decoded")


class WebRTCSession:
    def __init__(
        self,
        *,
        session_id: str,
        ice_servers: list[RTCIceServer],
        on_ice_candidate: Callable[[dict[str, Any]], None],
        on_ready: Callable[[], None],
    ) -> None:
        self.session_id = session_id
        self._track = FrameQueueVideoTrack()
        self._peer = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
        self._peer.addTrack(self._track)
        self._on_ice_candidate = on_ice_candidate
        self._on_ready = on_ready
        self._peer.on("icecandidate", self._handle_ice_candidate)
        self._peer.on("connectionstatechange", self._handle_connection_state_change)

    def push_frame(self, mime_type: str, data_base64: str) -> None:
        loop = asyncio.get_running_loop()
        loop.create_task(self._decode_and_push(mime_type, data_base64))

    def push_frame_bytes(self, mime_type: str, data: bytes) -> None:
        loop = asyncio.get_running_loop()
        loop.create_task(self._decode_and_push_bytes(mime_type, data))

    async def _decode_and_push(self, mime_type: str, data_base64: str) -> None:
        await asyncio.to_thread(self._track.push_frame, mime_type, data_base64)

    async def _decode_and_push_bytes(self, mime_type: str, data: bytes) -> None:
        await asyncio.to_thread(self._track.push_frame_bytes, mime_type, data)

    async def create_offer(self) -> RTCSessionDescription:
        offer = await self._peer.createOffer()

        # Inject higher bandwidth targets into the SDP
        sdp = offer.sdp
        if "m=video" in sdp:
            lines = sdp.split("\r\n")
            new_lines = []
            for line in lines:
                new_lines.append(line)
                if line.startswith("m=video"):
                    new_lines.append("b=AS:8000")
            sdp = "\r\n".join(new_lines)
            offer = RTCSessionDescription(sdp=sdp, type=offer.type)

        await self._peer.setLocalDescription(offer)
        assert self._peer.localDescription is not None
        return self._peer.localDescription

    async def set_answer(self, sdp: str) -> None:
        try:
            await self._peer.setRemoteDescription(
                RTCSessionDescription(sdp=sdp, type="answer")
            )
        except Exception:
            return

    async def add_ice_candidate(self, payload: dict[str, Any]) -> None:
        candidate = payload.get("candidate")
        if not candidate:
            return
        candidate_str = str(candidate)
        if candidate_str.startswith("candidate:"):
            candidate_str = candidate_str[len("candidate:") :]

        try:
            ice_candidate = candidate_from_sdp(candidate_str.strip())
            ice_candidate.sdpMid = payload.get("sdp_mid")
            sdp_mline_index = payload.get("sdp_mline_index")
            if sdp_mline_index is not None:
                try:
                    sdp_mline_index = int(sdp_mline_index)
                except (TypeError, ValueError):
                    sdp_mline_index = None
            ice_candidate.sdpMLineIndex = sdp_mline_index
            await self._peer.addIceCandidate(ice_candidate)
        except Exception:
            return

    async def close(self) -> None:
        await self._peer.close()

    def _handle_ice_candidate(self, candidate: RTCIceCandidate | None) -> None:
        if candidate is None:
            return
        self._on_ice_candidate(
            {
                "candidate": candidate.candidate,
                "sdp_mid": candidate.sdpMid,
                "sdp_mline_index": candidate.sdpMLineIndex,
            }
        )

    def _handle_connection_state_change(self) -> None:
        if self._peer.connectionState == "connected":
            self._on_ready()
