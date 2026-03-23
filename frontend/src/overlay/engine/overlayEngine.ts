import { getSpriteSet, SpritePlayer, type SpriteSet } from "../sprites";
import type { AgentEventPayload, FramePayload, SessionState, SessionStatePayload } from "../../protocol/types";
import type { LumonActionType } from "../sprites";

export interface SceneAgent {
  id: string;
  x: number;
  y: number;
  framePath: string;
  kind: string;
  summaryText: string;
  movementState: "anchored" | "local_glide" | "teleport_arrive";
  isWarping?: boolean;
  isMoving?: boolean;
  arrivalPulse?: boolean;
}

export interface SceneRipple {
  x: number;
  y: number;
  createdAt: number;
}

export interface SceneSnapshot {
  frameSrc: string | null;
  stageReady: boolean;
  sessionState: string;
  mainActionType: AgentEventPayload["action_type"] | null;
  caption: string;
  mainAgent: SceneAgent | null;
  subagents: SceneAgent[];
  ripples: SceneRipple[];
  targetPoint: { x: number; y: number } | null;
  targetRect: { x: number; y: number; width: number; height: number } | null;
  typing: boolean;
  fallbackMode: boolean;
}

const MAX_EVENT_QUEUE = 50;
const MAX_FRAME_QUEUE = 6;
const MAIN_SPRING_STIFFNESS = 250;
const MAIN_SPRING_DAMPING = 28;
const SUBAGENT_SPRING_STIFFNESS = 190;
const SUBAGENT_SPRING_DAMPING = 24;
const LOCAL_GLIDE_THRESHOLD = 56;
const TELEPORT_THRESHOLD = 92;
const ANCHORED_ACTION_THRESHOLD = 18;
const TARGET_COALESCE_WINDOW_MS = 250;
const TARGET_COALESCE_DISTANCE = 40;
const MOVEMENT_EPSILON = 0.42;
const VELOCITY_EPSILON = 8;
const EMIT_INTERVAL_MS = 1000 / 30;
const TAKEOVER_MAIN_SPRING_STIFFNESS = 72;
const TAKEOVER_MAIN_SPRING_DAMPING = 16;
const TRANSIENT_ACTION_HOLD_MS: Partial<Record<AgentEventPayload["action_type"], number>> = {
  click: 260,
  type: 420,
  navigate: 560,
  scroll: 560,
  read: 560,
  complete: 820,
  error: 820,
};

function terminalCaptionForState(state: SessionState): string {
  switch (state) {
    case "completed":
      return "Done.";
    case "failed":
      return "Something went wrong.";
    case "stopped":
      return "Stopped.";
    default:
      return "";
  }
}

export function resolveHotspotFromEvent(payload: Pick<AgentEventPayload, "cursor" | "target_rect">): { x: number; y: number } | null {
  if (payload.cursor) {
    return payload.cursor;
  }
  if (payload.target_rect) {
    return {
      x: payload.target_rect.x + Math.round(payload.target_rect.width / 2),
      y: payload.target_rect.y + Math.round(payload.target_rect.height / 2),
    };
  }
  return null;
}

export function spriteTargetFromHotspot(
  payload: Pick<AgentEventPayload, "action_type" | "cursor" | "target_rect">,
  hotspot: { x: number; y: number } | null,
): { x: number; y: number } {
  if (payload.action_type === "type" && payload.target_rect) {
    return {
      x: Math.max(22, Math.min(payload.target_rect.x - 18, 1258)),
      y: Math.max(22, Math.min(payload.target_rect.y - 10, 778)),
    };
  }

  if (!hotspot) {
    return {
      x: payload.cursor?.x ?? payload.target_rect?.x ?? 640,
      y: payload.cursor?.y ?? payload.target_rect?.y ?? 400,
    };
  }

  const horizontalOffset = hotspot.x > 1024 ? -28 : 28;
  const verticalOffset = hotspot.y < 120 ? 28 : -22;
  return {
    x: Math.max(22, Math.min(hotspot.x + horizontalOffset, 1258)),
    y: Math.max(22, Math.min(hotspot.y + verticalOffset, 778)),
  };
}

