from __future__ import annotations

from typing import Any

from app.protocol.enums import ActionType, AgentKind, AgentRuntimeState, RiskLevel, SubagentSource, VisibilityMode
from app.utils.ids import new_id, utc_timestamp


STATE_MAP: dict[str, AgentRuntimeState] = {
    "thinking": AgentRuntimeState.THINKING,
    "reading": AgentRuntimeState.READING,
    "navigating": AgentRuntimeState.NAVIGATING,
    "clicking": AgentRuntimeState.CLICKING,
    "typing": AgentRuntimeState.TYPING,
    "scrolling": AgentRuntimeState.SCROLLING,
    "waiting": AgentRuntimeState.WAITING,
    "done": AgentRuntimeState.DONE,
    "error": AgentRuntimeState.ERROR,
}

ACTION_MAP: dict[str, ActionType] = {
    "tool_start": ActionType.READ,
    "tool_complete": ActionType.COMPLETE,
    "navigate": ActionType.NAVIGATE,
    "click": ActionType.CLICK,
    "type": ActionType.TYPE,
    "scroll": ActionType.SCROLL,
    "read": ActionType.READ,
    "error": ActionType.ERROR,
    "subagent": ActionType.SPAWN_SUBAGENT,
    "subagent_result": ActionType.SUBAGENT_RESULT,
    "wait": ActionType.WAIT,
}


def normalize_external_event(
    raw: dict[str, Any],
    *,
    session_id: str,
    adapter_id: str,
    adapter_run_id: str,
    event_seq: int,
) -> dict[str, Any]:
    action_type = ACTION_MAP.get(raw.get("event_type", ""), ActionType.READ)
    raw_state = raw.get("state", "thinking")
    state = STATE_MAP.get(raw_state, AgentRuntimeState.THINKING)
    agent_kind = AgentKind.SAME_SCENE_SUBAGENT if raw.get("subagent") else AgentKind.MAIN
    visibility = VisibilityMode.SAME_SCENE_VISIBLE if agent_kind == AgentKind.SAME_SCENE_SUBAGENT else VisibilityMode.FOREGROUND
    risk_level = RiskLevel(raw.get("risk_level", "none")) if raw.get("risk_level") in RiskLevel._value2member_map_ else RiskLevel.NONE
    summary_text = raw.get("summary_text") or raw.get("label") or raw.get("event_type", "External event").replace("_", " ").title()

    return {
        "event_seq": event_seq,
        "event_id": raw.get("event_id", new_id("evt")),
        "source_event_id": raw.get("source_event_id", new_id("src")),
        "timestamp": raw.get("timestamp", utc_timestamp()),
        "session_id": session_id,
        "adapter_id": adapter_id,
        "adapter_run_id": adapter_run_id,
        "agent_id": raw.get("agent_id", "main_001"),
        "parent_agent_id": raw.get("parent_agent_id"),
        "agent_kind": agent_kind.value,
        "environment_id": raw.get("environment_id", "env_browser_main"),
        "visibility_mode": visibility.value,
        "action_type": action_type.value,
        "state": state.value,
        "summary_text": summary_text,
        "intent": raw.get("intent", summary_text),
        "risk_level": risk_level.value,
        "subagent_source": SubagentSource.ADAPTER.value if agent_kind == AgentKind.SAME_SCENE_SUBAGENT else None,
        "cursor": raw.get("cursor"),
        "target_rect": raw.get("target_rect"),
        "meta": raw.get("meta", {}),
    }
