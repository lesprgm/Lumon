from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import app.session.manager as session_manager
from app.config import RUNTIME_VERSION
from app.protocol.enums import SessionState
from app.protocol.models import BrowserCommandRecord, LocalObserveOpenCodeRequest
from app.session.manager import SessionManager, SessionRuntime
from starlette.websockets import WebSocketState


@pytest.mark.asyncio
async def test_disconnect_triggers_stop_after_grace_when_session_active() -> None:
    runtime = SessionRuntime(disconnect_grace_seconds=0)
    runtime.state = SessionState.RUNNING

    class DummySocket:
        application_state = SimpleNamespace(name="CONNECTED")
        accepted = False

        async def accept(self) -> None:
            self.accepted = True

        async def send_json(self, payload: dict) -> None:
            _ = payload

    dummy_socket = DummySocket()
    runtime._connections.add(dummy_socket)  # type: ignore[arg-type]

    stopped = False

    async def fake_stop() -> None:
        nonlocal stopped
        stopped = True

    runtime._connector.stop = fake_stop  # type: ignore[assignment]
    await runtime.disconnect(dummy_socket)  # type: ignore[arg-type]
    await asyncio.sleep(0.02)

    assert stopped is True
    assert runtime.state == SessionState.STOPPED


@pytest.mark.asyncio
async def test_reconnect_within_grace_prevents_stop() -> None:
    runtime = SessionRuntime(disconnect_grace_seconds=0.05)
    runtime.state = SessionState.RUNNING

    class DummySocket:
        application_state = SimpleNamespace(name="CONNECTED")

        def __init__(self) -> None:
            self.accepted = False
            self.sent: list[dict] = []

        async def accept(self) -> None:
            self.accepted = True

        async def send_json(self, payload: dict) -> None:
            self.sent.append(payload)

    old_socket = DummySocket()
    new_socket = DummySocket()
    runtime._connections.add(old_socket)  # type: ignore[arg-type]

    stopped = False

    async def fake_stop() -> None:
        nonlocal stopped
        stopped = True

    runtime._connector.stop = fake_stop  # type: ignore[assignment]
    await runtime.disconnect(old_socket)  # type: ignore[arg-type]
    await runtime.connect(new_socket)  # type: ignore[arg-type]
    await asyncio.sleep(0.07)

    assert stopped is False
    assert runtime.state == SessionState.RUNNING


@pytest.mark.asyncio
async def test_runtime_connect_tracks_reconnects_without_seeding_open_request() -> None:
    runtime = SessionRuntime(disconnect_grace_seconds=0)
    runtime.state = SessionState.RUNNING

    class DummySocket:
        application_state = SimpleNamespace(name="CONNECTED")

        def __init__(self) -> None:
            self.accepted = False
            self.sent: list[dict] = []

        async def accept(self) -> None:
            self.accepted = True

        async def send_json(self, payload: dict) -> None:
            self.sent.append(payload)

    first_socket = DummySocket()
    second_socket = DummySocket()

    await runtime.connect(first_socket)  # type: ignore[arg-type]
    assert runtime._artifact.metrics.ui_open_requested_at is None
    assert runtime._artifact.metrics.reconnect_count == 0

    await runtime.disconnect(first_socket)  # type: ignore[arg-type]
    await asyncio.sleep(0.02)
    await runtime.connect(second_socket)  # type: ignore[arg-type]

    assert runtime._artifact.metrics.ui_open_requested_at is None
    assert runtime._artifact.metrics.reconnect_count == 1


@pytest.mark.asyncio
async def test_broadcast_tolerates_connection_set_mutation_during_send() -> None:
    runtime = SessionRuntime()
    runtime.state = SessionState.RUNNING

    class MutatingSocket:
        application_state = WebSocketState.CONNECTED

        def __init__(self, runtime: SessionRuntime) -> None:
            self.runtime = runtime
            self.sent: list[dict] = []

        async def send_json(self, payload: dict) -> None:
            self.sent.append(payload)
            self.runtime._connections.discard(self)  # type: ignore[arg-type]

    socket = MutatingSocket(runtime)
    runtime._connections.add(socket)  # type: ignore[arg-type]

    await runtime.emit_session_state()

    assert len(socket.sent) == 1
    assert socket not in runtime._connections


