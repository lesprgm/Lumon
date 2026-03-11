from __future__ import annotations

import asyncio
import shutil

import pytest
import app.adapters.registry as registry

from app.adapters.opencode import OpenCodeConnector, _BridgeRuntimeProxy
from app.protocol.enums import ErrorCode, SessionState


class FakeRuntime:
    def __init__(self) -> None:
        self.session_id = "sess_demo_001"
        self.state = SessionState.IDLE
        self.task_text = ""
        self.adapter_run_id: str | None = None
        self.events: list[dict] = []
        self.frames: list[dict] = []
        self.approvals: list[dict] = []
        self.bridge_offers: list[dict] = []
        self.errors: list[dict] = []
        self.routing_decisions: list[dict] = []
        self.transitions: list[str] = []
        self.completed: tuple[str, str] | None = None
        self.session_state_emits = 0
        self.trace_id = "trace_test_001"
        self.latest_frame_generation = 0
        self.latest_command_frame_generation = 0
        self.latest_frame_seq: int | None = None

    async def emit_agent_event(self, payload: dict) -> None:
        self.events.append(payload)

    async def emit_frame(self, payload: dict) -> None:
        self.frames.append(payload)
        self.latest_frame_generation += 1
        frame_seq = payload.get("frame_seq")
        self.latest_frame_seq = frame_seq if isinstance(frame_seq, int) else self.latest_frame_seq

    async def emit_background_worker_update(self, payload: dict) -> None: ...

    async def emit_approval_required(self, payload: dict) -> None:
        self.approvals.append(payload)

    async def emit_bridge_offer(self, payload: dict) -> None:
        self.bridge_offers.append(payload)

    async def emit_error(self, code: ErrorCode, message: str, command_type: str | None = None, checkpoint_id: str | None = None) -> None:
        self.errors.append(
            {
                "code": code.value,
                "message": message,
                "command_type": command_type,
                "checkpoint_id": checkpoint_id,
            }
        )

    async def emit_session_state(self) -> None:
        self.session_state_emits += 1

    async def transition_to(self, state: SessionState, checkpoint_id: str | None = None) -> None:
        self.state = state
        self.transitions.append(state.value)

    async def complete_task(self, status: str, summary_text: str) -> None:
        self.completed = (status, summary_text)

    def emit_routing_decision(self, payload: dict) -> None:
        self.routing_decisions.append(payload)

    def timestamp(self) -> str:
        return "2026-03-10T12:00:00Z"


@pytest.mark.asyncio
async def test_opencode_demo_emits_normalized_events() -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)

    await connector._run_demo("Summarize the current repository state")

    assert runtime.events
    assert all(event["adapter_id"] == "opencode" for event in runtime.events)
    assert runtime.events[0]["summary_text"] == "OpenCode planning task execution"
    assert runtime.completed == ("completed", "OpenCode adapter demo completed the requested task flow")




def test_bridge_runtime_proxy_exposes_parent_frame_counters() -> None:
    runtime = FakeRuntime()
    runtime.latest_frame_generation = 7
    runtime.latest_command_frame_generation = 9
    runtime.latest_frame_seq = 41
    connector = OpenCodeConnector(runtime)
    proxy = _BridgeRuntimeProxy(connector, "playwright_native", "Open Wikipedia")

    assert proxy.latest_frame_generation == 7
    assert proxy.latest_command_frame_generation == 9
    assert proxy.latest_frame_seq == 41


@pytest.mark.asyncio
async def test_bridge_runtime_proxy_transition_to_running_clears_parent_waiting_state() -> None:
    runtime = FakeRuntime()
    runtime.state = SessionState.WAITING_FOR_APPROVAL
    connector = OpenCodeConnector(runtime)
    proxy = _BridgeRuntimeProxy(connector, "playwright_native", "Approve and resume")

    await proxy.transition_to(SessionState.RUNNING, checkpoint_id=None)

    assert runtime.state == SessionState.RUNNING
    assert runtime.transitions[-1] == SessionState.RUNNING.value


