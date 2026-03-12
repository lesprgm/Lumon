import { useEffect, useMemo, useRef, useState } from "react";

import type {
  AdapterCapabilities,
  AgentEventPayload,
  AnyClientEnvelope,
  BrowserContextPayload,
  InteractionMode,
} from "../protocol/types";
import type { SceneSnapshot } from "../overlay/engine/overlayEngine";
import { scaleRect, scaleX, scaleY, unscaleX, unscaleY } from "./stageMath";
import type { ActiveIntervention } from "../store/sessionStore";

const MAIN_SPRITE_WIDTH = 34;
const MAIN_SPRITE_X_OFFSET = MAIN_SPRITE_WIDTH / 2;
const MAIN_SPRITE_Y_OFFSET = 42;
const SUBAGENT_SPRITE_WIDTH = 20;
const SUBAGENT_SPRITE_X_OFFSET = SUBAGENT_SPRITE_WIDTH / 2;
const SUBAGENT_SPRITE_Y_OFFSET = 24;

function cueToneForAction(actionType: AgentEventPayload["action_type"] | null): "neutral" | "click" | "type" | "read" | "success" | "error" {
  if (actionType === "click") return "click";
  if (actionType === "type") return "type";
  if (actionType === "read" || actionType === "navigate" || actionType === "scroll") return "read";
  if (actionType === "complete") return "success";
  if (actionType === "error") return "error";
  return "neutral";
}

function drawCornerFocus(ctx: CanvasRenderingContext2D, rect: { x: number; y: number; width: number; height: number }, color: string): void {
  const corner = Math.min(12, rect.width / 4, rect.height / 4);
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.3;
  ctx.beginPath();
  ctx.moveTo(rect.x, rect.y + corner);
  ctx.lineTo(rect.x, rect.y);
  ctx.lineTo(rect.x + corner, rect.y);
  ctx.moveTo(rect.x + rect.width - corner, rect.y);
  ctx.lineTo(rect.x + rect.width, rect.y);
  ctx.lineTo(rect.x + rect.width, rect.y + corner);
  ctx.moveTo(rect.x, rect.y + rect.height - corner);
  ctx.lineTo(rect.x, rect.y + rect.height);
  ctx.lineTo(rect.x + corner, rect.y + rect.height);
  ctx.moveTo(rect.x + rect.width - corner, rect.y + rect.height);
  ctx.lineTo(rect.x + rect.width, rect.y + rect.height);
  ctx.lineTo(rect.x + rect.width, rect.y + rect.height - corner);
  ctx.stroke();
  ctx.restore();
}

function motionClassForSnapshot(snapshot: SceneSnapshot): string {
  if (snapshot.sessionState === "failed") {
    return "is-error";
  }
  if (snapshot.sessionState === "completed" || snapshot.mainActionType === "complete") {
    return "is-success";
  }
  if (snapshot.mainActionType === "click") {
    return "is-clicking";
  }
  if (snapshot.mainActionType === "type") {
    return "is-typing";
  }
  if (snapshot.mainActionType && ["navigate", "scroll", "read"].includes(snapshot.mainActionType)) {
    return "is-busy";
  }
  return "is-idle";
}

function snapSpritePosition(value: number): number {
  return Math.round(value);
}