@pytest.mark.asyncio
async def test_ui_ready_emits_error_when_frontend_runtime_is_stale() -> None:
    runtime = SessionRuntime()
    messages: list[dict] = []
    runtime.broadcast = _capture_broadcast(messages)  # type: ignore[assignment]

    await runtime.handle_client_message(
        {
            "type": "ui_ready",
            "payload": {
                "ready": True,
                "runtime_version": "stale-frontend-build",
                "supports_ui_telemetry": False,
                "supports_ui_ready_handshake": False,
            },
        }
    )

    assert runtime._artifact.metrics.ui_ready_at is not None
    assert runtime._artifact.events[-1]["type"] == "ui_handshake"
    assert (
        runtime._artifact.events[-1]["payload"]["expected_runtime_version"]
        == RUNTIME_VERSION
    )
    assert messages[-1]["type"] == "error"
    assert "frontend build is stale" in messages[-1]["payload"]["message"]


@pytest.mark.asyncio
async def test_manager_rejects_stale_websocket_with_policy_close() -> None:
    manager = SessionManager(allowed_origins=("http://127.0.0.1:8000",))

    class RejectSocket:
        def __init__(self) -> None:
            self.headers = {"origin": "http://127.0.0.1:8000"}
            self.query_params = {"session_id": "sess_missing", "token": "ws_missing"}
            self.accepted = False
            self.closed: tuple[int, str] | None = None

        async def accept(self) -> None:
            self.accepted = True

        async def close(self, code: int, reason: str) -> None:
            self.closed = (code, reason)

    socket = RejectSocket()
    await manager.connect(socket)  # type: ignore[arg-type]

    assert socket.accepted is True
    assert socket.closed is not None
    assert socket.closed[0] == 1008


@pytest.mark.asyncio
async def test_failed_command_mode_approval_does_not_resolve_active_intervention() -> (
    None
):
    runtime = SessionRuntime()
    runtime.state = SessionState.WAITING_FOR_APPROVAL
    runtime._active_approval_intervention_id = "intv_active_001"
    runtime._active_approval_payload = {
        "intervention_id": "intv_active_001",
        "checkpoint_id": "chk_active_001",
    }

    class FakeConnector:
        capabilities = {
            "supports_pause": True,
            "supports_approval": True,
            "supports_takeover": True,
            "supports_frames": True,
        }

        async def approve(self, checkpoint_id: str) -> dict[str, str]:
            assert checkpoint_id == "chk_active_001"
            return {"status": "failed", "reason": "delegate_crashed"}

    runtime._connector = FakeConnector()  # type: ignore[assignment]

    await runtime.handle_client_message(
        {"type": "approve", "payload": {"checkpoint_id": "chk_active_001"}}
    )

    assert runtime._active_approval_intervention_id == "intv_active_001"
    assert runtime._active_approval_payload is not None


@pytest.mark.asyncio
async def test_successful_command_mode_approval_resolves_active_intervention() -> None:
    runtime = SessionRuntime()
    runtime.state = SessionState.WAITING_FOR_APPROVAL
    runtime._active_approval_intervention_id = "intv_active_002"
    runtime._active_approval_payload = {
        "intervention_id": "intv_active_002",
        "checkpoint_id": "chk_active_002",
    }
    runtime._artifact.start_intervention(
        intervention_id="intv_active_002",
        kind="approval",
        headline="Needs your approval",
        reason_text="Risky click",
        started_at=runtime.timestamp(),
        source_url=None,
        target_summary=None,
        recommended_action="approve",
        checkpoint_id="chk_active_002",
        source_event_id="evt_active_002",
    )

    class FakeConnector:
        capabilities = {
            "supports_pause": True,
            "supports_approval": True,
            "supports_takeover": True,
            "supports_frames": True,
        }

        async def approve(self, checkpoint_id: str) -> dict[str, str]:
            assert checkpoint_id == "chk_active_002"
            await asyncio.sleep(0)
            return {"status": "success", "summary_text": "Approved and resumed"}

    runtime._connector = FakeConnector()  # type: ignore[assignment]

    await runtime.handle_client_message(
        {"type": "approve", "payload": {"checkpoint_id": "chk_active_002"}}
    )

    assert runtime._active_approval_intervention_id is None
    assert runtime._active_approval_payload is None
    resolutions = [item.resolution for item in runtime._artifact.interventions]
    assert "approved" in resolutions


