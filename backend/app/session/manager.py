from __future__ import annotations

import asyncio
import contextlib
import os
from collections import deque
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket, WebSocketException
from fastapi.websockets import WebSocketState
from starlette.status import WS_1008_POLICY_VIOLATION

from app.adapters.registry import create_connector
from app.config import DEFAULT_ADAPTER_ID, PROTOCOL_VERSION
from app.optional.langsmith_bridge import OptionalTraceBridgeMapper, optional_tracing_enabled
from app.protocol.enums import ErrorCode, SessionState
from app.protocol.models import (
    BrowserCommandRecord,
    BrowserCommandRequest,
    BrowserCommandResult,
    BrowserContextPayload,
    BridgeOfferPayload,
    ErrorPayload,
    LocalObserveOpenCodeRequest,
    TaskResultPayload,
)
from app.session.artifacts import SessionArtifactRecorder, environment_type_for_url
from app.session.opencode_attach import OpenCodeAttachService
from app.protocol.validation import ProtocolValidationError, validate_client_message, validate_server_message
from app.session.state_machine import can_transition, interaction_mode_for_state
from app.streaming.webrtc import WebRTCSession, parse_ice_servers
from app.utils.ids import new_id, utc_timestamp

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
    return value.strip().lower() not in {"0", "false", "no", "off"}


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
        self._lock = asyncio.Lock()
        self._next_optional_event_seq = 1
        self._optional_trace_mapper = OptionalTraceBridgeMapper()
        self._optional_trace_history: deque[str] = deque(maxlen=32)
        self._disconnect_grace_seconds = disconnect_grace_seconds
        self._disconnect_task: asyncio.Task[None] | None = None
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

    @property
    def latest_frame_generation(self) -> int:
        return self._latest_frame_generation

    @property
    def latest_command_frame_generation(self) -> int:
        return self._latest_command_frame_generation

    @property
    def latest_frame_seq(self) -> int | None:
        return self._latest_frame_seq

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def timestamp(self) -> str:
        return utc_timestamp()

    async def connect(self, websocket: WebSocket) -> None:
        self._cancel_disconnect_task()
        had_connections = bool(self._connections)
        await websocket.accept()
        self._connections.add(websocket)
        if self._artifact.metrics.ui_open_requested_at is None:
            self._artifact.note_ui_open_requested(self.timestamp())
        elif not had_connections:
            self._artifact.note_reconnect()
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

    async def handle_client_message(self, message: dict[str, Any]) -> None:
        try:
            validated = validate_client_message(message)
        except ProtocolValidationError as exc:
            await self.emit_error(exc.code, exc.message)
            return

        message_type = validated["type"]
        payload = validated["payload"]
        pending_intervention_resolution: tuple[str, str] | None = None
        handler = None
        async with self._lock:
            if message_type == "start_task":
                if self.state not in TERMINAL_STATES:
                    await self.emit_error(ErrorCode.INVALID_STATE, "Cannot start a new task from current state", command_type=message_type)
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
                self._artifact.note_ui_ready(self.timestamp())
                return

            if message_type == "webrtc_request":
                await self._start_webrtc()
                return

            if message_type == "webrtc_answer":
                if self._webrtc_session is None:
                    await self.emit_error(ErrorCode.INVALID_STATE, "No active WebRTC offer", command_type=message_type)
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

            if message_type == "approve" and self._active_approval_intervention_id is not None:
                pending_intervention_resolution = (self._active_approval_intervention_id, "approved")
            elif message_type == "reject" and self._active_approval_intervention_id is not None:
                pending_intervention_resolution = (self._active_approval_intervention_id, "denied")
            elif message_type == "accept_bridge" and self._active_bridge_intervention_id is not None:
                pending_intervention_resolution = (self._active_bridge_intervention_id, "approved")
            elif message_type == "decline_bridge" and self._active_bridge_intervention_id is not None:
                pending_intervention_resolution = (self._active_bridge_intervention_id, "dismissed")

            handler = getattr(self._connector, message_type, None)
            if handler is None:
                await self.emit_error(ErrorCode.UNKNOWN_COMMAND, f"Unknown message type: {message_type}", command_type=message_type)
                return
        handler_result = await handler(**payload)
        async with self._lock:
            if pending_intervention_resolution is None or handler_result is False:
                return
            if isinstance(handler_result, dict):
                result_status = str(handler_result.get("status") or "")
                result_reason = str(handler_result.get("reason") or "")
                if result_status in {"failed", "unsupported"}:
                    return
                if result_status == "blocked" and result_reason == "awaiting_approval":
                    return
            if handler_result is None:
                return
            intervention_id, resolution = pending_intervention_resolution
            self._artifact.resolve_intervention(intervention_id, resolution=resolution, resolved_at=self.timestamp())
            if message_type in {"approve", "reject"}:
                self._active_approval_intervention_id = None
                self._active_approval_payload = None
            else:
                self._active_bridge_intervention_id = None
                self._active_bridge_payload = None

    async def broadcast(self, message: dict[str, Any]) -> None:
        validated = validate_server_message(message)
        stale: list[WebSocket] = []
        for websocket in self._connections:
            if websocket.application_state != WebSocketState.CONNECTED:
                stale.append(websocket)
                continue
            try:
                await websocket.send_json(validated)
            except RuntimeError:
                stale.append(websocket)
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            self._connections.discard(websocket)

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
        self._latest_frame_generation += 1
        if command_snapshot:
            self._latest_command_frame_generation += 1
        frame_seq = payload_copy.get("frame_seq")
        if isinstance(frame_seq, int):
            self._latest_frame_seq = frame_seq
        if "mime_type" in payload_copy and "data_base64" in payload_copy:
            self._artifact.record_frame(str(payload_copy["mime_type"]), str(payload_copy["data_base64"]))
        self._latest_frame_payload = dict(payload_copy)
        if self._webrtc_session is not None:
            mime_type = str(payload_copy.get("mime_type") or "")
            data_base64 = str(payload_copy.get("data_base64") or "")
            if not skip_webrtc and mime_type and data_base64:
                self._webrtc_session.push_frame(mime_type, data_base64)
        if drop_frames_when_webrtc_ready() and self._webrtc_ready and not skip_webrtc:
            return
        await self.broadcast({"type": "frame", "payload": payload_copy})

    def push_webrtc_frame_bytes(self, mime_type: str, data: bytes) -> None:
        if self._webrtc_session is None:
            return
        self._webrtc_session.push_frame_bytes(mime_type, data)

    async def emit_agent_event(self, payload: dict[str, Any]) -> None:
        payload_event_seq = payload.get("event_seq")
        if isinstance(payload_event_seq, int) and payload_event_seq >= self._next_optional_event_seq:
            self._next_optional_event_seq = payload_event_seq + 1
        self._artifact.append_event({"type": "agent_event", "payload": payload})
        await self.broadcast({"type": "agent_event", "payload": payload})

    async def emit_background_worker_update(self, payload: dict[str, Any]) -> None:
        self._artifact.append_event({"type": "background_worker_update", "payload": payload})
        await self.broadcast({"type": "background_worker_update", "payload": payload})

    def emit_routing_decision(self, payload: dict[str, Any]) -> None:
        enriched = {
            "timestamp": payload.get("timestamp") or self.timestamp(),
            "session_id": payload.get("session_id") or self.session_id,
            "adapter_id": payload.get("adapter_id") or self.adapter_id,
            "adapter_run_id": payload.get("adapter_run_id") or self.adapter_run_id or "run_pending",
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
                    "event_name": str(enriched.get("reason_code") or "routing_decision"),
                    "severity": str(enriched.get("severity") or "info"),
                    "summary_text": str(enriched.get("summary_text") or enriched.get("reason_code") or "Routing decision"),
                    "meta": enriched,
                },
            }
            with contextlib.suppress(RuntimeError):
                loop = asyncio.get_running_loop()
                loop.create_task(self.broadcast(diagnostic_message))

    async def emit_approval_required(self, payload: dict[str, Any]) -> None:
        enriched = dict(payload)
        intervention_id = enriched.get("intervention_id") or new_id("intv")
        source_url = enriched.get("source_url")
        enriched.setdefault("intervention_id", intervention_id)
        enriched.setdefault("source_url", source_url)
        enriched.setdefault("target_summary", enriched.get("summary_text"))
        enriched.setdefault("headline", enriched.get("summary_text") or "Needs your approval")
        enriched.setdefault("reason_text", enriched.get("risk_reason") or "Lumon stopped here before a risky action.")
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
        enriched.setdefault("reason_text", enriched.get("summary_text") or "Lumon can open a visible browser view for this step.")
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
        self._artifact.record_browser_context(BrowserContextPayload.model_validate(validated), capture_keyframe=True)
        self._artifact.append_event({"type": "browser_context_update", "payload": validated})
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
        connector_supports_frames = bool(self._connector.capabilities.get("supports_frames", False))
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
                adapter_run_id=self.adapter_run_id or getattr(self._connector, "adapter_run_id", "run_pending"),
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
            self._optional_trace_history.append(str(normalized.payload.get("event_id", "")))
            await self.emit_agent_event(normalized.payload)
        else:
            self._optional_trace_history.append(str(normalized.payload.get("agent_id", "")))
            await self.emit_background_worker_update(normalized.payload)

    async def transition_to(self, target: SessionState, checkpoint_id: str | None = None) -> None:
        if self.state == target:
            if checkpoint_id is not None:
                self.active_checkpoint_id = checkpoint_id
            await self.emit_session_state()
            return
        if not can_transition(self.state, target):
            await self.emit_error(ErrorCode.INVALID_STATE, f"Illegal transition {self.state.value} -> {target.value}")
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
                headline="You’re in control",
                reason_text="Lumon handed the browser over to you.",
                started_at=self.timestamp(),
                source_url=context.url if context else None,
                target_summary=None,
                recommended_action="take_over",
            )
        elif target != SessionState.TAKEOVER and self._manual_intervention_id is not None:
            self._artifact.resolve_intervention(self._manual_intervention_id, resolution="taken_over", resolved_at=self.timestamp())
            self._manual_intervention_id = None
        await self.emit_session_state()
        if self.is_terminal():
            await self._close_webrtc()
        if self.is_terminal() and not self._connections:
            self._notify_terminal_no_connections()

    async def complete_task(self, status: str, summary_text: str) -> None:
        target_state = SessionState.COMPLETED if status == "completed" else SessionState.STOPPED if status == "stopped" else SessionState.FAILED
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
        for intervention_id in [self._active_approval_intervention_id, self._active_bridge_intervention_id, self._manual_intervention_id]:
            if intervention_id is not None:
                self._artifact.resolve_intervention(intervention_id, resolution="expired", resolved_at=self.timestamp())
        self._active_approval_intervention_id = None
        self._active_bridge_intervention_id = None
        self._manual_intervention_id = None
        self._active_approval_payload = None
        self._active_bridge_payload = None
        await self._close_webrtc()
        self._artifact.finalize(status=status, completed_at=self.timestamp(), summary_text=summary_text)
        await self.broadcast({"type": "task_result", "payload": payload})

    def clear_active_interventions(self, *, resolution: str = "expired") -> None:
        now = self.timestamp()
        for intervention_id in [self._active_approval_intervention_id, self._active_bridge_intervention_id]:
            if intervention_id is not None:
                self._artifact.resolve_intervention(intervention_id, resolution=resolution, resolved_at=now)
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
        if not self._disconnect_task.done() and self._disconnect_task is not current_task:
            self._disconnect_task.cancel()
        self._disconnect_task = None

    async def _disconnect_after_grace(self) -> None:
        try:
            await asyncio.sleep(self._disconnect_grace_seconds)
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
        attach_payload = payload if isinstance(payload, dict) else payload.model_dump(mode="json")
        if self.state not in TERMINAL_STATES:
            await self.emit_error(ErrorCode.INVALID_STATE, "Cannot attach a new observer from current state", command_type="attach_observer")
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
        self._latest_browser_context_payload = None
        self._active_approval_payload = None
        self._active_bridge_payload = None
        self._recent_browser_command_payloads.clear()
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

    async def ensure_opencode_browser_delegate(self, *, observed_session_id: str, task_text: str | None = None) -> None:
        ensure_delegate = getattr(self._connector, "ensure_browser_delegate", None)
        if ensure_delegate is None:
            raise RuntimeError("Current session does not support delegated browser commands")
        await ensure_delegate(observed_session_id=observed_session_id, task_text=task_text or self.task_text)

    async def execute_browser_command(self, payload: BrowserCommandRequest) -> dict[str, Any]:
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
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            loop.create_task(self.broadcast({"type": "browser_command", "payload": payload}))

    def current_artifact(self) -> dict[str, Any]:
        artifact = self._artifact.current_artifact(
            status=self.state.value if self.state.value in {"running", "completed", "failed", "stopped"} else "idle",
            summary_text=self.task_text or None,
        )
        return {
            "artifact": artifact.model_dump(mode="json"),
            "events": self._artifact.read_events(),
            "commands": self._artifact.read_commands(),
        }

    async def _replay_live_state(self, websocket: WebSocket) -> None:
        if self._latest_browser_context_payload is not None:
            await websocket.send_json(
                validate_server_message({"type": "browser_context_update", "payload": self._latest_browser_context_payload})
            )
        for payload in self._recent_browser_command_payloads:
            await websocket.send_json(validate_server_message({"type": "browser_command", "payload": payload}))
        if self._latest_frame_payload is not None:
            await websocket.send_json(validate_server_message({"type": "frame", "payload": self._latest_frame_payload}))
        if self._active_approval_payload is not None:
            await websocket.send_json(validate_server_message({"type": "approval_required", "payload": self._active_approval_payload}))
        if self._active_bridge_payload is not None:
            await websocket.send_json(validate_server_message({"type": "bridge_offer", "payload": self._active_bridge_payload}))
        if self._latest_webrtc_offer_payload is not None:
            await websocket.send_json(validate_server_message({"type": "webrtc_offer", "payload": self._latest_webrtc_offer_payload}))

    async def _start_webrtc(self) -> None:
        if not self._connector.capabilities.get("supports_frames", False):
            await self.emit_error(ErrorCode.INVALID_STATE, "Current adapter does not support WebRTC", command_type="webrtc_request")
            return
        await self._close_webrtc()
        ice_servers = parse_ice_servers()
        self._webrtc_ready = False

        def on_ice_candidate(candidate_payload: dict[str, Any]) -> None:
            with contextlib.suppress(RuntimeError):
                loop = asyncio.get_running_loop()
                loop.create_task(self.broadcast({"type": "webrtc_ice", "payload": candidate_payload}))

        def on_ready() -> None:
            self._webrtc_ready = True
            with contextlib.suppress(RuntimeError):
                loop = asyncio.get_running_loop()
                loop.create_task(self.broadcast({"type": "webrtc_ready", "payload": {"ready": True}}))

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


