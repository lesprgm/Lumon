from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import DEFAULT_ADAPTER_ID, PROTOCOL_VERSION, ViewportConfig
from app.protocol.enums import (
    ActionType,
    AgentKind,
    AgentRuntimeState,
    ErrorCode,
    InteractionMode,
    RiskLevel,
    SessionState,
    SubagentSource,
    VisibilityMode,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommandEnvelope(StrictModel):
    type: str
    payload: dict[str, Any]


class Cursor(StrictModel):
    x: int
    y: int


class TargetRect(StrictModel):
    x: int
    y: int
    width: int
    height: int


class BrowserContextPayload(StrictModel):
    session_id: str
    adapter_id: str = DEFAULT_ADAPTER_ID
    adapter_run_id: str
    url: str
    title: str | None = None
    domain: str
    environment_type: Literal["local", "docs", "app", "external"] = "external"
    timestamp: str


class StartTaskPayload(StrictModel):
    task_text: str
    demo_mode: bool = False
    adapter_id: Literal["playwright_native", "opencode"] = DEFAULT_ADAPTER_ID
    web_mode: Literal["observe_only", "delegate_playwright"] | None = None
    web_bridge: Literal["playwright_native"] | None = None
    auto_delegate: bool = False
    observer_mode: bool = False
    observed_session_id: str | None = None


class CheckpointPayload(StrictModel):
    checkpoint_id: str


class RemoteMouseMovePayload(StrictModel):
    x: float
    y: float


class RemoteMouseClickPayload(StrictModel):
    x: float
    y: float
    button: Literal["left", "right", "middle"] = "left"


class RemoteScrollPayload(StrictModel):
    delta_x: float
    delta_y: float


class RemoteKeyPayload(StrictModel):
    key: str


class EmptyPayload(StrictModel):
    pass



class AdapterCapabilities(StrictModel):
    supports_pause: bool
    supports_approval: bool
    supports_takeover: bool
    supports_frames: bool


class ViewportConfig(StrictModel):
    width: int
    height: int


class SessionStatePayload(StrictModel):
    session_id: str
    adapter_id: str
    adapter_run_id: str
    run_mode: Literal["demo", "live"] | None = None
    observer_mode: bool | None = None
    web_mode: Literal["observe_only", "delegate_playwright"] | None = None
    web_bridge: Literal["playwright_native"] | None = None
    state: SessionState
    interaction_mode: InteractionMode
    active_checkpoint_id: str | None = None
    task_text: str
    viewport: ViewportConfig
    capabilities: AdapterCapabilities | dict[str, bool]


class FramePayload(StrictModel):
    mime_type: Literal["image/jpeg", "image/png"]
    data_base64: str
    frame_seq: int


class WebRTCOfferPayload(StrictModel):
    sdp: str
    type: Literal["offer"]
    ice_servers: list[dict[str, Any]]


class BrowserElementRef(StrictModel):
    element_id: str
    label: str
    role: str
    typeable: bool
    clickable: bool
    input_type: str | None = None
    value_preview: str | None = None
    bbox: TargetRect | None = None
    page_version: int
    sensitive: bool | None = None


class BrowserEvidence(StrictModel):
    verified: bool
    final_url: str | None = None
    title: str | None = None
    domain: str | None = None
    page_version: int | None = None
    frame_emitted: bool | None = None
    keyframe_path: str | None = None
    element_id: str | None = None
    value_after: str | None = None
    value_redacted: bool | None = None
    focus_changed: bool | None = None
    dom_changed: bool | None = None
    viewport_changed: bool | None = None
    url_changed: bool | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class BrowserCommandRecord(StrictModel):
    command_id: str
    command: Literal["begin_task", "status", "inspect", "open", "click", "type", "scroll", "wait", "stop"]
    status: Literal["success", "blocked", "partial", "failed", "unsupported"]
    summary_text: str
    timestamp: str
    reason: str | None = None
    source_url: str | None = None
    domain: str | None = None
    page_version: int | None = None
    evidence: BrowserEvidence | None = None
    actionable_elements: list[BrowserElementRef] = Field(default_factory=list)
    intervention_id: str | None = None
    checkpoint_id: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class BridgeOfferPayload(StrictModel):
    intervention_id: str
    session_id: str
    adapter_id: str
    adapter_run_id: str
    web_mode: Literal["delegate_playwright"]
    web_bridge: Literal["playwright_native"]
    source_event_id: str
    source_url: str | None = None
    target_summary: str | None = None
    headline: str
    reason_text: str
    recommended_action: Literal["open_live_browser_view"] = "open_live_browser_view"
    summary_text: str
    intent: str


class AttachObserverPayload(StrictModel):
    task_text: str
    adapter_id: str
    web_mode: Literal["observe_only", "delegate_playwright"] | None = None
    web_bridge: Literal["playwright_native"] | None = None
    auto_delegate: bool = False
    observed_session_id: str | None = None


class ObserverEventPayload(StrictModel):
    source_event_id: str
    event_type: str
    state: str = "thinking"
    summary_text: str = ""
    agent_id: str = "main_001"
    parent_agent_id: str | None = None
    task_text: str | None = None


class ObserverCompletePayload(StrictModel):
    status: str
    summary_text: str


class OptionalTraceIngestPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    provider: Literal["langchain", "langsmith"]
    trace_id: str | None = None
    run_id: str | None = None
    parent_run_id: str | None = None


class UiReadyPayload(StrictModel):
    ready: bool


class WebRTCAnswerPayload(StrictModel):
    sdp: str
    type: Literal["answer"] = "answer"


class WebRTCIcePayload(StrictModel):
    candidate: str
    sdp_mid: str | None = None
    sdp_mline_index: int | None = None


class WebRTCReadyPayload(StrictModel):
    ready: bool = True


class AgentEventPayload(StrictModel):
    event_seq: int
    event_id: str
    source_event_id: str
    timestamp: str
    session_id: str
    adapter_id: str = DEFAULT_ADAPTER_ID
    adapter_run_id: str
    agent_id: str
    parent_agent_id: str | None = None
    agent_kind: AgentKind
    environment_id: str
    visibility_mode: VisibilityMode
    action_type: ActionType
    state: AgentRuntimeState
    summary_text: str
    intent: str
    risk_level: RiskLevel
    subagent_source: SubagentSource | None = None
    cursor: Cursor | None = None
    target_rect: TargetRect | None = None
    target_summary: str | None = None
    confidence: float | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_subagent_source(self) -> "AgentEventPayload":
        if (
            self.agent_kind == AgentKind.SAME_SCENE_SUBAGENT
            and self.subagent_source is None
        ):
            raise ValueError("same_scene_subagent events require subagent_source")
        if (
            self.agent_kind != AgentKind.SAME_SCENE_SUBAGENT
            and self.subagent_source is not None
        ):
            raise ValueError("only same_scene_subagent events may set subagent_source")
        return self


class BackgroundWorkerUpdatePayload(StrictModel):
    session_id: str
    adapter_id: str = DEFAULT_ADAPTER_ID
    adapter_run_id: str
    agent_id: str
    summary_text: str
    state: str
    timestamp: str


class DiagnosticEventPayload(StrictModel):
    timestamp: str
    session_id: str
    adapter_id: str = DEFAULT_ADAPTER_ID
    adapter_run_id: str
    trace_id: str
    category: str
    event_name: str
    severity: Literal["debug", "info", "warn", "error"] = "info"
    summary_text: str
    meta: dict[str, Any] = Field(default_factory=dict)


class ApprovalRequiredPayload(StrictModel):
    intervention_id: str
    session_id: str
    checkpoint_id: str
    event_id: str
    action_type: str
    source_url: str | None = None
    target_summary: str | None = None
    headline: str
    reason_text: str
    recommended_action: Literal["approve", "deny", "take_over"] = "approve"
    summary_text: str
    intent: str
    risk_level: RiskLevel
    risk_reason: str
    adapter_id: str = DEFAULT_ADAPTER_ID
    adapter_run_id: str


class PageVisitRecord(StrictModel):
    url: str
    domain: str
    title: str | None = None
    environment_type: Literal["local", "docs", "app", "external"] = "external"
    first_seen_at: str
    last_seen_at: str
    keyframe_path: str | None = None


class InterventionRecord(StrictModel):
    intervention_id: str
    kind: Literal["approval", "live_browser_view", "manual_control"]
    headline: str
    reason_text: str
    source_url: str | None = None
    target_summary: str | None = None
    recommended_action: str | None = None
    started_at: str
    resolved_at: str | None = None
    resolution: (
        Literal["approved", "denied", "taken_over", "dismissed", "expired"] | None
    ) = None
    checkpoint_id: str | None = None
    source_event_id: str | None = None
    keyframe_path: str | None = None
    domain: str | None = None


class SessionMetrics(StrictModel):
    attach_requested_at: str | None = None
    attached_at: str | None = None
    first_browser_event_at: str | None = None
    ui_open_requested_at: str | None = None
    ui_ready_at: str | None = None
    attach_latency_ms: int | None = None
    ui_open_latency_ms: int | None = None
    browser_episode_count: int = 0
    intervention_count: int = 0
    reconnect_count: int = 0
    duplicate_attach_prevented: int = 0
    browser_command_count: int = 0
    verified_browser_action_count: int = 0
    browser_blocked_count: int = 0
    browser_partial_count: int = 0
    stale_target_count: int = 0
    session_completed: bool = False
    artifact_written: bool = False


class SessionArtifact(StrictModel):
    session_id: str
    adapter_id: str
    adapter_run_id: str
    task_text: str
    observer_mode: bool = False
    status: Literal["completed", "failed", "stopped", "running", "idle"] = "idle"
    started_at: str
    completed_at: str | None = None
    summary_text: str | None = None
    browser_context: BrowserContextPayload | None = None
    pages_visited: list[PageVisitRecord] = Field(default_factory=list)
    interventions: list[InterventionRecord] = Field(default_factory=list)
    browser_commands: list[BrowserCommandRecord] = Field(default_factory=list)
    keyframes: list[str] = Field(default_factory=list)
    metrics: SessionMetrics = Field(default_factory=SessionMetrics)


class TaskResultPayload(StrictModel):
    session_id: str
    status: Literal["completed", "failed", "stopped"]
    summary_text: str
    task_text: str
    adapter_id: str = DEFAULT_ADAPTER_ID
    adapter_run_id: str


class ErrorPayload(StrictModel):
    code: ErrorCode
    message: str
    session_id: str | None = None
    command_type: str | None = None
    checkpoint_id: str | None = None
    protocol_version: str = PROTOCOL_VERSION


CLIENT_MESSAGE_MODELS: dict[str, type[StrictModel]] = {
    "start_task": StartTaskPayload,
    "attach_observer": AttachObserverPayload,
    "observer_event": ObserverEventPayload,
    "observer_complete": ObserverCompletePayload,
    "ingest_optional_trace": OptionalTraceIngestPayload,
    "ui_ready": UiReadyPayload,
    "webrtc_request": EmptyPayload,
    "webrtc_answer": WebRTCAnswerPayload,
    "webrtc_ice": WebRTCIcePayload,
    "accept_bridge": EmptyPayload,
    "decline_bridge": EmptyPayload,
    "pause": EmptyPayload,
    "resume": EmptyPayload,
    "approve": CheckpointPayload,
    "reject": CheckpointPayload,
    "remote_mouse_move": RemoteMouseMovePayload,
    "remote_mouse_down": RemoteMouseClickPayload,
    "remote_mouse_up": RemoteMouseClickPayload,
    "remote_click": RemoteMouseClickPayload,
    "remote_scroll": RemoteScrollPayload,
    "remote_key_down": RemoteKeyPayload,
    "remote_key_up": RemoteKeyPayload,
    "start_takeover": EmptyPayload,
    "end_takeover": EmptyPayload,
    "stop": EmptyPayload,
}

SERVER_MESSAGE_MODELS: dict[str, type[StrictModel]] = {
    "session_state": SessionStatePayload,
    "browser_context_update": BrowserContextPayload,
    "frame": FramePayload,
    "webrtc_offer": WebRTCOfferPayload,
    "webrtc_ice": WebRTCIcePayload,
    "webrtc_ready": WebRTCReadyPayload,
    "agent_event": AgentEventPayload,
    "browser_command": BrowserCommandRecord,
    "background_worker_update": BackgroundWorkerUpdatePayload,
    "diagnostic_event": DiagnosticEventPayload,
    "approval_required": ApprovalRequiredPayload,
    "bridge_offer": BridgeOfferPayload,
    "task_result": TaskResultPayload,
    "error": ErrorPayload,
}

class LocalObserveOpenCodeRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    project_directory: str
    observed_session_id: str
    frontend_origin: str | None = None
    web_mode: str | None = None
    auto_delegate: bool = False

class LocalObserveOpenCodeResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    session_id: str
    open_url: str
    already_attached: bool

class BrowserCommandRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    command_id: str
    command: str
    project_directory: str
    observed_session_id: str
    task_text: str | None = None
    frontend_origin: str | None = None

class BrowserCommandResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    command_id: str
    command: str
    status: str
    summary_text: str
    reason: str | None = None
    session_id: str
