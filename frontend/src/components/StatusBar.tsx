import { useEffect, useMemo, useState } from "react";

import { getSpriteSet, SPRITE_FAMILY_OPTIONS, type SpriteFamily } from "../overlay/sprites";
import type { SessionStoreState } from "../store/sessionStore";

export function StatusBar({
  state,
  leftRailCollapsed,
  onToggleLeftRail,
  spriteFamily,
  onSpriteFamilyChange,
}: {
  state: SessionStoreState;
  leftRailCollapsed: boolean;
  onToggleLeftRail: () => void;
  spriteFamily: SpriteFamily;
  onSpriteFamilyChange: (family: SpriteFamily) => void;
}) {
  const sessionState = state.session?.state ?? "idle";
  const statusLabel =
    sessionState === "waiting_for_approval"
      ? "waiting"
      : sessionState === "takeover"
        ? "manual"
        : sessionState === "running" || sessionState === "starting"
          ? "working"
        : sessionState === "completed"
          ? "done"
        : sessionState === "failed"
          ? "error"
          : "watching";
  const spriteSet = useMemo(() => getSpriteSet(spriteFamily), [spriteFamily]);
  const showIdleSprite = (sessionState === "idle" || sessionState === "paused") && state.connectionState !== "error";
  // The top-bar mascot is intentionally calmer than the in-page sprite. It only
  // uses non-disruptive emotes and "teleports" between a few fixed perches.
  const idleSequence = useMemo(
    () =>
      [
        { lane: "22%", emote: "idle" as const, durationMs: 7600 },
        { lane: "50%", emote: "idle" as const, durationMs: 6800 },
        { lane: "78%", emote: "busy" as const, durationMs: 4200 },
        { lane: "50%", emote: "idle" as const, durationMs: 7200 },
        { lane: "28%", emote: "success" as const, durationMs: 3200 },
        { lane: "64%", emote: "idle" as const, durationMs: 7400 },
      ],
    [],
  );
  const [idleSequenceIndex, setIdleSequenceIndex] = useState(1);
  const [idleFrameIndex, setIdleFrameIndex] = useState(0);
  const [idleSpriteCycle, setIdleSpriteCycle] = useState(0);
  const [hoverThought, setHoverThought] = useState<string | null>(null);
  // Hover copy stays short on purpose so it reads like a tiny ambient thought,
  // not a second UI panel competing with the stage.
  const hoverThoughts = useMemo(
    () =>
      spriteFamily === "dog"
        ? [
            "👋",
            "still watching.",
            "good dog. good oversight.",
            "no weird clicks.",
            "that button looked suspicious.",
            "i saw that.",
            "maybe not that tab.",
            "subtle. very subtle.",
            "carry on.",
            "this better not be prod.",
            "great. another modal.",
            "bold move.",
            "i'm logging this mentally.",
            "that felt unnecessary.",
            "manual review energy.",
            "please let this be the right button.",
          ]
        : [
            "👋",
            "still watching.",
            "no weird clicks.",
            "tiny lobster, big oversight.",
            "all clear.",
            "that button looked suspicious.",
            "i saw that.",
            "maybe not that tab.",
            "subtle. very subtle.",
            "absolutely intentional, i hope.",
            "carry on.",
            "this better not be prod.",
            "great. another modal.",
            "this workflow has layers.",
            "bold move.",
            "i'm logging this mentally.",
            "that felt unnecessary.",
            "we're really doing this.",
            "interesting definition of safe.",
            "not my first sketchy redirect.",
            "clean click. questionable intent.",
            "i respect the confidence.",
            "one more popup and i'm filing a complaint.",
            "this could have been a shortcut.",
            "please let this be the right button.",
            "i've seen worse. barely.",
            "the audit trail writes itself.",
            "ah yes, the scenic route.",
            "we're flirting with a bad idea.",
            "impeccable chaos.",
            "if this opens a login wall, i'm judging.",
            "manual review energy.",
          ],
    [spriteFamily],
  );

  const activeIdleStep = idleSequence[idleSequenceIndex] ?? idleSequence[0];
  const idleEmote = activeIdleStep.emote;
  const idleAnimation = spriteSet.manifest.animations[idleEmote];
  const idleFrameDurationMs =
    idleAnimation.frame_duration_ms *
    (idleEmote === "idle" ? 1.85 : idleEmote === "busy" ? 0.9 : 1.05);
  const idleFramePath = idleAnimation.frame_paths[idleFrameIndex % idleAnimation.frame_paths.length];

  useEffect(() => {
    if (!showIdleSprite) {
      setIdleSequenceIndex(1);
      setIdleFrameIndex(0);
      setHoverThought(null);
      return;
    }

    // Step through the source sprite frames for the current emote while the
    // mascot is perched in the top bar.
    const frameInterval = window.setInterval(() => {
      setIdleFrameIndex((value) => (value + 1) % idleAnimation.frame_paths.length);
    }, idleFrameDurationMs);

    return () => window.clearInterval(frameInterval);
  }, [idleAnimation.frame_paths.length, idleFrameDurationMs, showIdleSprite]);

  useEffect(() => {
    if (!showIdleSprite) {
      return;
    }

    // Change perches slowly so the mascot feels ambient rather than restless.
    const laneTimeout = window.setTimeout(() => {
      setIdleSequenceIndex((value) => (value + 1) % idleSequence.length);
      setIdleFrameIndex(0);
      setIdleSpriteCycle((value) => value + 1);
    }, activeIdleStep.durationMs);

    return () => window.clearTimeout(laneTimeout);
  }, [activeIdleStep.durationMs, idleSequence.length, showIdleSprite]);

  return (
    <header className="status-bar">
      {showIdleSprite ? (
        <div className="status-idle-layer" aria-hidden="true">
          {hoverThought ? (
            <div className="status-thought-bubble" style={{ left: activeIdleStep.lane }}>
              {hoverThought}
            </div>
          ) : null}
          <img
            key={`${idleSequenceIndex}-${idleEmote}-${idleSpriteCycle}`}
            className={`status-idle-sprite status-idle-sprite-${idleEmote}`}
            style={{ left: activeIdleStep.lane }}
            src={`${spriteSet.assetBasePath}/${idleFramePath}`}
            alt=""
            onMouseEnter={() => {
              setHoverThought(hoverThoughts[Math.floor(Math.random() * hoverThoughts.length)] ?? "👋");
            }}
            onMouseLeave={() => setHoverThought(null)}
          />
        </div>
      ) : null}
      <div className="status-brand">
        <strong>Lumon</strong>
      </div>
      <div className="status-meta">
        <span className={`connection-indicator connection-${state.connectionState}`}>
          <span className="connection-dot" />
          <span className="connection-label">{statusLabel}</span>
        </span>
        <div className="rail-toggle-group" aria-label="Observation drawers">
          <div className="sprite-family-picker" role="group" aria-label="Mascot">
            {SPRITE_FAMILY_OPTIONS.map((option) => (
              <button
                key={option.family}
                type="button"
                className={`sprite-family-button${spriteFamily === option.family ? " is-active" : ""}`}
                onClick={() => onSpriteFamilyChange(option.family)}
                aria-pressed={spriteFamily === option.family}
                title={`Use ${option.label.toLowerCase()} mascot`}
              >
                {option.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            className={`rail-toggle ${leftRailCollapsed ? "" : "is-active"}`}
            onClick={onToggleLeftRail}
            aria-pressed={!leftRailCollapsed}
            aria-label={leftRailCollapsed ? "Show activity" : "Hide activity"}
            title={leftRailCollapsed ? "Show activity" : "Hide activity"}
          >
            ⏱
          </button>
        </div>
      </div>
    </header>
  );
}
