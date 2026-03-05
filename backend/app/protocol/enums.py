from __future__ import annotations

from enum import StrEnum


class SessionState(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    TAKEOVER = "takeover"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class InteractionMode(StrEnum):
    WATCH = "watch"
    APPROVAL = "approval"
    TAKEOVER = "takeover"


class AgentKind(StrEnum):
    MAIN = "main"
    SAME_SCENE_SUBAGENT = "same_scene_subagent"
    BACKGROUND_WORKER = "background_worker"


class VisibilityMode(StrEnum):
    FOREGROUND = "foreground"
    SAME_SCENE_VISIBLE = "same_scene_visible"
    BACKGROUND_HIDDEN = "background_hidden"


class ActionType(StrEnum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SCROLL = "scroll"
    READ = "read"
    SPAWN_SUBAGENT = "spawn_subagent"
    SUBAGENT_RESULT = "subagent_result"
    WAIT = "wait"
    COMPLETE = "complete"
    ERROR = "error"


class AgentRuntimeState(StrEnum):
    THINKING = "thinking"
    NAVIGATING = "navigating"
    READING = "reading"
    CLICKING = "clicking"
    TYPING = "typing"
    SCROLLING = "scrolling"
    WAITING = "waiting"
    HANDOFF = "handoff"
    DONE = "done"
    ERROR = "error"


class RiskLevel(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SubagentSource(StrEnum):
    ADAPTER = "adapter"
    SIMULATED = "simulated"


class ErrorCode(StrEnum):
    INVALID_STATE = "INVALID_STATE"
    CHECKPOINT_STALE = "CHECKPOINT_STALE"
    CHECKPOINT_SUSPENDED = "CHECKPOINT_SUSPENDED"
    UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
    BAD_PAYLOAD = "BAD_PAYLOAD"
