from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.config import DEFAULT_ADAPTER_ID, VIEWPORT_HEIGHT, VIEWPORT_WIDTH
from app.protocol.enums import ErrorCode
from app.utils.ids import utc_timestamp

FIXTURE_ROOT = Path(__file__).resolve().parent
MESSAGES_DIR = FIXTURE_ROOT / "messages"
TIMELINES_DIR = FIXTURE_ROOT / "timelines"


def wrap(message_type: str, payload: dict) -> dict:
    return {"type": message_type, "payload": payload}


def frame_payload(frame_seq: int = 1) -> dict:
    image = Image.new("RGB", (16, 16), (245, 245, 245))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=80)
    return {
        "mime_type": "image/jpeg",
        "data_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "frame_seq": frame_seq,
    }


def base_session_state(state: str, interaction_mode: str, checkpoint_id: str | None = None) -> dict:
    return {
        "session_id": "sess_demo_001",
        "adapter_id": DEFAULT_ADAPTER_ID,
        "adapter_run_id": "run_demo_001",
        "state": state,
        "interaction_mode": interaction_mode,
        "active_checkpoint_id": checkpoint_id,
        "task_text": "Find a hotel in NYC next weekend under $250",
        "viewport": {"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        "capabilities": {
            "supports_pause": True,
            "supports_approval": True,
            "supports_takeover": True,
            "supports_frames": True,
        },
    }


def agent_event(
    *,
    event_seq: int,
    event_id: str,
    source_event_id: str,
    agent_id: str,
    agent_kind: str,
    visibility_mode: str,
    action_type: str,
    state: str,
    summary_text: str,
    intent: str,
    risk_level: str = "none",
    cursor: dict | None = None,
    target_rect: dict | None = None,
    parent_agent_id: str | None = None,
    subagent_source: str | None = None,
    adapter_id: str = DEFAULT_ADAPTER_ID,
) -> dict:
    return {
        "event_seq": event_seq,
        "event_id": event_id,
        "source_event_id": source_event_id,
        "timestamp": utc_timestamp(),
        "session_id": "sess_demo_001",
        "adapter_id": adapter_id,
        "adapter_run_id": "run_demo_001",
        "agent_id": agent_id,
        "parent_agent_id": parent_agent_id,
        "agent_kind": agent_kind,
        "environment_id": "env_browser_main",
        "visibility_mode": visibility_mode,
        "action_type": action_type,
        "state": state,
        "summary_text": summary_text,
        "intent": intent,
        "risk_level": risk_level,
        "subagent_source": subagent_source,
        "cursor": cursor,
        "target_rect": target_rect,
        "meta": {},
    }


def approval_required() -> dict:
    return {
        "session_id": "sess_demo_001",
        "checkpoint_id": "chk_demo_001",
        "event_id": "evt_submit_001",
        "action_type": "click",
        "summary_text": "Ready to submit search",
        "intent": "Submit the shortlist form",
        "risk_level": "high",
        "risk_reason": "Final irreversible transition",
        "adapter_id": DEFAULT_ADAPTER_ID,
        "adapter_run_id": "run_demo_001",
    }


def background_worker_update() -> dict:
    return {
        "session_id": "sess_demo_001",
        "adapter_id": DEFAULT_ADAPTER_ID,
        "adapter_run_id": "run_demo_001",
        "agent_id": "worker_001",
        "summary_text": "Comparing hotel ratings in the background",
        "state": "running",
        "timestamp": utc_timestamp(),
    }


def task_result(status: str = "completed") -> dict:
    return {
        "session_id": "sess_demo_001",
        "status": status,
        "summary_text": "Shortlisted three hotels under budget",
        "task_text": "Find a hotel in NYC next weekend under $250",
        "adapter_id": DEFAULT_ADAPTER_ID,
        "adapter_run_id": "run_demo_001",
    }


def error_payload(code: ErrorCode, message: str, command_type: str | None = None, checkpoint_id: str | None = None) -> dict:
    return {
        "code": code.value,
        "message": message,
        "session_id": "sess_demo_001",
        "command_type": command_type,
        "checkpoint_id": checkpoint_id,
        "protocol_version": "1.3.1",
    }


