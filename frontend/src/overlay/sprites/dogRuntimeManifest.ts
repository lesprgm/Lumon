import type { SpriteRuntimeManifest } from "./types";

export const dogRuntimeManifest = {
  "sprite_id": "lumon_dog_v1",
  "version": 1,
  "asset_root": ".",
  "logical_canvas": {
    "width": 968,
    "height": 721
  },
  "default_anchor": {
    "kind": "bottom_center",
    "normalized_x": 0.5,
    "normalized_y": 1.0,
    "pixel_x": 484,
    "pixel_y": 720
  },
  "default_animation": "idle",
  "animations": {
    "idle": {
      "sheet_path": "idle/idle_sheet_normalized.png",
      "frames_dir": "idle/frames",
      "frame_paths": [
        "idle/frames/idle_01.png",
        "idle/frames/idle_02.png",
        "idle/frames/idle_03.png",
        "idle/frames/idle_04.png",
        "idle/frames/idle_05.png",
        "idle/frames/idle_06.png"
      ],
      "frame_count": 6,
      "fps": 8,
      "frame_duration_ms": 125,
      "loop": true,
      "hold_last_frame_ms": 0
    },
    "busy": {
      "sheet_path": "busy/busy_sheet_normalized.png",
      "frames_dir": "busy/frames",
      "frame_paths": [
        "busy/frames/busy_01.png",
        "busy/frames/busy_02.png",
        "busy/frames/busy_03.png",
        "busy/frames/busy_04.png",
        "busy/frames/busy_05.png",
        "busy/frames/busy_06.png"
      ],
      "frame_count": 6,
      "fps": 10,
      "frame_duration_ms": 100,
      "loop": true,
      "hold_last_frame_ms": 0
    },
    "success": {
      "sheet_path": "success/success_sheet_normalized.png",
      "frames_dir": "success/frames",
      "frame_paths": [
        "success/frames/success_01.png",
        "success/frames/success_02.png",
        "success/frames/success_03.png",
        "success/frames/success_04.png",
        "success/frames/success_05.png",
        "success/frames/success_06.png",
        "success/frames/success_07.png",
        "success/frames/success_08.png",
        "success/frames/success_09.png"
      ],
      "frame_count": 9,
      "fps": 8,
      "frame_duration_ms": 125,
      "loop": false,
      "hold_last_frame_ms": 450
    },
    "error": {
      "sheet_path": "error/error_sheet_normalized.png",
      "frames_dir": "error/frames",
      "frame_paths": [
        "error/frames/error_01.png",
        "error/frames/error_02.png",
        "error/frames/error_03.png",
        "error/frames/error_04.png",
        "error/frames/error_05.png",
        "error/frames/error_06.png"
      ],
      "frame_count": 6,
      "fps": 8,
      "frame_duration_ms": 125,
      "loop": false,
      "hold_last_frame_ms": 650
    },
    "locomotion": {
      "sheet_path": "locomotion/locomotion_sheet_normalized.png",
      "frames_dir": "locomotion/frames",
      "frame_paths": [
        "locomotion/frames/locomotion_01.png",
        "locomotion/frames/locomotion_02.png",
        "locomotion/frames/locomotion_03.png",
        "locomotion/frames/locomotion_04.png",
        "locomotion/frames/locomotion_05.png",
        "locomotion/frames/locomotion_06.png"
      ],
      "frame_count": 6,
      "fps": 12,
      "frame_duration_ms": 83,
      "loop": true,
      "hold_last_frame_ms": 0
    }
  },
  "runtime_state_map": {
    "session_state_to_animation": {
      "idle": "idle",
      "starting": "idle",
      "running": "idle",
      "pause_requested": "idle",
      "paused": "idle",
      "waiting_for_approval": "idle",
      "takeover": "idle",
      "completed": "success",
      "failed": "error",
      "stopped": "idle"
    },
    "action_type_to_animation": {
      "navigate": "busy",
      "click": "busy",
      "type": "busy",
      "scroll": "busy",
      "read": "busy",
      "wait": "idle",
      "complete": "success",
      "error": "error"
    },
    "moving_animation": "locomotion",
    "priority": [
      "error",
      "success",
      "busy",
      "locomotion",
      "idle"
    ],
    "default": "idle"
  },
  "recommended_transitions": {
    "on_success_complete": {
      "next_animation": "idle",
      "delay_ms": 450
    },
    "on_error_complete": {
      "next_animation": "idle",
      "delay_ms": 650
    }
  }
} as const satisfies SpriteRuntimeManifest;