@pytest.mark.asyncio
async def test_connect_replays_browser_context_frame_commands_and_active_intervention() -> (
    None
):
    runtime = SessionRuntime()
    runtime.state = SessionState.RUNNING
    runtime.adapter_id = "opencode"
    runtime.adapter_run_id = "run_replay_001"
    runtime.task_text = "Open Wikipedia"

    await runtime.emit_browser_context_update(
        {
            "session_id": runtime.session_id,
            "adapter_id": "opencode",
            "adapter_run_id": "run_replay_001",
            "timestamp": runtime.timestamp(),
            "url": "https://www.wikipedia.org",
            "domain": "www.wikipedia.org",
            "title": "Wikipedia",
            "environment_type": "external",
        }
    )
    await runtime.emit_frame(
        {
            "mime_type": "image/png",
            "data_base64": "ZmFrZQ==",
            "frame_seq": 2,
        }
    )
    runtime.record_browser_command(
        BrowserCommandRecord(
            command_id="cmd_replay_001",
            command="open",
            status="success",
            summary_text="Opened wikipedia.org.",
            timestamp=runtime.timestamp(),
            source_url="https://www.wikipedia.org",
            domain="www.wikipedia.org",
            page_version=1,
            meta={},
        )
    )
    await runtime.emit_approval_required(
        {
            "session_id": runtime.session_id,
            "checkpoint_id": "chk_replay_001",
            "event_id": "evt_replay_001",
            "action_type": "click",
            "summary_text": "Ready to submit",
            "intent": "Stop before submitting",
            "risk_level": "high",
            "risk_reason": "This will submit the search.",
            "adapter_id": "opencode",
            "adapter_run_id": "run_replay_001",
        }
    )

    class DummySocket:
        application_state = SimpleNamespace(name="CONNECTED")

        def __init__(self) -> None:
            self.sent: list[dict] = []

        async def accept(self) -> None:
            return None

        async def send_json(self, payload: dict) -> None:
            self.sent.append(payload)

    socket = DummySocket()
    await runtime.connect(socket)  # type: ignore[arg-type]

    message_types = [message["type"] for message in socket.sent]
    assert message_types[0] == "session_state"
    assert "browser_context_update" in message_types
    assert "browser_command" in message_types
    assert "frame" in message_types
    assert "approval_required" in message_types


@pytest.mark.asyncio
async def test_local_observe_attach_reuses_existing_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeConnector:
        adapter_id = "opencode"
        capabilities = {
            "supports_pause": False,
            "supports_approval": False,
            "supports_takeover": False,
            "supports_frames": False,
        }

        def __init__(self, runtime: SessionRuntime) -> None:
            self.runtime = runtime
            self.adapter_run_id = "run_attach_observer_001"
            self.observed_session_id = None

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            auto_delegate: bool = False,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (
                task_text,
                demo_mode,
                web_mode,
                web_bridge,
                auto_delegate,
                observer_mode,
                bridge_context,
            )
            self.observed_session_id = observed_session_id
            self.runtime.adapter_run_id = self.adapter_run_id
            self.runtime.state = SessionState.RUNNING

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def accept_bridge(self) -> None: ...
        async def decline_bridge(self) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(
        session_manager,
        "create_connector",
        lambda runtime, adapter_id: FakeConnector(runtime),
    )
    manager = SessionManager(allowed_origins=("http://127.0.0.1:5173",))
    payload = LocalObserveOpenCodeRequest(
        project_directory="/Users/leslie/Documents/Lumon",
        observed_session_id="ses_local_attach_001",
        web_mode="observe_only",
        auto_delegate=False,
    )

    first = await manager.attach_local_opencode_observer(
        payload, frontend_origin="http://127.0.0.1:5173"
    )
    second = await manager.attach_local_opencode_observer(
        payload, frontend_origin="http://127.0.0.1:5173"
    )

    assert first["already_attached"] is False
    assert second["already_attached"] is True
    assert first["session_id"] == second["session_id"]
    runtime = manager._sessions[first["session_id"]]
    assert runtime._artifact.metrics.attach_requested_at is not None
    assert runtime._artifact.metrics.attached_at is not None
    assert (
        runtime._artifact.metrics.attach_requested_at
        <= runtime._artifact.metrics.attached_at
    )
    assert runtime._artifact.metrics.attach_latency_ms is not None
    assert runtime._artifact.metrics.duplicate_attach_prevented == 1


