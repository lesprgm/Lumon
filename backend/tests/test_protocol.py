import pytest

from app.fixtures.build_fixtures import agent_event, main as build_fixtures
from app.protocol.models import BrowserCommandRequest, BrowserCommandResult
from app.protocol.enums import ErrorCode, SessionState
from app.protocol.validation import (
    ProtocolValidationError,
    validate_client_message,
    validate_server_message,
)
from app.session.state_machine import can_transition, interaction_mode_for_state


def test_validate_client_message_start_task() -> None:
    message = validate_client_message(
        {
            "type": "start_task",
            "payload": {
                "task_text": "Find a hotel",
                "demo_mode": True,
                "adapter_id": "opencode",
            },
        }
    )
    assert message["type"] == "start_task"
    assert message["payload"]["task_text"] == "Find a hotel"
    assert message["payload"]["adapter_id"] == "opencode"


def test_validate_client_message_attach_observer() -> None:
    message = validate_client_message(
        {
            "type": "attach_observer",
            "payload": {
                "task_text": "OpenCode interactive session",
                "adapter_id": "opencode",
                "web_mode": "observe_only",
                "auto_delegate": True,
                "web_bridge": "playwright_native",
                "observed_session_id": "ses_attach_001",
            },
        }
    )
    assert message["type"] == "attach_observer"
    assert message["payload"]["auto_delegate"] is True
    assert message["payload"]["observed_session_id"] == "ses_attach_001"


def test_validate_client_message_ingest_optional_trace() -> None:
    message = validate_client_message(
        {
            "type": "ingest_optional_trace",
            "payload": {
                "provider": "langchain",
                "trace_id": "trace_001",
                "run_id": "run_trace_001",
                "parent_run_id": "run_parent_001",
                "event_type": "tool_start",
                "state": "thinking",
                "summary_text": "Inspecting docs",
                "intent": "Read repo documentation",
                "cursor": {"x": 320, "y": 200},
                "target_rect": {"x": 300, "y": 180, "width": 80, "height": 30},
                "meta": {"same_scene_visible": True},
                "subagent": False,
                "agent_id": "trace_main_001",
                "parent_agent_id": None,
            },
        }
    )
    assert message["type"] == "ingest_optional_trace"
    assert message["payload"]["provider"] == "langchain"
    assert message["payload"]["run_id"] == "run_trace_001"


def test_validate_client_message_webrtc_answer() -> None:
    message = validate_client_message(
        {
            "type": "webrtc_answer",
            "payload": {
                "sdp": "v=0\n...",
                "type": "answer",
            },
        }
    )
    assert message["type"] == "webrtc_answer"
    assert message["payload"]["type"] == "answer"


def test_validate_client_message_webrtc_ice() -> None:
    message = validate_client_message(
        {
            "type": "webrtc_ice",
            "payload": {
                "candidate": "candidate:0 1 UDP 2122252543 192.168.1.2 54400 typ host",
                "sdp_mid": "0",
                "sdp_mline_index": 0,
            },
        }
    )
    assert message["type"] == "webrtc_ice"
    assert message["payload"]["sdp_mid"] == "0"


def test_validate_client_message_webrtc_request_with_demo_local_profile() -> None:
    message = validate_client_message(
        {
            "type": "webrtc_request",
            "payload": {
                "stream_profile": "demo_local",
            },
        }
    )
    assert message["type"] == "webrtc_request"
    assert message["payload"]["stream_profile"] == "demo_local"


def test_validate_server_bridge_offer_message() -> None:
    message = validate_server_message(
        {
            "type": "bridge_offer",
            "payload": {
                "intervention_id": "intv_offer_001",
                "session_id": "sess_demo_001",
                "adapter_id": "opencode",
                "adapter_run_id": "run_demo_001",
                "web_mode": "delegate_playwright",
                "web_bridge": "playwright_native",
                "source_event_id": "src_offer_001",
                "headline": "Live browser view",
                "reason_text": "Lumon can open a visible browser view for this online step.",
                "summary_text": "OpenCode can delegate browser control to playwright_native",
                "intent": "Search the web for Lumon docs",
            },
        }
    )
    assert message["type"] == "bridge_offer"
    assert message["payload"]["web_mode"] == "delegate_playwright"
    assert message["payload"]["web_bridge"] == "playwright_native"


def test_validate_server_webrtc_offer_message() -> None:
    message = validate_server_message(
        {
            "type": "webrtc_offer",
            "payload": {
                "sdp": "v=0\n...",
                "type": "offer",
                "ice_servers": [{"urls": ["stun:stun.l.google.com:19302"]}],
            },
        }
    )
    assert message["type"] == "webrtc_offer"
    assert message["payload"]["type"] == "offer"