export function resolveMainSpringConfig(sessionState: SessionState): {
  stiffness: number;
  damping: number;
} {
  if (sessionState === "takeover") {
    return {
      stiffness: TAKEOVER_MAIN_SPRING_STIFFNESS,
      damping: TAKEOVER_MAIN_SPRING_DAMPING,
    };
  }
  return {
    stiffness: MAIN_SPRING_STIFFNESS,
    damping: MAIN_SPRING_DAMPING,
  };
}

interface TrackedAgent extends SceneAgent {
  targetX: number;
  targetY: number;
  warpUntilMs: number;
  arrivalPulseUntilMs: number;
  lastTargetUpdateMs: number;
  lastActionType: AgentEventPayload["action_type"] | null;
  vx: number;
  vy: number;
}

function toSpriteActionType(actionType: AgentEventPayload["action_type"]): LumonActionType {
  switch (actionType) {
    case "navigate":
    case "click":
    case "type":
    case "scroll":
    case "read":
    case "wait":
    case "complete":
    case "error":
      return actionType;
    case "spawn_subagent":
      return "wait";
    case "subagent_result":
      return "complete";
  }
}

export class OverlayEngine {
  private player: SpritePlayer;
  private readonly listeners = new Set<(snapshot: SceneSnapshot) => void>();
  private sessionState: SessionState = "idle";
  private stageReady = false;
  private frameSrc: string | null = null;
  private caption = "Awaiting run";
  private captionVisibleUntilMs = 0;
  private mainActionType: AgentEventPayload["action_type"] | null = null;
  private mainActionVisibleUntilMs = 0;
  private mainAgent: TrackedAgent | null = null;
  private subagents = new Map<string, TrackedAgent>();
  private ripples: SceneRipple[] = [];
  private targetPoint: { x: number; y: number } | null = null;
  private targetRect: { x: number; y: number; width: number; height: number } | null = null;
  private targetVisualVisibleUntilMs = 0;
  private typing = false;
  private pendingEvents: AgentEventPayload[] = [];
  private pendingFrames: FramePayload[] = [];
  private lastTickMs: number | null = null;
  private lastEmitMs = 0;
  private lastEventMs = 0;
  private fallbackMode = false;

  constructor(spriteSet: SpriteSet = getSpriteSet("lobster")) {
    this.player = new SpritePlayer(spriteSet.manifest, spriteSet.assetBasePath);
  }

  setFallbackMode(isActive: boolean): void {
    if (this.fallbackMode !== isActive) {
      this.fallbackMode = isActive;
      this.emit(performance.now(), true);
    }
  }

  setSpriteSet(spriteSet: SpriteSet): void {
    const nowMs = performance.now();
    this.player = new SpritePlayer(spriteSet.manifest, spriteSet.assetBasePath);
    const nextFrame = this.player.update(nowMs, {
      sessionState: this.sessionState,
      actionType: this.mainActionType ? toSpriteActionType(this.mainActionType) : undefined,
      isMoving: this.mainAgent ? this._isTrackedAgentMoving(this.mainAgent) : false,
    });
    if (this.mainAgent) {
      this.mainAgent = { ...this.mainAgent, framePath: nextFrame.framePath };
    }
    if (this.subagents.size > 0) {
      for (const [agentId, agent] of this.subagents) {
        this.subagents.set(agentId, { ...agent, framePath: nextFrame.framePath });
      }
    }
    this.emit(performance.now(), true);
  }

  subscribe(listener: (snapshot: SceneSnapshot) => void): () => void {
    this.listeners.add(listener);
    listener(this.snapshot());
    return () => this.listeners.delete(listener);
  }

  reset(): void {
    this.sessionState = "idle";
    this.stageReady = false;
    this.frameSrc = null;
    this.caption = "Awaiting run";
    this.captionVisibleUntilMs = 0;
    this.mainActionType = null;
    this.mainActionVisibleUntilMs = 0;
    this.mainAgent = null;
    this.subagents.clear();
    this.ripples = [];
    this.targetPoint = null;
    this.targetRect = null;
    this.targetVisualVisibleUntilMs = 0;
    this.typing = false;
    this.pendingEvents = [];
    this.pendingFrames = [];
    this.lastTickMs = null;
    this.lastEmitMs = 0;
    this.lastEventMs = performance.now();
    this.player.syncToRuntime({ sessionState: "idle", actionType: undefined, isMoving: false }, performance.now());
    this.emit(performance.now(), true);
  }

