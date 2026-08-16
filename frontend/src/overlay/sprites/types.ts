export type LumonSessionState =
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

export type LumonActionType =
  | "navigate"
  | "click"
  | "type"
  | "scroll"
  | "read"
  | "wait"
  | "complete"
  | "error";

export type LumonSpriteAnimationId =
  | "idle"
  | "locomotion"
  | "busy"
  | "reading"
  | "success"
  | "error";

export interface SpriteAnimationConfig {
  frame_paths: string[];
  frame_duration_ms: number;
  loop: boolean;
  hold_last_frame_ms: number;
}

export interface SpriteRuntimeStateMap {
  session_state_to_animation: Record<LumonSessionState, LumonSpriteAnimationId>;
  action_type_to_animation: Record<LumonActionType, LumonSpriteAnimationId>;
  moving_animation: LumonSpriteAnimationId;
}

export interface SpriteRuntimeManifest {
  animations: Record<LumonSpriteAnimationId, SpriteAnimationConfig>;
  runtime_state_map: SpriteRuntimeStateMap;
}

export interface SpriteRuntimeInput {
  sessionState?: LumonSessionState;
  actionType?: LumonActionType;
  isMoving?: boolean;
}

export interface SpritePlaybackSnapshot {
  animationId: LumonSpriteAnimationId;
  framePath: string;
}