def timeline_entries() -> dict[str, list[dict]]:
    navigate = wrap(
        "agent_event",
        agent_event(
            event_seq=1,
            event_id="evt_nav_001",
            source_event_id="src_nav_001",
            agent_id="main_001",
            agent_kind="main",
            visibility_mode="foreground",
            action_type="navigate",
            state="navigating",
            summary_text="Opening travel site",
            intent="Navigate to the hotel search page",
            cursor={"x": 220, "y": 120},
            target_rect={"x": 160, "y": 80, "width": 220, "height": 60},
        ),
    )
    click = wrap(
        "agent_event",
        agent_event(
            event_seq=2,
            event_id="evt_click_001",
            source_event_id="src_click_001",
            agent_id="main_001",
            agent_kind="main",
            visibility_mode="foreground",
            action_type="click",
            state="clicking",
            summary_text="Opening destination input",
            intent="Focus the destination field",
            cursor={"x": 340, "y": 255},
            target_rect={"x": 300, "y": 230, "width": 80, "height": 40},
        ),
    )
    masked_type = wrap(
        "agent_event",
        agent_event(
            event_seq=3,
            event_id="evt_type_001",
            source_event_id="src_type_001",
            agent_id="main_001",
            agent_kind="main",
            visibility_mode="foreground",
            action_type="type",
            state="typing",
            summary_text="Entering travel details",
            intent="Type the destination and dates",
            cursor={"x": 355, "y": 255},
            target_rect={"x": 300, "y": 230, "width": 260, "height": 40},
        ),
    )
    masked_type["payload"]["meta"] = {"masked": True, "text_mask": "***"}
    submit_risky = wrap(
        "agent_event",
        agent_event(
            event_seq=4,
            event_id="evt_submit_001",
            source_event_id="src_submit_001",
            agent_id="main_001",
            agent_kind="main",
            visibility_mode="foreground",
            action_type="click",
            state="clicking",
            summary_text="Ready to submit search",
            intent="Submit the search form",
            risk_level="high",
            cursor={"x": 680, "y": 640},
            target_rect={"x": 620, "y": 610, "width": 120, "height": 52},
        ),
    )
    same_scene_spawn = wrap(
        "agent_event",
        agent_event(
            event_seq=5,
            event_id="evt_sub_spawn_001",
            source_event_id="src_sub_spawn_001",
            agent_id="subagent_001",
            parent_agent_id="main_001",
            agent_kind="same_scene_subagent",
            visibility_mode="same_scene_visible",
            action_type="spawn_subagent",
            state="handoff",
            summary_text="Helper checks hotel ratings",
            intent="Verify rating quality for shortlisted hotels",
            subagent_source="simulated",
            cursor={"x": 900, "y": 420},
            target_rect={"x": 860, "y": 380, "width": 140, "height": 60},
        ),
    )
    same_scene_done = wrap(
        "agent_event",
        agent_event(
            event_seq=6,
            event_id="evt_sub_done_001",
            source_event_id="src_sub_done_001",
            agent_id="subagent_001",
            parent_agent_id="main_001",
            agent_kind="same_scene_subagent",
            visibility_mode="same_scene_visible",
            action_type="subagent_result",
            state="done",
            summary_text="Helper returned rating summary",
            intent="Return hotel rating findings",
            subagent_source="simulated",
            cursor={"x": 930, "y": 410},
            target_rect={"x": 860, "y": 380, "width": 160, "height": 60},
        ),
    )
    complete = wrap(
        "agent_event",
        agent_event(
            event_seq=7,
            event_id="evt_complete_001",
            source_event_id="src_complete_001",
            agent_id="main_001",
            agent_kind="main",
            visibility_mode="foreground",
            action_type="complete",
            state="done",
            summary_text="Shortlist complete",
            intent="Present final shortlist",
            cursor={"x": 790, "y": 210},
            target_rect={"x": 720, "y": 170, "width": 140, "height": 54},
        ),
    )
    mapped_adapter = wrap(
        "agent_event",
        agent_event(
            event_seq=8,
            event_id="evt_mapped_001",
            source_event_id="src_external_001",
            agent_id="main_001",
            agent_kind="main",
            visibility_mode="foreground",
            action_type="read",
            state="reading",
            summary_text="Reading filter results",
            intent="Inspect the loaded search results",
            cursor={"x": 800, "y": 300},
            target_rect={"x": 720, "y": 260, "width": 220, "height": 80},
            adapter_id="claude_code",
        ),
    )
    opencode_mapped = wrap(
        "agent_event",
        agent_event(
            event_seq=9,
            event_id="evt_opencode_001",
            source_event_id="src_opencode_001",
            agent_id="main_001",
            agent_kind="main",
            visibility_mode="foreground",
            action_type="read",
            state="reading",
            summary_text="OpenCode reads repository context",
            intent="Inspect project state before acting",
            cursor={"x": 640, "y": 240},
            target_rect={"x": 580, "y": 200, "width": 200, "height": 72},
            adapter_id="opencode",
        ),
    )

    return {
        "happy_path": [
            {"delay_ms": 0, "message": wrap("session_state", base_session_state("idle", "watch"))},
            {"delay_ms": 100, "message": wrap("session_state", base_session_state("starting", "watch"))},
            {"delay_ms": 100, "message": wrap("session_state", base_session_state("running", "watch"))},
            {"delay_ms": 80, "message": wrap("frame", frame_payload(1))},
            {"delay_ms": 80, "message": navigate},
            {"delay_ms": 80, "message": click},
            {"delay_ms": 80, "message": masked_type},
            {"delay_ms": 80, "message": wrap("background_worker_update", background_worker_update())},
            {"delay_ms": 80, "message": wrap("session_state", base_session_state("waiting_for_approval", "approval", "chk_demo_001"))},
            {"delay_ms": 0, "message": submit_risky},
            {"delay_ms": 0, "message": wrap("approval_required", approval_required())},
            {"delay_ms": 200, "message": wrap("session_state", base_session_state("running", "watch"))},
            {"delay_ms": 80, "message": same_scene_spawn},
            {"delay_ms": 80, "message": same_scene_done},
            {"delay_ms": 80, "message": complete},
            {"delay_ms": 80, "message": wrap("session_state", base_session_state("completed", "watch"))},
            {"delay_ms": 0, "message": wrap("task_result", task_result("completed"))},
        ],
        "pause_resume": [
            {"delay_ms": 0, "message": wrap("session_state", base_session_state("running", "watch"))},
            {"delay_ms": 50, "message": wrap("session_state", base_session_state("pause_requested", "watch"))},
            {"delay_ms": 100, "message": wrap("session_state", base_session_state("paused", "watch"))},
            {"delay_ms": 100, "message": wrap("session_state", base_session_state("running", "watch"))},
        ],
        "takeover": [
            {"delay_ms": 0, "message": wrap("session_state", base_session_state("running", "watch"))},
            {"delay_ms": 80, "message": wrap("session_state", base_session_state("takeover", "takeover"))},
            {"delay_ms": 140, "message": wrap("session_state", base_session_state("paused", "watch"))},
            {"delay_ms": 140, "message": wrap("session_state", base_session_state("running", "watch"))},
        ],
        "approval_takeover": [
            {"delay_ms": 0, "message": wrap("session_state", base_session_state("waiting_for_approval", "approval", "chk_demo_001"))},
            {"delay_ms": 0, "message": wrap("approval_required", approval_required())},
            {"delay_ms": 100, "message": wrap("session_state", base_session_state("takeover", "takeover", "chk_demo_001"))},
            {"delay_ms": 100, "message": wrap("session_state", base_session_state("paused", "watch"))},
            {"delay_ms": 0, "message": wrap("error", error_payload(ErrorCode.CHECKPOINT_STALE, "Checkpoint invalidated by takeover", checkpoint_id="chk_demo_001"))},
            {"delay_ms": 0, "message": wrap("session_state", base_session_state("waiting_for_approval", "approval", "chk_demo_002"))},
            {"delay_ms": 0, "message": wrap("approval_required", {**approval_required(), "checkpoint_id": "chk_demo_002"})},
        ],
        "stale_checkpoint_error": [
            {"delay_ms": 0, "message": wrap("error", error_payload(ErrorCode.CHECKPOINT_STALE, "Checkpoint is stale", command_type="approve", checkpoint_id="chk_stale_001"))},
        ],
        "invalid_state_commands": [
            {"delay_ms": 0, "message": wrap("error", error_payload(ErrorCode.INVALID_STATE, "Cannot pause from current state", command_type="pause"))},
            {"delay_ms": 0, "message": wrap("error", error_payload(ErrorCode.INVALID_STATE, "Cannot end takeover from current state", command_type="end_takeover"))},
            {"delay_ms": 0, "message": wrap("error", error_payload(ErrorCode.INVALID_STATE, "Cannot resume from current state", command_type="resume"))},
        ],
        "unknown_command": [
            {"delay_ms": 0, "message": wrap("error", error_payload(ErrorCode.UNKNOWN_COMMAND, "Unknown message type: explode", command_type="explode"))},
        ],
        "mapped_adapter_event": [
            {"delay_ms": 0, "message": mapped_adapter},
        ],
        "opencode_mapped_event": [
            {"delay_ms": 0, "message": opencode_mapped},
        ],
    }