@pytest.mark.asyncio
async def test_local_observe_attach_rolls_back_failed_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingConnector:
        adapter_id = "opencode"
        capabilities = {
            "supports_pause": False,
            "supports_approval": False,
            "supports_takeover": False,
            "supports_frames": False,
        }

        def __init__(self, runtime: SessionRuntime) -> None:
            self.runtime = runtime
            self.adapter_run_id = "run_attach_failure_001"

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            auto_delegate: bool = False,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (
                task_text,
                demo_mode,
                web_mode,
                web_bridge,
                auto_delegate,
                observer_mode,
                observed_session_id,
                bridge_context,
            )
            raise RuntimeError("attach failed")

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def accept_bridge(self) -> None: ...
        async def decline_bridge(self) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(
        session_manager,
        "create_connector",
        lambda runtime, adapter_id: FailingConnector(runtime),
    )
    manager = SessionManager(allowed_origins=("http://127.0.0.1:5173",))
    payload = LocalObserveOpenCodeRequest(
        project_directory="/Users/leslie/Documents/Lumon",
        observed_session_id="ses_local_attach_fail_001",
        web_mode="observe_only",
        auto_delegate=False,
    )

    with pytest.raises(RuntimeError, match="attach failed"):
        await manager.attach_local_opencode_observer(
            payload, frontend_origin="http://127.0.0.1:5173"
        )

    assert manager._sessions == {}
    assert (
        manager._opencode_attach.runtime_for_observed_session(
            manager._sessions, "ses_local_attach_fail_001"
        )
        is None
    )