@pytest.mark.asyncio
async def test_opencode_demo_with_browser_bridge_relabels_child_events(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id
    connector.selected_web_mode = "delegate_playwright"
    connector.selected_web_bridge = "playwright_native"

    class InstantBridgeConnector:
        adapter_id = "playwright_native"
        capabilities = {
            "supports_pause": False,
            "supports_approval": False,
            "supports_takeover": False,
            "supports_frames": True,
        }

        def __init__(self, bridge_runtime) -> None:
            self.runtime = bridge_runtime
            self.adapter_run_id = "run_bridge_001"

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (observer_mode, observed_session_id)
            self.runtime.adapter_run_id = self.adapter_run_id
            await self.runtime.emit_frame({"mime_type": "image/png", "data_base64": "bridge_frame", "frame_seq": 1})
            await self.runtime.emit_agent_event(
                {
                    "event_seq": 1,
                    "event_id": "evt_bridge_001",
                    "source_event_id": "src_bridge_001",
                    "timestamp": "2026-03-10T12:00:00Z",
                    "session_id": "child_session",
                    "adapter_id": "playwright_native",
                    "adapter_run_id": self.adapter_run_id,
                    "agent_id": "main_001",
                    "parent_agent_id": None,
                    "agent_kind": "main",
                    "environment_id": "env_browser_main",
                    "visibility_mode": "foreground",
                    "action_type": "navigate",
                    "state": "navigating",
                    "summary_text": "Playwright opened the web page",
                    "intent": task_text,
                    "risk_level": "none",
                    "subagent_source": None,
                    "cursor": {"x": 320, "y": 160},
                    "target_rect": {"x": 280, "y": 132, "width": 140, "height": 48},
                    "meta": {"provider": "playwright_native"},
                }
            )
            await self.runtime.complete_task("completed", "Playwright bridge completed")

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(registry, "create_connector", lambda bridge_runtime, adapter_id: InstantBridgeConnector(bridge_runtime))

    await connector._run_demo("Search the web for Lumon docs")

    bridge_events = [event for event in runtime.events if event["summary_text"] == "Playwright opened the web page"]
    assert bridge_events
    assert bridge_events[0]["adapter_id"] == "opencode"
    assert bridge_events[0]["meta"]["web_bridge"] == "playwright_native"
    assert bridge_events[0]["meta"]["bridge_source_adapter_id"] == "playwright_native"
    assert runtime.frames[0] == {"mime_type": "image/png", "data_base64": "bridge_frame", "frame_seq": 1}
    assert runtime.frames[-1]["mime_type"] == "image/png"
    assert runtime.frames[-1]["data_base64"] == "bridge_frame"
    assert runtime.frames[-1]["frame_seq"] >= 1
    assert runtime.completed == ("completed", "OpenCode adapter demo completed the requested task flow")


@pytest.mark.asyncio
async def test_opencode_bridge_failure_stops_parent_demo_run(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id
    connector.selected_web_mode = "delegate_playwright"
    connector.selected_web_bridge = "playwright_native"

    class FailingBridgeConnector:
        adapter_id = "playwright_native"
        capabilities = {
            "supports_pause": True,
            "supports_approval": True,
            "supports_takeover": True,
            "supports_frames": True,
        }

        def __init__(self, bridge_runtime) -> None:
            self.runtime = bridge_runtime

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (observer_mode, observed_session_id)
            await self.runtime.complete_task("failed", "Playwright bridge failed")

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(registry, "create_connector", lambda bridge_runtime, adapter_id: FailingBridgeConnector(bridge_runtime))

    await connector._run_demo("Search the web for Lumon docs")

    assert runtime.completed == ("failed", "Playwright bridge failed")


@pytest.mark.asyncio
async def test_opencode_bridge_controls_delegate_to_active_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id
    connector.selected_web_mode = "delegate_playwright"
    connector.selected_web_bridge = "playwright_native"
    delegated: list[tuple[str, str | None]] = []

    class PersistentBridgeConnector:
        adapter_id = "playwright_native"
        capabilities = {
            "supports_pause": True,
            "supports_approval": True,
            "supports_takeover": True,
            "supports_frames": True,
        }

        def __init__(self, bridge_runtime) -> None:
            self.runtime = bridge_runtime

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (observer_mode, observed_session_id)
            await self.runtime.emit_session_state()

        async def pause(self) -> None:
            delegated.append(("pause", None))

        async def resume(self) -> None:
            delegated.append(("resume", None))

        async def approve(self, checkpoint_id: str) -> None:
            delegated.append(("approve", checkpoint_id))

        async def reject(self, checkpoint_id: str) -> None:
            delegated.append(("reject", checkpoint_id))

        async def start_takeover(self) -> None:
            delegated.append(("start_takeover", None))

        async def end_takeover(self) -> None:
            delegated.append(("end_takeover", None))

        async def stop(self) -> None:
            delegated.append(("stop", None))

    monkeypatch.setattr(registry, "create_connector", lambda bridge_runtime, adapter_id: PersistentBridgeConnector(bridge_runtime))

    await connector._launch_web_bridge({"type": "browser.search", "summary": "Search the web"}, "Search the web", demo_mode=False)

    assert connector.capabilities["supports_pause"] is True
    await connector.pause()
    await connector.resume()
    await connector.approve("chk_001")
    await connector.reject("chk_002")
    await connector.start_takeover()
    await connector.end_takeover()
    await connector.stop()

    assert delegated == [
        ("pause", None),
        ("resume", None),
        ("approve", "chk_001"),
        ("reject", "chk_002"),
        ("start_takeover", None),
        ("end_takeover", None),
        ("stop", None),
    ]


@pytest.mark.asyncio
async def test_opencode_bridge_relabels_approval_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id
    connector.selected_web_mode = "delegate_playwright"
    connector.selected_web_bridge = "playwright_native"

    class ApprovalBridgeConnector:
        adapter_id = "playwright_native"
        capabilities = {
            "supports_pause": True,
            "supports_approval": True,
            "supports_takeover": True,
            "supports_frames": True,
        }

        def __init__(self, bridge_runtime) -> None:
            self.runtime = bridge_runtime

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (observer_mode, observed_session_id)
            await self.runtime.transition_to(SessionState.WAITING_FOR_APPROVAL, checkpoint_id="chk_bridge_001")
            await self.runtime.emit_approval_required(
                {
                    "session_id": "child_session",
                    "checkpoint_id": "chk_bridge_001",
                    "event_id": "evt_bridge_approval_001",
                    "action_type": "click",
                    "summary_text": "Approve the bridged browser step",
                    "intent": task_text,
                    "risk_level": "high",
                    "risk_reason": "Bridge requested approval",
                    "adapter_id": "playwright_native",
                    "adapter_run_id": "run_bridge_approval_001",
                }
            )
            await self.runtime.complete_task("completed", "Bridge approval completed")

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(registry, "create_connector", lambda bridge_runtime, adapter_id: ApprovalBridgeConnector(bridge_runtime))

    await connector._launch_web_bridge({"type": "browser.search", "summary": "Search the web"}, "Search the web", demo_mode=False)
    await connector._wait_for_bridge_completion()

    assert runtime.approvals == [
        {
            "session_id": "sess_demo_001",
            "checkpoint_id": "chk_bridge_001",
            "event_id": "evt_bridge_approval_001",
            "action_type": "click",
            "summary_text": "Approve the bridged browser step",
            "intent": "Search the web",
            "risk_level": "high",
            "risk_reason": "Bridge requested approval",
            "adapter_id": "opencode",
            "adapter_run_id": connector.adapter_run_id,
        }
    ]
    assert "waiting_for_approval" in runtime.transitions


@pytest.mark.asyncio
async def test_opencode_prevents_duplicate_bridge_launches(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id
    connector.selected_web_mode = "delegate_playwright"
    connector.selected_web_bridge = "playwright_native"
    create_calls: list[str] = []

    class WaitingBridgeConnector:
        adapter_id = "playwright_native"
        capabilities = {
            "supports_pause": False,
            "supports_approval": False,
            "supports_takeover": False,
            "supports_frames": True,
        }

        def __init__(self, bridge_runtime) -> None:
            self.runtime = bridge_runtime

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (observer_mode, observed_session_id)
            create_calls.append(task_text)

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(registry, "create_connector", lambda bridge_runtime, adapter_id: WaitingBridgeConnector(bridge_runtime))

    trigger = {"type": "browser.search", "summary": "Search the web for docs"}
    await connector._launch_web_bridge(trigger, "Search the web for docs", demo_mode=False)
    await connector._launch_web_bridge(trigger, "Search the web for docs", demo_mode=False)

    assert create_calls == ["Search the web for docs"]


@pytest.mark.asyncio
async def test_opencode_unsupported_pause_emits_error() -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)

    await connector.pause()

    assert runtime.errors[-1]["code"] == ErrorCode.INVALID_STATE.value
    assert runtime.errors[-1]["command_type"] == "pause"


def test_opencode_build_run_command_uses_attach_and_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)

    monkeypatch.setenv("OPENCODE_ATTACH_URL", "http://127.0.0.1:4096")
    monkeypatch.setenv("OPENCODE_MODEL", "openai/gpt-5")
    monkeypatch.setenv("OPENCODE_AGENT", "builder")
    monkeypatch.setenv("OPENCODE_VARIANT", "high")

    command = connector._build_run_command("Summarize this repository")

    assert command == (
        "opencode",
        "run",
        "--format",
        "json",
        "--attach",
        "http://127.0.0.1:4096",
        "--model",
        "openai/gpt-5",
        "--agent",
        "builder",
        "--variant",
        "high",
        "Summarize this repository",
    )


def test_opencode_normalizes_browser_search_events() -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)

    normalized = connector._normalize_opencode_event(
        {
            "type": "browser.search",
            "summary": "Searching the web for hotel options",
            "intent": "Look up hotel options online",
            "state": "running",
        }
    )

    assert normalized["action_type"] == "navigate"
    assert normalized["summary_text"] == "Searching the web for hotel options"
    assert normalized["adapter_id"] == "opencode"