function clampStagePosition(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function liveViewLabel(observerMode: boolean, supportsFrames: boolean, adapterId: string): string {
  if (supportsFrames) {
    return "live browser view";
  }
  if (observerMode) {
    return "watching your agent";
  }
  return adapterId === "playwright_native" ? "live browser view" : "current task";
}

function browserContextLabel(environmentType: BrowserContextPayload["environment_type"] | null | undefined): string {
  switch (environmentType) {
    case "local":
      return "Local page";
    case "docs":
      return "Docs page";
    case "app":
      return "App page";
    case "external":
      return "External site";
    default:
      return "Current page";
  }
}

export function LiveStage({
  snapshot,
  onStageReady,
  adapterId,
  taskText,
  supportsFrames,
  videoStream,
  videoStatus,
  frameFps,
  activeIntervention,
  browserContext,
  capabilities,
  interactionMode,
  observerMode,
  reviewMode,
  onCommand,
  sessionStatus,
}: {
  snapshot: SceneSnapshot;
  onStageReady: (ready: boolean) => void;
  adapterId: string;
  taskText: string;
  supportsFrames: boolean;
  videoStream: MediaStream | null;
  videoStatus: "idle" | "connecting" | "connected" | "disconnected" | "failed" | "closed";
  frameFps: number | null;
  activeIntervention: ActiveIntervention | null;
  browserContext: BrowserContextPayload | null;
  capabilities: AdapterCapabilities | null;
  interactionMode: InteractionMode;
  observerMode: boolean;
  reviewMode: boolean;
  onCommand: (message: AnyClientEnvelope) => void;
  sessionStatus?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [hoverCelebration, setHoverCelebration] = useState(false);
  const [videoFps, setVideoFps] = useState<number | null>(null);

  const hasStageEvidence = Boolean(snapshot.frameSrc) || Boolean(videoStream) || Boolean(activeIntervention) || !supportsFrames;
  const overlaySpritesDisabled = import.meta.env.VITE_LUMON_OVERLAY_SPRITES === "false";
  const renderSprites = reviewMode || !overlaySpritesDisabled;
  const showVideo = Boolean(videoStream) && videoStatus !== "failed";

  useEffect(() => {
    if (!videoRef.current) {
      return;
    }
    if (!videoStream) {
      videoRef.current.srcObject = null;
      return;
    }
    videoRef.current.srcObject = videoStream;
  }, [videoStream]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !showVideo) {
      setVideoFps(null);
      return;
    }
    let frames = 0;
    let start = performance.now();
    let rafId = 0;

    const updateFps = () => {
      const now = performance.now();
      const elapsed = (now - start) / 1000;
      if (elapsed >= 1) {
        setVideoFps(frames / elapsed);
        frames = 0;
        start = now;
      }
    };

    const onVideoFrame = () => {
      frames += 1;
      updateFps();
      if ("requestVideoFrameCallback" in video) {
        (video as HTMLVideoElement & { requestVideoFrameCallback: (cb: () => void) => void }).requestVideoFrameCallback(onVideoFrame);
      } else {
        rafId = window.requestAnimationFrame(onVideoFrame);
      }
    };

    if ("requestVideoFrameCallback" in video) {
      (video as HTMLVideoElement & { requestVideoFrameCallback: (cb: () => void) => void }).requestVideoFrameCallback(onVideoFrame);
    } else {
      rafId = window.requestAnimationFrame(onVideoFrame);
    }

  






  return (

) => {
      if (rafId) {
        window.cancelAnimationFrame(rafId);
      }
    };
  }, [showVideo]);

  const displayFps = showVideo ? videoFps : frameFps;
  const fpsSource = showVideo ? "video" : "frames";

  useEffect(() => {
    onStageReady(hasStageEvidence);
  }, [hasStageEvidence, onStageReady]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const stage = stageRef.current;
    if (!canvas || !stage) {
      return;
    }
    const rect = stage.getBoundingClientRect();
    const canvasWidth = rect.width;
    const canvasHeight = rect.height;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      return;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const cueTone = cueToneForAction(snapshot.mainActionType);

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (snapshot.targetRect) {
      const scaledRect = scaleRect(snapshot.targetRect, canvasWidth, canvasHeight);
      if (cueTone === "type") {
        ctx.save();
        ctx.strokeStyle = "rgba(77, 171, 247, 0.54)";
        ctx.lineWidth = 1.4;
        ctx.shadowColor = "rgba(77, 171, 247, 0.14)";
        ctx.shadowBlur = 8;
        ctx.strokeRect(scaledRect.x, scaledRect.y, scaledRect.width, scaledRect.height);
        ctx.beginPath();
        ctx.moveTo(scaledRect.x + 6, scaledRect.y + scaledRect.height + 4);
        ctx.lineTo(scaledRect.x + scaledRect.width - 6, scaledRect.y + scaledRect.height + 4);
        ctx.stroke();
        ctx.restore();
      } else if (cueTone === "read") {
        drawCornerFocus(ctx, scaledRect, "rgba(148, 163, 184, 0.5)");
      } else if (cueTone === "success") {
        ctx.save();
        ctx.strokeStyle = "rgba(34, 197, 94, 0.42)";
        ctx.lineWidth = 1.4;
        ctx.shadowColor = "rgba(34, 197, 94, 0.14)";
        ctx.shadowBlur = 8;
        ctx.strokeRect(scaledRect.x, scaledRect.y, scaledRect.width, scaledRect.height);
        ctx.restore();
      } else if (cueTone === "error") {
        ctx.save();
        ctx.strokeStyle = "rgba(239, 68, 68, 0.42)";
        ctx.lineWidth = 1.4;
        ctx.shadowColor = "rgba(239, 68, 68, 0.14)";
        ctx.shadowBlur = 8;
        ctx.strokeRect(scaledRect.x, scaledRect.y, scaledRect.width, scaledRect.height);
        ctx.restore();
      } else {
        ctx.save();
        ctx.strokeStyle = "rgba(255, 208, 92, 0.24)";
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.shadowColor = "rgba(255, 208, 92, 0.12)";
        ctx.shadowBlur = 6;
        ctx.strokeRect(
          scaledRect.x,
          scaledRect.y,
          scaledRect.width,
          scaledRect.height,
        );
        ctx.restore();
      }
    }

    if (snapshot.targetPoint) {
      const x = scaleX(snapshot.targetPoint.x, canvasWidth);
      const y = scaleY(snapshot.targetPoint.y, canvasHeight);
      const markerStroke =
        cueTone === "type"
          ? "rgba(77, 171, 247, 0.96)"
          : cueTone === "read"
            ? "rgba(148, 163, 184, 0.86)"
            : cueTone === "success"
              ? "rgba(34, 197, 94, 0.94)"
              : cueTone === "error"
                ? "rgba(239, 68, 68, 0.94)"
                : "rgba(255, 208, 92, 0.95)";
      const markerFill =
        cueTone === "type"
          ? "rgba(239, 248, 255, 0.95)"
          : cueTone === "read"
            ? "rgba(248, 250, 252, 0.95)"
            : cueTone === "success"
              ? "rgba(240, 253, 244, 0.95)"
              : cueTone === "error"
                ? "rgba(254, 242, 242, 0.95)"
                : "rgba(255, 245, 214, 0.95)";
      ctx.save();
      ctx.strokeStyle = markerStroke;
      ctx.fillStyle = markerFill;
      ctx.lineWidth = cueTone === "read" ? 1.1 : 1.25;
      ctx.shadowColor = markerStroke.replace("0.95", "0.22").replace("0.96", "0.22").replace("0.94", "0.22").replace("0.86", "0.18");
      ctx.shadowBlur = cueTone === "read" ? 6 : 8;
      ctx.beginPath();
      ctx.arc(x, y, cueTone === "type" ? 5 : cueTone === "read" ? 3.5 : 4, 0, Math.PI * 2);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(x, y, cueTone === "type" ? 1.1 : 1.25, 0, Math.PI * 2);
      ctx.fill();
      if (cueTone === "type") {
        ctx.beginPath();
        ctx.moveTo(x, y - 8);
        ctx.lineTo(x, y + 8);
        ctx.stroke();
      } else if (cueTone === "read") {
        ctx.beginPath();
        ctx.moveTo(x - 5, y);
        ctx.lineTo(x + 5, y);
        ctx.stroke();
      } else {
        ctx.beginPath();
        ctx.moveTo(x - 7, y);
        ctx.lineTo(x - 3, y);
        ctx.moveTo(x + 3, y);
        ctx.lineTo(x + 7, y);
        ctx.moveTo(x, y - 7);
        ctx.lineTo(x, y - 3);
        ctx.moveTo(x, y + 3);
        ctx.lineTo(x, y + 7);
        ctx.stroke();
      }
      ctx.restore();
    }

    for (const ripple of snapshot.ripples) {
      ctx.beginPath();
      ctx.arc(scaleX(ripple.x, canvasWidth), scaleY(ripple.y, canvasHeight), 18, 0, Math.PI * 2);
      ctx.strokeStyle =
        cueTone === "error"
          ? "rgba(239, 68, 68, 0.8)"
          : cueTone === "success"
            ? "rgba(34, 197, 94, 0.74)"
            : cueTone === "type"
              ? "rgba(77, 171, 247, 0.8)"
              : "rgba(255, 208, 92, 0.74)";
      ctx.lineWidth = 2;
      ctx.stroke();
    }

    if (snapshot.typing && snapshot.mainAgent) {
      ctx.fillStyle = "rgba(0, 0, 0, 0.72)";
      ctx.fillRect(scaleX(snapshot.mainAgent.x, canvasWidth) - 20, scaleY(snapshot.mainAgent.y, canvasHeight) - 52, 40, 18);
      ctx.fillStyle = "#ffffff";
      ctx.fillText("...", scaleX(snapshot.mainAgent.x, canvasWidth) - 8, scaleY(snapshot.mainAgent.y, canvasHeight) - 39);
    }
  }, [snapshot]);

  const mainStyle = useMemo(() => {
    if (!snapshot.mainAgent || !stageRef.current) {
      return null;
    }
    const rect = stageRef.current.getBoundingClientRect();
    return {
      left: snapSpritePosition(scaleX(snapshot.mainAgent.x, rect.width) - MAIN_SPRITE_X_OFFSET),
      top: snapSpritePosition(scaleY(snapshot.mainAgent.y, rect.height) - MAIN_SPRITE_Y_OFFSET),
    };
  }, [snapshot]);

  const stageDimensions = useMemo(() => {
    if (!stageRef.current) {
      return null;
    }
    const rect = stageRef.current.getBoundingClientRect();
    return { width: rect.width, height: rect.height };
  }, [snapshot]);

  useEffect(() => {
    if (motionClassForSnapshot(snapshot) !== "is-idle") {
      setHoverCelebration(false);
    }
  }, [snapshot]);

  const mainMotionClass = useMemo(() => {
    if (hoverCelebration && motionClassForSnapshot(snapshot) === "is-idle") {
      return "is-hover-success";
    }
    return motionClassForSnapshot(snapshot);
  }, [hoverCelebration, snapshot]);

  const mainSpriteClasses = useMemo(() => {
    const classes = ["sprite", "sprite-main", mainMotionClass, `movement-${snapshot.mainAgent?.movementState ?? "anchored"}`];
    if (snapshot.mainAgent?.isMoving) {
      classes.push("is-moving");
    }
    if (snapshot.mainAgent?.arrivalPulse) {
      classes.push("is-arriving");
    }
    return classes.join(" ");
  }, [mainMotionClass, snapshot.mainAgent?.arrivalPulse, snapshot.mainAgent?.isMoving]);

  const showCaption =
    Boolean(snapshot.caption) &&
    snapshot.caption !== "Awaiting run" &&
    !activeIntervention &&
    interactionMode !== "takeover";

  const interventionState =
    activeIntervention?.kind === "approval"
      ? "approval"
      : activeIntervention?.kind === "live_browser_view"
        ? "bridge"
        : interactionMode === "takeover" || activeIntervention?.kind === "manual_control"
          ? "takeover"
          : null;

  const interventionLineStyle = useMemo(() => {
    if (!interventionState || !snapshot.mainAgent || !stageDimensions) {
      return null;
    }
    const sourceX = scaleX(snapshot.mainAgent.x, stageDimensions.width);
    const sourceY = scaleY(snapshot.mainAgent.y, stageDimensions.height);
    const targetX = stageDimensions.width / 2;
    const targetY = stageDimensions.height - 138;
    const dx = targetX - sourceX;
    const dy = targetY - sourceY;
    const length = Math.max(Math.sqrt(dx * dx + dy * dy) - 28, 0);
    const angle = Math.atan2(dy, dx) * (180 / Math.PI);
    return {
      width: `${length}px`,
      transform: `translate3d(${snapSpritePosition(sourceX)}px, ${snapSpritePosition(sourceY)}px, 0) rotate(${angle}deg)`,
    };
  }, [interventionState, snapshot.mainAgent, stageDimensions]);

  const interventionOriginStyle = useMemo(() => {
    if (!interventionState || !snapshot.mainAgent || !stageDimensions) {
      return null;
    }
    return {
      left: `${snapSpritePosition(scaleX(snapshot.mainAgent.x, stageDimensions.width))}px`,
      top: `${snapSpritePosition(scaleY(snapshot.mainAgent.y, stageDimensions.height))}px`,
    };
  }, [interventionState, snapshot.mainAgent, stageDimensions]);

  const captionAnchor = useMemo(() => {
    if (!showCaption || !stageDimensions) {
      return null;
    }

    const anchorX = snapshot.targetPoint
      ? scaleX(snapshot.targetPoint.x, stageDimensions.width)
      : snapshot.mainAgent
        ? scaleX(snapshot.mainAgent.x, stageDimensions.width)
        : stageDimensions.width / 2;
    const anchorY = snapshot.targetPoint
      ? scaleY(snapshot.targetPoint.y, stageDimensions.height)
      : snapshot.mainAgent
        ? scaleY(snapshot.mainAgent.y, stageDimensions.height)
        : stageDimensions.height / 2;

    const placeRight = anchorX < stageDimensions.width * 0.62;
    const bubbleWidth = Math.min(stageDimensions.width * 0.3, 288);
    const horizontalOffset = placeRight ? 34 : -(bubbleWidth + 34);
    const bubbleLeft = clampStagePosition(anchorX + horizontalOffset, 16, stageDimensions.width - bubbleWidth - 16);
    const placeBelow = anchorY < 92;
    const bubbleTop = clampStagePosition(anchorY + (placeBelow ? 18 : -50), 70, stageDimensions.height - 72);
    return {
      bubbleStyle: {
        left: `${snapSpritePosition(bubbleLeft)}px`,
        top: `${snapSpritePosition(bubbleTop)}px`,
      },
      bubbleClassName: `caption-anchor ${placeRight ? "is-right" : "is-left"} ${placeBelow ? "is-below" : "is-above"}`,
      tailStyle: {
        left: `${snapSpritePosition(anchorX)}px`,
        top: `${snapSpritePosition(anchorY)}px`,
      },
    };
  }, [showCaption, snapshot.targetPoint, snapshot.mainAgent, stageDimensions]);

  const captionToneClass = useMemo(() => {
    switch (cueToneForAction(snapshot.mainActionType)) {
      case "click":
        return "tone-click";
      case "type":
        return "tone-type";
      case "read":
        return "tone-read";
      case "success":
        return "tone-success";
      case "error":
        return "tone-error";
      default:
        return "tone-neutral";
    }
  }, [snapshot.mainActionType]);

  const hasVisibleBrowserTarget =
    Boolean(browserContext?.url) &&
    browserContext?.url !== "about:blank" &&
    browserContext?.domain !== "unknown";
  const placeholderHeadline = reviewMode
    ? "No keyframe captured for this step"
    : hasVisibleBrowserTarget
      ? "Waiting for the first visible page"
      : "Waiting for a visible page";
  const placeholderBody = reviewMode
    ? "This review step has browser context but no saved frame. Use the activity and command history to understand what happened here."
    : taskText;
  const placeholderNote = reviewMode
    ? "Review mode can still show the page, domain, intervention state, and command results even when no keyframe was captured."
    : snapshot.caption || "Lumon stays quiet until there is a page worth watching.";








  const canRemoteControl = (interactionMode === "takeover" || sessionStatus === "completed" || sessionStatus === "stopped" || sessionStatus === "failed") && !reviewMode;

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!canRemoteControl || !stageRef.current || !stageDimensions) return;
    const rect = stageRef.current.getBoundingClientRect();
    const x = unscaleX(e.clientX - rect.left, stageDimensions.width);
    const y = unscaleY(e.clientY - rect.top, stageDimensions.height);
    onCommand({ type: "remote_mouse_move", payload: { x, y } });
  };

  const handleMouseDown = (e: React.MouseEvent) => {
    if (!canRemoteControl || !stageRef.current || !stageDimensions) return;
    const rect = stageRef.current.getBoundingClientRect();
    const x = unscaleX(e.clientX - rect.left, stageDimensions.width);
    const y = unscaleY(e.clientY - rect.top, stageDimensions.height);
    const button = e.button === 0 ? "left" : e.button === 2 ? "right" : "middle";
    onCommand({ type: "remote_mouse_down", payload: { x, y, button } });
  };

  const handleMouseUp = (e: React.MouseEvent) => {
    if (!canRemoteControl || !stageRef.current || !stageDimensions) return;
    const rect = stageRef.current.getBoundingClientRect();
    const x = unscaleX(e.clientX - rect.left, stageDimensions.width);
    const y = unscaleY(e.clientY - rect.top, stageDimensions.height);
    const button = e.button === 0 ? "left" : e.button === 2 ? "right" : "middle";
    onCommand({ type: "remote_mouse_up", payload: { x, y, button } });
  };

  const handleWheel = (e: React.WheelEvent) => {
    if (!canRemoteControl) return;
    onCommand({ type: "remote_scroll", payload: { delta_x: e.deltaX, delta_y: e.deltaY } });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!canRemoteControl) return;
    onCommand({ type: "remote_key_down", payload: { key: e.key } });
  };

  const handleKeyUp = (e: React.KeyboardEvent) => {
    if (!canRemoteControl) return;
    onCommand({ type: "remote_key_up", payload: { key: e.key } });
  };

  return (
    <section
    className="live-stage"
    ref={stageRef}
    onMouseMove={handleMouseMove}
    onMouseDown={handleMouseDown}
    onMouseUp={handleMouseUp}
    onWheel={handleWheel}
    onKeyDown={handleKeyDown}
    onKeyUp={handleKeyUp}
    tabIndex={0}
    style={{ outline: "none" }}
  >
      <div className="stage-frame">
        <div className="stage-chrome stage-chrome-top">
          <div className="stage-browser-shell">
            <div className="stage-browser-dots" aria-hidden="true">
              <span />
              <span />
              <span />
            </div>
            <div className="stage-browser-address">
              {browserContext?.domain || liveViewLabel(observerMode, supportsFrames, adapterId)}
            </div>
            {browserContext ? (
              <div className="stage-browser-context" aria-label="Current page context">
                <span className="stage-browser-context-pill">{browserContextLabel(browserContext.environment_type)}</span>
                {browserContext.title ? <span className="stage-browser-title">{browserContext.title}</span> : null}
              </div>
            ) : null}
          </div>
        </div>
        {showVideo ? (
          <>
            <video className="browser-feed browser-feed-video" ref={videoRef} autoPlay muted playsInline />
            {videoStatus === "connecting" ? (
              <div className="stage-connecting">Connecting live video…</div>
            ) : null}
          </>
        ) : snapshot.frameSrc ? (
          <img className="browser-feed" src={snapshot.frameSrc} alt="Browser feed" />
        ) : (
          <div className={`browser-feed placeholder ${supportsFrames ? "" : "adapter-shell"}`}>
            {supportsFrames && hasVisibleBrowserTarget ? (
              <div className="stage-placeholder-shell">
                <div className="stage-placeholder-window">
                  <div className="stage-placeholder-toolbar">
                    <span />
                    <span />
                    <span />
                  </div>
                  <div className="stage-placeholder-canvas">
                    <div className="stage-placeholder-copy">
                      <span className="stage-placeholder-kicker">live browser</span>
                      <h3>{placeholderHeadline}</h3>
                      <p>{placeholderBody}</p>
                      <small>{placeholderNote}</small>
                    </div>
                    <div className="stage-placeholder-skeleton" aria-hidden="true">
                      <div className="stage-placeholder-line line-wide" />
                      <div className="stage-placeholder-line line-mid" />
                      <div className="stage-placeholder-line line-short" />
                      <div className="stage-placeholder-grid">
                        <span />
                        <span />
                        <span />
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="adapter-shell-content">
                <span className="adapter-shell-badge">{supportsFrames ? "getting ready" : "watching"}</span>
                <h3>{reviewMode ? "No keyframe captured for this step" : supportsFrames ? "Waiting for a visible page" : "No live page yet"}</h3>
                <p>{reviewMode ? placeholderBody : taskText}</p>
                <small>
                  {reviewMode
                    ? placeholderNote
                    : snapshot.caption ||
                    (supportsFrames
                      ? "Lumon will open the full browser stage once the first real page is visible."
                      : "Lumon will stay out of the way until online work matters.")}
                </small>
              </div>
            )}
          </div>
        )}
        <canvas ref={canvasRef} className="overlay-canvas" />
        {displayFps !== null ? (
          <div className="stage-fps">{`fps ${displayFps.toFixed(1)} (${fpsSource})`}</div>
        ) : null}
        {renderSprites && snapshot.mainAgent && mainStyle ? (
          <div
            className="sprite-positioner sprite-positioner-main"
            style={{
              transform: `translate3d(${mainStyle.left}px, ${mainStyle.top}px, 0)`,
            }}
          >
            <img
              className={mainSpriteClasses}
              src={snapshot.mainAgent.framePath}
              alt="Main sprite"
              onMouseEnter={() => {
                if (motionClassForSnapshot(snapshot) === "is-idle") {
                  setHoverCelebration(true);
                }
              }}
              onMouseLeave={() => setHoverCelebration(false)}
            />
          </div>
        ) : null}
        {renderSprites
          ? snapshot.subagents
              .filter((agent) => agent.id !== snapshot.mainAgent?.id)
              .map((agent) => (
              <div
                key={agent.id}
                className="sprite-positioner sprite-positioner-subagent"
                style={{
                  transform: `translate3d(${snapSpritePosition(scaleX(agent.x, stageDimensions?.width ?? 1280) - SUBAGENT_SPRITE_X_OFFSET)}px, ${snapSpritePosition(scaleY(agent.y, stageDimensions?.height ?? 800) - SUBAGENT_SPRITE_Y_OFFSET)}px, 0)`,
                }}
              >
                <img
                  className={`sprite sprite-subagent movement-${agent.movementState} ${agent.isMoving ? "is-moving" : ""} ${agent.arrivalPulse ? "is-arriving" : ""}`}
                  src={agent.framePath}
                  alt="Subagent sprite"
                />
              </div>
            ))
          : null}
        {showCaption && captionAnchor ? (
          <>
            <div className="caption-tail-anchor" style={captionAnchor.tailStyle} aria-hidden="true" />
            <div className={captionAnchor.bubbleClassName} style={captionAnchor.bubbleStyle}>
              <div className={`caption-bubble ${captionToneClass}`}>{snapshot.caption}</div>
            </div>
          </>
        ) : null}
        {interventionOriginStyle ? <div className="intervention-origin-pulse" style={interventionOriginStyle} aria-hidden="true" /> : null}
        {interventionLineStyle ? <div className="intervention-link" style={interventionLineStyle} aria-hidden="true" /> : null}
        {activeIntervention?.kind === "approval" ? (
          <div className="intervention-overlay">
            <div className="intervention-card intervention-card-approval">
              <div className="intervention-kicker-row">
                <span className="intervention-kicker">Lumon paused here</span>
                <span className="intervention-state-pill">needs your approval</span>
              </div>
              <div className="intervention-copy">
                <strong>{activeIntervention.headline || activeIntervention.summaryText}</strong>
                <p>{activeIntervention.intent || "The agent is ready to continue, but this next step needs a human decision."}</p>
              </div>
              <div className="intervention-reason">
                <span>Why Lumon stopped</span>
                <strong>{activeIntervention.reasonText || activeIntervention.riskReason || "This next step needs a human decision."}</strong>
              </div>
              {activeIntervention.sourceUrl || activeIntervention.targetSummary ? (
                <div className="intervention-context-line">
                  {activeIntervention.sourceUrl ? <span>{activeIntervention.sourceUrl}</span> : null}
                  {activeIntervention.targetSummary ? <strong>{activeIntervention.targetSummary}</strong> : null}
                </div>
              ) : null}
              <div className="intervention-actions">
                <button className="intervention-button intervention-button-ghost" onClick={() => onCommand({ type: "reject", payload: { checkpoint_id: activeIntervention.checkpointId ?? "" } })}>
                  Deny
                </button>
                {capabilities?.supports_takeover ? (
                  <button className="intervention-button intervention-button-soft" onClick={() => onCommand({ type: "start_takeover", payload: {} })}>
                    Take over
                  </button>
                ) : null}
                <button className="intervention-button intervention-button-primary" onClick={() => onCommand({ type: "approve", payload: { checkpoint_id: activeIntervention.checkpointId ?? "" } })}>
                  Approve
                </button>
              </div>
            </div>
          </div>
        ) : null}
        {activeIntervention?.kind === "live_browser_view" ? (
          <div className="intervention-overlay">
            <div className="intervention-card intervention-card-bridge">
              <div className="intervention-kicker-row">
                <span className="intervention-kicker">Live browser view</span>
                <span className="intervention-state-pill">ready to open</span>
              </div>
              <div className="intervention-copy">
                <strong>{activeIntervention.headline || "Open a visible browser view"}</strong>
                <p>{activeIntervention.summaryText}</p>
              </div>
              <div className="intervention-reason">
                <span>What changes</span>
                <strong>{activeIntervention.reasonText || "You will be able to watch this web step live."}</strong>
              </div>
              {activeIntervention.sourceUrl || activeIntervention.targetSummary ? (
                <div className="intervention-context-line">
                  {activeIntervention.sourceUrl ? <span>{activeIntervention.sourceUrl}</span> : null}
                  {activeIntervention.targetSummary ? <strong>{activeIntervention.targetSummary}</strong> : null}
                </div>
              ) : null}
              <div className="intervention-actions">
                <button className="intervention-button intervention-button-ghost" onClick={() => onCommand({ type: "decline_bridge", payload: {} })}>
                  Not now
                </button>
                <button className="intervention-button intervention-button-primary" onClick={() => onCommand({ type: "accept_bridge", payload: {} })}>
                  Open view
                </button>
              </div>
            </div>
          </div>
        ) : null}
        {(interactionMode === "takeover" || activeIntervention?.kind === "manual_control") && activeIntervention?.kind !== "approval" ? (
          <div className="intervention-overlay">
            <div className="intervention-card intervention-card-takeover">
              <div className="intervention-kicker-row">
                <span className="intervention-kicker">You are in control</span>
                <span className="intervention-state-pill">manual control</span>
              </div>
              <div className="intervention-copy">
                <strong>{activeIntervention?.headline || "You now control the page"}</strong>
                <p>{activeIntervention?.reasonText || "The agent is paused here until you return control."}</p>
              </div>
              {activeIntervention?.sourceUrl || activeIntervention?.targetSummary ? (
                <div className="intervention-context-line">
                  {activeIntervention.sourceUrl ? <span>{activeIntervention.sourceUrl}</span> : null}
                  {activeIntervention.targetSummary ? <strong>{activeIntervention.targetSummary}</strong> : null}
                </div>
              ) : null}
              <div className="intervention-actions">
                <button className="intervention-button intervention-button-primary" onClick={() => onCommand({ type: "end_takeover", payload: {} })}>
                  Return control
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
