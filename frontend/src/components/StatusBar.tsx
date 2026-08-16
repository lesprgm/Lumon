import { useEffect, useState } from "react";

import { LOBSTER_ASSET_BASE_PATH, lobsterRuntimeManifest } from "../overlay/sprites";
import type { SessionStoreState } from "../store/sessionStore";

const TAKEOVER_THOUGHT = "Manual control is active.";
const TAKEOVER_THOUGHT_DURATION_MS = 3600;

function statusLabelFor(
  sessionState: NonNullable<SessionStoreState["session"]>["state"] | "idle",
): string {
  switch (sessionState) {
    case "waiting_for_approval":
      return "waiting";
    case "takeover":
      return "manual";
    case "running":
    case "starting":
      return "working";
    case "completed":
      return "done";
    case "failed":
      return "error";
    default:
      return "watching";
  }
}

export function StatusBar({
  state,
  leftRailCollapsed,
  onToggleLeftRail,
  showActivityToggle = true,
  reviewActionLabel,
  onReviewAction,
  onReturnControl,
}: {
  state: SessionStoreState;
  leftRailCollapsed: boolean;
  onToggleLeftRail: () => void;
  showActivityToggle?: boolean;
  reviewActionLabel?: string | null;
  onReviewAction?: (() => void) | null;
  onReturnControl?: (() => void) | null;
}) {
  const sessionState = state.session?.state ?? "idle";
  const statusLabel = statusLabelFor(sessionState);
  const takeoverActive =
    state.session?.interaction_mode === "takeover" ||
    sessionState === "takeover" ||
    state.activeIntervention?.kind === "manual_control";
  const showTakeoverSprite = takeoverActive && state.connectionState !== "error";
  const animation = lobsterRuntimeManifest.animations.reading;
  const [frameIndex, setFrameIndex] = useState(0);
  const [thoughtVisible, setThoughtVisible] = useState(false);

  useEffect(() => {
    if (!showTakeoverSprite) {
      setFrameIndex(0);
      return;
    }
    const interval = window.setInterval(() => {
      setFrameIndex((current) => (current + 1) % animation.frame_paths.length);
    }, animation.frame_duration_ms);
    return () => window.clearInterval(interval);
  }, [animation.frame_duration_ms, animation.frame_paths.length, showTakeoverSprite]);

  useEffect(() => {
    if (!showTakeoverSprite) {
      setThoughtVisible(false);
      return;
    }
    setThoughtVisible(true);
    const timeout = window.setTimeout(
      () => setThoughtVisible(false),
      TAKEOVER_THOUGHT_DURATION_MS,
    );
    return () => window.clearTimeout(timeout);
  }, [showTakeoverSprite]);

  return (
    <header className="status-bar">
      {showTakeoverSprite ? (
        <div className="status-idle-layer" aria-hidden="true">
          {thoughtVisible ? (
            <div className="status-thought-bubble" style={{ left: "50%" }}>
              {TAKEOVER_THOUGHT}
            </div>
          ) : null}
          <img
            className="status-idle-sprite status-idle-sprite-reading"
            style={{ left: "50%" }}
            src={`${LOBSTER_ASSET_BASE_PATH}/${animation.frame_paths[frameIndex]}`}
            alt=""
            data-status-sprite-mode="takeover"
            data-status-sprite-emote="reading"
            data-status-sprite-frame={frameIndex}
            data-status-stop-region="center"
            onMouseEnter={() => setThoughtVisible(true)}
            onMouseLeave={() => setThoughtVisible(false)}
          />
        </div>
      ) : null}
      <div className="status-brand">
        <strong>Lumon</strong>
      </div>
      <div className="status-meta">
        <span className={`connection-indicator connection-${state.connectionState} status-${statusLabel}`}>
          <span className="connection-dot" />
          <span className="connection-label">{statusLabel}</span>
        </span>
        {reviewActionLabel && onReviewAction ? (
          <button type="button" className="status-inline-action" onClick={onReviewAction}>
            {reviewActionLabel}
          </button>
        ) : null}
        {takeoverActive && onReturnControl ? (
          <button type="button" className="status-inline-action status-inline-action-primary" onClick={onReturnControl}>
            Return control
          </button>
        ) : null}
        <div className="rail-toggle-group" aria-label="Observation drawers">
          {showActivityToggle ? (
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
          ) : null}
        </div>
      </div>
    </header>
  );
}