  setStageReady(ready: boolean): void {
    this.stageReady = ready;
    if (ready) {
      for (const frame of this.pendingFrames.splice(0)) {
        this.applyFrame(frame);
      }
      for (const event of this.pendingEvents.splice(0)) {
        this.applyEvent(event);
      }
      this.emit(performance.now(), true);
    }
  }

  applySessionState(payload: SessionStatePayload): void {
    this.sessionState = payload.state;
    const nowMs = performance.now();

    if (payload.state === "takeover" && !this.mainAgent) {
      const takeoverFrame = this.player.update(nowMs, {
        sessionState: payload.state,
        actionType: undefined,
        isMoving: false,
      }).framePath;
      this.mainAgent = {
        id: "main-takeover",
        x: 960,
        y: 540,
        targetX: 960,
        targetY: 540,
        framePath: takeoverFrame,
        kind: "main",
        summaryText: "Manual control is active.",
        movementState: "anchored",
        warpUntilMs: 0,
        arrivalPulseUntilMs: 0,
        lastTargetUpdateMs: nowMs,
        lastActionType: null,
        vx: 0,
        vy: 0,
      };
    }

    if (payload.state === "completed" || payload.state === "failed" || payload.state === "stopped") {
      this.caption = terminalCaptionForState(payload.state);
      this.captionVisibleUntilMs = nowMs + 2400;
    }

    this.player.syncToRuntime({ sessionState: payload.state }, performance.now());
    this.emit(performance.now(), true);
  }

  enqueueFrame(payload: FramePayload): void {
    if (!this.stageReady) {
      this.applyFrame(payload);
      this.pendingFrames.push(payload);
      this.pendingFrames = this.pendingFrames.slice(-MAX_FRAME_QUEUE);
      this.emit(performance.now(), true);
      return;
    }
    this.applyFrame(payload);
    this.emit(performance.now(), true);
  }

  enqueueEvent(payload: AgentEventPayload): void {
    if (!this.stageReady) {
      this.pendingEvents.push(payload);
      this.pendingEvents = this.pendingEvents.slice(-MAX_EVENT_QUEUE);
      return;
    }
    this.applyEvent(payload);
    this.emit(performance.now(), true);
  }

  tick(nowMs: number): void {
    const dtSeconds = this.lastTickMs === null ? 1 / 60 : Math.min((nowMs - this.lastTickMs) / 1000, 0.05);
    this.lastTickMs = nowMs;

    // Override logic when fallbackMode is active
    let effectiveActionType = this.mainActionType ? toSpriteActionType(this.mainActionType) : undefined;
    let effectiveCaption = this.caption;

    if (this.fallbackMode) {
      effectiveActionType = "read";

      if (!this.mainAgent) {
        // Ensure mainAgent exists in fallback mode even if no events received
        this.mainAgent = {
          id: "main-fallback",
          x: 960,
          y: 540,
          targetX: 960,
          targetY: 540,
          framePath: "",
          kind: "main",
          summaryText: this.caption || "Waiting for visible page",
          movementState: "anchored",
          warpUntilMs: 0,
          arrivalPulseUntilMs: 0,
          lastTargetUpdateMs: nowMs,
          lastActionType: "read",
          vx: 0,
          vy: 0,
        };
      } else {
        // Glide to center
        this.mainAgent.targetX = 960;
        this.mainAgent.targetY = 540;
        // Force glide state so it doesn't snap if it was far away
        this.mainAgent.movementState = "local_glide";
      }
    } else if (this.sessionState === "running" && nowMs - this.lastEventMs > 2500 && !this.mainActionType) {
      effectiveActionType = "wait";
      if (!effectiveCaption || effectiveCaption === "Awaiting run") {
        effectiveCaption = "Planning next step...";
      }
    }

    const nextFrame = this.player.update(nowMs, {
      sessionState: this.sessionState,
      actionType: effectiveActionType,
      isMoving: this.mainAgent ? this._isTrackedAgentMoving(this.mainAgent) : false,
    });
    const { stiffness: mainSpringStiffness, damping: mainSpringDamping } = resolveMainSpringConfig(this.sessionState);
    if (this.mainAgent) {
      this.mainAgent = {
        ...this._advanceAgentPosition(
          this.mainAgent,
          mainSpringStiffness,
          mainSpringDamping,
          dtSeconds,
          nowMs,
        ),
        framePath: nextFrame.framePath,
      };
    }
    if (this.subagents.size > 0) {
      for (const [agentId, agent] of this.subagents) {
        this.subagents.set(
          agentId,
          this._advanceAgentPosition(
            agent,
            SUBAGENT_SPRING_STIFFNESS,
            SUBAGENT_SPRING_DAMPING,
            dtSeconds,
            nowMs,
          ),
        );
      }
    }
    this.ripples = this.ripples.filter((ripple) => nowMs - ripple.createdAt < 500);
    if (this.targetRect && nowMs > this.targetVisualVisibleUntilMs) {
      this.targetRect = null;
    }
    if (this.targetPoint && nowMs > this.targetVisualVisibleUntilMs) {
      this.targetPoint = null;
    }
    if (this.mainActionType && nowMs > this.mainActionVisibleUntilMs) {
      this.mainActionType = null;
      this.typing = false;
    }
    if (this.caption && nowMs > this.captionVisibleUntilMs) {
      this.caption = "";
    }
    this.emit(nowMs, true);
  }