@pytest.mark.asyncio
async def test_opencode_observe_only_never_offers_bridge() -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id

    await connector.start_task(
        "OpenCode interactive session",
        demo_mode=False,
        web_mode="observe_only",
        observer_mode=True,
    )

    await connector.observer_event(
        source_event_id="part_observe_001",
        event_type="browser.search",
        state="navigating",
        summary_text="OpenCode started a browser search",
        intent="Search the web for Lumon docs",
        meta={"browser_candidate": True},
    )

    assert runtime.bridge_offers == []
    assert connector.pending_bridge_offer is None
    assert connector.bridge_connector is None
    assert runtime.events[-1]["summary_text"] == "OpenCode started a browser search"
    assert runtime.events[-1]["meta"]["web_mode"] == "observe_only"


@pytest.mark.asyncio
async def test_opencode_observer_event_updates_task_text_and_emits_delegation_offer(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id
    await connector.start_task(
        "OpenCode interactive session",
        demo_mode=False,
        web_mode="delegate_playwright",
        observer_mode=True,
    )

    create_calls: list[str] = []
    bridge_offers: list[dict] = []

    async def fake_emit_bridge_offer(payload: dict) -> None:
        bridge_offers.append(payload)

    runtime.emit_bridge_offer = fake_emit_bridge_offer  # type: ignore[attr-defined]

    class ObserverBridgeConnector:
        adapter_id = "playwright_native"
        capabilities = {
            "supports_pause": False,
            "supports_approval": False,
            "supports_takeover": False,
            "supports_frames": True,
        }

        def __init__(self, bridge_runtime) -> None:
            self.runtime = bridge_runtime

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (demo_mode, web_mode, web_bridge, observer_mode, observed_session_id)
            create_calls.append(task_text)
            await self.runtime.complete_task("completed", "Browser bridge completed")

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(registry, "create_connector", lambda bridge_runtime, adapter_id: ObserverBridgeConnector(bridge_runtime))

    await connector.observer_event(
        source_event_id="part_001",
        event_type="browser.search",
        state="navigating",
        summary_text="OpenCode started a browser search",
        intent="Search the web for Lumon docs",
        task_text="Search the web for Lumon docs",
        meta={"browser_candidate": True},
    )

    assert runtime.task_text == "Search the web for Lumon docs"
    assert create_calls == []
    assert bridge_offers == [
        {
            "intervention_id": bridge_offers[0]["intervention_id"],
            "session_id": "sess_demo_001",
            "adapter_id": "opencode",
            "adapter_run_id": connector.adapter_run_id,
            "web_mode": "delegate_playwright",
            "web_bridge": "playwright_native",
            "source_event_id": "part_001",
            "source_url": None,
            "target_summary": "OpenCode started a browser search",
            "headline": "Live browser view",
            "reason_text": "Lumon can open a visible browser view for this online step.",
            "recommended_action": "open_live_browser_view",
            "summary_text": "OpenCode started a browser search",
            "intent": "Search the web for Lumon docs",
        }
    ]
    assert runtime.events[-1]["summary_text"] == "OpenCode started a browser search"
    await connector.accept_bridge()
    assert create_calls == ["Search the web for Lumon docs"]


@pytest.mark.asyncio
async def test_opencode_observer_auto_delegate_launches_bridge_without_offer(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id
    await connector.start_task(
        "OpenCode interactive session",
        demo_mode=False,
        web_mode="delegate_playwright",
        auto_delegate=True,
        observer_mode=True,
    )

    create_calls: list[str] = []

    class AutoBridgeConnector:
        adapter_id = "playwright_native"
        capabilities = {
            "supports_pause": False,
            "supports_approval": False,
            "supports_takeover": False,
            "supports_frames": True,
        }

        def __init__(self, bridge_runtime) -> None:
            self.runtime = bridge_runtime

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (demo_mode, web_mode, web_bridge, observer_mode, observed_session_id)
            create_calls.append(task_text)
            await self.runtime.complete_task("completed", "Browser bridge completed")

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(registry, "create_connector", lambda bridge_runtime, adapter_id: AutoBridgeConnector(bridge_runtime))

    await connector.observer_event(
        source_event_id="part_auto_001",
        event_type="browser.search",
        state="navigating",
        summary_text="OpenCode started a browser search",
        intent="Search the web for Lumon docs",
        task_text="Search the web for Lumon docs",
        meta={"browser_candidate": True},
    )

    assert create_calls == ["Search the web for Lumon docs"]
    assert runtime.bridge_offers == []
    assert connector.pending_bridge_offer is None


@pytest.mark.asyncio
async def test_opencode_observer_auto_delegate_ignores_non_browser_events(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id
    await connector.start_task(
        "OpenCode interactive session",
        demo_mode=False,
        web_mode="delegate_playwright",
        auto_delegate=True,
        observer_mode=True,
    )

    create_calls: list[str] = []

    class AutoBridgeConnector:
        adapter_id = "playwright_native"
        capabilities = {
            "supports_pause": False,
            "supports_approval": False,
            "supports_takeover": False,
            "supports_frames": True,
        }

        def __init__(self, bridge_runtime) -> None:
            self.runtime = bridge_runtime

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (demo_mode, web_mode, web_bridge, observer_mode, observed_session_id, bridge_context)
            create_calls.append(task_text)

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(registry, "create_connector", lambda bridge_runtime, adapter_id: AutoBridgeConnector(bridge_runtime))

    await connector.observer_event(
        source_event_id="part_auto_ignore_001",
        event_type="tool_start",
        state="reading",
        summary_text="OpenCode is reading local files",
        intent="Inspect local files",
        task_text="Inspect local files",
        meta={"browser_candidate": False, "tool_name": "grep"},
    )

    assert create_calls == []
    assert runtime.bridge_offers == []
    assert connector.pending_bridge_offer is None


@pytest.mark.asyncio
async def test_opencode_bridge_passes_source_url_context(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id
    await connector.start_task(
        "OpenCode interactive session",
        demo_mode=False,
        web_mode="delegate_playwright",
        auto_delegate=True,
        observer_mode=True,
    )

    bridge_contexts: list[dict] = []
    bridge_tasks: list[str] = []

    class AutoBridgeConnector:
        adapter_id = "playwright_native"
        capabilities = {
            "supports_pause": False,
            "supports_approval": False,
            "supports_takeover": False,
            "supports_frames": True,
        }

        def __init__(self, bridge_runtime) -> None:
            self.runtime = bridge_runtime

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (demo_mode, web_mode, web_bridge, observer_mode, observed_session_id)
            bridge_tasks.append(task_text)
            bridge_contexts.append(dict(bridge_context or {}))
            await self.runtime.complete_task("completed", "Browser bridge completed")

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(registry, "create_connector", lambda bridge_runtime, adapter_id: AutoBridgeConnector(bridge_runtime))

    await connector.observer_event(
        source_event_id="part_auto_url_001",
        event_type="navigate",
        state="navigating",
        summary_text="OpenCode fetched Playwright docs",
        intent="Fetch the docs page",
        task_text="Search the web for Playwright docs",
        meta={
            "browser_candidate": True,
            "tool_name": "webfetch",
            "tool_title": "webfetch https://playwright.dev/docs/api/class-page",
            "output_preview": "Fetched https://playwright.dev/docs/api/class-page successfully",
        },
    )

    assert bridge_tasks == ["Open and inspect this exact URL in the browser: https://playwright.dev/docs/api/class-page"]
    assert bridge_contexts[0]["source_url"] == "https://playwright.dev/docs/api/class-page"


@pytest.mark.asyncio
async def test_opencode_observer_completion_waits_for_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id
    await connector.start_task(
        "OpenCode interactive session",
        demo_mode=False,
        web_mode="delegate_playwright",
        observer_mode=True,
    )

    class WaitingBridgeConnector:
        adapter_id = "playwright_native"
        capabilities = {
            "supports_pause": True,
            "supports_approval": True,
            "supports_takeover": True,
            "supports_frames": True,
        }

        def __init__(self, bridge_runtime) -> None:
            self.runtime = bridge_runtime

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (task_text, demo_mode, web_mode, web_bridge, observer_mode, observed_session_id)
            await self.runtime.emit_session_state()

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(registry, "create_connector", lambda bridge_runtime, adapter_id: WaitingBridgeConnector(bridge_runtime))

    await connector.observer_event(
        source_event_id="part_002",
        event_type="browser.search",
        state="navigating",
        summary_text="OpenCode started a browser search",
        intent="Search the web for Lumon docs",
        meta={"browser_candidate": True},
    )
    await connector.accept_bridge()
    await connector.observer_complete("completed", "OpenCode interactive session completed")

    assert runtime.completed is None
    assert connector.pending_observer_completion == ("completed", "OpenCode interactive session completed")

    await connector._on_bridge_complete("completed", "Bridge complete")

    assert runtime.completed == ("completed", "OpenCode interactive session completed")


@pytest.mark.asyncio
async def test_opencode_observer_decline_bridge_suppresses_repeat_offer() -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id
    await connector.start_task(
        "OpenCode interactive session",
        demo_mode=False,
        web_mode="delegate_playwright",
        observer_mode=True,
    )
    bridge_offers: list[dict] = []

    async def fake_emit_bridge_offer(payload: dict) -> None:
        bridge_offers.append(payload)

    runtime.emit_bridge_offer = fake_emit_bridge_offer  # type: ignore[attr-defined]

    await connector.observer_event(
        source_event_id="part_003",
        event_type="browser.search",
        state="navigating",
        summary_text="OpenCode started a browser search",
        intent="Search the web for Lumon docs",
        meta={"browser_candidate": True},
    )
    await connector.decline_bridge()
    await connector.observer_event(
        source_event_id="part_003",
        event_type="browser.search",
        state="navigating",
        summary_text="OpenCode started a browser search",
        intent="Search the web for Lumon docs",
        meta={"browser_candidate": True},
    )

    assert len(bridge_offers) == 1
    reason_codes = [item.get("reason_code") for item in runtime.routing_decisions]
    assert "bridge_offer_declined" in reason_codes
    assert "observer_event_duplicate_ignored" in reason_codes


@pytest.mark.asyncio
async def test_opencode_observer_declined_cooldown_suppresses_offer() -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id
    await connector.start_task(
        "OpenCode interactive session",
        demo_mode=False,
        web_mode="delegate_playwright",
        observer_mode=True,
    )
    bridge_offers: list[dict] = []

    async def fake_emit_bridge_offer(payload: dict) -> None:
        bridge_offers.append(payload)

    runtime.emit_bridge_offer = fake_emit_bridge_offer  # type: ignore[attr-defined]
    connector.declined_bridge_source_ids.add("part_cooldown_001")

    await connector.observer_event(
        source_event_id="part_cooldown_001",
        event_type="browser.search",
        state="navigating",
        summary_text="OpenCode started a browser search",
        intent="Search the web for Lumon docs",
        meta={"browser_candidate": True},
    )

    assert bridge_offers == []
    reason_codes = [item.get("reason_code") for item in runtime.routing_decisions]
    assert "bridge_offer_declined_cooldown" in reason_codes


@pytest.mark.asyncio
async def test_opencode_observer_pending_bridge_offer_suppresses_new_offer() -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id
    await connector.start_task(
        "OpenCode interactive session",
        demo_mode=False,
        web_mode="delegate_playwright",
        observer_mode=True,
    )
    bridge_offers: list[dict] = []

    async def fake_emit_bridge_offer(payload: dict) -> None:
        bridge_offers.append(payload)

    runtime.emit_bridge_offer = fake_emit_bridge_offer  # type: ignore[attr-defined]

    await connector.observer_event(
        source_event_id="part_pending_001",
        event_type="browser.search",
        state="navigating",
        summary_text="OpenCode started a browser search",
        intent="Search the web for Lumon docs",
        meta={"browser_candidate": True},
    )
    await connector.observer_event(
        source_event_id="part_pending_002",
        event_type="browser.search",
        state="navigating",
        summary_text="OpenCode started another browser search",
        intent="Search the web for Lumon release notes",
        meta={"browser_candidate": True},
    )

    assert len(bridge_offers) == 1
    reason_codes = [item.get("reason_code") for item in runtime.routing_decisions]
    assert "bridge_offer_pending" in reason_codes


@pytest.mark.asyncio
async def test_opencode_observer_dedupes_repeated_source_event_ids() -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id
    await connector.start_task(
        "OpenCode interactive session",
        demo_mode=False,
        web_mode="observe_only",
        observer_mode=True,
    )

    await connector.observer_event(
        source_event_id="part_duplicate_001",
        event_type="tool_start",
        state="thinking",
        summary_text="OpenCode inspected the repository",
        intent="Inspect the repository",
    )
    await connector.observer_event(
        source_event_id="part_duplicate_001",
        event_type="tool_start",
        state="thinking",
        summary_text="OpenCode inspected the repository",
        intent="Inspect the repository",
    )

    assert len(runtime.events) == 1
    reason_codes = [item.get("reason_code") for item in runtime.routing_decisions]
    assert "observer_event_duplicate_ignored" in reason_codes


@pytest.mark.asyncio
async def test_opencode_observer_failed_completion_stops_active_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id
    await connector.start_task(
        "OpenCode interactive session",
        demo_mode=False,
        web_mode="delegate_playwright",
        observer_mode=True,
    )
    stop_calls: list[str] = []

    class StopAwareBridgeConnector:
        adapter_id = "playwright_native"
        capabilities = {
            "supports_pause": False,
            "supports_approval": False,
            "supports_takeover": False,
            "supports_frames": True,
        }

        def __init__(self, bridge_runtime) -> None:
            self.runtime = bridge_runtime

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (task_text, demo_mode, web_mode, web_bridge, observer_mode, observed_session_id)
            await self.runtime.emit_session_state()

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...

        async def stop(self) -> None:
            stop_calls.append("stop")

    monkeypatch.setattr(registry, "create_connector", lambda bridge_runtime, adapter_id: StopAwareBridgeConnector(bridge_runtime))

    await connector.observer_event(
        source_event_id="part_fail_001",
        event_type="browser.search",
        state="navigating",
        summary_text="OpenCode started a browser search",
        intent="Search the web for Lumon docs",
        meta={"browser_candidate": True},
    )
    await connector.accept_bridge()
    await connector.observer_complete("failed", "OpenCode interactive session failed")

    assert stop_calls == ["stop"]
    assert runtime.completed == ("failed", "OpenCode interactive session failed")


@pytest.mark.asyncio
async def test_opencode_accept_bridge_emits_accept_and_launch_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id
    await connector.start_task(
        "OpenCode interactive session",
        demo_mode=False,
        web_mode="delegate_playwright",
        observer_mode=True,
    )

    class InstantBridgeConnector:
        adapter_id = "playwright_native"
        capabilities = {
            "supports_pause": False,
            "supports_approval": False,
            "supports_takeover": False,
            "supports_frames": True,
        }

        def __init__(self, bridge_runtime) -> None:
            self.runtime = bridge_runtime

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (task_text, demo_mode, web_mode, web_bridge, observer_mode, observed_session_id, bridge_context)
            await self.runtime.complete_task("completed", "Bridge complete")

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(registry, "create_connector", lambda bridge_runtime, adapter_id: InstantBridgeConnector(bridge_runtime))

    await connector.observer_event(
        source_event_id="part_accept_001",
        event_type="browser.search",
        state="navigating",
        summary_text="OpenCode started a browser search",
        intent="Search the web for Lumon docs",
        meta={"browser_candidate": True},
    )
    await connector.accept_bridge()

    reason_codes = [item.get("reason_code") for item in runtime.routing_decisions]
    assert "bridge_offer_accepted" in reason_codes
    assert "bridge_launch_started" in reason_codes


@pytest.mark.asyncio
async def test_opencode_launch_web_bridge_guard_reason_when_already_running(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id
    connector.selected_web_mode = "delegate_playwright"
    connector.selected_web_bridge = "playwright_native"

    class WaitingBridgeConnector:
        adapter_id = "playwright_native"
        capabilities = {
            "supports_pause": False,
            "supports_approval": False,
            "supports_takeover": False,
            "supports_frames": True,
        }

        def __init__(self, bridge_runtime) -> None:
            self.runtime = bridge_runtime

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (task_text, demo_mode, web_mode, web_bridge, observer_mode, observed_session_id, bridge_context)

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(registry, "create_connector", lambda bridge_runtime, adapter_id: WaitingBridgeConnector(bridge_runtime))

    trigger = {"type": "browser.search", "summary": "Search the web for docs", "id": "part_guard_001"}
    await connector._launch_web_bridge(trigger, "Search the web for docs", demo_mode=False)
    await connector._launch_web_bridge(trigger, "Search the web for docs", demo_mode=False)

    reason_codes = [item.get("reason_code") for item in runtime.routing_decisions]
    assert "bridge_launch_started" in reason_codes
    assert "bridge_launch_guard_already_running" in reason_codes


@pytest.mark.asyncio
async def test_opencode_wait_for_bridge_completion_clears_bridge_state() -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)

    completion = asyncio.Event()
    completion.set()
    connector.bridge_completion = completion
    connector.bridge_result = ("completed", "Bridge complete")
    connector.bridge_connector = object()
    connector.bridge_runtime = object()
    connector.active_web_bridge = "playwright_native"

    result = await connector._wait_for_bridge_completion()

    assert result == ("completed", "Bridge complete")
    assert connector.bridge_result is None
    assert connector.bridge_completion is None
    assert connector.bridge_connector is None
    assert connector.bridge_runtime is None
    assert connector.active_web_bridge is None


@pytest.mark.asyncio
async def test_opencode_ensure_browser_delegate_relaunches_after_command_ready_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    runtime.adapter_run_id = connector.adapter_run_id

    class TimeoutBridgeConnector:
        adapter_id = "playwright_native"
        capabilities = {
            "supports_pause": False,
            "supports_approval": False,
            "supports_takeover": False,
            "supports_frames": True,
        }

        def __init__(self, bridge_runtime) -> None:
            self.runtime = bridge_runtime
            self.command_mode = True
            self.command_ready = asyncio.Event()
            self.stop_calls = 0

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (task_text, demo_mode, web_mode, web_bridge, observer_mode, observed_session_id, bridge_context)

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def start_takeover(self) -> None: ...
        async def end_takeover(self) -> None: ...

        async def stop(self) -> None:
            self.stop_calls += 1

    created_connectors: list[TimeoutBridgeConnector] = []

    def create_timeout_connector(bridge_runtime, adapter_id):
        _ = adapter_id
        instance = TimeoutBridgeConnector(bridge_runtime)
        created_connectors.append(instance)
        return instance

    monkeypatch.setattr(registry, "create_connector", create_timeout_connector)

    async def immediate_timeout(awaitable, timeout):
        _ = (awaitable, timeout)
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "wait_for", immediate_timeout)

    with pytest.raises(RuntimeError, match="did not become ready"):
        await connector.ensure_browser_delegate(observed_session_id="sess_observer_001", task_text="Open docs")

    assert len(created_connectors) == 1
    assert created_connectors[0].stop_calls == 1
    assert connector.bridge_connector is None
    assert connector.bridge_completion is not None
    assert connector.bridge_completion.is_set()

    monkeypatch.undo()
    monkeypatch.setattr(registry, "create_connector", create_timeout_connector)

    class ReadyBridgeConnector(TimeoutBridgeConnector):
        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (task_text, demo_mode, web_mode, web_bridge, observer_mode, observed_session_id, bridge_context)
            self.command_ready.set()

    def create_ready_connector(bridge_runtime, adapter_id):
        _ = adapter_id
        instance = ReadyBridgeConnector(bridge_runtime)
        created_connectors.append(instance)
        return instance

    monkeypatch.setattr(registry, "create_connector", create_ready_connector)

    await connector.ensure_browser_delegate(observed_session_id="sess_observer_001", task_text="Open docs")

    assert len(created_connectors) == 2
    assert connector.bridge_connector is created_connectors[-1]
    assert connector._bridge_is_running()


class _AsyncBytesStream:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = iter(lines)

    def __aiter__(self) -> "_AsyncBytesStream":
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._lines)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeProcess:
    def __init__(self, stdout_lines: list[bytes], stderr_lines: list[bytes], return_code: int = 0) -> None:
        self.stdout = _AsyncBytesStream(stdout_lines)
        self.stderr = _AsyncBytesStream(stderr_lines)
        self._return_code = return_code
        self.returncode: int | None = None

    async def wait(self) -> int:
        self.returncode = self._return_code
        return self._return_code

    def terminate(self) -> None:
        self.returncode = self._return_code


@pytest.mark.asyncio
async def test_opencode_live_stream_emits_browser_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)

    fake_process = _FakeProcess(
        stdout_lines=[
            b'{"type":"browser.search","summary":"Opening wikipedia.org","intent":"Open https://www.wikipedia.org","state":"running"}\n',
            b'{"type":"tool_complete","summary":"Search finished","intent":"Return the result","state":"done"}\n',
        ],
        stderr_lines=[],
        return_code=0,
    )

    async def fake_create_subprocess_exec(*args, **kwargs):  # type: ignore[no-untyped-def]
        return fake_process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await connector._run_live("Open https://www.wikipedia.org")

    assert runtime.events[0]["action_type"] == "navigate"
    assert runtime.events[0]["summary_text"] == "Opening wikipedia.org"
    assert runtime.events[1]["action_type"] == "complete"
    assert runtime.completed == ("completed", "OpenCode adapter run completed")


@pytest.mark.asyncio
async def test_opencode_live_stream_fails_on_structured_error(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)

    fake_process = _FakeProcess(
        stdout_lines=[
            b'{"type":"error","error":{"name":"UnknownError","data":{"message":"Error: Was there a typo in the url or port?"}}}\n',
        ],
        stderr_lines=[],
        return_code=0,
    )

    async def fake_create_subprocess_exec(*args, **kwargs):  # type: ignore[no-untyped-def]
        return fake_process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await connector._run_live("Open https://www.wikipedia.org")

    assert runtime.events[0]["action_type"] == "error"
    assert runtime.events[0]["summary_text"] == "Error: Was there a typo in the url or port?"
    assert runtime.errors[-1]["message"] == "OpenCode run failed: Error: Was there a typo in the url or port?"
    assert runtime.completed == ("failed", "OpenCode adapter run failed")


@pytest.mark.asyncio
async def test_opencode_live_missing_cli_fails_without_demo_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)

    async def fail_if_demo_called(task_text: str) -> None:
        _ = task_text
        raise AssertionError("_run_demo must not be called in live mode when opencode is unavailable")

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(connector, "_run_demo", fail_if_demo_called)

    await connector._run("open wiki", demo_mode=False)

    assert runtime.completed == ("failed", "OpenCode adapter run failed")
    assert runtime.errors
    assert "opencode" in runtime.errors[-1]["message"].lower()
    assert "not found" in runtime.errors[-1]["message"].lower()


