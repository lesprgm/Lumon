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

    async def fake_launch_browser() -> None:
        return None

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