def _serialize_ice_servers(servers: list) -> list[dict[str, Any]]:
    serialized = []
    for server in servers:
        urls = getattr(server, "urls", None)
        if urls is None:
            continue
        serialized.append({"urls": urls})
    return serialized


class SessionManager:
    def __init__(self, *, allowed_origins: tuple[str, ...], disconnect_grace_seconds: float = DEFAULT_DISCONNECT_GRACE_SECONDS) -> None:
        self._allowed_origins = set(allowed_origins)
        self._sessions: dict[str, SessionRuntime] = {}
        self._socket_sessions: dict[WebSocket, str] = {}
        self._opencode_attach = OpenCodeAttachService()
        self._lock = asyncio.Lock()
        self._disconnect_grace_seconds = disconnect_grace_seconds

    def create_session(self) -> dict[str, str]:
        runtime = self._new_runtime()
        self._sessions[runtime.session_id] = runtime
        return {"session_id": runtime.session_id, "ws_token": runtime.join_token}

    def session_exists(self, session_id: str) -> bool:
        return session_id in self._sessions

    def _validate_origin(self, websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        if not origin or origin not in self._allowed_origins:
            raise WebSocketException(code=WS_1008_POLICY_VIOLATION, reason="WebSocket origin not allowed")

    def _resolve_runtime(self, websocket: WebSocket) -> SessionRuntime:
        session_id = websocket.query_params.get("session_id")
        token = websocket.query_params.get("token")
        if not session_id or not token:
            raise WebSocketException(code=WS_1008_POLICY_VIOLATION, reason="Missing session credentials")
        runtime = self._sessions.get(session_id)
        if runtime is None or token != runtime.join_token:
            raise WebSocketException(code=WS_1008_POLICY_VIOLATION, reason="Invalid session credentials")
        return runtime

    def _runtime_for_socket(self, websocket: WebSocket) -> SessionRuntime:
        session_id = self._socket_sessions.get(websocket)
        if session_id is None:
            raise WebSocketException(code=WS_1008_POLICY_VIOLATION, reason="Socket is not bound to a session")
        runtime = self._sessions.get(session_id)
        if runtime is None:
            raise WebSocketException(code=WS_1008_POLICY_VIOLATION, reason="Session no longer exists")
        return runtime

    async def connect(self, websocket: WebSocket) -> None:
        self._validate_origin(websocket)
        runtime = self._resolve_runtime(websocket)
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
            runtime, already_attached = self._opencode_attach.prepare_runtime(payload, self._sessions, self._new_runtime)
            runtime._artifact.note_attach_requested(runtime.timestamp())
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
                    self._opencode_attach.rollback_prepared_runtime(payload, self._sessions, runtime)
                raise
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
            runtime, already_attached = self._opencode_attach.prepare_runtime(attach_payload, self._sessions, self._new_runtime)
            runtime._artifact.note_attach_requested(runtime.timestamp())

        if not already_attached:
            try:
                await self._opencode_attach.attach_runtime(
                    runtime,
                    attach_payload,
                    bridge_for_web_mode=bridge_for_opencode_web_mode,
                )
            except Exception:
                async with self._lock:
                    self._opencode_attach.rollback_prepared_runtime(attach_payload, self._sessions, runtime)
                raise
        runtime._artifact.note_attached(runtime.timestamp())
        should_record_result = False
        try:
            await runtime.ensure_opencode_browser_delegate(
                observed_session_id=payload.observed_session_id,
                task_text=payload.task_text or runtime.task_text,
            )
            result = await runtime.execute_browser_command(payload)
        except RuntimeError as exc:
            result = BrowserCommandResult(
                command_id=payload.command_id,
                command=payload.command,
                status="failed",
                summary_text="Lumon could not prepare the live browser delegate.",
                reason=str(exc) or "delegate_unavailable",
                session_id=runtime.session_id,
                source_url=None,
                domain=None,
                page_version=None,
                evidence=None,
                actionable_elements=[],
                intervention_id=None,
                checkpoint_id=None,
                meta={"error": str(exc)},
            ).model_dump(mode="json")
            should_record_result = True
        validated = BrowserCommandResult.model_validate(
            {
                **result,
                "session_id": runtime.session_id,
                "open_url": self._build_frontend_open_url(frontend_origin, runtime),
                "already_attached": already_attached,
            }
        ).model_dump(mode="json")
        if should_record_result:
            runtime.record_browser_command(
                BrowserCommandRecord(
                    command_id=validated["command_id"],
                    command=validated["command"],
                    status=validated["status"],
                    summary_text=validated["summary_text"],
                    timestamp=runtime.timestamp(),
                    reason=validated.get("reason"),
                    source_url=validated.get("source_url"),
                    domain=validated.get("domain"),
                    page_version=validated.get("page_version"),
                    evidence=validated.get("evidence"),
                    actionable_elements=validated.get("actionable_elements") or [],
                    intervention_id=validated.get("intervention_id"),
                    checkpoint_id=validated.get("checkpoint_id"),
                    meta=validated.get("meta") or {},
                )
            )
        return validated

    async def resolve_local_checkpoint(
        self,
        session_id: str,
        checkpoint_id: str,
        *,
        approve: bool,
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

    def _new_runtime(self) -> SessionRuntime:
        return SessionRuntime(
            disconnect_grace_seconds=self._disconnect_grace_seconds,
            on_terminal_no_connections=self._prune_terminal_session,
        )

    def _prune_terminal_session(self, session_id: str) -> None:
        runtime = self._sessions.pop(session_id, None)
        if runtime is None:
            return
        self._opencode_attach.prune_runtime(runtime)

    def _build_frontend_open_url(self, frontend_origin: str, runtime: SessionRuntime) -> str:
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
