from __future__ import annotations

import asyncio
import contextlib
import os
from collections import deque
from collections.abc import Callable
from typing import Any

from app.adapters.registry import create_connector
from app.config import (
    DEFAULT_ADAPTER_ID,
    FRONTEND_RUNTIME_FEATURES,
    PROTOCOL_VERSION,
    RUNTIME_VERSION,
)
from app.optional.langsmith_bridge import (
    OptionalTraceBridgeMapper,
    optional_tracing_enabled,
)
from app.protocol.enums import ErrorCode, SessionState
from app.protocol.models import (
    AttachObserverPayload,
    BridgeOfferPayload,
    BrowserCommandRecord,
    BrowserCommandRequest,
    BrowserCommandResult,
    BrowserContextPayload,
    ErrorPayload,
    LocalObserveOpenCodeRequest,
    TaskResultPayload,
    UiTelemetryPayload,
)
from app.protocol.validation import (
    ProtocolValidationError,
    validate_client_message,
    validate_server_message,
)
from app.session.artifacts import SessionArtifactRecorder
from app.session.opencode_attach import OpenCodeAttachService
from app.session.state_machine import can_transition, interaction_mode_for_state
from app.streaming.webrtc import WebRTCSession, parse_ice_servers
from app.utils.ids import new_id, utc_timestamp
from fastapi import WebSocket, WebSocketException
from fastapi.websockets import WebSocketState
from starlette.status import WS_1008_POLICY_VIOLATION

TERMINAL_STATES = {
    SessionState.IDLE,
    SessionState.STOPPED,
    SessionState.COMPLETED,
    SessionState.FAILED,
}

OPENCODE_WEB_MODES = {"observe_only", "delegate_playwright"}
DEFAULT_DISCONNECT_GRACE_SECONDS = 5.0


def diagnostics_enabled() -> bool:
    value = os.getenv("LUMON_DIAGNOSTICS_ENABLED")
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def drop_frames_when_webrtc_ready() -> bool:
    value = os.getenv("LUMON_DISABLE_FRAME_STREAM_ON_WEBRTC")
    if value is None:
        return True
    return value.strip().lower() in {"1", "true", "yes", "on"}


def normalize_opencode_web_mode(
    *,
    adapter_id: str,
    web_mode: str | None,
    web_bridge: str | None,
    observer_mode: bool,
) -> str | None:
    if adapter_id != "opencode":
        return None
    if web_mode in OPENCODE_WEB_MODES:
        return web_mode
    if web_bridge == "playwright_native":
        return "delegate_playwright"
    if observer_mode:
        return "observe_only"
    return None


def bridge_for_opencode_web_mode(web_mode: str | None) -> str | None:
    if web_mode == "delegate_playwright":
        return "playwright_native"
    return None


