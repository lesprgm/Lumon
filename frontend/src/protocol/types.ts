export type AdapterId = "playwright_native" | "opencode";
export type WebBridgeId = "playwright_native";
export type WebModeId = "observe_only" | "delegate_playwright";
export type SpriteFamily = "lobster" | "dog";
export type SessionState =
  | "idle"
  | "starting"
  | "running"
  | "pause_requested"
  | "paused"
  | "waiting_for_approval"
  | "takeover"
  | "completed"
  | "failed"
  | "stopped";

export type InteractionMode = "watch" | "approval" | "takeover";
export type AgentKind = "main" | "same_scene_subagent" | "background_worker";
export type VisibilityMode = "foreground" | "same_scene_visible" | "background_hidden";
export type ActionType =
  | "navigate"
  | "click"
  | "type"
  | "scroll"
  | "read"
  | "spawn_subagent"
  | "subagent_result"
  | "wait"
  | "complete"
  | "error";
export type RiskLevel = "none" | "low" | "medium" | "high";
export type ErrorCode =
  | "INVALID_STATE"
  | "CHECKPOINT_STALE"
  | "CHECKPOINT_SUSPENDED"
  | "UNKNOWN_COMMAND"
  | "BAD_PAYLOAD";

export interface Viewport {
  width: number;
  height: number;
}

export interface AdapterCapabilities {
  supports_pause: boolean;
  supports_approval: boolean;
  supports_takeover: boolean;
  supports_frames: boolean;
}

export interface SessionBootstrapPayload {
  session_id: string;
  ws_token: string;
  ws_path: string;
  protocol_version: string;
}

export interface SessionStatePayload {
  session_id: string;
  adapter_id: AdapterId;
  adapter_run_id: string;
  run_mode?: "demo" | "live";
  observer_mode?: boolean;
  web_mode?: WebModeId | null;
  web_bridge?: WebBridgeId | null;
  state: SessionState;
  interaction_mode: InteractionMode;
  active_checkpoint_id: string | null;
  task_text: string;
  viewport: Viewport;
  capabilities: AdapterCapabilities;
}

export interface FramePayload {
  mime_type: "image/jpeg" | "image/png";
  data_base64: string;
  frame_seq: number;
}

export interface WebRTCOfferPayload {
  sdp: string;
  type: "offer";
  ice_servers: Array<Record<string, unknown>>;
}

export interface WebRTCAnswerPayload {
  sdp: string;
  type: "answer";
}

export interface WebRTCIcePayload {
  candidate: string;
  sdp_mid?: string | null;
  sdp_mline_index?: number | null;
}

export interface WebRTCReadyPayload {
  ready: true;
}

export interface Cursor {
  x: number;
  y: number;
}

export interface TargetRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface BrowserContextPayload {
  session_id: string;
  adapter_id: AdapterId | string;
  adapter_run_id: string;
  url: string;
  title: string | null;
  domain: string;
  environment_type: "local" | "docs" | "app" | "external";
  timestamp: string;
}

export interface AgentEventPayload {
  event_seq: number;
  event_id: string;
  source_event_id: string;
  timestamp: string;
  session_id: string;
  adapter_id: AdapterId | string;
  adapter_run_id: string;
  agent_id: string;
  parent_agent_id: string | null;
  agent_kind: AgentKind;
  environment_id: string;
  visibility_mode: VisibilityMode;
  action_type: ActionType;
  state: string;
  summary_text: string;
  intent: string;
  risk_level: RiskLevel;
  subagent_source: "adapter" | "simulated" | null;
  cursor: Cursor | null;
  target_rect: TargetRect | null;
  target_summary?: string | null;
  confidence?: number | null;
  meta: Record<string, unknown>;
}

export interface BackgroundWorkerUpdatePayload {
  session_id: string;
  adapter_id: AdapterId | string;
  adapter_run_id: string;
  agent_id: string;
  summary_text: string;
  state: string;
  timestamp: string;
}

export interface ApprovalRequiredPayload {
  intervention_id: string;
  session_id: string;
  checkpoint_id: string;
  event_id: string;
  action_type: string;
  source_url?: string | null;
  target_summary?: string | null;
  headline: string;
  reason_text: string;
  recommended_action: "approve" | "deny" | "take_over";
  summary_text: string;
  intent: string;
  risk_level: RiskLevel;
  risk_reason: string;
  adapter_id: AdapterId | string;
  adapter_run_id: string;
}

export interface BridgeOfferPayload {
  intervention_id: string;
  session_id: string;
  adapter_id: AdapterId | string;
  adapter_run_id: string;
  web_mode: "delegate_playwright";
  web_bridge: WebBridgeId;
  source_event_id: string;
  source_url?: string | null;
  target_summary?: string | null;
  headline: string;
  reason_text: string;
  recommended_action: "open_live_browser_view";
  summary_text: string;
  intent: string;
}

export interface TaskResultPayload {
  session_id: string;
  status: "completed" | "failed" | "stopped";
  summary_text: string;
  task_text: string;
  adapter_id: AdapterId | string;
  adapter_run_id: string;
}

export interface ErrorPayload {
  code: ErrorCode;
  message: string;
  session_id?: string | null;
  command_type?: string | null;
  checkpoint_id?: string | null;
  protocol_version: string;
}

export type ServerPayloadMap = {
  session_state: SessionStatePayload;
  browser_context_update: BrowserContextPayload;
  frame: FramePayload;
  webrtc_offer: WebRTCOfferPayload;
  webrtc_ice: WebRTCIcePayload;
  webrtc_ready: WebRTCReadyPayload;
  agent_event: AgentEventPayload;
  browser_command: BrowserCommandRecord;
  background_worker_update: BackgroundWorkerUpdatePayload;
  approval_required: ApprovalRequiredPayload;
  bridge_offer: BridgeOfferPayload;
  task_result: TaskResultPayload;
  error: ErrorPayload;
};

