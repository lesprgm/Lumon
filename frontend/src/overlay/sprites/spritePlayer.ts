import type {
  LumonSpriteAnimationId,
  SpritePlaybackSnapshot,
  SpriteRuntimeInput,
  SpriteRuntimeManifest,
} from "./types";
import { resolveSpriteAssetPath } from "./spriteLoader";

export class SpritePlayer {
  private readonly manifest: SpriteRuntimeManifest;
  private readonly assetBasePath: string;
  private activeAnimationId: LumonSpriteAnimationId;
  private animationStartedAtMs = 0;

  constructor(manifest: SpriteRuntimeManifest, assetBasePath = "") {
    this.manifest = manifest;
    this.assetBasePath = assetBasePath;
    this.activeAnimationId = "idle";
  }

  get animationId(): LumonSpriteAnimationId {
    return this.activeAnimationId;
  }

  setAnimation(
    animationId: LumonSpriteAnimationId,
    nowMs: number,
    options: { restart?: boolean } = {},
  ): void {
    const shouldRestart = options.restart ?? false;
    if (!shouldRestart && animationId === this.activeAnimationId) {
      return;
    }
    this.activeAnimationId = animationId;
    this.animationStartedAtMs = nowMs;
  }

  resolveAnimationId(input: SpriteRuntimeInput = {}): LumonSpriteAnimationId {
    const sessionAnimation = input.sessionState
      ? this.manifest.runtime_state_map.session_state_to_animation[input.sessionState]
      : undefined;
    const actionAnimation = input.actionType
      ? this.manifest.runtime_state_map.action_type_to_animation[input.actionType]
      : undefined;

    if (sessionAnimation === "success" || sessionAnimation === "error") {
      return sessionAnimation;
    }
    if (actionAnimation === "success" || actionAnimation === "error") {
      return actionAnimation;
    }
    if (input.isMoving) {
      return this.manifest.runtime_state_map.moving_animation;
    }
    return actionAnimation ?? sessionAnimation ?? "idle";
  }

  syncToRuntime(input: SpriteRuntimeInput, nowMs: number): void {
    this.setAnimation(this.resolveAnimationId(input), nowMs);
  }

  update(nowMs: number, input?: SpriteRuntimeInput): SpritePlaybackSnapshot {
    if (input) {
      this.syncToRuntime(input, nowMs);
    }

    const animationId = this.activeAnimationId;
    const animation = this.manifest.animations[animationId];
    const frameCount = animation.frame_paths.length;
    const elapsedMs = Math.max(0, nowMs - this.animationStartedAtMs);

    if (!animation.loop) {
      const fullDurationMs = frameCount * animation.frame_duration_ms;
      const holdUntilMs = fullDurationMs + animation.hold_last_frame_ms;

      if (elapsedMs >= holdUntilMs) {
        this.setAnimation("idle", nowMs, { restart: true });
        return this.update(nowMs);
      }
    }

    let frameIndex = 0;

    if (animation.loop) {
      frameIndex = Math.floor(elapsedMs / animation.frame_duration_ms) % frameCount;
    } else {
      const rawFrameIndex = Math.floor(elapsedMs / animation.frame_duration_ms);
      frameIndex = Math.min(rawFrameIndex, frameCount - 1);
    }

    return {
      animationId,
      framePath: resolveSpriteAssetPath(
        animation.frame_paths[frameIndex],
        this.assetBasePath,
      ),
    };
  }
}
