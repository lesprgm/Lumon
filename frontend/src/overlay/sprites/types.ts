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

export type LumonSpriteAnimationId = "idle" | "locomotion" | "busy" | "success" | "error";

export interface SpriteAnchor {
  kind: "bottom_center";
  normalized_x: number;
  normalized_y: number;
  pixel_x: number;
  pixel_y: number;
}

export interface SpriteAnimationConfig {
  sheet_path: string;
  frames_dir: string;
  frame_paths: string[];
  frame_count: number;
  fps: number;
  frame_duration_ms: number;
  loop: boolean;
  hold_last_frame_ms: number;
}

export interface SpriteRuntimeStateMap {
  session_state_to_animation: Record<LumonSessionState, LumonSpriteAnimationId>;
  action_type_to_animation: Record<LumonActionType, LumonSpriteAnimationId>;
  moving_animation: LumonSpriteAnimationId | null;
  priority: LumonSpriteAnimationId[];
  default: LumonSpriteAnimationId;
}

export interface RecommendedTransition {
  next_animation: LumonSpriteAnimationId;
  delay_ms: number;
}

export interface SpriteRuntimeManifest {
  sprite_id: string;
  version: number;
  asset_root: string;
  logical_canvas: {
    width: number;
    height: number;
  };
  default_anchor: SpriteAnchor;
  default_animation: LumonSpriteAnimationId;
  animations: Record<LumonSpriteAnimationId, SpriteAnimationConfig>;
  runtime_state_map: SpriteRuntimeStateMap;
  recommended_transitions: {
    on_success_complete: RecommendedTransition;
    on_error_complete: RecommendedTransition;
  };
}

export interface SpriteRuntimeInput {
  sessionState?: LumonSessionState;
  actionType?: LumonActionType;
  isMoving?: boolean;
}

export interface SpritePlaybackSnapshot {
  animationId: LumonSpriteAnimationId;
  frameIndex: number;
  framePath: string;
  elapsedMs: number;
  isOneShotComplete: boolean;
  anchor: SpriteAnchor;
}