@pytest.mark.asyncio
async def test_opencode_live_missing_cli_emits_runtime_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)

    monkeypatch.setattr(shutil, "which", lambda _name: None)

    await connector._run("open wiki", demo_mode=False)

    assert runtime.routing_decisions
    reason_codes = [item.get("reason_code") for item in runtime.routing_decisions]
    assert "opencode_cli_missing_live" in reason_codes


@pytest.mark.asyncio
async def test_opencode_demo_mode_still_uses_demo_when_cli_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)
    demo_called = False

    async def fake_demo(task_text: str) -> None:
        nonlocal demo_called
        _ = task_text
        demo_called = True
        await runtime.complete_task(status="completed", summary_text="demo path executed")

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(connector, "_run_demo", fake_demo)

    await connector._run("open wiki", demo_mode=True)

    assert demo_called is True
    assert runtime.completed == ("completed", "demo path executed")


@pytest.mark.asyncio
async def test_opencode_live_filenotfound_fails_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)

    async def fake_create_subprocess_exec(*args, **kwargs):  # type: ignore[no-untyped-def]
        _ = (args, kwargs)
        raise FileNotFoundError("opencode")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await connector._run_live("open wiki")

    assert runtime.completed == ("failed", "OpenCode adapter run failed")
    assert runtime.errors
    assert "opencode" in runtime.errors[-1]["message"].lower()
    assert "not found" in runtime.errors[-1]["message"].lower()


@pytest.mark.asyncio
async def test_opencode_live_spawn_oserror_fails_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)

    async def fake_create_subprocess_exec(*args, **kwargs):  # type: ignore[no-untyped-def]
        _ = (args, kwargs)
        raise PermissionError("permission denied")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await connector._run_live("open wiki")

    assert runtime.completed == ("failed", "OpenCode adapter run failed")
    assert runtime.errors
    assert "unable to launch opencode" in runtime.errors[-1]["message"].lower()


@pytest.mark.asyncio
async def test_opencode_unexpected_runtime_error_marks_task_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    connector = OpenCodeConnector(runtime)

    async def boom(task_text: str) -> None:
        _ = task_text
        raise RuntimeError("boom")

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/opencode")
    monkeypatch.setattr(connector, "_run_live", boom)

    await connector._run("open wiki", demo_mode=False)

    assert runtime.completed == ("failed", "OpenCode adapter run failed")
    assert "failed" in runtime.transitions
    assert runtime.errors
    assert "runtime failed: boom" in runtime.errors[-1]["message"].lower()
