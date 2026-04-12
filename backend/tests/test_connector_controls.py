from __future__ import annotations

import asyncio

import pytest

from app.adapters.playwright_native import PlaywrightNativeConnector
from app.protocol.models import BrowserCommandRequest
from app.protocol.enums import SessionState
from app.session.manager import SessionRuntime


@pytest.mark.asyncio
async def test_approve_with_stale_checkpoint_emits_error() -> None:
    runtime = SessionRuntime()
    messages: list[dict] = []
    runtime.broadcast = _capture_broadcast(messages)  # type: ignore[assignment]
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    runtime.state = SessionState.WAITING_FOR_APPROVAL
    connector.latest_checkpoint_id = "chk_real"

    await connector.approve("chk_other")

    assert messages[-1]["type"] == "error"
    assert messages[-1]["payload"]["code"] == "CHECKPOINT_STALE"
    assert messages[-1]["payload"]["checkpoint_id"] == "chk_other"
    assert runtime.state == SessionState.WAITING_FOR_APPROVAL


@pytest.mark.asyncio
async def test_takeover_invalidates_waiting_checkpoint_and_resumes_running() -> None:
    runtime = SessionRuntime()
    messages: list[dict] = []
    runtime.broadcast = _capture_broadcast(messages)  # type: ignore[assignment]
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    runtime.state = SessionState.WAITING_FOR_APPROVAL
    connector.latest_checkpoint_id = "chk_live"
    connector.approval_future = asyncio.get_running_loop().create_future()

    await connector.start_takeover()

    assert runtime.state == SessionState.TAKEOVER
    assert connector.suspended_checkpoint_id == "chk_live"
    assert connector.approval_future.done() is True
    assert connector.approval_future.result() is False

    await connector.end_takeover()

    assert runtime.state == SessionState.RUNNING
    resume_intent = runtime.consume_resume_intent()
    assert resume_intent["pending"] is True
    assert resume_intent["reason"] == "takeover_returned_control"
    assert any(
        message["type"] == "error"
        and message["payload"]["code"] == "CHECKPOINT_STALE"
        and message["payload"]["checkpoint_id"] == "chk_live"
        for message in messages
    )


@pytest.mark.asyncio
async def test_takeover_from_paused_restores_paused_state() -> None:
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    runtime.state = SessionState.PAUSED

    await connector.start_takeover()

    assert runtime.state == SessionState.TAKEOVER

    await connector.end_takeover()

    assert runtime.state == SessionState.PAUSED
    resume_intent = runtime.consume_resume_intent()
    assert resume_intent["pending"] is False


@pytest.mark.asyncio
async def test_direct_takeover_focuses_real_browser_window_and_tracks_url() -> None:
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    runtime.state = SessionState.RUNNING
    connector._headless = False
    connector.takeover_mode = "direct"

    class FakePage:
        def __init__(self) -> None:
            self.url = "https://www.wikipedia.org/wiki/OpenAI"
            self.focus_calls = 0
            self._event_handlers: dict[str, list] = {}

        async def bring_to_front(self) -> None:
            self.focus_calls += 1

        async def screenshot(self, *, type: str = "png", **kwargs):
            _ = type
            _ = kwargs
            return b"png"

        def on(self, event: str, handler) -> None:
            self._event_handlers.setdefault(event, []).append(handler)

    fake_page = FakePage()
    connector.page = fake_page  # type: ignore[assignment]

    await connector.start_takeover()

    assert runtime.state == SessionState.TAKEOVER
    assert fake_page.focus_calls == 1
    assert connector.takeover_url == "https://www.wikipedia.org/wiki/OpenAI"

    await connector.end_takeover()

    assert runtime.state == SessionState.RUNNING


@pytest.mark.asyncio
async def test_status_metadata_reports_takeover_mode_and_url() -> None:
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    connector.takeover_mode = "direct"
    connector.takeover_url = "https://www.wikipedia.org/wiki/OpenAI"
    connector._capture_command_frame = _capture_command_frame_true  # type: ignore[assignment]
    connector._browser_status_context = _status_context_factory(
        "https://www.wikipedia.org/wiki/OpenAI",
        "OpenAI - Wikipedia",
        "www.wikipedia.org",
    )  # type: ignore[assignment]

    result = await connector._execute_browser_command(
        BrowserCommandRequest(
            project_directory="/repo",
            observed_session_id="sess_observed_1",
            command_id="cmd_status_takeover",
            command="status",
        )
    )

    assert result["status"] == "success"
    assert result["meta"]["takeover_mode"] == "direct"
    assert result["meta"]["takeover_url"] == "https://www.wikipedia.org/wiki/OpenAI"