  private applyFrame(payload: FramePayload): void {
    this.frameSrc = `data:${payload.mime_type};base64,${payload.data_base64}`;
  }

  private applyEvent(payload: AgentEventPayload): void {
    const nowMs = performance.now();
    const isMicroAction = payload.action_type === "read" || payload.action_type === "wait" || payload.action_type === "scroll";
    const isWithinCoalesceWindow = nowMs - this.lastEventMs <= TARGET_COALESCE_WINDOW_MS;

    if (isMicroAction && isWithinCoalesceWindow && this.lastEventMs > 0) {
      const hotspot = this._resolveHotspot(payload);
      const spriteTarget = this._spriteTargetFromHotspot(payload, hotspot);
      const existingAgent =
        payload.agent_kind === "same_scene_subagent"
          ? this.subagents.get(payload.agent_id) ?? null
          : this.mainAgent;
      const agent = this._mergeAgentMotion(
        existingAgent,
        payload.agent_id,
        payload.agent_kind,
        payload.action_type,
        spriteTarget.x,
        spriteTarget.y,
        "",
        this.mainAgent?.summaryText ?? this.caption,
        nowMs,
      );
      if (payload.agent_kind === "same_scene_subagent") {
        this.subagents.set(payload.agent_id, agent);
      } else if (payload.agent_kind === "main") {
        this.mainAgent = agent;
      }
      if (payload.action_type === "click" && hotspot) {
        this.ripples.push({ x: hotspot.x, y: hotspot.y, createdAt: nowMs });
      }
      this.lastEventMs = nowMs;
      return;
    }

    this.lastEventMs = nowMs;
    this.caption = payload.summary_text;
    this.captionVisibleUntilMs = nowMs + (payload.action_type === "click" ? 850 : 1200);
    const hotspot = this._resolveHotspot(payload);
    this.targetPoint = hotspot;
    this.targetRect = payload.target_rect;
    this.targetVisualVisibleUntilMs = hotspot || payload.target_rect ? nowMs + 950 : 0;
    if (payload.agent_kind === "main") {
      const shouldReplaceVisualAction =
        !this.mainActionType ||
        nowMs >= this.mainActionVisibleUntilMs ||
        this._actionPriority(payload.action_type) >= this._actionPriority(this.mainActionType);
      if (shouldReplaceVisualAction) {
        this.mainActionType = payload.action_type;
        this.mainActionVisibleUntilMs = nowMs + (TRANSIENT_ACTION_HOLD_MS[payload.action_type] ?? 420);
      }
      this.typing = this.mainActionType === "type";
    }

    const spriteTarget = this._spriteTargetFromHotspot(payload, hotspot);
    const existingAgent =
      payload.agent_kind === "same_scene_subagent"
        ? this.subagents.get(payload.agent_id) ?? null
        : this.mainAgent;
    const agent = this._mergeAgentMotion(
      existingAgent,
      payload.agent_id,
      payload.agent_kind,
      payload.action_type,
      spriteTarget.x,
      spriteTarget.y,
      "",
      payload.summary_text,
      nowMs,
    );

    const nextFrame = this.player.update(nowMs, {
      sessionState: this.sessionState,
      actionType: toSpriteActionType(payload.action_type),
      isMoving: payload.agent_kind === "main" ? this._isTrackedAgentMoving(agent) : false,
    });
    agent.framePath = nextFrame.framePath;

    if (payload.agent_kind === "same_scene_subagent") {
      this.subagents.set(payload.agent_id, agent);
      if (payload.action_type === "subagent_result") {
        globalThis.setTimeout(() => {
          this.subagents.delete(payload.agent_id);
          this.emit(performance.now(), true);
        }, 600);
      }
    } else if (payload.agent_kind === "main") {
      this.mainAgent = agent;
    }

    if (payload.action_type === "click" && hotspot) {
      this.ripples.push({ x: hotspot.x, y: hotspot.y, createdAt: nowMs });
    }
  }