def test_browser_command_models_require_expected_fields() -> None:
    request = BrowserCommandRequest.model_validate(
        {
            "project_directory": "/Users/leslie/Documents/Lumon",
            "observed_session_id": "ses_browser_001",
            "command_id": "cmd_001",
            "command": "open",
            "url": "https://www.wikipedia.org/",
        }
    )
    assert request.command == "open"

    result = BrowserCommandResult.model_validate(
        {
            "command_id": "cmd_001",
            "command": "open",
            "status": "success",
            "summary_text": "Opened wikipedia.org.",
            "session_id": "sess_001",
            "evidence": {
                "verified": True,
                "final_url": "https://www.wikipedia.org/",
                "domain": "www.wikipedia.org",
                "details": {},
            },
            "actionable_elements": [],
            "meta": {},
        }
    )
    assert result.status == "success"


def test_validate_server_browser_command_message() -> None:
    message = validate_server_message(
        {
            "type": "browser_command",
            "payload": {
                "command_id": "cmd_001",
                "command": "open",
                "status": "success",
                "summary_text": "Opened wikipedia.org.",
                "timestamp": "2026-03-15T21:00:00Z",
                "source_url": "https://www.wikipedia.org/",
                "domain": "www.wikipedia.org",
                "page_version": 2,
                "evidence": {
                    "verified": True,
                    "final_url": "https://www.wikipedia.org/",
                    "domain": "www.wikipedia.org",
                    "page_version": 2,
                    "frame_emitted": True,
                    "details": {},
                },
                "actionable_elements": [],
                "meta": {},
            },
        }
    )
    assert message["type"] == "browser_command"
    assert message["payload"]["command"] == "open"


def test_validate_server_diagnostic_event_message() -> None:
    message = validate_server_message(
        {
            "type": "diagnostic_event",
            "payload": {
                "timestamp": "2026-03-17T00:00:00Z",
                "session_id": "sess_diag_001",
                "adapter_id": "opencode",
                "adapter_run_id": "run_diag_001",
                "trace_id": "trace_diag_001",
                "category": "routing",
                "event_name": "browser_signal",
                "severity": "info",
                "summary_text": "Bridge launch decision",
                "meta": {"reason_code": "browser_signal"},
            },
        }
    )
    assert message["type"] == "diagnostic_event"
    assert message["payload"]["event_name"] == "browser_signal"


def test_reject_unknown_command() -> None:
    try:
        validate_client_message({"type": "explode", "payload": {}})
    except ProtocolValidationError as exc:
        assert exc.code == ErrorCode.UNKNOWN_COMMAND
    else:  # pragma: no cover
        raise AssertionError("Expected ProtocolValidationError")


def test_validate_server_fixture_message() -> None:
    build_fixtures()
    message = {
        "type": "session_state",
        "payload": {
            "session_id": "sess_demo_001",
            "adapter_id": "playwright_native",
            "adapter_run_id": "run_demo_001",
            "state": "running",
            "interaction_mode": "watch",
            "active_checkpoint_id": None,
            "task_text": "Find a hotel",
            "viewport": {"width": 1280, "height": 800},
            "capabilities": {
                "supports_pause": True,
                "supports_approval": True,
                "supports_takeover": True,
                "supports_frames": True,
            },
        },
    }
    validated = validate_server_message(message)
    assert validated["payload"]["state"] == "running"


def test_state_machine_rules() -> None:
    assert can_transition(SessionState.RUNNING, SessionState.PAUSE_REQUESTED)
    assert can_transition(SessionState.TAKEOVER, SessionState.RUNNING)
    assert not can_transition(SessionState.COMPLETED, SessionState.RUNNING)
    assert interaction_mode_for_state(SessionState.TAKEOVER) == "takeover"


def test_same_scene_subagent_requires_subagent_source() -> None:
    message = {
        "type": "agent_event",
        "payload": agent_event(
            event_seq=5,
            event_id="evt_sub_001",
            source_event_id="src_sub_001",
            agent_id="subagent_001",
            parent_agent_id="main_001",
            agent_kind="same_scene_subagent",
            visibility_mode="same_scene_visible",
            action_type="spawn_subagent",
            state="handoff",
            summary_text="Helper checks hotel ratings",
            intent="Verify ratings",
        ),
    }

    with pytest.raises(ProtocolValidationError) as exc:
        validate_server_message(message)

    assert exc.value.code == ErrorCode.BAD_PAYLOAD


def test_main_agent_rejects_subagent_source_field() -> None:
    message = {
        "type": "agent_event",
        "payload": {
            **agent_event(
                event_seq=2,
                event_id="evt_main_002",
                source_event_id="src_main_002",
                agent_id="main_001",
                agent_kind="main",
                visibility_mode="foreground",
                action_type="click",
                state="clicking",
                summary_text="Open results",
                intent="Click search",
            ),
            "subagent_source": "simulated",
        },
    }

    with pytest.raises(ProtocolValidationError) as exc:
        validate_server_message(message)

    assert exc.value.code == ErrorCode.BAD_PAYLOAD
