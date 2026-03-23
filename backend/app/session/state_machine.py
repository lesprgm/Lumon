from __future__ import annotations

from app.protocol.enums import InteractionMode, SessionState


ALLOWED_TRANSITIONS: dict[SessionState, set[SessionState]] = {
    SessionState.IDLE: {SessionState.STARTING, SessionState.STOPPED},
    SessionState.STARTING: {SessionState.RUNNING, SessionState.FAILED, SessionState.STOPPED},
    SessionState.RUNNING: {
        SessionState.PAUSE_REQUESTED,
        SessionState.WAITING_FOR_APPROVAL,
        SessionState.TAKEOVER,
        SessionState.COMPLETED,
        SessionState.FAILED,
        SessionState.STOPPED,
    },
    SessionState.PAUSE_REQUESTED: {
        SessionState.PAUSED,
        SessionState.RUNNING,
        SessionState.TAKEOVER,
        SessionState.FAILED,
        SessionState.STOPPED,
    },
    SessionState.PAUSED: {
        SessionState.RUNNING,
        SessionState.WAITING_FOR_APPROVAL,
        SessionState.TAKEOVER,
        SessionState.COMPLETED,
        SessionState.FAILED,
        SessionState.STOPPED,
    },
    SessionState.WAITING_FOR_APPROVAL: {
        SessionState.RUNNING,
        SessionState.TAKEOVER,
        SessionState.STOPPED,
        SessionState.FAILED,
    },
    SessionState.TAKEOVER: {
        SessionState.RUNNING,
        SessionState.PAUSED,
        SessionState.FAILED,
        SessionState.STOPPED,
    },
    SessionState.COMPLETED: set(),
    SessionState.FAILED: set(),
    SessionState.STOPPED: set(),
}


def can_transition(current: SessionState, target: SessionState) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def interaction_mode_for_state(state: SessionState) -> InteractionMode:
    if state == SessionState.WAITING_FOR_APPROVAL:
        return InteractionMode.APPROVAL
    if state == SessionState.TAKEOVER:
        return InteractionMode.TAKEOVER
    return InteractionMode.WATCH