@pytest.mark.asyncio
async def test_illegal_transition_emits_error_and_keeps_state() -> None:
    runtime = SessionRuntime()
    messages: list[dict] = []
    runtime.broadcast = _capture_broadcast(messages)  # type: ignore[assignment]
    runtime.state = SessionState.COMPLETED

    await runtime.transition_to(SessionState.RUNNING)

    assert runtime.state == SessionState.COMPLETED
    assert messages[-1]["type"] == "error"
    assert messages[-1]["payload"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_emit_session_state_includes_selected_web_mode() -> None:
    runtime = SessionRuntime()
    messages: list[dict] = []
    runtime.broadcast = _capture_broadcast(messages)  # type: ignore[assignment]
    runtime.adapter_id = "opencode"
    runtime.web_mode = "delegate_playwright"
    runtime.web_bridge = "playwright_native"

    await runtime.emit_session_state()

    assert messages[-1]["type"] == "session_state"
    assert messages[-1]["payload"]["web_mode"] == "delegate_playwright"
    assert messages[-1]["payload"]["web_bridge"] == "playwright_native"


@pytest.mark.asyncio
async def test_emit_routing_decision_enriches_trace_and_broadcasts_diagnostic_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMON_DIAGNOSTICS_ENABLED", "1")
    runtime = SessionRuntime()
    messages: list[dict] = []
    runtime.broadcast = _capture_broadcast(messages)  # type: ignore[assignment]

    runtime.emit_routing_decision(
        {
            "source_event_type": "browser.search",
            "reason_code": "browser_signal",
            "summary_text": "Bridge launch decision",
            "category": "routing",
        }
    )
    await asyncio.sleep(0)

    assert runtime._artifact.events[-1]["type"] == "routing_decision"
    assert runtime._artifact.events[-1]["payload"]["trace_id"] == runtime.trace_id
    assert messages[-1]["type"] == "diagnostic_event"
    assert messages[-1]["payload"]["event_name"] == "browser_signal"


@pytest.mark.asyncio
async def test_record_browser_command_appends_event_without_runtime_error() -> None:
    runtime = SessionRuntime()
    messages: list[dict] = []
    runtime.broadcast = _capture_broadcast(messages)  # type: ignore[assignment]

    runtime.record_browser_command(
        BrowserCommandRecord(
            command_id="cmd_001",
            command="open",
            status="success",
            summary_text="Opened example.com.",
            timestamp="2026-03-16T00:00:00Z",
            source_url="https://example.com",
            domain="example.com",
            page_version=1,
        )
    )
    await asyncio.sleep(0)

    assert runtime._artifact.commands[-1].command_id == "cmd_001"
    assert runtime._artifact.events[-1]["type"] == "browser_command"


@pytest.mark.asyncio
async def test_emit_frame_keeps_latest_frame_state_when_websocket_broadcast_is_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = SessionRuntime()
    messages: list[dict] = []
    runtime.broadcast = _capture_broadcast(messages)  # type: ignore[assignment]
    runtime._webrtc_ready = True

    monkeypatch.delenv("LUMON_DISABLE_FRAME_STREAM_ON_WEBRTC", raising=False)

    await runtime.emit_frame(
        {
            "mime_type": "image/png",
            "data_base64": "ZmFrZQ==",
            "frame_seq": 7,
        }
    )

    assert messages == []
    assert runtime.latest_frame_seq == 7
    assert runtime._latest_frame_payload is not None
    assert runtime._latest_frame_payload["frame_seq"] == 7
    assert runtime._artifact.latest_frame is not None


@pytest.mark.asyncio
async def test_start_task_resets_stale_frame_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeConnector:
        adapter_id = "playwright_native"
        capabilities = {
            "supports_pause": True,
            "supports_approval": True,
            "supports_takeover": True,
            "supports_frames": True,
        }

        def __init__(self, runtime: SessionRuntime) -> None:
            self.runtime = runtime
            self.adapter_run_id = "run_reset_001"

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            auto_delegate: bool = False,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (
                task_text,
                demo_mode,
                web_mode,
                web_bridge,
                auto_delegate,
                observer_mode,
                observed_session_id,
                bridge_context,
            )

    monkeypatch.setattr(
        session_manager,
        "create_connector",
        lambda runtime, adapter_id: FakeConnector(runtime),
    )
    runtime = SessionRuntime()
    runtime.state = SessionState.IDLE
    runtime._latest_frame_payload = {
        "frame_seq": 99,
        "mime_type": "image/png",
        "data_base64": "stale",
    }
    runtime._latest_frame_seq = 99
    runtime._latest_frame_generation = 8
    runtime._latest_command_frame_generation = 4

    await runtime.handle_client_message(
        {
            "type": "start_task",
            "payload": {
                "task_text": "Open the browser",
                "demo_mode": False,
                "adapter_id": "playwright_native",
                "observer_mode": False,
            },
        }
    )

    assert runtime._latest_frame_payload is None
    assert runtime._latest_frame_seq is None
    assert runtime.latest_frame_generation == 0
    assert runtime.latest_command_frame_generation == 0


@pytest.mark.asyncio
async def test_start_task_uses_connector_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool, str | None, str | None, bool]] = []

    class FakeConnector:
        adapter_id = "playwright_native"
        capabilities = {
            "supports_pause": True,
            "supports_approval": True,
            "supports_takeover": True,
            "supports_frames": True,
        }

        def __init__(self, runtime: SessionRuntime) -> None:
            self.runtime = runtime
            self.adapter_run_id = "run_fake_001"

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            auto_delegate: bool = False,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (observer_mode, observed_session_id)
            calls.append((task_text, demo_mode, web_mode, web_bridge, auto_delegate))

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(
        session_manager,
        "create_connector",
        lambda runtime, adapter_id: FakeConnector(runtime),
    )
    runtime = SessionRuntime()

    await runtime.handle_client_message(
        {
            "type": "start_task",
            "payload": {
                "task_text": "Find a hotel in NYC",
                "demo_mode": False,
                "adapter_id": "opencode",
                "web_mode": "delegate_playwright",
            },
        }
    )

    assert runtime.task_text == "Find a hotel in NYC"
    assert runtime.adapter_id == "opencode"
    assert runtime.web_mode == "delegate_playwright"
    assert runtime.web_bridge == "playwright_native"
    assert calls == [
        (
            "Find a hotel in NYC",
            False,
            "delegate_playwright",
            "playwright_native",
            False,
        )
    ]