def main() -> None:
    MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)

    message_examples = {
        "session_state.json": wrap("session_state", base_session_state("running", "watch")),
        "frame.json": wrap("frame", frame_payload()),
        "agent_event.json": wrap(
            "agent_event",
            agent_event(
                event_seq=1,
                event_id="evt_example_001",
                source_event_id="src_example_001",
                agent_id="main_001",
                agent_kind="main",
                visibility_mode="foreground",
                action_type="click",
                state="clicking",
                summary_text="Clicking Search",
                intent="Open the search results",
                cursor={"x": 420, "y": 220},
                target_rect={"x": 380, "y": 190, "width": 80, "height": 60},
            ),
        ),
        "background_worker_update.json": wrap("background_worker_update", background_worker_update()),
        "approval_required.json": wrap("approval_required", approval_required()),
        "task_result.json": wrap("task_result", task_result()),
        "error.json": wrap("error", error_payload(ErrorCode.INVALID_STATE, "Cannot resume from current state", command_type="resume")),
    }

    for name, data in message_examples.items():
        (MESSAGES_DIR / name).write_text(json.dumps(data, indent=2))

    for name, entries in timeline_entries().items():
        (TIMELINES_DIR / f"{name}.json").write_text(json.dumps(entries, indent=2))


if __name__ == "__main__":
    main()