class SessionRuntime:
    def __init__(
        self,
        session_id: str | None = None,
        join_token: str | None = None,
        *,
        disconnect_grace_seconds: float = DEFAULT_DISCONNECT_GRACE_SECONDS,
        on_terminal_no_connections: Callable[[str], None] | None = None,
    ) -> None:
        self.session_id = session_id or new_id("sess")
        self.join_token = join_token or new_id("ws")
        self.adapter_id = DEFAULT_ADAPTER_ID
        self.adapter_run_id: str | None = None
        self.run_mode = "live"
        self.observer_mode = False
        self.web_mode: str | None = None
        self.web_bridge: str | None = None
        self.task_text = ""
        self.state = SessionState.IDLE
        self.active_checkpoint_id: str | None = None
        self._connections: set[WebSocket] = set()
        self._has_connected_once = False
        self._lock = asyncio.Lock()
        self._next_optional_event_seq = 1
        self._optional_trace_mapper = OptionalTraceBridgeMapper()
        self._optional_trace_history: deque[str] = deque(maxlen=32)
        self._disconnect_grace_seconds = disconnect_grace_seconds
        self._disconnect_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._on_terminal_no_connections = on_terminal_no_connections
        self._connector = create_connector(self, self.adapter_id)
        self._artifact = SessionArtifactRecorder(
            session_id=self.session_id,
            adapter_id=self.adapter_id,
            adapter_run_id="run_pending",
            task_text=self.task_text,
            observer_mode=self.observer_mode,
            started_at=self.timestamp(),
        )
        self.trace_id = new_id("trace")
        self._active_approval_intervention_id: str | None = None
        self._active_bridge_intervention_id: str | None = None
        self._manual_intervention_id: str | None = None
        self._latest_frame_payload: dict[str, Any] | None = None
        self._latest_frame_seq: int | None = None
        self._latest_frame_generation = 0
        self._latest_command_frame_generation = 0
        self._latest_browser_context_payload: dict[str, Any] | None = None
        self._active_approval_payload: dict[str, Any] | None = None
        self._active_bridge_payload: dict[str, Any] | None = None
        self._recent_browser_command_payloads: deque[dict[str, Any]] = deque(maxlen=40)
        self._webrtc_session: WebRTCSession | None = None
        self._latest_webrtc_offer_payload: dict[str, Any] | None = None
        self._webrtc_ready = False
        self._ui_visible: bool | None = None
        self._resume_intent_seq = 0
        self._resume_intent_consumed_seq = 0
        self._resume_intent_reason: str | None = None
        self._resume_intent_requested_at: str | None = None

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    @property
    def latest_frame_seq(self) -> int | None:
        return self._latest_frame_seq

    @property
    def latest_frame_generation(self) -> int:
        return self._latest_frame_generation

    @property
    def latest_command_frame_generation(self) -> int:
        return self._latest_command_frame_generation

    @property
    def latest_frame_payload(self) -> dict[str, Any] | None:
        return self._latest_frame_payload

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def timestamp(self) -> str:
        return utc_timestamp()

    def _schedule_broadcast(self, message: dict[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self.broadcast(message))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def connect(self, websocket: WebSocket) -> None:
        self._cancel_disconnect_task()
        had_connections = bool(self._connections)
        is_reconnect = self._has_connected_once and not had_connections
        await websocket.accept()
        self._connections.add(websocket)
        if is_reconnect:
            self._artifact.note_reconnect()
        self._has_connected_once = True
        await self.emit_session_state(websocket)
        await self._replay_live_state(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)
        if self._connections:
            return
        await self._close_webrtc()
        if self.is_terminal():
            self._notify_terminal_no_connections()
            return
        self._schedule_disconnect_stop()

    @contextlib.asynccontextmanager
    async def browser_command_activity(self):
        self._cancel_disconnect_task()
        try:
            yield
        finally:
            if self._has_connected_once and not self._connections and not self.is_terminal():
                self._schedule_disconnect_stop()

    async def handle_client_message(self, message: dict[str, Any]) -> None:
        try:
            validated = validate_client_message(message)
        except ProtocolValidationError as exc:
            await self.emit_error(exc.code, exc.message)
            return

        message_type = validated["type"]
        payload = validated["payload"]
        async with self._lock:
            if message_type == "start_task":
                if self.state not in TERMINAL_STATES:
                    await self.emit_error(
                        ErrorCode.INVALID_STATE,
                        "Cannot start a new task from current state",
                        command_type=message_type,
                    )
                    return
                await self._close_webrtc()
                self.task_text = payload["task_text"]
                self.adapter_id = payload["adapter_id"]
                self.run_mode = "demo" if payload.get("demo_mode", False) else "live"
                self.observer_mode = payload.get("observer_mode", False)
                self.web_mode = normalize_opencode_web_mode(
                    adapter_id=self.adapter_id,
                    web_mode=payload.get("web_mode"),
                    web_bridge=payload.get("web_bridge"),
                    observer_mode=payload.get("observer_mode", False),
                )
                self.web_bridge = bridge_for_opencode_web_mode(self.web_mode)
                self.active_checkpoint_id = None
                self._optional_trace_mapper = OptionalTraceBridgeMapper()
                self._optional_trace_history.clear()
                self._latest_frame_payload = None
                self._latest_frame_seq = None
                self._latest_frame_generation = 0
                self._latest_command_frame_generation = 0
                self._latest_browser_context_payload = None
                self._active_approval_payload = None
                self._active_bridge_payload = None
                self._recent_browser_command_payloads.clear()
                self._resume_intent_seq = 0
                self._resume_intent_consumed_seq = 0
                self._resume_intent_reason = None
                self._resume_intent_requested_at = None
                self._artifact = SessionArtifactRecorder(
                    session_id=self.session_id,
                    adapter_id=self.adapter_id,
                    adapter_run_id="run_pending",
                    task_text=self.task_text,
                    observer_mode=self.observer_mode,
                    started_at=self.timestamp(),
                )
                self.trace_id = new_id("trace")
                self._connector = create_connector(self, self.adapter_id)
                await self._connector.start_task(
                    payload["task_text"],
                    demo_mode=payload.get("demo_mode", False),
                    web_mode=self.web_mode,
                    web_bridge=self.web_bridge,
                    auto_delegate=payload.get("auto_delegate", False),
                    observer_mode=payload.get("observer_mode", False),
                    observed_session_id=payload.get("observed_session_id"),
                )
                return

            if message_type == "attach_observer":
                await self._close_webrtc()
                await self.attach_observer(payload)
                return

            if message_type == "ingest_optional_trace":
                await self.ingest_optional_trace(payload)
                return

            if message_type == "ui_ready":
                timestamp = self.timestamp()
                self._artifact.note_ui_ready(timestamp)
                runtime_version = payload.get("runtime_version")
                supports_ui_telemetry = payload.get("supports_ui_telemetry")
                supports_ui_ready_handshake = payload.get("supports_ui_ready_handshake")
                handshake_payload: dict[str, Any] = {
                    "timestamp": timestamp,
                    "session_id": self.session_id,
                    "runtime_version": runtime_version,
                    "expected_runtime_version": RUNTIME_VERSION,
                    "supports_ui_telemetry": supports_ui_telemetry,
                    "supports_ui_ready_handshake": supports_ui_ready_handshake,
                }
                stale_reason: str | None = None
                if (
                    isinstance(runtime_version, str)
                    and runtime_version
                    and runtime_version != RUNTIME_VERSION
                ):
                    stale_reason = (
                        "Lumon frontend build is stale. Run `./lumon restart` "
                        "so the served UI matches the backend runtime."
                    )
                else:
                    handshake_capable = (
                        supports_ui_telemetry is not None
                        or supports_ui_ready_handshake is not None
                        or isinstance(runtime_version, str)
                    )
                    missing_features: list[str] = []
                    if handshake_capable:
                        if (
                            FRONTEND_RUNTIME_FEATURES.get("ui_telemetry") is True
                            and supports_ui_telemetry is not True
                        ):
                            missing_features.append("ui_telemetry")
                        if (
                            FRONTEND_RUNTIME_FEATURES.get("ui_ready_handshake") is True
                            and supports_ui_ready_handshake is not True
                        ):
                            missing_features.append("ui_ready_handshake")
                    if missing_features:
                        handshake_payload["missing_features"] = missing_features
                        stale_reason = (
                            "Lumon frontend build is missing required runtime features. "
                            "Run `./lumon restart` so the served UI matches the backend runtime."
                        )
                self._artifact.append_event(
                    {"type": "ui_handshake", "payload": handshake_payload}
                )
                if stale_reason is not None:
                    await self.emit_error(
                        ErrorCode.INVALID_STATE,
                        stale_reason,
                        command_type=message_type,
                    )
                return

            if message_type == "webrtc_request":
                await self._start_webrtc()
                return

            if message_type == "webrtc_answer":
                if self._webrtc_session is None:
                    await self.emit_error(
                        ErrorCode.INVALID_STATE,
                        "No active WebRTC offer",
                        command_type=message_type,
                    )
                    return
                await self._webrtc_session.set_answer(payload["sdp"])
                return

            if message_type == "webrtc_ice":
                if self._webrtc_session is None:
                    return
                try:
                    await self._webrtc_session.add_ice_candidate(payload)
                except Exception:
                    return
                return

            approval_intervention_id = (
                self._active_approval_intervention_id
                if message_type in {"approve", "reject"}
                else None
            )
            bridge_intervention_id = (
                self._active_bridge_intervention_id
                if message_type in {"accept_bridge", "decline_bridge"}
                else None
            )

            handler = getattr(self._connector, message_type, None)
            if handler is None:
                await self.emit_error(
                    ErrorCode.UNKNOWN_COMMAND,
                    f"Unknown message type: {message_type}",
                    command_type=message_type,
                )
                return
            handler_result = await handler(**payload)

            if (
                message_type == "approve"
                and approval_intervention_id is not None
                and self._intervention_action_succeeded(handler_result)
            ):
                self._artifact.resolve_intervention(
                    approval_intervention_id,
                    resolution="approved",
                    resolved_at=self.timestamp(),
                )
                if self._active_approval_intervention_id == approval_intervention_id:
                    self._active_approval_intervention_id = None
                    self._active_approval_payload = None
            elif (
                message_type == "reject"
                and approval_intervention_id is not None
                and self._intervention_action_succeeded(handler_result)
            ):
                self._artifact.resolve_intervention(
                    approval_intervention_id,
                    resolution="denied",
                    resolved_at=self.timestamp(),
                )
                if self._active_approval_intervention_id == approval_intervention_id:
                    self._active_approval_intervention_id = None
                    self._active_approval_payload = None
            elif (
                message_type == "accept_bridge"
                and bridge_intervention_id is not None
                and self._intervention_action_succeeded(handler_result)
            ):
                self._artifact.resolve_intervention(
                    bridge_intervention_id,
                    resolution="approved",
                    resolved_at=self.timestamp(),
                )
                if self._active_bridge_intervention_id == bridge_intervention_id:
                    self._active_bridge_intervention_id = None
                    self._active_bridge_payload = None
            elif (
                message_type == "decline_bridge"
                and bridge_intervention_id is not None
                and self._intervention_action_succeeded(handler_result)
            ):
                self._artifact.resolve_intervention(
                    bridge_intervention_id,
                    resolution="dismissed",
                    resolved_at=self.timestamp(),
                )
                if self._active_bridge_intervention_id == bridge_intervention_id:
                    self._active_bridge_intervention_id = None
                    self._active_bridge_payload = None

    @staticmethod
    def _intervention_action_succeeded(result: Any) -> bool:
        if isinstance(result, bool):
            return result
        if isinstance(result, dict):
            status = str(result.get("status") or "").strip().lower()
            if status:
                return status not in {"failed", "error"}
            return True
        if result is None:
            return True
        return bool(result)

    async def broadcast(self, message: dict[str, Any]) -> None:
        validated = validate_server_message(message)
        stale: list[WebSocket] = []
        active: list[WebSocket] = []
        for ws in list(self._connections):
            if ws.application_state == WebSocketState.CONNECTED:
                active.append(ws)
            else:
                stale.append(ws)

        async def _send(ws: WebSocket) -> None:
            try:
                await ws.send_json(validated)
            except Exception:
                stale.append(ws)

        await asyncio.gather(*[_send(ws) for ws in active], return_exceptions=True)
        for ws in stale:
            self._connections.discard(ws)

    async def emit_session_state(self, websocket: WebSocket | None = None) -> None:
        if self.adapter_run_id is not None:
            self._artifact.update_session_identity(
                adapter_id=self.adapter_id,
                adapter_run_id=self.adapter_run_id,
                task_text=self.task_text,
                observer_mode=self.observer_mode,
            )
        payload = {
            "session_id": self.session_id,
            "adapter_id": self.adapter_id,
            "adapter_run_id": self.adapter_run_id or "run_pending",
            "run_mode": self.run_mode,
            "observer_mode": self.observer_mode,
            "web_mode": self.web_mode,
            "web_bridge": self.web_bridge,
            "state": self.state.value,
            "interaction_mode": interaction_mode_for_state(self.state).value,
            "takeover_mode": getattr(self._connector, "takeover_mode", None),
            "takeover_url": getattr(self._connector, "takeover_url", None),
            "active_checkpoint_id": self.active_checkpoint_id,
            "task_text": self.task_text,
            "viewport": {"width": 1280, "height": 800},
            "capabilities": self._connector.capabilities,
        }
        message = {"type": "session_state", "payload": payload}
        if websocket is not None:
            await websocket.send_json(validate_server_message(message))
            return
        await self.broadcast(message)

    async def emit_frame(self, payload: dict[str, Any]) -> None:
        payload_copy = dict(payload)
        skip_webrtc = bool(payload_copy.pop("__skip_webrtc", False))
        command_snapshot = bool(payload_copy.pop("__command_snapshot", False))
        force_ui_broadcast = bool(payload_copy.pop("__force_ui_broadcast", False))
        if self._webrtc_session is not None:
            mime_type = str(payload_copy.get("mime_type") or "")
            data_base64 = str(payload_copy.get("data_base64") or "")
            if not skip_webrtc and mime_type and data_base64:
                self._webrtc_session.push_frame(mime_type, data_base64)
        if "mime_type" in payload_copy and "data_base64" in payload_copy:
            self._artifact.record_frame(
                str(payload_copy["mime_type"]), str(payload_copy["data_base64"])
            )
            frame_timestamp = payload_copy.get("timestamp")
            if not isinstance(frame_timestamp, str) or not frame_timestamp:
                frame_timestamp = self.timestamp()
            self._artifact.note_first_frame(frame_timestamp)
        self._latest_frame_generation += 1
        if command_snapshot:
            self._latest_command_frame_generation += 1
        frame_seq = payload_copy.get("frame_seq")
        if isinstance(frame_seq, int):
            self._latest_frame_seq = frame_seq
        elif self._latest_frame_seq is None:
            self._latest_frame_seq = 1
        else:
            self._latest_frame_seq += 1
        self._latest_frame_payload = dict(payload_copy)
        takeover_mode = getattr(self._connector, "takeover_mode", None)
        if (
            drop_frames_when_webrtc_ready()
            and self._webrtc_ready
            and not skip_webrtc
            and takeover_mode != "direct"
        ):
            return
        should_throttle_hidden = (
            takeover_mode == "direct"
            and self._ui_visible is False
            and not force_ui_broadcast
            and not command_snapshot
        )
        if should_throttle_hidden:
            frame_seq = payload_copy.get("frame_seq")
            if isinstance(frame_seq, int) and frame_seq % 4 != 0:
                return
        await self.broadcast({"type": "frame", "payload": payload_copy})

    def push_webrtc_frame_bytes(self, mime_type: str, data: bytes) -> None:
        if self._webrtc_session is None:
            return
        self._webrtc_session.push_frame_bytes(mime_type, data)

    async def emit_agent_event(self, payload: dict[str, Any]) -> None:
        payload_event_seq = payload.get("event_seq")
        if (
            isinstance(payload_event_seq, int)
            and payload_event_seq >= self._next_optional_event_seq
        ):
            self._next_optional_event_seq = payload_event_seq + 1
        self._artifact.append_event({"type": "agent_event", "payload": payload})
        await self.broadcast({"type": "agent_event", "payload": payload})

    async def emit_background_worker_update(self, payload: dict[str, Any]) -> None:
        self._artifact.append_event(
            {"type": "background_worker_update", "payload": payload}
        )
        await self.broadcast({"type": "background_worker_update", "payload": payload})

    def emit_routing_decision(self, payload: dict[str, Any]) -> None:
        enriched = {
            "timestamp": payload.get("timestamp") or self.timestamp(),
            "session_id": payload.get("session_id") or self.session_id,
            "adapter_id": payload.get("adapter_id") or self.adapter_id,
            "adapter_run_id": payload.get("adapter_run_id")
            or self.adapter_run_id
            or "run_pending",
            "trace_id": payload.get("trace_id") or self.trace_id,
            **payload,
        }
        self._artifact.append_event({"type": "routing_decision", "payload": enriched})
        if diagnostics_enabled():
            diagnostic_message = {
                "type": "diagnostic_event",
                "payload": {
                    "timestamp": enriched["timestamp"],
                    "session_id": enriched["session_id"],
                    "adapter_id": enriched["adapter_id"],
                    "adapter_run_id": enriched["adapter_run_id"],
                    "trace_id": enriched["trace_id"],
                    "category": str(enriched.get("category") or "routing"),
                    "event_name": str(
                        enriched.get("reason_code") or "routing_decision"
                    ),
                    "severity": str(enriched.get("severity") or "info"),
                    "summary_text": str(
                        enriched.get("summary_text")
                        or enriched.get("reason_code")
                        or "Routing decision"
                    ),
                    "meta": enriched,
                },
            }
            self._schedule_broadcast(diagnostic_message)

    async def emit_approval_required(self, payload: dict[str, Any]) -> None:
        enriched = dict(payload)
        intervention_id = enriched.get("intervention_id") or new_id("intv")
        source_url = enriched.get("source_url")
        enriched.setdefault("intervention_id", intervention_id)
        enriched.setdefault("source_url", source_url)
        enriched.setdefault("target_summary", enriched.get("summary_text"))
        enriched.setdefault(
            "headline", enriched.get("summary_text") or "Needs your approval"
        )
        enriched.setdefault(
            "reason_text",
            enriched.get("risk_reason") or "Lumon stopped here before a risky action.",
        )
        enriched.setdefault("recommended_action", "approve")
        self._active_approval_intervention_id = str(intervention_id)
        self._active_approval_payload = dict(enriched)
        self._artifact.start_intervention(
            intervention_id=str(intervention_id),
            kind="approval",
            headline=str(enriched["headline"]),
            reason_text=str(enriched["reason_text"]),
            started_at=self.timestamp(),
            source_url=str(source_url) if source_url else None,
            target_summary=str(enriched.get("target_summary") or "") or None,
            recommended_action=str(enriched.get("recommended_action") or ""),
            checkpoint_id=str(enriched["checkpoint_id"]),
            source_event_id=str(enriched["event_id"]),
        )
        self._artifact.append_event({"type": "approval_required", "payload": enriched})
        await self.broadcast({"type": "approval_required", "payload": enriched})

    async def emit_bridge_offer(self, payload: dict[str, Any]) -> None:
        enriched = dict(payload)
        intervention_id = enriched.get("intervention_id") or new_id("intv")
        source_url = enriched.get("source_url")
        enriched.setdefault("intervention_id", intervention_id)
        enriched.setdefault("source_url", source_url)
        enriched.setdefault("target_summary", enriched.get("summary_text"))
        enriched.setdefault("headline", "Live browser view")
        enriched.setdefault(
            "reason_text",
            enriched.get("summary_text")
            or "Lumon can open a visible browser view for this step.",
        )
        enriched.setdefault("recommended_action", "open_live_browser_view")
        validated = BridgeOfferPayload(**enriched).model_dump(mode="json")
        self._active_bridge_intervention_id = str(intervention_id)
        self._active_bridge_payload = dict(validated)
        self._artifact.start_intervention(
            intervention_id=str(intervention_id),
            kind="live_browser_view",
            headline=str(validated["headline"]),
            reason_text=str(validated["reason_text"]),
            started_at=self.timestamp(),
            source_url=str(source_url) if source_url else None,
            target_summary=str(validated.get("target_summary") or "") or None,
            recommended_action=str(validated.get("recommended_action") or ""),
            source_event_id=str(validated["source_event_id"]),
        )
        self._artifact.append_event({"type": "bridge_offer", "payload": validated})
        await self.broadcast({"type": "bridge_offer", "payload": validated})

    async def emit_browser_context_update(self, payload: dict[str, Any]) -> None:
        validated = BrowserContextPayload(**payload).model_dump(mode="json")
        self._latest_browser_context_payload = dict(validated)
        self._artifact.record_browser_context(
            BrowserContextPayload.model_validate(validated), capture_keyframe=True
        )
        self._artifact.append_event(
            {"type": "browser_context_update", "payload": validated}
        )
        self._artifact.note_browser_episode(str(validated["timestamp"]))
        await self.broadcast({"type": "browser_context_update", "payload": validated})

    async def emit_error(
        self,
        code: ErrorCode,
        message: str,
        command_type: str | None = None,
        checkpoint_id: str | None = None,
    ) -> None:
        payload = ErrorPayload(
            code=code,
            message=message,
            session_id=self.session_id,
            command_type=command_type,
            checkpoint_id=checkpoint_id,
        ).model_dump(mode="json")
        await self.broadcast({"type": "error", "payload": payload})

    def _allow_optional_trace_coordinates(self) -> bool:
        connector_supports_frames = bool(
            self._connector.capabilities.get("supports_frames", False)
        )
        delegated_browser_surface = self.web_bridge == "playwright_native"
        return not connector_supports_frames and not delegated_browser_surface

    async def ingest_optional_trace(self, payload: dict[str, Any]) -> None:
        if not optional_tracing_enabled():
            await self.emit_error(
                ErrorCode.INVALID_STATE,
                "Optional tracing integration is disabled",
                command_type="ingest_optional_trace",
            )
            return
        if self.state in TERMINAL_STATES:
            await self.emit_error(
                ErrorCode.INVALID_STATE,
                "Cannot ingest optional traces for a terminal session",
                command_type="ingest_optional_trace",
            )
            return
        try:
            normalized = self._optional_trace_mapper.normalize_trace(
                payload,
                session_id=self.session_id,
                adapter_id=self.adapter_id,
                adapter_run_id=self.adapter_run_id
                or getattr(self._connector, "adapter_run_id", "run_pending"),
                event_seq=self._next_optional_event_seq,
                allow_visual_coordinates=self._allow_optional_trace_coordinates(),
            )
        except Exception as exc:
            await self.emit_error(
                ErrorCode.INVALID_STATE,
                f"Optional trace ingest failed: {exc}",
                command_type="ingest_optional_trace",
            )
            return

        if normalized is None:
            return

        if normalized.kind == "agent_event":
            self._optional_trace_history.append(
                str(normalized.payload.get("event_id", ""))
            )
            await self.emit_agent_event(normalized.payload)
        else:
            self._optional_trace_history.append(
                str(normalized.payload.get("agent_id", ""))
            )
            await self.emit_background_worker_update(normalized.payload)

    async def transition_to(
        self, target: SessionState, checkpoint_id: str | None = None
    ) -> None:
        if self.state == target:
            if checkpoint_id is not None:
                self.active_checkpoint_id = checkpoint_id
            await self.emit_session_state()
            return
        if not can_transition(self.state, target):
            await self.emit_error(
                ErrorCode.INVALID_STATE,
                f"Illegal transition {self.state.value} -> {target.value}",
            )
            return
        self.state = target
        self.active_checkpoint_id = checkpoint_id
        if target == SessionState.TAKEOVER and self._manual_intervention_id is None:
            intervention_id = new_id("intv")
            self._manual_intervention_id = intervention_id
            context = self._artifact.current_browser_context
            self._artifact.start_intervention(
                intervention_id=intervention_id,
                kind="manual_control",
                headline="You're in control",
                reason_text="Lumon handed the browser over to you.",
                started_at=self.timestamp(),
                source_url=context.url if context else None,
                target_summary=None,
                recommended_action="take_over",
            )
        elif (
            target != SessionState.TAKEOVER and self._manual_intervention_id is not None
        ):
            self._artifact.resolve_intervention(
                self._manual_intervention_id,
                resolution="taken_over",
                resolved_at=self.timestamp(),
            )
            self._manual_intervention_id = None
        await self.emit_session_state()
        if self.is_terminal():
            await self._close_webrtc()
        if self.is_terminal() and not self._connections:
            self._notify_terminal_no_connections()

    async def complete_task(self, status: str, summary_text: str) -> None:
        target_state = (
            SessionState.COMPLETED
            if status == "completed"
            else SessionState.STOPPED
            if status == "stopped"
            else SessionState.FAILED
        )
        await self.transition_to(target_state, checkpoint_id=None)
        payload = TaskResultPayload(
            session_id=self.session_id,
            status=status,
            summary_text=summary_text,
            task_text=self.task_text,
            adapter_id=self.adapter_id,
            adapter_run_id=self.adapter_run_id or self._connector.adapter_run_id,
        ).model_dump(mode="json")
        self._artifact.append_event({"type": "task_result", "payload": payload})
        for intervention_id in [
            self._active_approval_intervention_id,
            self._active_bridge_intervention_id,
            self._manual_intervention_id,
        ]:
            if intervention_id is not None:
                self._artifact.resolve_intervention(
                    intervention_id, resolution="expired", resolved_at=self.timestamp()
                )
        self._active_approval_intervention_id = None
        self._active_bridge_intervention_id = None
        self._manual_intervention_id = None
        self._active_approval_payload = None
        self._active_bridge_payload = None
        self._resume_intent_consumed_seq = self._resume_intent_seq
        await self._close_webrtc()
        self._artifact.finalize(
            status=status, completed_at=self.timestamp(), summary_text=summary_text
        )
        await self.broadcast({"type": "task_result", "payload": payload})

    def clear_active_interventions(self, *, resolution: str = "expired") -> None:
        now = self.timestamp()
        for intervention_id in [
            self._active_approval_intervention_id,
            self._active_bridge_intervention_id,
        ]:
            if intervention_id is not None:
                self._artifact.resolve_intervention(
                    intervention_id, resolution=resolution, resolved_at=now
                )
        self._active_approval_intervention_id = None
        self._active_bridge_intervention_id = None
        self._active_approval_payload = None
        self._active_bridge_payload = None

    def _schedule_disconnect_stop(self) -> None:
        if self._disconnect_task is not None and not self._disconnect_task.done():
            return
        self._disconnect_task = asyncio.create_task(self._disconnect_after_grace())

    def _cancel_disconnect_task(self) -> None:
        if self._disconnect_task is None:
            return
        current_task = asyncio.current_task()
        if (
            not self._disconnect_task.done()
            and self._disconnect_task is not current_task
        ):
            self._disconnect_task.cancel()
        self._disconnect_task = None

    async def _disconnect_after_grace(self) -> None:
        try:
            await asyncio.sleep(self._disconnect_grace_seconds)
            async with self._lock:
                if self._connections or self.is_terminal():
                    return
                await self._connector.stop()
                await self.transition_to(SessionState.STOPPED)
        except asyncio.CancelledError:
            return
        finally:
            self._disconnect_task = None

    def _notify_terminal_no_connections(self) -> None:
        self._cancel_disconnect_task()
        if self._on_terminal_no_connections is not None:
            self._on_terminal_no_connections(self.session_id)

    async def attach_observer(
        self,
        payload: AttachObserverPayload | dict[str, Any],
    ) -> None:
        attach_payload = (
            payload if isinstance(payload, dict) else payload.model_dump(mode="json")
        )
        if self.state not in TERMINAL_STATES:
            await self.emit_error(
                ErrorCode.INVALID_STATE,
                "Cannot attach a new observer from current state",
                command_type="attach_observer",
            )
            return
        self.task_text = attach_payload["task_text"]
        self.adapter_id = attach_payload["adapter_id"]
        self.run_mode = "live"
        self.observer_mode = True
        self.web_mode = normalize_opencode_web_mode(
            adapter_id=self.adapter_id,
            web_mode=attach_payload.get("web_mode"),
            web_bridge=attach_payload.get("web_bridge"),
            observer_mode=True,
        )
        self.web_bridge = bridge_for_opencode_web_mode(self.web_mode)
        self.active_checkpoint_id = None
        self._optional_trace_mapper = OptionalTraceBridgeMapper()
        self._optional_trace_history.clear()
        self._latest_frame_payload = None
        self._latest_frame_seq = None
        self._latest_frame_generation = 0
        self._latest_command_frame_generation = 0
        self._latest_browser_context_payload = None
        self._active_approval_payload = None
        self._active_bridge_payload = None
        self._recent_browser_command_payloads.clear()
        self._resume_intent_seq = 0
        self._resume_intent_consumed_seq = 0
        self._resume_intent_reason = None
        self._resume_intent_requested_at = None
        self._artifact = SessionArtifactRecorder(
            session_id=self.session_id,
            adapter_id=self.adapter_id,
            adapter_run_id="run_pending",
            task_text=self.task_text,
            observer_mode=True,
            started_at=self.timestamp(),
        )
        self.trace_id = new_id("trace")
        self._connector = create_connector(self, self.adapter_id)
        await self._connector.start_task(
            attach_payload["task_text"],
            demo_mode=False,
            web_mode=self.web_mode,
            web_bridge=self.web_bridge,
            auto_delegate=attach_payload.get("auto_delegate", False),
            observer_mode=True,
            observed_session_id=attach_payload.get("observed_session_id"),
        )
        self._artifact.note_attached(self.timestamp())

    def note_duplicate_attach_prevented(self) -> None:
        self._artifact.note_duplicate_attach_prevented()

    async def ensure_opencode_browser_delegate(
        self, *, observed_session_id: str, task_text: str | None = None
    ) -> None:
        ensure_delegate = getattr(self._connector, "ensure_browser_delegate", None)
        if ensure_delegate is None:
            raise RuntimeError(
                "Current session does not support delegated browser commands"
            )
        await ensure_delegate(
            observed_session_id=observed_session_id,
            task_text=task_text or self.task_text,
        )

    async def execute_browser_command(
        self, payload: BrowserCommandRequest
    ) -> dict[str, Any]:
        execute = getattr(self._connector, "execute_browser_command", None)
        if execute is None:
            raise RuntimeError("Current session does not support browser commands")
        return await execute(payload)

    async def capture_live_keyframe(self, reason: str) -> str | None:
        return self._artifact.capture_keyframe(reason=reason)

    def record_browser_command(self, record: BrowserCommandRecord) -> None:
        self._artifact.append_command(record)
        payload = record.model_dump(mode="json")
        self._recent_browser_command_payloads.append(payload)
        self._artifact.append_event({"type": "browser_command", "payload": payload})
        self._schedule_broadcast({"type": "browser_command", "payload": payload})

    def record_ui_telemetry(self, payload: UiTelemetryPayload) -> None:
        timestamp = payload.timestamp or self.timestamp()
        if payload.event == "video_quality_sample":
            visibility_state = payload.meta.get("visibility_state")
            hidden_meta = payload.meta.get("hidden")
            if isinstance(hidden_meta, bool):
                self._ui_visible = not hidden_meta
            elif isinstance(visibility_state, str):
                normalized_visibility = visibility_state.strip().lower()
                if normalized_visibility in {"visible", "prerender"}:
                    self._ui_visible = True
                elif normalized_visibility in {"hidden", "unloaded"}:
                    self._ui_visible = False
        self._artifact.record_ui_telemetry(
            event=payload.event,
            timestamp=timestamp,
            meta=payload.meta,
        )
        self._artifact.append_event(
            {
                "type": "ui_telemetry",
                "payload": {
                    **payload.model_dump(mode="json"),
                    "timestamp": timestamp,
                },
            }
        )

    def current_artifact(self) -> dict[str, Any]:
        artifact = self._artifact.current_artifact(
            status=self.state.value
            if self.state.value in {"running", "completed", "failed", "stopped"}
            else "idle",
            summary_text=self.task_text or None,
        )
        return {
            "artifact": artifact.model_dump(mode="json"),
            "events": self._artifact.read_events(),
            "commands": self._artifact.read_commands(),
        }

    def request_resume_intent(self, *, reason: str) -> dict[str, Any]:
        self._resume_intent_seq += 1
        self._resume_intent_reason = reason
        self._resume_intent_requested_at = self.timestamp()
        payload = {
            "session_id": self.session_id,
            "resume_intent_seq": self._resume_intent_seq,
            "reason": reason,
            "requested_at": self._resume_intent_requested_at,
        }
        self._artifact.append_event(
            {"type": "resume_intent_requested", "payload": payload}
        )
        return payload

    def _pending_resume_intent_payload(self, *, after_seq: int = 0) -> dict[str, Any]:
        pending_seq = self._resume_intent_seq
        if pending_seq <= max(after_seq, self._resume_intent_consumed_seq):
            return {
                "session_id": self.session_id,
                "pending": False,
                "resume_intent_seq": pending_seq,
            }
        return {
            "session_id": self.session_id,
            "pending": True,
            "resume_intent_seq": pending_seq,
            "reason": self._resume_intent_reason,
            "requested_at": self._resume_intent_requested_at,
        }

    def peek_resume_intent(self, *, after_seq: int = 0) -> dict[str, Any]:
        return self._pending_resume_intent_payload(after_seq=after_seq)

    def acknowledge_resume_intent(self, *, resume_intent_seq: int) -> dict[str, Any]:
        bounded_target = min(max(0, int(resume_intent_seq)), self._resume_intent_seq)
        if bounded_target > self._resume_intent_consumed_seq:
            self._resume_intent_consumed_seq = bounded_target
            self._artifact.append_event(
                {
                    "type": "resume_intent_acknowledged",
                    "payload": {
                        "session_id": self.session_id,
                        "acknowledged_seq": bounded_target,
                        "timestamp": self.timestamp(),
                    },
                }
            )
        return {
            "session_id": self.session_id,
            "resume_intent_seq": self._resume_intent_seq,
            "consumed_seq": self._resume_intent_consumed_seq,
            "acknowledged": self._resume_intent_consumed_seq >= int(resume_intent_seq),
        }

    def consume_resume_intent(self, *, after_seq: int = 0) -> dict[str, Any]:
        payload = self._pending_resume_intent_payload(after_seq=after_seq)
        if payload.get("pending") is True:
            self.acknowledge_resume_intent(
                resume_intent_seq=int(payload.get("resume_intent_seq") or 0)
            )
        return payload

    async def _replay_live_state(self, websocket: WebSocket) -> None:
        if self._latest_browser_context_payload is not None:
            await websocket.send_json(
                validate_server_message(
                    {
                        "type": "browser_context_update",
                        "payload": self._latest_browser_context_payload,
                    }
                )
            )
        for payload in self._recent_browser_command_payloads:
            await websocket.send_json(
                validate_server_message({"type": "browser_command", "payload": payload})
            )
        if self._latest_frame_payload is not None:
            await websocket.send_json(
                validate_server_message(
                    {"type": "frame", "payload": self._latest_frame_payload}
                )
            )
        if self._active_approval_payload is not None:
            await websocket.send_json(
                validate_server_message(
                    {
                        "type": "approval_required",
                        "payload": self._active_approval_payload,
                    }
                )
            )
        if self._active_bridge_payload is not None:
            await websocket.send_json(
                validate_server_message(
                    {"type": "bridge_offer", "payload": self._active_bridge_payload}
                )
            )
        if self._latest_webrtc_offer_payload is not None:
            await websocket.send_json(
                validate_server_message(
                    {
                        "type": "webrtc_offer",
                        "payload": self._latest_webrtc_offer_payload,
                    }
                )
            )

    async def _start_webrtc(self) -> None:
        if not self._connector.capabilities.get("supports_frames", False):
            await self.emit_error(
                ErrorCode.INVALID_STATE,
                "Current adapter does not support WebRTC",
                command_type="webrtc_request",
            )
            return
        await self._close_webrtc()
        ice_servers = parse_ice_servers()
        self._webrtc_ready = False

        def on_ice_candidate(candidate_payload: dict[str, Any]) -> None:
            self._schedule_broadcast(
                {"type": "webrtc_ice", "payload": candidate_payload}
            )

        def on_ready() -> None:
            self._webrtc_ready = True
            self._schedule_broadcast(
                {"type": "webrtc_ready", "payload": {"ready": True}}
            )

        self._webrtc_session = WebRTCSession(
            session_id=self.session_id,
            ice_servers=ice_servers,
            on_ice_candidate=on_ice_candidate,
            on_ready=on_ready,
        )
        offer = await self._webrtc_session.create_offer()
        offer_payload = {
            "sdp": offer.sdp,
            "type": offer.type,
            "ice_servers": _serialize_ice_servers(ice_servers),
        }
        self._latest_webrtc_offer_payload = dict(offer_payload)
        await self.broadcast({"type": "webrtc_offer", "payload": offer_payload})

    async def _close_webrtc(self) -> None:
        if self._webrtc_session is None:
            return
        session = self._webrtc_session
        self._webrtc_session = None
        self._latest_webrtc_offer_payload = None
        self._webrtc_ready = False
        with contextlib.suppress(Exception):
            await session.close()

    async def shutdown(self) -> None:
        disconnect_task = self._disconnect_task
        self._cancel_disconnect_task()
        if disconnect_task is not None and disconnect_task is not asyncio.current_task():
            await asyncio.gather(disconnect_task, return_exceptions=True)
        await self._close_webrtc()
        await self._connector.stop()
        background_tasks = list(self._background_tasks)
        self._background_tasks.clear()
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)