@pytest.mark.asyncio
async def test_start_task_defaults_to_live_when_demo_mode_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool, str | None, str | None, bool]] = []

    class FakeConnector:
        adapter_id = "playwright_native"
        capabilities = {
            "supports_pause": True,
            "supports_approval": True,
            "supports_takeover": True,
            "supports_frames": True,
        }

        def __init__(self, runtime: SessionRuntime) -> None:
            self.runtime = runtime
            self.adapter_run_id = "run_fake_default_live_001"

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            auto_delegate: bool = False,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (observer_mode, observed_session_id, bridge_context)
            calls.append((task_text, demo_mode, web_mode, web_bridge, auto_delegate))

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(
        session_manager,
        "create_connector",
        lambda runtime, adapter_id: FakeConnector(runtime),
    )
    runtime = SessionRuntime()

    await runtime.handle_client_message(
        {
            "type": "start_task",
            "payload": {
                "task_text": "Open https://www.wikipedia.org, click search, type OpenAI, and stop before submit",
                "adapter_id": "opencode",
                "web_mode": "delegate_playwright",
            },
        }
    )

    assert runtime.run_mode == "live"
    assert calls == [
        (
            "Open https://www.wikipedia.org, click search, type OpenAI, and stop before submit",
            False,
            "delegate_playwright",
            "playwright_native",
            False,
        )
    ]


@pytest.mark.asyncio
async def test_attach_observer_uses_live_opencode_observer_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool, str | None, str | None, bool, bool, str | None]] = []

    class FakeConnector:
        adapter_id = "opencode"
        capabilities = {
            "supports_pause": False,
            "supports_approval": False,
            "supports_takeover": False,
            "supports_frames": False,
        }

        def __init__(self, runtime: SessionRuntime) -> None:
            self.runtime = runtime
            self.adapter_run_id = "run_attach_001"

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            auto_delegate: bool = False,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            calls.append(
                (
                    task_text,
                    demo_mode,
                    web_mode,
                    web_bridge,
                    auto_delegate,
                    observer_mode,
                    observed_session_id,
                )
            )

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(
        session_manager,
        "create_connector",
        lambda runtime, adapter_id: FakeConnector(runtime),
    )
    runtime = SessionRuntime()

    await runtime.handle_client_message(
        {
            "type": "attach_observer",
            "payload": {
                "task_text": "OpenCode interactive session",
                "adapter_id": "opencode",
                "web_mode": "observe_only",
                "observed_session_id": "ses_attach_001",
            },
        }
    )

    assert runtime.adapter_id == "opencode"
    assert runtime.run_mode == "live"
    assert runtime.web_mode == "observe_only"
    assert runtime.web_bridge is None
    assert calls == [
        (
            "OpenCode interactive session",
            False,
            "observe_only",
            None,
            False,
            True,
            "ses_attach_001",
        )
    ]


@pytest.mark.asyncio
async def test_explicit_observe_only_overrides_legacy_web_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool, str | None, str | None, bool]] = []

    class FakeConnector:
        adapter_id = "opencode"
        capabilities = {
            "supports_pause": False,
            "supports_approval": False,
            "supports_takeover": False,
            "supports_frames": False,
        }

        def __init__(self, runtime: SessionRuntime) -> None:
            self.runtime = runtime
            self.adapter_run_id = "run_attach_002"

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            auto_delegate: bool = False,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (observer_mode, observed_session_id)
            calls.append((task_text, demo_mode, web_mode, web_bridge, auto_delegate))

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(
        session_manager,
        "create_connector",
        lambda runtime, adapter_id: FakeConnector(runtime),
    )
    runtime = SessionRuntime()

    await runtime.handle_client_message(
        {
            "type": "attach_observer",
            "payload": {
                "task_text": "OpenCode interactive session",
                "adapter_id": "opencode",
                "web_mode": "observe_only",
                "web_bridge": "playwright_native",
                "observed_session_id": "ses_attach_002",
            },
        }
    )

    assert runtime.web_mode == "observe_only"
    assert runtime.web_bridge is None
    assert calls == [
        ("OpenCode interactive session", False, "observe_only", None, False)
    ]