@pytest.mark.asyncio
async def test_direct_takeover_hud_esc_invokes_end_takeover() -> None:
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    runtime.state = SessionState.RUNNING
    connector._headless = False
    connector.takeover_mode = "direct"

    class FakePage:
        def __init__(self) -> None:
            self.url = "https://www.wikipedia.org/wiki/OpenAI"
            self.scripts: list[str] = []
            self.bound_names: list[str] = []
            self.focus_calls = 0

        async def expose_binding(self, name: str, callback) -> None:
            _ = callback
            self.bound_names.append(name)

        async def evaluate(self, script: str) -> None:
            self.scripts.append(script)

        async def bring_to_front(self) -> None:
            self.focus_calls += 1

    fake_page = FakePage()
    connector.page = fake_page  # type: ignore[assignment]

    await connector.start_takeover()

    assert runtime.state == SessionState.TAKEOVER
    assert "__lumonEndTakeover" in fake_page.bound_names
    assert any("__lumonTakeoverEscHandler" in script for script in fake_page.scripts)

    await connector.end_takeover()

    assert runtime.state == SessionState.RUNNING


@pytest.mark.asyncio
async def test_direct_takeover_hud_refreshes_only_for_main_frame_navigation() -> None:
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    connector._headless = False
    connector.takeover_mode = "direct"
    runtime.state = SessionState.TAKEOVER

    refresh_calls: list[str] = []

    async def fake_install_hud() -> None:
        refresh_calls.append("install")

    connector._install_direct_takeover_hud = fake_install_hud  # type: ignore[assignment]

    class FakePage:
        def __init__(self) -> None:
            self.url = "https://www.wikipedia.org/wiki/OpenAI"
            self.main_frame = object()

    page = FakePage()
    connector.page = page  # type: ignore[assignment]

    class ChildFrame:
        def parent_frame(self):
            return object()

    await connector._handle_page_frame_navigated(page, ChildFrame())  # type: ignore[arg-type]
    assert refresh_calls == []

    await connector._handle_page_frame_navigated(page, page.main_frame)  # type: ignore[arg-type]
    assert refresh_calls == ["install"]


@pytest.mark.asyncio
async def test_start_takeover_honors_mode_preference_when_supported() -> None:
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    runtime.state = SessionState.RUNNING
    connector._headless = False
    connector.takeover_mode = "remote"

    class FakePage:
        def __init__(self) -> None:
            self.url = "https://www.wikipedia.org/wiki/OpenAI"
            self.focus_calls = 0

        async def expose_binding(self, name: str, callback) -> None:
            _ = (name, callback)

        async def evaluate(self, script: str) -> None:
            _ = script

        async def bring_to_front(self) -> None:
            self.focus_calls += 1

    connector.page = FakePage()  # type: ignore[assignment]

    await connector.start_takeover(mode_preference="direct")

    assert runtime.state == SessionState.TAKEOVER
    assert connector.takeover_mode == "direct"


@pytest.mark.asyncio
async def test_start_takeover_direct_mode_relaunches_headed_runtime_when_needed() -> (
    None
):
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    runtime.state = SessionState.RUNNING
    connector._headless = True
    connector.takeover_mode = "remote"
    connector.current_page_url = "https://www.wikipedia.org/wiki/OpenAI"

    launch_modes: list[bool] = []
    launch_urls: list[str | None] = []
    shutdown_count = 0

    class FakePage:
        def __init__(self) -> None:
            self.url = "about:blank"
            self.gotos: list[str] = []
            self.focus_calls = 0

        async def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
            _ = wait_until
            self.url = url
            self.gotos.append(url)

        async def bring_to_front(self) -> None:
            self.focus_calls += 1

        async def expose_binding(self, name: str, callback) -> None:
            _ = (name, callback)

        async def evaluate(self, script: str) -> None:
            _ = script

    async def fake_shutdown_browser(*, stop_playwright: bool = True) -> None:
        nonlocal shutdown_count
        _ = stop_playwright
        shutdown_count += 1
        connector.playwright = None
        connector.browser = None
        connector.context = None
        connector.page = None
        connector.action_layer = None

    async def fake_launch_browser(*, headless_override: bool | None = None) -> None:
        launch_modes.append(bool(headless_override))
        connector._headless = bool(headless_override)
        connector.playwright = object()  # type: ignore[assignment]
        connector.browser = object()  # type: ignore[assignment]
        connector.context = object()  # type: ignore[assignment]
        connector.page = FakePage()  # type: ignore[assignment]
        connector.action_layer = object()  # type: ignore[assignment]
        connector.takeover_mode = "remote" if connector._headless else "direct"
        connector.capabilities["supports_direct_takeover"] = not connector._headless

    async def fake_start_stream_transport() -> None:
        if connector.page is None:
            launch_urls.append(None)
            return
        launch_urls.append(str(getattr(connector.page, "url", None)))

    connector._close_browser_runtime = fake_shutdown_browser  # type: ignore[assignment]
    connector._shutdown_browser = (  # type: ignore[assignment]
        lambda: fake_shutdown_browser(stop_playwright=True)
    )
    connector._launch_browser = fake_launch_browser  # type: ignore[assignment]
    connector._start_stream_transport = fake_start_stream_transport  # type: ignore[assignment]

    connector.playwright = object()  # type: ignore[assignment]
    connector.browser = object()  # type: ignore[assignment]
    connector.context = object()  # type: ignore[assignment]
    connector.page = FakePage()  # type: ignore[assignment]
    connector.action_layer = object()  # type: ignore[assignment]

    await connector.start_takeover(mode_preference="direct")

    assert runtime.state == SessionState.TAKEOVER
    assert connector.takeover_mode == "direct"
    assert launch_modes == [False]
    assert shutdown_count == 1
    assert connector.page is not None
    assert (
        getattr(connector.page, "gotos", [])[0]
        == "https://www.wikipedia.org/wiki/OpenAI"
    )
    assert launch_urls == []