  private snapshot(effectiveCaption?: string): SceneSnapshot {
    let captionToUse = effectiveCaption ?? this.caption;
    if (!this.fallbackMode && this.sessionState === "running" && (performance.now() - this.lastEventMs > 2500) && !this.mainActionType && (!captionToUse || captionToUse === "Awaiting run")) {
      captionToUse = "Planning next step...";
    }
    return {
      frameSrc: this.frameSrc,
      stageReady: this.stageReady,
      sessionState: this.sessionState,
      mainActionType: this.mainActionType,
      caption: captionToUse,
      mainAgent: this.mainAgent ? this._toSceneAgent(this.mainAgent, performance.now()) : null,
      subagents: [...this.subagents.values()].map((agent) => this._toSceneAgent(agent, performance.now())),
      ripples: this.ripples,
      targetPoint: this.targetPoint,
      targetRect: this.targetRect,
      typing: this.typing,
      fallbackMode: this.fallbackMode,
    };
  }

  private _actionPriority(actionType: AgentEventPayload["action_type"]): number {
    switch (actionType) {
      case "error":
        return 5;
      case "complete":
        return 4;
      case "click":
      case "type":
        return 3;
      case "navigate":
      case "scroll":
      case "read":
        return 2;
      default:
        return 1;
    }
  }

  private _mergeAgentMotion(
    existing: TrackedAgent | null,
    agentId: string,
    agentKind: string,
    actionType: AgentEventPayload["action_type"],
    targetX: number,
    targetY: number,
    framePath: string,
    summaryText: string,
    nowMs: number,
  ): TrackedAgent {
    if (!existing) {
      return {
        id: agentId,
        x: targetX,
        y: targetY,
        targetX,
        targetY,
        framePath,
        kind: agentKind,
        summaryText,
        movementState: "anchored",
        warpUntilMs: 0,
        arrivalPulseUntilMs: nowMs + 180,
        lastTargetUpdateMs: nowMs,
        lastActionType: actionType,
        vx: 0,
        vy: 0,
      };
    }

    const targetDistance = Math.hypot(targetX - existing.targetX, targetY - existing.targetY);
    const isAnchoredAction =
      (actionType === "type" || actionType === "read") &&
      targetDistance <= ANCHORED_ACTION_THRESHOLD;
    const shouldCoalesce =
      nowMs - existing.lastTargetUpdateMs <= TARGET_COALESCE_WINDOW_MS &&
      targetDistance <= TARGET_COALESCE_DISTANCE &&
      existing.movementState === "local_glide";

    if (isAnchoredAction || shouldCoalesce) {
      return {
        ...existing,
        id: agentId,
        framePath,
        kind: agentKind,
        summaryText,
        movementState: "anchored",
        lastTargetUpdateMs: nowMs,
        lastActionType: actionType,
      };
    }

    if (targetDistance >= TELEPORT_THRESHOLD) {
      return {
        ...existing,
        id: agentId,
        x: targetX,
        y: targetY,
        targetX,
        targetY,
        framePath,
        kind: agentKind,
        summaryText,
        movementState: "teleport_arrive",
        warpUntilMs: nowMs + 210,
        arrivalPulseUntilMs: nowMs + 210,
        lastTargetUpdateMs: nowMs,
        lastActionType: actionType,
        vx: 0,
        vy: 0,
      };
    }

    if (targetDistance <= LOCAL_GLIDE_THRESHOLD) {
      return {
        ...existing,
        id: agentId,
        targetX,
        targetY,
        framePath,
        kind: agentKind,
        summaryText,
        movementState: "local_glide",
        lastTargetUpdateMs: nowMs,
        lastActionType: actionType,
      };
    }

    return {
      ...existing,
      id: agentId,
      x: targetX,
      y: targetY,
      targetX,
      targetY,
      framePath,
      kind: agentKind,
      summaryText,
      movementState: "teleport_arrive",
      warpUntilMs: nowMs + 210,
      arrivalPulseUntilMs: nowMs + 210,
      lastTargetUpdateMs: nowMs,
      lastActionType: actionType,
      vx: 0,
      vy: 0,
    };
  }