def _serialize_ice_servers(servers: list) -> list[dict[str, Any]]:
    serialized = []
    for server in servers:
        urls = getattr(server, "urls", None)
        if urls is None:
            continue
        serialized.append({"urls": urls})
    return serialized


class SessionManager:
    def __init__(
        self,
        *,
        allowed_origins: tuple[str, ...],
        disconnect_grace_seconds: float = DEFAULT_DISCONNECT_GRACE_SECONDS,
        bootstrap_session_ttl_seconds: float | None = None,
    ) -> None:
        self._allowed_origins = set(allowed_origins)
        self._sessions: dict[str, SessionRuntime] = {}
        self._socket_sessions: dict[WebSocket, str] = {}
        self._opencode_attach = OpenCodeAttachService()
        self._lock = asyncio.Lock()
        self._disconnect_grace_seconds = disconnect_grace_seconds
        self._bootstrap_session_ttl_seconds = bootstrap_session_ttl_seconds
        self._bootstrap_expiry_tasks: dict[str, asyncio.Task[None]] = {}

    async def shutdown(self) -> None:
        expiry_tasks = list(self._bootstrap_expiry_tasks.values())
        self._bootstrap_expiry_tasks.clear()
        for task in expiry_tasks:
            task.cancel()
        if expiry_tasks:
            await asyncio.gather(*expiry_tasks, return_exceptions=True)

        async with self._lock:
            runtimes = list(self._sessions.values())
            self._sessions.clear()
            self._socket_sessions.clear()

        first_error: Exception | None = None
        for runtime in runtimes:
            try:
                await runtime.shutdown()
            except Exception as exc:
                first_error = first_error or exc
        if first_error is not None:
            raise first_error

    def create_session(self) -> dict[str, str]:
        runtime = self._new_runtime()
        self._sessions[runtime.session_id] = runtime
        self._schedule_bootstrap_expiry(runtime.session_id)
        return {"session_id": runtime.session_id, "ws_token": runtime.join_token}

    def session_exists(self, session_id: str) -> bool:
        return session_id in self._sessions

    def _validate_origin(self, websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        if not origin or origin not in self._allowed_origins:
            raise WebSocketException(
                code=WS_1008_POLICY_VIOLATION, reason="WebSocket origin not allowed"
            )

    def _resolve_runtime(self, websocket: WebSocket) -> SessionRuntime:
        session_id = websocket.query_params.get("session_id")
        token = websocket.query_params.get("token")
        if not session_id or not token:
            raise WebSocketException(
                code=WS_1008_POLICY_VIOLATION, reason="Missing session credentials"
            )
        runtime = self._sessions.get(session_id)
        if runtime is None or token != runtime.join_token:
            raise WebSocketException(
                code=WS_1008_POLICY_VIOLATION, reason="Invalid session credentials"
            )
        return runtime

    def _runtime_for_socket(self, websocket: WebSocket) -> SessionRuntime:
        session_id = self._socket_sessions.get(websocket)
        if session_id is None:
            raise WebSocketException(
                code=WS_1008_POLICY_VIOLATION, reason="Socket is not bound to a session"
            )
        runtime = self._sessions.get(session_id)
        if runtime is None:
            raise WebSocketException(
                code=WS_1008_POLICY_VIOLATION, reason="Session no longer exists"
            )
        return runtime

    async def connect(self, websocket: WebSocket) -> None:
        try:
            self._validate_origin(websocket)
            runtime = self._resolve_runtime(websocket)
        except WebSocketException as exc:
            with contextlib.suppress(Exception):
                await websocket.accept()
            with contextlib.suppress(Exception):
                await websocket.close(
                    code=int(getattr(exc, "code", WS_1008_POLICY_VIOLATION)),
                    reason=str(getattr(exc, "reason", "WebSocket policy violation")),
                )
            return
        self._cancel_bootstrap_expiry(runtime.session_id)
        async with self._lock:
            self._socket_sessions[websocket] = runtime.session_id
        await runtime.connect(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        session_id = self._socket_sessions.pop(websocket, None)
        if session_id is None:
            return
        runtime = self._sessions.get(session_id)
        if runtime is None:
            return
        await runtime.disconnect(websocket)

    async def handle(self, websocket: WebSocket, message: dict[str, Any]) -> None:
        runtime = self._runtime_for_socket(websocket)
        await runtime.handle_client_message(message)

    async def attach_local_opencode_observer(
        self,
        payload: LocalObserveOpenCodeRequest,
        *,
        frontend_origin: str,
    ) -> dict[str, Any]:
        async with self._lock:
            runtime, already_attached = self._opencode_attach.prepare_runtime(
                payload, self._sessions, self._new_runtime
            )
            attach_requested_at = runtime.timestamp()
            if already_attached:
                runtime.note_duplicate_attach_prevented()

        if not already_attached:
            try:
                await self._opencode_attach.attach_runtime(
                    runtime,
                    payload,
                    bridge_for_web_mode=bridge_for_opencode_web_mode,
                )
            except Exception:
                async with self._lock:
                    self._opencode_attach.rollback_prepared_runtime(
                        payload, self._sessions, runtime
                    )
                raise
            runtime._artifact.note_attach_requested(attach_requested_at)
        runtime._artifact.note_attached(runtime.timestamp())

        return self._opencode_attach.build_attach_response(
            runtime=runtime,
            frontend_origin=frontend_origin,
            build_frontend_open_url=self._build_frontend_open_url,
            already_attached=already_attached,
        )

    async def execute_local_opencode_browser_command(
        self,
        payload: BrowserCommandRequest,
        *,
        frontend_origin: str,
    ) -> dict[str, Any]:
        attach_payload = LocalObserveOpenCodeRequest(
            project_directory=payload.project_directory,
            observed_session_id=payload.observed_session_id,
            frontend_origin=frontend_origin,
            web_mode="delegate_playwright",
            auto_delegate=True,
        )
        async with self._lock:
            runtime, already_attached = self._opencode_attach.prepare_runtime(
                attach_payload, self._sessions, self._new_runtime
            )
            attach_requested_at = runtime.timestamp()

        if not already_attached:
            try:
                await self._opencode_attach.attach_runtime(
                    runtime,
                    attach_payload,
                    bridge_for_web_mode=bridge_for_opencode_web_mode,
                )
            except Exception:
                async with self._lock:
                    self._opencode_attach.rollback_prepared_runtime(
                        attach_payload, self._sessions, runtime
                    )
                raise
            runtime._artifact.note_attach_requested(attach_requested_at)
        runtime._artifact.note_attached(runtime.timestamp())
        async with runtime._lock, runtime.browser_command_activity():
            await runtime.ensure_opencode_browser_delegate(
                observed_session_id=payload.observed_session_id,
                task_text=payload.task_text or runtime.task_text,
            )
            result = await runtime.execute_browser_command(payload)
        validated = BrowserCommandResult.model_validate(
            {
                **result,
                "session_id": runtime.session_id,
                "open_url": self._build_frontend_open_url(frontend_origin, runtime),
                "already_attached": already_attached,
            }
        ).model_dump(mode="json")
        return validated

    def _new_runtime(self) -> SessionRuntime:
        return SessionRuntime(
            disconnect_grace_seconds=self._disconnect_grace_seconds,
            on_terminal_no_connections=self._prune_terminal_session,
        )

    def _prune_terminal_session(self, session_id: str) -> None:
        self._cancel_bootstrap_expiry(session_id)
        runtime = self._sessions.pop(session_id, None)
        if runtime is None:
            return
        self._opencode_attach.prune_runtime(runtime)

    def _schedule_bootstrap_expiry(self, session_id: str) -> None:
        ttl_seconds = self._bootstrap_session_ttl_seconds
        if ttl_seconds is None or ttl_seconds <= 0:
            return
        self._cancel_bootstrap_expiry(session_id)
        self._bootstrap_expiry_tasks[session_id] = asyncio.create_task(
            self._expire_bootstrap_session(session_id, ttl_seconds)
        )

    def _cancel_bootstrap_expiry(self, session_id: str) -> None:
        task = self._bootstrap_expiry_tasks.pop(session_id, None)
        if task is None or task.done():
            return
        task.cancel()

    async def _expire_bootstrap_session(
        self, session_id: str, ttl_seconds: float
    ) -> None:
        try:
            await asyncio.sleep(ttl_seconds)
            runtime = self._sessions.get(session_id)
            if runtime is None:
                return
            if runtime.connection_count > 0:
                return
            self._sessions.pop(session_id, None)
            self._opencode_attach.prune_runtime(runtime)
        except asyncio.CancelledError:
            return
        finally:
            self._bootstrap_expiry_tasks.pop(session_id, None)

    def _build_frontend_open_url(
        self, frontend_origin: str, runtime: SessionRuntime
    ) -> str:
        from urllib.parse import urlencode

        query = urlencode(
            {
                "session_id": runtime.session_id,
                "ws_token": runtime.join_token,
                "ws_path": "/ws/session",
                "protocol_version": PROTOCOL_VERSION,
            }
        )
        return f"{frontend_origin}/?{query}"

    def artifact_for_session(self, session_id: str) -> dict[str, Any] | None:
        runtime = self._sessions.get(session_id)
        if runtime is None:
            return None
        return runtime.current_artifact()

    async def resolve_local_checkpoint(
        self, session_id: str, checkpoint_id: str, *, approve: bool
    ) -> dict[str, Any]:
        runtime = self._sessions.get(session_id)
        if runtime is None:
            raise KeyError(session_id)
        await runtime.handle_client_message(
            {
                "type": "approve" if approve else "reject",
                "payload": {"checkpoint_id": checkpoint_id},
            }
        )
        return runtime.current_artifact()

    def record_local_ui_telemetry(
        self, session_id: str, payload: UiTelemetryPayload
    ) -> dict[str, Any]:
        runtime = self._sessions.get(session_id)
        if runtime is None:
            raise KeyError(session_id)
        runtime.record_ui_telemetry(payload)
        return {"ok": True}

    def consume_local_resume_intent(
        self, session_id: str, *, after_seq: int = 0
    ) -> dict[str, Any]:
        return self.read_local_resume_intent(
            session_id, after_seq=after_seq, consume=True
        )

    def read_local_resume_intent(
        self, session_id: str, *, after_seq: int = 0, consume: bool = False
    ) -> dict[str, Any]:
        runtime = self._sessions.get(session_id)
        if runtime is None:
            raise KeyError(session_id)
        if consume:
            return runtime.consume_resume_intent(after_seq=after_seq)
        return runtime.peek_resume_intent(after_seq=after_seq)

    def acknowledge_local_resume_intent(
        self, session_id: str, *, resume_intent_seq: int
    ) -> dict[str, Any]:
        runtime = self._sessions.get(session_id)
        if runtime is None:
            raise KeyError(session_id)
        return runtime.acknowledge_resume_intent(resume_intent_seq=resume_intent_seq)
