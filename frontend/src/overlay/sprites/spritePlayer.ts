import type {
  LumonSpriteAnimationId,
  SpritePlaybackSnapshot,
  SpriteRuntimeInput,
  SpriteRuntimeManifest,
} from "./types";
import { resolveSpriteAssetPath } from "./spriteLoader";

function recommendedTransitionKey(
  animationId: LumonSpriteAnimationId,
): "on_success_complete" | "on_error_complete" | null {
  if (animationId === "success") {
    return "on_success_complete";
  }
  if (animationId === "error") {
    return "on_error_complete";
  }
  return null;
}

export class SpritePlayer {
  private readonly manifest: SpriteRuntimeManifest;
  private readonly assetBasePath: string;
  private activeAnimationId: LumonSpriteAnimationId;
  private animationStartedAtMs = 0;

  constructor(manifest: SpriteRuntimeManifest, assetBasePath = "") {
    this.manifest = manifest;
    this.assetBasePath = assetBasePath;
    this.activeAnimationId = manifest.default_animation;
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
    if (input.isMoving && this.manifest.runtime_state_map.moving_animation) {
      return this.manifest.runtime_state_map.moving_animation;
    }

    const candidates = new Set<LumonSpriteAnimationId>();
    if (sessionAnimation) {
      candidates.add(sessionAnimation);
    }
    if (actionAnimation) {
      candidates.add(actionAnimation);
    }
    candidates.add(this.manifest.runtime_state_map.default);

    for (const animationId of this.manifest.runtime_state_map.priority) {
      if (candidates.has(animationId)) {
        return animationId;
      }
    }

    return this.manifest.default_animation;
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
    const elapsedMs = Math.max(0, nowMs - this.animationStartedAtMs);

    if (!animation.loop) {
      const fullDurationMs = animation.frame_count * animation.frame_duration_ms;
      const holdUntilMs = fullDurationMs + animation.hold_last_frame_ms;

      if (elapsedMs >= holdUntilMs) {
        const transitionKey = recommendedTransitionKey(animationId);
        if (transitionKey) {
          const nextAnimation = this.manifest.recommended_transitions[transitionKey].next_animation;
          this.setAnimation(nextAnimation, nowMs, { restart: true });
          return this.update(nowMs);
        }
      }
    }

    let frameIndex = 0;
    let isOneShotComplete = false;

    if (animation.loop) {
      frameIndex = Math.floor(elapsedMs / animation.frame_duration_ms) % animation.frame_count;
    } else {
      const rawFrameIndex = Math.floor(elapsedMs / animation.frame_duration_ms);
      frameIndex = Math.min(rawFrameIndex, animation.frame_count - 1);
      isOneShotComplete =
        elapsedMs >= animation.frame_count * animation.frame_duration_ms + animation.hold_last_frame_ms;
    }

    return {
      animationId,
      frameIndex,
      framePath: resolveSpriteAssetPath(
        animation.frame_paths[frameIndex],
        this.assetBasePath,
        this.manifest.asset_root,
      ),
      elapsedMs,
      isOneShotComplete,
      anchor: this.manifest.default_anchor,
    };
  }
}
