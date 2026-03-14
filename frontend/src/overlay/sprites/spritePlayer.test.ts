import { describe, expect, it } from "vitest";

import { lobsterRuntimeManifest } from "./lobsterRuntimeManifest";
import { SpritePlayer } from "./spritePlayer";

describe("SpritePlayer", () => {
  it("maps action types to busy animation", () => {
    const player = new SpritePlayer(lobsterRuntimeManifest, "/assets/lobster");
    const snapshot = player.update(100, { sessionState: "running", actionType: "click" });
    expect(snapshot.animationId).toBe("busy");
    expect(snapshot.framePath).toContain("busy/");
  });

  it("uses locomotion animation while the sprite is moving", () => {
    const player = new SpritePlayer(lobsterRuntimeManifest, "/assets/lobster");
    const snapshot = player.update(100, { sessionState: "running", isMoving: true });
    expect(snapshot.animationId).toBe("locomotion");
    expect(snapshot.framePath).toContain("locomotion/");
  });

  it("returns to idle after one-shot success finishes", () => {
    const player = new SpritePlayer(lobsterRuntimeManifest, "/assets/lobster");
    player.setAnimation("success", 0, { restart: true });
    const snapshot = player.update(2000);
    expect(snapshot.animationId).toBe("idle");
  });
});