export type ClientPayloadMap = {
  start_task: { task_text: string; demo_mode: boolean; adapter_id: AdapterId; web_mode?: WebModeId | null; web_bridge?: WebBridgeId | null };
  ui_ready: { ready: true };
  webrtc_request: Record<string, never>;
  webrtc_answer: WebRTCAnswerPayload;
  webrtc_ice: WebRTCIcePayload;
  accept_bridge: Record<string, never>;
  decline_bridge: Record<string, never>;
  pause: Record<string, never>;
  resume: Record<string, never>;
  approve: { checkpoint_id: string };
  reject: { checkpoint_id: string };
  start_takeover: Record<string, never>;
  end_takeover: Record<string, never>;
  stop: Record<string, never>;

  remote_mouse_move: { x: number; y: number };
  remote_mouse_down: { x: number; y: number; button?: "left" | "right" | "middle" };
  remote_mouse_up: { x: number; y: number; button?: "left" | "right" | "middle" };
  remote_click: { x: number; y: number; button?: "left" | "right" | "middle" };
  remote_scroll: { delta_x: number; delta_y: number };
  remote_key_down: { key: string };
  remote_key_up: { key: string };
};

export type ClientMessageType = keyof ClientPayloadMap;
export type ServerMessageType = keyof ServerPayloadMap;

export type ServerEnvelope<K extends keyof ServerPayloadMap> = {
  type: K;
  payload: ServerPayloadMap[K];
};

export type AnyServerEnvelope = {
  [K in keyof ServerPayloadMap]: ServerEnvelope<K>;
}[keyof ServerPayloadMap];

export type ClientEnvelope<K extends keyof ClientPayloadMap> = {
  type: K;
  payload: ClientPayloadMap[K];
};

export type AnyClientEnvelope = {
  [K in keyof ClientPayloadMap]: ClientEnvelope<K>;
}[keyof ClientPayloadMap];

export interface InterventionRecord {
  intervention_id: string;
  kind: "approval" | "live_browser_view" | "manual_control";
  headline: string;
  reason_text: string;
  source_url?: string | null;
  target_summary?: string | null;
  recommended_action?: string | null;
  started_at: string;
  resolved_at?: string | null;
  resolution?: "approved" | "denied" | "taken_over" | "dismissed" | "expired" | null;
  checkpoint_id?: string | null;
  source_event_id?: string | null;
  keyframe_path?: string | null;
  domain?: string | null;
}

export interface SessionMetrics {
  attach_requested_at?: string | null;
  attached_at?: string | null;
  first_browser_event_at?: string | null;
  ui_open_requested_at?: string | null;
  ui_ready_at?: string | null;
  attach_latency_ms?: number | null;
  ui_open_latency_ms?: number | null;
  browser_episode_count: number;
  intervention_count: number;
  reconnect_count: number;
  duplicate_attach_prevented: number;
  browser_command_count?: number;
  verified_browser_action_count?: number;
  browser_blocked_count?: number;
  browser_partial_count?: number;
  stale_target_count?: number;
  session_completed: boolean;
  artifact_written: boolean;
}

export interface BrowserElementRef {
  element_id: string;
  label: string;
  role: string;
  typeable: boolean;
  clickable: boolean;
  input_type?: string | null;
  value_preview?: string | null;
  bbox?: TargetRect | null;
  page_version: number;
  sensitive?: boolean;
}

export interface BrowserEvidence {
  verified: boolean;
  final_url?: string | null;
  title?: string | null;
  domain?: string | null;
  page_version?: number | null;
  frame_emitted?: boolean;
  keyframe_path?: string | null;
  element_id?: string | null;
  value_after?: string | null;
  value_redacted?: boolean | null;
  focus_changed?: boolean | null;
  dom_changed?: boolean | null;
  viewport_changed?: boolean | null;
  url_changed?: boolean | null;
  details: Record<string, unknown>;
}

export interface BrowserCommandRecord {
  command_id: string;
  command: "begin_task" | "status" | "inspect" | "open" | "click" | "type" | "scroll" | "wait" | "stop";
  status: "success" | "blocked" | "partial" | "failed" | "unsupported";
  summary_text: string;
  timestamp: string;
  reason?: string | null;
  source_url?: string | null;
  domain?: string | null;
  page_version?: number | null;
  evidence?: BrowserEvidence | null;
  actionable_elements: BrowserElementRef[];
  intervention_id?: string | null;
  checkpoint_id?: string | null;
  meta: Record<string, unknown>;
}

export interface PageVisitRecord {
  url: string;
  domain: string;
  title?: string | null;
  environment_type: "local" | "docs" | "app" | "external";
  first_seen_at: string;
  last_seen_at: string;
  keyframe_path?: string | null;
}

export interface SessionArtifact {
  session_id: string;
  adapter_id: AdapterId | string;
  adapter_run_id: string;
  task_text: string;
  observer_mode: boolean;
  status: string;
  started_at: string;
  completed_at?: string | null;
  summary_text?: string | null;
  browser_context?: BrowserContextPayload | null;
  pages_visited: PageVisitRecord[];
  interventions: InterventionRecord[];
  keyframes: string[];
  metrics: SessionMetrics;
}

export interface SessionArtifactResponse {
  artifact: SessionArtifact;
  events: Array<{ type: string; payload: unknown }>;
  commands?: BrowserCommandRecord[];
}