@pytest.mark.asyncio
async def test_end_takeover_direct_mode_relaunches_headless_runtime_and_restores_url() -> (
    None
):
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    runtime.state = SessionState.TAKEOVER
    connector._headless = False
    connector.takeover_mode = "direct"
    connector.takeover_url = "https://www.wikipedia.org/wiki/OpenAI"
    connector.current_page_url = "https://www.wikipedia.org/wiki/OpenAI"
    connector.resume_state_after_takeover = SessionState.RUNNING

    launch_modes: list[bool] = []
    launch_urls: list[str | None] = []
    shutdown_count = 0
    resume_reasons: list[str] = []

    class FakePage:
        def __init__(self) -> None:
            self.url = "about:blank"
            self.gotos: list[str] = []

        async def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
            _ = wait_until
            self.url = url
            self.gotos.append(url)

        async def expose_binding(self, name: str, callback) -> None:
            _ = (name, callback)

        async def evaluate(self, script: str):
            _ = script
            return {
                "innerWidth": 1280,
                "innerHeight": 800,
                "outerWidth": 1280,
                "outerHeight": 800,
                "dpr": 2,
                "scale": 1,
                "viewportWidth": 1280,
                "viewportHeight": 800,
                "scrollX": 0,
                "scrollY": 0,
            }

        async def screenshot(self, *, type: str = "png", **kwargs):
            _ = type
            _ = kwargs
            return b"png"

    async def fake_close_browser_runtime(*, stop_playwright: bool = False) -> None:
        _ = stop_playwright
        nonlocal shutdown_count
        shutdown_count += 1
        connector.playwright = None
        connector.browser = None
        connector.context = None
        connector.page = None
        connector.action_layer = None

    async def fake_launch_browser(*, headless_override: bool | None = None) -> None:
        launch_modes.append(bool(headless_override))
        connector._headless = bool(headless_override)
        connector.playwright = object()  # type: ignore[assignment]
        connector.browser = object()  # type: ignore[assignment]
        connector.context = object()  # type: ignore[assignment]
        connector.page = FakePage()  # type: ignore[assignment]
        connector.action_layer = object()  # type: ignore[assignment]
        connector.takeover_mode = "remote" if connector._headless else "direct"
        connector.capabilities["supports_direct_takeover"] = True

    async def fake_start_stream_transport() -> None:
        if connector.page is None:
            launch_urls.append(None)
            return
        launch_urls.append(str(getattr(connector.page, "url", None)))

    async def fake_remove_hud() -> None:
        return None

    async def fake_sync_page_version(*, force: bool) -> bool:
        _ = force
        connector.current_page_url = str(getattr(connector.page, "url", "") or "")
        return False

    def fake_request_resume_intent(*, reason: str) -> None:
        resume_reasons.append(reason)

    connector._close_browser_runtime = fake_close_browser_runtime  # type: ignore[assignment]
    connector._launch_browser = fake_launch_browser  # type: ignore[assignment]
    connector._start_stream_transport = fake_start_stream_transport  # type: ignore[assignment]
    connector._emit_snapshot_frame = _async_true  # type: ignore[assignment]
    connector._remove_direct_takeover_hud = fake_remove_hud  # type: ignore[assignment]
    connector._sync_page_version = fake_sync_page_version  # type: ignore[assignment]
    runtime.request_resume_intent = fake_request_resume_intent  # type: ignore[assignment]

    await connector.end_takeover()

    assert runtime.state == SessionState.RUNNING
    assert connector._headless is True
    assert connector.takeover_mode == "remote"
    assert shutdown_count == 1
    assert launch_modes == [True]
    assert launch_urls == ["about:blank"]
    assert connector.page is not None
    assert (
        getattr(connector.page, "gotos", [])[0]
        == "https://www.wikipedia.org/wiki/OpenAI"
    )
    assert resume_reasons == ["takeover_returned_control"]


