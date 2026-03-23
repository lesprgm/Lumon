import { describe, expect, it } from "vitest";

import { lobsterRuntimeManifest } from "./lobsterRuntimeManifest";
import { SpritePlayer } from "./spritePlayer";

describe("SpritePlayer", () => {
  it("keeps frame_count aligned with frame_paths length for smoothed lobster animations", () => {
    for (const animationId of ["idle", "busy", "reading", "success", "error", "locomotion"] as const) {
      const animation = lobsterRuntimeManifest.animations[animationId];
      expect(animation.frame_count).toBe(animation.frame_paths.length);
    }
  });

  it("maps action types to busy animation", () => {
    const player = new SpritePlayer(lobsterRuntimeManifest, "/assets/lobster");
    const snapshot = player.update(100, { sessionState: "running", actionType: "click" });
    expect(snapshot.animationId).toBe("busy");
    expect(snapshot.framePath).toContain("busy/");
  });

  it("maps read actions to the reading animation", () => {
    const player = new SpritePlayer(lobsterRuntimeManifest, "/assets/lobster");
    const snapshot = player.update(125, { sessionState: "running", actionType: "read" });
    expect(snapshot.animationId).toBe("reading");
    expect(snapshot.framePath).toContain("reading/");
  });

  it("uses locomotion animation while the sprite is moving", () => {
    const player = new SpritePlayer(lobsterRuntimeManifest, "/assets/lobster");
    const snapshot = player.update(100, { sessionState: "running", isMoving: true });
    expect(snapshot.animationId).toBe("locomotion");
    expect(snapshot.framePath).toContain("locomotion/");
  });

  it("maps completed and failed runtime states to success and error animations", () => {
    const player = new SpritePlayer(lobsterRuntimeManifest, "/assets/lobster");

    const successSnapshot = player.update(100, { sessionState: "completed" });
    expect(successSnapshot.animationId).toBe("success");
    expect(successSnapshot.framePath).toContain("success/");

    const errorSnapshot = player.update(200, { sessionState: "failed" });
    expect(errorSnapshot.animationId).toBe("error");
    expect(errorSnapshot.framePath).toContain("error/");
  });

  it("returns to idle after one-shot success finishes", () => {
    const player = new SpritePlayer(lobsterRuntimeManifest, "/assets/lobster");
    player.setAnimation("success", 0, { restart: true });
    const snapshot = player.update(2000);
    expect(snapshot.animationId).toBe("idle");
  });

  it("returns to idle after one-shot error finishes", () => {
    const player = new SpritePlayer(lobsterRuntimeManifest, "/assets/lobster");
    player.setAnimation("error", 0, { restart: true });
    const snapshot = player.update(2500);
    expect(snapshot.animationId).toBe("idle");
  });

  it("uses eased frame orderings for smoother lobster motion", () => {
    expect(lobsterRuntimeManifest.animations.idle.frame_paths).toEqual([
      "idle/frames/idle_01.png",
      "idle/frames/idle_02.png",
      "idle/frames/idle_03.png",
      "idle/frames/idle_04.png",
      "idle/frames/idle_05.png",
      "idle/frames/idle_04.png",
      "idle/frames/idle_03.png",
      "idle/frames/idle_02.png",
    ]);

    expect(lobsterRuntimeManifest.animations.locomotion.frame_paths).toEqual([
      "locomotion/frames/locomotion_01.png",
      "locomotion/frames/locomotion_01.png",
      "locomotion/frames/locomotion_02.png",
      "locomotion/frames/locomotion_03.png",
      "locomotion/frames/locomotion_04.png",
      "locomotion/frames/locomotion_04.png",
      "locomotion/frames/locomotion_05.png",
      "locomotion/frames/locomotion_06.png",
    ]);

    expect(lobsterRuntimeManifest.animations.busy.frame_paths).toEqual([
      "busy/frames/busy_01.png",
      "busy/frames/busy_02.png",
      "busy/frames/busy_03.png",
      "busy/frames/busy_04.png",
      "busy/frames/busy_05.png",
      "busy/frames/busy_06.png",
      "busy/frames/busy_05.png",
      "busy/frames/busy_04.png",
      "busy/frames/busy_03.png",
      "busy/frames/busy_02.png",
    ]);

    expect(lobsterRuntimeManifest.animations.reading.frame_paths).toEqual([
      "reading/frames/reading_01.png",
      "reading/frames/reading_02.png",
      "reading/frames/reading_03.png",
      "reading/frames/reading_04.png",
      "reading/frames/reading_05.png",
      "reading/frames/reading_06.png",
      "reading/frames/reading_05.png",
      "reading/frames/reading_04.png",
      "reading/frames/reading_03.png",
      "reading/frames/reading_02.png",
    ]);

    expect(lobsterRuntimeManifest.animations.success.frame_paths).toEqual([
      "success/frames/success_01.png",
      "success/frames/success_02.png",
      "success/frames/success_03.png",
      "success/frames/success_03.png",
      "success/frames/success_04.png",
      "success/frames/success_05.png",
      "success/frames/success_06.png",
      "success/frames/success_06.png",
    ]);

    expect(lobsterRuntimeManifest.animations.error.frame_paths).toEqual([
      "error/frames/error_01.png",
      "error/frames/error_02.png",
      "error/frames/error_03.png",
      "error/frames/error_03.png",
      "error/frames/error_04.png",
      "error/frames/error_05.png",
      "error/frames/error_06.png",
      "error/frames/error_06.png",
    ]);
  });

  it("times looping lobster animations on the intended eased poses", () => {
    const player = new SpritePlayer(lobsterRuntimeManifest, "/assets/lobster");

    player.setAnimation("idle", 0, { restart: true });
    expect(player.update(500).framePath).toContain("idle/frames/idle_05.png");
    expect(player.update(625).framePath).toContain("idle/frames/idle_04.png");

    player.setAnimation("locomotion", 0, { restart: true });
    expect(player.update(0).framePath).toContain("locomotion/frames/locomotion_01.png");
    expect(player.update(100).framePath).toContain("locomotion/frames/locomotion_01.png");
    expect(player.update(400).framePath).toContain("locomotion/frames/locomotion_04.png");
    expect(player.update(500).framePath).toContain("locomotion/frames/locomotion_04.png");

    player.setAnimation("busy", 0, { restart: true });
    expect(player.update(500).framePath).toContain("busy/frames/busy_06.png");
    expect(player.update(700).framePath).toContain("busy/frames/busy_04.png");

    player.setAnimation("reading", 0, { restart: true });
    expect(player.update(480).framePath).toContain("reading/frames/reading_05.png");
    expect(player.update(720).framePath).toContain("reading/frames/reading_05.png");
  });

  it("holds the intended peak poses in one-shot success and error animations", () => {
    const player = new SpritePlayer(lobsterRuntimeManifest, "/assets/lobster");

    player.setAnimation("success", 0, { restart: true });
    expect(player.update(280).framePath).toContain("success/frames/success_03.png");
    expect(player.update(420).framePath).toContain("success/frames/success_03.png");

    player.setAnimation("error", 0, { restart: true });
    expect(player.update(332).framePath).toContain("error/frames/error_03.png");
    expect(player.update(498).framePath).toContain("error/frames/error_03.png");
  });
});