  private _advanceAgentPosition(
    agent: TrackedAgent,
    stiffness: number,
    damping: number,
    dtSeconds: number,
    nowMs: number,
  ): TrackedAgent {
    const ax = (agent.targetX - agent.x) * stiffness - agent.vx * damping;
    const ay = (agent.targetY - agent.y) * stiffness - agent.vy * damping;
    let nextVx = agent.vx + ax * dtSeconds;
    let nextVy = agent.vy + ay * dtSeconds;
    let nextX = agent.x + nextVx * dtSeconds;
    let nextY = agent.y + nextVy * dtSeconds;
    const closeEnough =
      Math.abs(agent.targetX - nextX) < MOVEMENT_EPSILON &&
      Math.abs(agent.targetY - nextY) < MOVEMENT_EPSILON &&
      Math.abs(nextVx) < VELOCITY_EPSILON &&
      Math.abs(nextVy) < VELOCITY_EPSILON;
    if (closeEnough) {
      nextX = agent.targetX;
      nextY = agent.targetY;
      nextVx = 0;
      nextVy = 0;
    }
    return {
      ...agent,
      x: nextX,
      y: nextY,
      vx: nextVx,
      vy: nextVy,
      movementState:
        agent.movementState === "local_glide" && closeEnough
          ? "anchored"
          : nowMs > agent.warpUntilMs && agent.movementState === "teleport_arrive"
            ? "anchored"
            : agent.movementState,
      warpUntilMs: nowMs > agent.warpUntilMs ? 0 : agent.warpUntilMs,
      arrivalPulseUntilMs: closeEnough && (agent.x !== agent.targetX || agent.y !== agent.targetY) ? nowMs + 150 : nowMs > agent.arrivalPulseUntilMs ? 0 : agent.arrivalPulseUntilMs,
    };
  }

  private _toSceneAgent(agent: TrackedAgent, nowMs: number): SceneAgent {
    return {
      id: agent.id,
      x: agent.x,
      y: agent.y,
      framePath: agent.framePath,
      kind: agent.kind,
      summaryText: agent.summaryText,
      movementState: agent.movementState,
      isWarping: nowMs <= agent.warpUntilMs,
      isMoving:
        agent.movementState === "local_glide" &&
        (Math.abs(agent.targetX - agent.x) > MOVEMENT_EPSILON || Math.abs(agent.targetY - agent.y) > MOVEMENT_EPSILON),
      arrivalPulse: nowMs <= agent.arrivalPulseUntilMs,
    };
  }

  private _isTrackedAgentMoving(agent: TrackedAgent): boolean {
    return (
      Math.abs(agent.targetX - agent.x) > MOVEMENT_EPSILON ||
      Math.abs(agent.targetY - agent.y) > MOVEMENT_EPSILON ||
      Math.abs(agent.vx) > VELOCITY_EPSILON ||
      Math.abs(agent.vy) > VELOCITY_EPSILON
    );
  }

  private _resolveHotspot(payload: AgentEventPayload): { x: number; y: number } | null {
    return resolveHotspotFromEvent(payload);
  }

  private _spriteTargetFromHotspot(
    payload: AgentEventPayload,
    hotspot: { x: number; y: number } | null,
  ): { x: number; y: number } {
    return spriteTargetFromHotspot(payload, hotspot);
  }

  private emit(nowMs: number = performance.now(), force = false): void {
    if (!force && nowMs - this.lastEmitMs < EMIT_INTERVAL_MS) {
      return;
    }
    this.lastEmitMs = nowMs;
    const snapshot = this.snapshot();
    for (const listener of this.listeners) {
      listener(snapshot);
    }
  }
}