@pytest.mark.asyncio
async def test_attach_observer_passes_auto_delegate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool, str | None, str | None, bool]] = []

    class FakeConnector:
        adapter_id = "opencode"
        capabilities = {
            "supports_pause": False,
            "supports_approval": False,
            "supports_takeover": False,
            "supports_frames": False,
        }

        def __init__(self, runtime: SessionRuntime) -> None:
            self.runtime = runtime
            self.adapter_run_id = "run_attach_003"

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            auto_delegate: bool = False,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (observer_mode, observed_session_id)
            calls.append((task_text, demo_mode, web_mode, web_bridge, auto_delegate))

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(
        session_manager,
        "create_connector",
        lambda runtime, adapter_id: FakeConnector(runtime),
    )
    runtime = SessionRuntime()

    await runtime.handle_client_message(
        {
            "type": "attach_observer",
            "payload": {
                "task_text": "OpenCode interactive session",
                "adapter_id": "opencode",
                "web_mode": "delegate_playwright",
                "auto_delegate": True,
                "observed_session_id": "ses_attach_003",
            },
        }
    )

    assert runtime.web_mode == "delegate_playwright"
    assert runtime.web_bridge == "playwright_native"
    assert calls == [
        (
            "OpenCode interactive session",
            False,
            "delegate_playwright",
            "playwright_native",
            True,
        )
    ]


@pytest.mark.asyncio
async def test_ingest_optional_trace_rejected_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LUMON_OPTIONAL_TRACING", raising=False)
    runtime = SessionRuntime()
    messages: list[dict] = []
    runtime.broadcast = _capture_broadcast(messages)  # type: ignore[assignment]
    runtime.state = SessionState.RUNNING

    await runtime.handle_client_message(
        {
            "type": "ingest_optional_trace",
            "payload": {
                "provider": "langchain",
                "run_id": "run_trace_001",
                "event_type": "tool_start",
                "summary_text": "Inspecting docs",
            },
        }
    )

    assert messages[-1]["type"] == "error"
    assert messages[-1]["payload"]["command_type"] == "ingest_optional_trace"


@pytest.mark.asyncio
async def test_ingest_optional_trace_rejected_for_terminal_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMON_OPTIONAL_TRACING", "1")
    runtime = SessionRuntime()
    messages: list[dict] = []
    runtime.broadcast = _capture_broadcast(messages)  # type: ignore[assignment]
    runtime.state = SessionState.COMPLETED

    await runtime.handle_client_message(
        {
            "type": "ingest_optional_trace",
            "payload": {
                "provider": "langchain",
                "run_id": "run_trace_001",
                "event_type": "tool_start",
                "summary_text": "Inspecting docs",
            },
        }
    )

    assert messages[-1]["type"] == "error"
    assert "terminal session" in messages[-1]["payload"]["message"]


@pytest.mark.asyncio
async def test_ingest_optional_trace_emits_canonical_agent_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMON_OPTIONAL_TRACING", "1")
    runtime = SessionRuntime()
    messages: list[dict] = []
    runtime.broadcast = _capture_broadcast(messages)  # type: ignore[assignment]
    runtime.state = SessionState.RUNNING
    runtime.adapter_id = "opencode"
    runtime.adapter_run_id = "run_demo_001"

    await runtime.handle_client_message(
        {
            "type": "ingest_optional_trace",
            "payload": {
                "provider": "langchain",
                "trace_id": "trace_001",
                "run_id": "run_trace_001",
                "event_type": "tool_start",
                "state": "thinking",
                "summary_text": "Inspecting docs",
                "intent": "Read repo documentation",
            },
        }
    )

    assert messages[-1]["type"] == "agent_event"
    assert messages[-1]["payload"]["summary_text"] == "Inspecting docs"
    assert messages[-1]["payload"]["meta"]["optional_trace"] is True
    assert messages[-1]["payload"]["meta"]["provider"] == "langchain"