@pytest.mark.asyncio
async def test_end_takeover_remote_emits_context_and_snapshot_when_feed_is_empty() -> (
    None
):
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    runtime.state = SessionState.TAKEOVER
    connector._headless = True
    connector.takeover_mode = "remote"
    connector.current_page_url = "https://en.wikipedia.org/wiki/AI_agent"
    connector.resume_state_after_takeover = SessionState.RUNNING

    snapshot_calls: list[bool] = []

    class FakePage:
        url = "https://en.wikipedia.org/wiki/AI_agent"

    async def fake_emit_snapshot_frame(*, command_snapshot: bool = False) -> bool:
        snapshot_calls.append(command_snapshot)
        return True

    async def fake_sync_page_version(*, force: bool) -> bool:
        _ = force
        connector.current_page_url = "https://en.wikipedia.org/wiki/AI_agent"
        return False

    connector.page = FakePage()  # type: ignore[assignment]
    connector._emit_snapshot_frame = fake_emit_snapshot_frame  # type: ignore[assignment]
    connector._sync_page_version = fake_sync_page_version  # type: ignore[assignment]

    assert runtime.latest_frame_payload is None

    await connector.end_takeover()

    assert runtime.state == SessionState.RUNNING
    assert snapshot_calls == [False]


@pytest.mark.asyncio
async def test_resume_from_invalid_state_emits_error() -> None:
    runtime = SessionRuntime()
    messages: list[dict] = []
    runtime.broadcast = _capture_broadcast(messages)  # type: ignore[assignment]
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    runtime.state = SessionState.RUNNING

    await connector.resume()

    assert messages[-1]["type"] == "session_state"
    assert messages[-1]["payload"]["state"] == "running"

    runtime.state = SessionState.IDLE
    await connector.resume()

    assert messages[-1]["type"] == "error"
    assert messages[-1]["payload"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_live_bridge_flow_opens_the_source_url_instead_of_searching() -> None:
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    messages: list[dict] = []
    runtime.broadcast = _capture_broadcast(messages)  # type: ignore[assignment]
    connector.bridge_context = {"source_url": "https://www.wikipedia.org"}

    calls: list[tuple] = []

    class FakeActionLayer:
        async def navigate(
            self,
            url: str,
            *,
            html_content: str | None = None,
            summary_text: str,
            intent: str,
            fast: bool = False,
        ) -> None:
            calls.append(("navigate", url, summary_text, intent, html_content, fast))

    connector.action_layer = FakeActionLayer()  # type: ignore[assignment]
    connector._emit_snapshot_frame = _fake_emit_snapshot_frame  # type: ignore[assignment]

    async def no_wait() -> None:
        return None

    connector._wait_for_run_permission = no_wait  # type: ignore[assignment]

    await connector._run_live_bridge_flow("Open wikipedia.org")

    assert calls[0][:4] == (
        "navigate",
        "https://www.wikipedia.org",
        "Opening https://www.wikipedia.org",
        "Open https://www.wikipedia.org in the browser",
    )
    assert messages[-1]["type"] == "task_result"
    assert (
        messages[-1]["payload"]["summary_text"]
        == "Opened https://www.wikipedia.org in the live browser view"
    )


@pytest.mark.asyncio
async def test_default_webrtc_primary_uses_cdp_screencast_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    connector.page = object()  # type: ignore[assignment]
    connector.cdp_session = object()  # type: ignore[assignment]
    connector.stream_mode = "live"
    connector.webrtc_primary = True

    calls: list[str] = []

    class FakeStreamer:
        def __init__(self, cdp_session, emit_frame, profile_config=None) -> None:
            assert cdp_session is connector.cdp_session
            assert callable(emit_frame)
            _ = profile_config

        async def start(self) -> None:
            calls.append("start")

        async def stop(self) -> None:
            calls.append("stop")

    async def fake_stop_webrtc_loop() -> None:
        calls.append("stop-webrtc-loop")

    async def fake_watch_live_stream_health() -> None:
        calls.append("watch-health")

    monkeypatch.setattr(
        "app.adapters.playwright_native.CDPScreencastStreamer", FakeStreamer
    )
    connector._stop_webrtc_capture_loop = fake_stop_webrtc_loop  # type: ignore[assignment]
    connector._watch_live_stream_health = fake_watch_live_stream_health  # type: ignore[assignment]

    await connector._start_stream_transport()

    assert calls[:2] == ["stop-webrtc-loop", "start"]
    assert connector.live_streamer is not None


@pytest.mark.asyncio
async def test_command_mode_type_redacts_sensitive_values() -> None:
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    connector.page_version = 1
    connector.current_page_url = "https://example.com/login"
    connector.element_refs["el_1"] = {
        "element_id": "el_1",
        "label": "Password",
        "role": "input",
        "selector": "#password",
        "typeable": True,
        "clickable": True,
        "input_type": "password",
        "page_version": 1,
        "bbox": None,
        "sensitive": True,
    }

    class FakeActionLayer:
        async def type_text(
            self,
            selector: str,
            value: str,
            summary_text: str,
            intent: str,
            masked: bool = True,
        ) -> dict[str, str]:
            _ = (selector, value, summary_text, intent, masked)
            return {"value_after": "hunter2"}

    connector.action_layer = FakeActionLayer()  # type: ignore[assignment]
    connector._sync_page_version = _async_noop_bool  # type: ignore[assignment]
    connector._emit_snapshot_frame_with_retry = _async_true  # type: ignore[assignment]
    connector._capture_live_keyframe = _async_none  # type: ignore[assignment]
    connector._browser_status_context = _status_context_factory(
        "https://example.com/login", "Login", "example.com"
    )  # type: ignore[assignment]

    result = await connector._execute_browser_command(
        BrowserCommandRequest(
            project_directory="/repo",
            observed_session_id="sess_observed_1",
            command_id="cmd_sensitive",
            command="type",
            element_id="el_1",
            text="hunter2",
        ),
        approval_granted=True,
    )

    assert result["status"] == "success"
    assert result["evidence"]["value_after"] is None
    assert result["evidence"]["value_redacted"] is True


@pytest.mark.asyncio
async def test_command_mode_type_uses_friendly_selector_labels() -> None:
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    connector.page_version = 1
    connector.current_page_url = "https://www.wikipedia.org"

    calls: list[tuple[str, str, str, str, bool]] = []

    class FakeActionLayer:
        async def type_text(
            self,
            selector: str,
            value: str,
            summary_text: str,
            intent: str,
            masked: bool = True,
        ) -> dict[str, str]:
            calls.append((selector, value, summary_text, intent, masked))
            return {"value_after": value}

    connector.action_layer = FakeActionLayer()  # type: ignore[assignment]
    connector._sync_page_version = _async_noop_bool  # type: ignore[assignment]
    connector._emit_snapshot_frame_with_retry = _async_true  # type: ignore[assignment]
    connector._capture_live_keyframe = _async_none  # type: ignore[assignment]
    connector._browser_status_context = _status_context_factory(
        "https://www.wikipedia.org", "Wikipedia", "www.wikipedia.org"
    )  # type: ignore[assignment]

    result = await connector._execute_browser_command(
        BrowserCommandRequest(
            project_directory="/repo",
            observed_session_id="sess_observed_1",
            command_id="cmd_type_search",
            command="type",
            selector="input[name='search']",
            text="OpenAI",
        ),
        approval_granted=True,
    )

    assert calls == [
        (
            "input[name='search']",
            "OpenAI",
            "Typing into search box",
            "Type into search box",
            False,
        )
    ]
    assert result["status"] == "success"
    assert result["summary_text"] == "Typed into search box."


@pytest.mark.asyncio
async def test_command_mode_approve_replays_pending_request_without_reentering_public_command_api() -> (
    None
):
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    connector.command_mode = True
    runtime.state = SessionState.WAITING_FOR_APPROVAL

    request = {
        "project_directory": "/repo",
        "observed_session_id": "sess_observed_1",
        "command_id": "cmd_resume",
        "command": "click",
        "element_id": "el_resume",
    }
    connector.pending_browser_commands["click:cmd_resume"] = {
        "state": "awaiting_approval",
        "request": request,
        "result": {"command_id": "cmd_resume"},
        "checkpoint_id": "chk_resume",
    }

    calls: list[str] = []

    async def fake_locked_execute(payload, *, command_key: str, approval_granted: bool):
        calls.append(
            f"{payload.command}:{payload.command_id}:{command_key}:{approval_granted}"
        )
        return {"command_id": "cmd_resume", "command": "click", "status": "success"}

    connector._execute_browser_command_locked = fake_locked_execute  # type: ignore[assignment]

    result = await connector.approve("chk_resume")

    assert calls == ["click:cmd_resume:click:cmd_resume:True"]
    assert result == {
        "command_id": "cmd_resume",
        "command": "click",
        "status": "success",
    }
    assert runtime.state == SessionState.RUNNING
    assert connector.pending_browser_commands == {}


@pytest.mark.asyncio
async def test_command_mode_returns_failed_result_when_delegate_is_gone() -> None:
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    connector.command_mode = True
    connector.command_ready.set()
    connector.command_delegate_error = "delegate_crashed"

    result = await connector.execute_browser_command(
        BrowserCommandRequest(
            project_directory="/repo",
            observed_session_id="sess_observed_1",
            command_id="cmd_dead",
            command="status",
        )
    )

    assert result["status"] == "failed"
    assert result["reason"] == "delegate_crashed"


@pytest.mark.asyncio
async def test_command_cache_keys_include_command_name() -> None:
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    connector.command_mode = True
    connector.command_ready.set()

    calls: list[tuple[str, str]] = []

    async def fake_execute(payload, *, approval_granted=False):
        calls.append((payload.command, payload.command_id))
        return {
            "command_id": payload.command_id,
            "command": payload.command,
            "status": "success",
            "summary_text": f"{payload.command} ok",
            "session_id": runtime.session_id,
            "meta": {},
            "actionable_elements": [],
        }

    connector._bridge_is_alive = lambda: True  # type: ignore[assignment]
    connector._maybe_switch_to_foreground_page = _async_none  # type: ignore[assignment]
    connector._execute_browser_command = fake_execute  # type: ignore[assignment]

    begin_result = await connector.execute_browser_command(
        BrowserCommandRequest(
            project_directory="/repo",
            observed_session_id="sess_observed_1",
            command_id="cmd_shared",
            command="begin_task",
            task_text="Open Wikipedia",
        )
    )
    open_result = await connector.execute_browser_command(
        BrowserCommandRequest(
            project_directory="/repo",
            observed_session_id="sess_observed_1",
            command_id="cmd_shared",
            command="open",
            url="https://www.wikipedia.org",
        )
    )

    assert begin_result["command"] == "begin_task"
    assert open_result["command"] == "open"
    assert calls == [("begin_task", "cmd_shared"), ("open", "cmd_shared")]


@pytest.mark.asyncio
async def test_command_delegate_marks_ready_before_stream_transport_finishes() -> None:
    runtime = SessionRuntime()
    runtime.state = SessionState.STARTING
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    connector.command_mode = True

    transport_started = asyncio.Event()
    allow_transport_finish = asyncio.Event()

    async def fake_launch_browser(*, headless_override: bool | None = None) -> None:
        _ = headless_override
        connector.playwright = object()  # type: ignore[assignment]
        connector.browser = object()  # type: ignore[assignment]
        connector.context = object()  # type: ignore[assignment]
        connector.page = object()  # type: ignore[assignment]
        connector.action_layer = object()  # type: ignore[assignment]

    async def fake_start_stream_transport() -> None:
        transport_started.set()
        await allow_transport_finish.wait()

    async def fake_shutdown_browser() -> None:
        return None

    connector._launch_browser = fake_launch_browser  # type: ignore[assignment]
    connector._start_stream_transport = fake_start_stream_transport  # type: ignore[assignment]
    connector._shutdown_browser = fake_shutdown_browser  # type: ignore[assignment]

    delegate_task = asyncio.create_task(connector._run_command_delegate())

    await asyncio.wait_for(transport_started.wait(), timeout=0.2)
    await asyncio.wait_for(connector.command_ready.wait(), timeout=0.2)
    assert runtime.state == SessionState.RUNNING

    allow_transport_finish.set()
    connector.command_stop_event.set()
    await asyncio.wait_for(delegate_task, timeout=0.2)


@pytest.mark.asyncio
async def test_command_delegate_launches_headless_for_lumon_first_startup() -> None:
    runtime = SessionRuntime()
    runtime.state = SessionState.STARTING
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    connector.command_mode = True

    launch_modes: list[bool] = []
    transport_started = asyncio.Event()

    async def fake_launch_browser(*, headless_override: bool | None = None) -> None:
        launch_modes.append(bool(headless_override))
        connector._headless = bool(headless_override)
        connector.browser = object()  # type: ignore[assignment]
        connector.context = object()  # type: ignore[assignment]
        connector.page = object()  # type: ignore[assignment]
        connector.action_layer = object()  # type: ignore[assignment]

    async def fake_start_stream_transport() -> None:
        transport_started.set()

    async def fake_shutdown_browser() -> None:
        return None

    connector._launch_browser = fake_launch_browser  # type: ignore[assignment]
    connector._start_stream_transport = fake_start_stream_transport  # type: ignore[assignment]
    connector._shutdown_browser = fake_shutdown_browser  # type: ignore[assignment]

    delegate_task = asyncio.create_task(connector._run_command_delegate())
    await asyncio.wait_for(connector.command_ready.wait(), timeout=0.2)
    await asyncio.wait_for(transport_started.wait(), timeout=0.2)

    connector.command_stop_event.set()
    await asyncio.wait_for(delegate_task, timeout=0.2)

    assert launch_modes == [True]


@pytest.mark.asyncio
async def test_capture_command_frame_accepts_fresh_generation_even_when_snapshot_retries_fail() -> (
    None
):
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector

    async def fake_emit_snapshot_frame_with_retry(
        *, attempts=5, delay_seconds=0.2, command_snapshot=False
    ) -> bool:
        _ = (attempts, delay_seconds, command_snapshot)

        async def bump_generation() -> None:
            await asyncio.sleep(0.02)
            runtime._latest_command_frame_generation += 1

        asyncio.create_task(bump_generation())
        return False

    connector._emit_snapshot_frame_with_retry = fake_emit_snapshot_frame_with_retry  # type: ignore[assignment]
    connector._capture_live_keyframe = _async_none  # type: ignore[assignment]

    frame_emitted, keyframe_path = await connector._capture_command_frame(
        "command_open"
    )

    assert frame_emitted is True
    assert keyframe_path is None


@pytest.mark.asyncio
async def test_begin_task_followed_by_same_url_open_skips_duplicate_navigation() -> (
    None
):
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector

    navigations: list[str] = []

    class FakeActionLayer:
        async def navigate(
            self,
            url: str,
            *,
            html_content: str | None = None,
            summary_text: str,
            intent: str,
            fast: bool = False,
        ) -> None:
            _ = (html_content, summary_text, intent, fast)
            navigations.append(url)

        async def _emit_event(self, **kwargs):
            pass

    async def fake_sync_page_version(*, force: bool) -> bool:
        _ = force
        connector.current_page_url = "https://www.wikipedia.org"
        connector.page_version = 1
        return False

    connector.action_layer = FakeActionLayer()  # type: ignore[assignment]
    connector._sync_page_version = fake_sync_page_version  # type: ignore[assignment]
    connector._emit_snapshot_frame_with_retry = _async_true  # type: ignore[assignment]
    connector._capture_live_keyframe = _async_none  # type: ignore[assignment]
    connector._browser_status_context = _status_context_factory(
        "https://www.wikipedia.org", "Wikipedia", "www.wikipedia.org"
    )  # type: ignore[assignment]

    await connector._execute_browser_command(
        BrowserCommandRequest(
            project_directory="/repo",
            observed_session_id="sess_observed_1",
            command_id="cmd_begin",
            command="begin_task",
            task_text="Open https://www.wikipedia.org and inspect the page.",
        )
    )
    await connector._execute_browser_command(
        BrowserCommandRequest(
            project_directory="/repo",
            observed_session_id="sess_observed_1",
            command_id="cmd_open",
            command="open",
            url="https://www.wikipedia.org",
        )
    )

    assert navigations == ["https://www.wikipedia.org"]


@pytest.mark.asyncio
async def test_begin_task_followed_by_same_url_open_skips_duplicate_navigation_with_canonical_url() -> (
    None
):
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector

    navigations: list[str] = []

    class FakeActionLayer:
        async def navigate(
            self,
            url: str,
            *,
            html_content: str | None = None,
            summary_text: str,
            intent: str,
            fast: bool = False,
        ) -> None:
            _ = (html_content, summary_text, intent, fast)
            navigations.append(url)

        async def _emit_event(self, **kwargs):
            pass

    async def fake_sync_page_version(*, force: bool) -> bool:
        _ = force
        connector.current_page_url = "https://www.wikipedia.org/"
        connector.page_version = 1
        return False

    connector.action_layer = FakeActionLayer()  # type: ignore[assignment]
    connector._sync_page_version = fake_sync_page_version  # type: ignore[assignment]
    connector._emit_snapshot_frame_with_retry = _async_true  # type: ignore[assignment]
    connector._capture_live_keyframe = _async_none  # type: ignore[assignment]
    connector._browser_status_context = _status_context_factory(
        "https://www.wikipedia.org/", "Wikipedia", "www.wikipedia.org"
    )  # type: ignore[assignment]

    await connector._execute_browser_command(
        BrowserCommandRequest(
            project_directory="/repo",
            observed_session_id="sess_observed_1",
            command_id="cmd_begin_canonical",
            command="begin_task",
            task_text="Open https://www.wikipedia.org and inspect the page.",
        )
    )
    await connector._execute_browser_command(
        BrowserCommandRequest(
            project_directory="/repo",
            observed_session_id="sess_observed_1",
            command_id="cmd_open_canonical",
            command="open",
            url="https://www.wikipedia.org",
        )
    )

    assert navigations == ["https://www.wikipedia.org"]


@pytest.mark.asyncio
async def test_begin_task_uses_explicit_url_when_task_text_has_no_url() -> None:
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector

    navigations: list[str] = []

    class FakeActionLayer:
        async def navigate(
            self,
            url: str,
            *,
            html_content: str | None = None,
            summary_text: str,
            intent: str,
            fast: bool = False,
        ) -> None:
            _ = (html_content, summary_text, intent, fast)
            navigations.append(url)

        async def _emit_event(self, **kwargs):
            pass

    async def fake_sync_page_version(*, force: bool) -> bool:
        _ = force
        connector.current_page_url = "http://127.0.0.1:8000/__lumon_harness__/search"
        connector.page_version = 1
        return False

    connector.action_layer = FakeActionLayer()  # type: ignore[assignment]
    connector._sync_page_version = fake_sync_page_version  # type: ignore[assignment]
    connector._capture_command_frame = _capture_command_frame_true  # type: ignore[assignment]
    connector._browser_status_context = _status_context_factory(
        "http://127.0.0.1:8000/__lumon_harness__/search",
        "Local search",
        "127.0.0.1",
    )  # type: ignore[assignment]

    result = await connector._execute_browser_command(
        BrowserCommandRequest(
            project_directory="/repo",
            observed_session_id="sess_observed_1",
            command_id="cmd_begin_local",
            command="begin_task",
            task_text="Open the local trace page and inspect it.",
            url="http://127.0.0.1:8000/__lumon_harness__/search",
        )
    )

    assert result["status"] == "success"
    assert navigations == ["http://127.0.0.1:8000/__lumon_harness__/search"]


@pytest.mark.asyncio
async def test_stop_webrtc_capture_loop_cancels_live_stream_health_task() -> None:
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector

    async def wait_forever() -> None:
        await asyncio.sleep(60)

    connector.live_stream_health_task = asyncio.create_task(wait_forever())

    await connector._stop_webrtc_capture_loop()

    assert connector.live_stream_health_task is None


@pytest.mark.asyncio
async def test_stale_target_fails_after_navigation_changes_page_version() -> None:
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    connector.page_version = 2
    connector.current_page_url = "https://example.com/next"
    connector.element_refs["el_old"] = {
        "element_id": "el_old",
        "label": "Old search box",
        "role": "input",
        "selector": "#search",
        "typeable": True,
        "clickable": True,
        "input_type": "text",
        "page_version": 1,
        "bbox": None,
        "sensitive": False,
    }

    result = await connector._execute_browser_command(
        BrowserCommandRequest(
            project_directory="/repo",
            observed_session_id="sess_observed_1",
            command_id="cmd_stale",
            command="click",
            element_id="el_old",
        )
    )

    assert result["status"] == "failed"
    assert result["reason"] == "stale_target"


@pytest.mark.asyncio
async def test_browser_action_timeout_is_not_reported_as_delegate_crash() -> None:
    runtime = SessionRuntime()
    connector = PlaywrightNativeConnector(runtime)
    runtime._connector = connector
    connector.command_mode = True
    connector.command_ready.set()
    connector.current_page_url = "https://en.wikipedia.org/wiki/Main_Page"
    connector.page_version = 3

    connector._bridge_is_alive = lambda: True  # type: ignore[assignment]
    connector._maybe_switch_to_foreground_page = _async_none  # type: ignore[assignment]

    async def fake_execute(_payload, *, approval_granted=False):
        _ = approval_granted
        raise RuntimeError(
            'Locator.bounding_box: Timeout 30000ms exceeded. Call log: waiting for locator("#searchInput").first'
        )

    connector._execute_browser_command = fake_execute  # type: ignore[assignment]

    result = await connector.execute_browser_command(
        BrowserCommandRequest(
            project_directory="/repo",
            observed_session_id="sess_observed_1",
            command_id="cmd_timeout",
            command="type",
            element_id="el_1",
            text="OpenAI",
        )
    )

    assert result["status"] == "failed"
    assert result["reason"] == "target_resolution_timeout"
    assert connector.command_delegate_error is None


async def _fake_emit_snapshot_frame() -> bool:
    return True


async def _async_true(*_args, **_kwargs) -> bool:
    return True


async def _capture_command_frame_true(*_args, **_kwargs) -> tuple[bool, None]:
    return True, None


async def _async_none(*_args, **_kwargs):
    return None


async def _async_noop_bool(*_args, **_kwargs) -> bool:
    return False


def _status_context_factory(url: str, title: str, domain: str):
    async def _status_context():
        return {
            "url": url,
            "title": title,
            "domain": domain,
            "environment_type": "external",
            "active_element": None,
            "scroll_y": 0,
        }

    return _status_context


def _capture_broadcast(messages: list[dict]):
    async def _broadcast(message: dict) -> None:
        messages.append(message)

    return _broadcast