@pytest.mark.asyncio
async def test_ingest_optional_trace_uses_monotonic_event_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMON_OPTIONAL_TRACING", "1")
    runtime = SessionRuntime()
    messages: list[dict] = []
    runtime.broadcast = _capture_broadcast(messages)  # type: ignore[assignment]
    runtime.state = SessionState.RUNNING
    runtime.adapter_id = "opencode"
    runtime.adapter_run_id = "run_demo_001"

    await runtime.emit_agent_event(
        {
            "event_seq": 5,
            "event_id": "evt_existing_001",
            "source_event_id": "src_existing_001",
            "timestamp": "2026-03-11T00:00:00Z",
            "session_id": runtime.session_id,
            "adapter_id": "opencode",
            "adapter_run_id": "run_demo_001",
            "agent_id": "main_001",
            "parent_agent_id": None,
            "agent_kind": "main",
            "environment_id": "env_browser_main",
            "visibility_mode": "foreground",
            "action_type": "read",
            "state": "reading",
            "summary_text": "Existing event",
            "intent": "Existing event",
            "risk_level": "none",
            "subagent_source": None,
            "cursor": None,
            "target_rect": None,
            "meta": {},
        }
    )

    await runtime.handle_client_message(
        {
            "type": "ingest_optional_trace",
            "payload": {
                "provider": "langchain",
                "trace_id": "trace_002",
                "run_id": "run_trace_002",
                "event_type": "tool_complete",
                "state": "done",
                "summary_text": "Trace complete",
            },
        }
    )

    assert messages[-1]["type"] == "agent_event"
    assert messages[-1]["payload"]["event_seq"] == 6


@pytest.mark.asyncio
async def test_ingest_optional_trace_strips_coordinates_when_playwright_is_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMON_OPTIONAL_TRACING", "1")
    runtime = SessionRuntime()
    messages: list[dict] = []
    runtime.broadcast = _capture_broadcast(messages)  # type: ignore[assignment]
    runtime.state = SessionState.RUNNING
    runtime.adapter_id = "playwright_native"
    runtime.adapter_run_id = "run_demo_001"

    await runtime.handle_client_message(
        {
            "type": "ingest_optional_trace",
            "payload": {
                "provider": "langchain",
                "trace_id": "trace_003",
                "run_id": "run_trace_003",
                "event_type": "click",
                "state": "clicking",
                "summary_text": "Clicking CTA",
                "cursor": {"x": 320, "y": 240},
                "target_rect": {"x": 300, "y": 220, "width": 40, "height": 20},
            },
        }
    )

    assert messages[-1]["type"] == "agent_event"
    assert messages[-1]["payload"]["cursor"] is None
    assert messages[-1]["payload"]["target_rect"] is None


@pytest.mark.asyncio
async def test_ingest_optional_trace_emits_background_worker_update_for_hidden_subagents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMON_OPTIONAL_TRACING", "1")
    runtime = SessionRuntime()
    messages: list[dict] = []
    runtime.broadcast = _capture_broadcast(messages)  # type: ignore[assignment]
    runtime.state = SessionState.RUNNING
    runtime.adapter_id = "opencode"
    runtime.adapter_run_id = "run_demo_001"

    await runtime.handle_client_message(
        {
            "type": "ingest_optional_trace",
            "payload": {
                "provider": "langsmith",
                "trace_id": "trace_004",
                "run_id": "run_trace_004",
                "parent_run_id": "run_trace_001",
                "event_type": "subagent",
                "state": "thinking",
                "summary_text": "Background comparer running",
                "subagent": True,
            },
        }
    )

    assert messages[-1]["type"] == "background_worker_update"


@pytest.mark.asyncio
async def test_ingest_optional_trace_deduplicates_bursty_repeats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMON_OPTIONAL_TRACING", "1")
    runtime = SessionRuntime()
    messages: list[dict] = []
    runtime.broadcast = _capture_broadcast(messages)  # type: ignore[assignment]
    runtime.state = SessionState.RUNNING
    runtime.adapter_id = "opencode"
    runtime.adapter_run_id = "run_demo_001"

    payload = {
        "provider": "langchain",
        "trace_id": "trace_dup",
        "run_id": "run_dup",
        "event_type": "read",
        "state": "reading",
        "summary_text": "Reading docs",
    }

    await runtime.handle_client_message(
        {"type": "ingest_optional_trace", "payload": payload}
    )
    first_count = len(messages)
    await runtime.handle_client_message(
        {"type": "ingest_optional_trace", "payload": payload}
    )

    assert len(messages) == first_count


def _capture_broadcast(messages: list[dict]):
    async def _broadcast(message: dict) -> None:
        messages.append(message)

    return _broadcast
