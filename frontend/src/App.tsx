import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import { LiveStage } from "./components/LiveStage";
import { ReviewMetricsSummary } from "./components/ReviewMetricsSummary";
import { StatusBar } from "./components/StatusBar";
import { TimelinePanel } from "./components/TimelinePanel";
import { demoTimeline } from "./fixtures/demoTimeline";
import { startFixtureReplay } from "./lib/fixtureReplay";
import {
  bootstrapSession,
  buildSessionWebSocketUrl,
  getBackendOrigin,
  sessionBootstrapFromUrl,
} from "./lib/sessionBootstrap";
import { readStoredSpriteFamily, writeStoredSpriteFamily } from "./lib/spriteSelection";
import {
  buildReviewStepSummary,
  defaultReviewSelection,
  deriveReviewSteps,
  getAdjacentReviewSelection,
  jumpToNextReviewStep,
  resolveReviewSelection,
  summarizeOutcome,
  type ReviewSelectionKey,
} from "./lib/reviewMode";
import { resolveReviewKeyframePath } from "./lib/reviewKeyframes";
import { SessionSocket } from "./lib/sessionSocket";
import { WebRTCClient, type WebRTCStatus } from "./lib/webrtcClient";
import { OverlayEngine, type SceneSnapshot, resolveHotspotFromEvent, spriteTargetFromHotspot } from "./overlay/engine/overlayEngine";
import { getSpriteSet, preloadSpriteFrames, SpritePlayer, type LumonActionType, type SpriteFamily } from "./overlay/sprites";
import type {
  ActionType,
  AgentEventPayload,
  AnyClientEnvelope,
  AnyServerEnvelope,
  ApprovalRequiredPayload,
  BridgeOfferPayload,
  SessionArtifactResponse,
} from "./protocol/types";
import type { ActiveIntervention, SessionStoreState } from "./store/sessionStore";
import { initialSessionStoreState, sessionStoreReducer } from "./store/sessionStore";

// Replay should be explicit-only; live websocket mode is the default user path.
const REPLAY_MODE = import.meta.env.VITE_LUMON_REPLAY === "true";
const WEBRTC_ENABLED = import.meta.env.VITE_LUMON_WEBRTC !== "false";

function buildPreviewState(search: string): {
  activeIntervention: ActiveIntervention | null;
  interactionModeOverride: "watch" | "takeover" | null;
} {
  const params = new URLSearchParams(search);
  const preview = params.get("preview");
  if (!preview) {
    return {
      activeIntervention: null,
      interactionModeOverride: null,
    };
  }

  if (preview === "approval") {
    const payload: ApprovalRequiredPayload = {
      intervention_id: "intv_preview_approval",
      session_id: "sess_preview",
      checkpoint_id: "chk_preview_001",
      event_id: "evt_preview_001",
      action_type: "submit",
      source_url: "https://example.com/checkout",
      target_summary: "Submit the final booking form",
      headline: "About to send your details",
      reason_text: "This will submit personal information through a live form.",
      recommended_action: "take_over",
      summary_text: "Ready to submit booking details",
      intent: "The agent has filled the form and wants to continue with the final submission step.",
      risk_level: "high",
      risk_reason: "This will send personal details through a live form.",
      adapter_id: "playwright_native",
      adapter_run_id: "run_preview_001",
    };
    return {
      activeIntervention: {
        interventionId: payload.intervention_id,
        kind: "approval",
        headline: payload.headline,
        reasonText: payload.reason_text,
        sourceUrl: payload.source_url ?? null,
        targetSummary: payload.target_summary ?? null,
        recommendedAction: payload.recommended_action,
        summaryText: payload.summary_text,
        intent: payload.intent,
        checkpointId: payload.checkpoint_id,
        sourceEventId: payload.event_id,
        riskReason: payload.risk_reason,
      },
      interactionModeOverride: "watch",
    };
  }

  if (preview === "bridge") {
    const payload: BridgeOfferPayload = {
      intervention_id: "intv_preview_bridge",
      session_id: "sess_preview",
      adapter_id: "opencode",
      adapter_run_id: "run_preview_001",
      web_mode: "delegate_playwright",
      web_bridge: "playwright_native",
      source_event_id: "src_preview_001",
      source_url: "https://example.com/search",
      target_summary: "Open this page in a visible browser view",
      headline: "Live browser view",
      reason_text: "Lumon can open this web step so you can follow it live.",
      recommended_action: "open_live_browser_view",
      summary_text: "Open a visible browser view before the agent continues.",
      intent: "Lumon can switch this web step into a live browser surface so you can watch the interaction unfold.",
    };
    return {
      activeIntervention: {
        interventionId: payload.intervention_id,
        kind: "live_browser_view",
        headline: payload.headline,
        reasonText: payload.reason_text,
        sourceUrl: payload.source_url ?? null,
        targetSummary: payload.target_summary ?? null,
        recommendedAction: payload.recommended_action,
        summaryText: payload.summary_text,
        intent: payload.intent,
        checkpointId: null,
        sourceEventId: payload.source_event_id,
        riskReason: null,
      },
      interactionModeOverride: "watch",
    };
  }

  if (preview === "takeover") {
    return {
      activeIntervention: {
        interventionId: "intv_preview_manual",
        kind: "manual_control",
        headline: "You are in control",
        reasonText: "The agent is paused until you return control.",
        sourceUrl: "https://example.com/settings",
        targetSummary: "Profile settings",
        recommendedAction: "return_control",
        summaryText: "Manual control is active.",
        intent: "Take over the page until you are ready to return control.",
        checkpointId: null,
        sourceEventId: null,
        riskReason: null,
      },
      interactionModeOverride: "takeover",
    };
  }

  return {
    activeIntervention: null,
    interactionModeOverride: null,
  };
}

function toSpriteActionType(actionType: ActionType): ActionType {
  if (actionType === "spawn_subagent") return "wait";
  if (actionType === "subagent_result") return "complete";
  return actionType;
}

function buildKeyframeUrl(backendOrigin: string, sessionId: string, keyframePath: string | null | undefined): string | null {
  if (!keyframePath) {
    return null;
  }
  const filename = keyframePath.split("/").at(-1);
  if (!filename) {
    return null;
  }
  return `${backendOrigin}/api/session-artifacts/${sessionId}/keyframes/${filename}`;
}

function toReviewSpriteAction(actionType: ActionType): LumonActionType {
  if (actionType === "spawn_subagent") {
    return "wait";
  }
  if (actionType === "subagent_result") {
    return "complete";
  }
  return actionType;
}

function buildReviewSnapshot(
  response: SessionArtifactResponse,
  selectedKey: ReviewSelectionKey,
  selectedEvent: AgentEventPayload | null,
  selectedIntervention: SessionArtifactResponse["artifact"]["interventions"][number] | null,
  backendOrigin: string,
  spriteFamily: SpriteFamily,
): SceneSnapshot {
  const spriteSet = getSpriteSet(spriteFamily);
  const spritePlayer = new SpritePlayer(spriteSet.manifest, spriteSet.assetBasePath);
  const activeAgentEvent = selectedEvent;
  const hotspot = activeAgentEvent ? resolveHotspotFromEvent(activeAgentEvent) : null;
  const spriteTarget = activeAgentEvent ? spriteTargetFromHotspot(activeAgentEvent, hotspot) : null;
  const keyframePath = resolveReviewKeyframePath(response, selectedKey, selectedEvent, selectedIntervention);
  const framePath = spritePlayer.update(performance.now(), {
    sessionState: response.artifact.status === "failed" ? "failed" : response.artifact.status === "completed" ? "completed" : "running",
    actionType: activeAgentEvent ? toReviewSpriteAction(activeAgentEvent.action_type) : undefined,
    isMoving: false,
  }).framePath;

  return {
    frameSrc: buildKeyframeUrl(backendOrigin, response.artifact.session_id, keyframePath),
    stageReady: true,
    sessionState: response.artifact.status,
    mainActionType: activeAgentEvent?.action_type ?? null,
    caption:
      selectedIntervention?.headline ??
      activeAgentEvent?.summary_text ??
      response.artifact.summary_text ??
      "Reviewing this run",
    mainAgent:
      spriteTarget && activeAgentEvent
        ? {
            id: activeAgentEvent.agent_id,
            x: spriteTarget.x,
            y: spriteTarget.y,
            framePath,
            kind: activeAgentEvent.agent_kind,
            summaryText: activeAgentEvent.summary_text,
            movementState: "anchored",
          }
        : null,
    subagents: [],
    ripples: [],
    targetPoint: hotspot,
    targetRect: activeAgentEvent?.target_rect ?? null,
    typing: activeAgentEvent?.action_type === "type",
  };
}

export default function App() {
  const [state, dispatch] = useReducer(sessionStoreReducer, initialSessionStoreState);
  const [leftRailCollapsed, setLeftRailCollapsed] = useState(true);
  const [spriteFamily, setSpriteFamily] = useState<SpriteFamily>(() => readStoredSpriteFamily());
  const [snapshot, setSnapshot] = useState<SceneSnapshot>({
    frameSrc: null,
    stageReady: false,
    sessionState: "idle",
    mainActionType: null,
    caption: "Awaiting run",
    mainAgent: null,
    subagents: [],
    ripples: [],
    targetPoint: null,
    targetRect: null,
    typing: false,
  });
  const [reviewData, setReviewData] = useState<SessionArtifactResponse | null>(null);
  const [reviewSelection, setReviewSelection] = useState<ReviewSelectionKey>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewMetricsVisible, setReviewMetricsVisible] = useState(false);
  const [webrtcStream, setWebrtcStream] = useState<MediaStream | null>(null);
  const [webrtcStatus, setWebrtcStatus] = useState<WebRTCStatus>("idle");
  const [webrtcRetryNonce, setWebrtcRetryNonce] = useState(0);
  const [frameFps, setFrameFps] = useState<number | null>(null);
  const engineRef = useRef<OverlayEngine | null>(null);
  const socketRef = useRef<SessionSocket | null>(null);
  const webrtcRef = useRef<WebRTCClient | null>(null);
  const webrtcSessionRef = useRef<string | null>(null);
  const frameTimesRef = useRef<number[]>([]);
  const previewState = useMemo(() => buildPreviewState(window.location.search), []);
  const uiReadySessionRef = useRef<string | null>(null);
  const searchParams = useMemo(() => new URLSearchParams(window.location.search), []);
  const reviewSessionId = searchParams.get("review_session");
  const backendOrigin = useMemo(() => getBackendOrigin(), []);
  const isReviewMode = Boolean(reviewSessionId);
  const spriteSet = useMemo(() => getSpriteSet(spriteFamily), [spriteFamily]);

  if (!engineRef.current) {
    engineRef.current = new OverlayEngine(spriteSet);
  }

  useEffect(() => {
    writeStoredSpriteFamily(spriteFamily);
    preloadSpriteFrames(spriteSet.manifest, spriteSet.assetBasePath).catch(() => undefined);
    engineRef.current?.setSpriteSet(spriteSet);
  }, [spriteFamily, spriteSet]);

  useEffect(() => {
    if (isReviewMode) {
      return;
    }
    return engineRef.current!.subscribe(setSnapshot);
  }, [isReviewMode]);

  useEffect(() => {
    if (!isReviewMode || !reviewSessionId) {
      return;
    }
    let cancelled = false;
    setReviewLoading(true);
    setReviewError(null);
    void (async () => {
      try {
        const response = await fetch(`${backendOrigin}/api/session-artifacts/${reviewSessionId}`, {
          headers: { Accept: "application/json" },
        });
        if (!response.ok) {
          throw new Error(`Review load failed with status ${response.status}`);
        }
        const payload = (await response.json()) as SessionArtifactResponse;
        if (cancelled) {
          return;
        }
        const steps = deriveReviewSteps(payload);
        setReviewData(payload);
        setReviewSelection(defaultReviewSelection(steps));
        setReviewMetricsVisible(false);
      } catch (error) {
        if (!cancelled) {
          setReviewError(error instanceof Error ? error.message : "Unable to load review data.");
        }
      } finally {
        if (!cancelled) {
          setReviewLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [backendOrigin, isReviewMode, reviewSessionId]);

  useEffect(() => {
    let stopReplay: () => void = () => {};
    let cancelled = false;
    if (isReviewMode) {
      dispatch({ type: "connection_state", payload: "disconnected" });
      return () => undefined;
    }
    if (REPLAY_MODE) {
      dispatch({ type: "connection_state", payload: "connected" });
      stopReplay = startFixtureReplay(demoTimeline, handleServerMessage);
    } else {
      dispatch({ type: "connection_state", payload: "connecting" });
      void (async () => {
        try {
          const bootstrap = sessionBootstrapFromUrl(window.location.search) ?? (await bootstrapSession());
          if (cancelled) {
            return;
          }
          socketRef.current = new SessionSocket(
            buildSessionWebSocketUrl(bootstrap),
            handleServerMessage,
            (connectionState) => dispatch({ type: "connection_state", payload: connectionState }),
          );
          socketRef.current.connect();
        } catch {
          if (!cancelled) {
            dispatch({ type: "connection_state", payload: "error" });
          }
        }
      })();
    }
    return () => {
      cancelled = true;
      stopReplay();
      socketRef.current?.disconnect();
      webrtcRef.current?.close();
      webrtcRef.current = null;
    };
  }, [isReviewMode]);

  useEffect(() => {
    if (isReviewMode) {
      return;
    }
    let animationFrame = 0;
    const tick = (time: number) => {
      engineRef.current?.tick(time);
      animationFrame = window.requestAnimationFrame(tick);
    };
    animationFrame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(animationFrame);
  }, [isReviewMode]);

  useEffect(() => {
    if (
      REPLAY_MODE ||
      isReviewMode ||
      state.connectionState !== "connected" ||
      !state.session?.session_id
    ) {
      return;
    }
    if (uiReadySessionRef.current === state.session.session_id) {
      return;
    }
    uiReadySessionRef.current = state.session.session_id;
    socketRef.current?.send({ type: "ui_ready", payload: { ready: true } });
  }, [isReviewMode, state.connectionState, state.session?.session_id]);

  useEffect(() => {
    if (isReviewMode || REPLAY_MODE) {
      return;
    }
    if (state.connectionState === "connected") {
      return;
    }
    uiReadySessionRef.current = null;
    engineRef.current?.reset();
    dispatch({ type: "live_reset" });
  }, [isReviewMode, state.connectionState]);

  const handleServerMessage = (message: AnyServerEnvelope) => {
    dispatch({ type: "server_message", payload: message });
    if (WEBRTC_ENABLED) {
      webrtcRef.current?.handleServerMessage(message);
    }
    if (message.type === "session_state") {
      engineRef.current?.applySessionState(message.payload);
    }
    if (message.type === "frame") {
      engineRef.current?.enqueueFrame(message.payload);
      const now = Date.now();
      const times = frameTimesRef.current;
      times.push(now);
      if (times.length > 30) {
        times.splice(0, times.length - 30);
      }
      if (times.length >= 2) {
        const elapsed = (times[times.length - 1] - times[0]) / 1000;
        if (elapsed > 0) {
          setFrameFps((times.length - 1) / elapsed);
        }
      }
    }
    if (message.type === "agent_event") {
      engineRef.current?.enqueueEvent(message.payload);
    }
  };

  const sendCommand = (message: AnyClientEnvelope) => {
    if (REPLAY_MODE || isReviewMode) {
      return;
    }
    if (message.type === "accept_bridge") {
      dispatch({ type: "resolve_intervention_local", payload: { resolution: "approved" } });
    }
    if (message.type === "decline_bridge") {
      dispatch({ type: "resolve_intervention_local", payload: { resolution: "dismissed" } });
    }
    if (message.type === "approve") {
      dispatch({ type: "resolve_intervention_local", payload: { resolution: "approved" } });
    }
    if (message.type === "reject") {
      dispatch({ type: "resolve_intervention_local", payload: { resolution: "denied" } });
    }
    socketRef.current?.send(message);
  };

  useEffect(() => {
    if (!WEBRTC_ENABLED || REPLAY_MODE || isReviewMode) {
      return;
    }
    if (state.connectionState !== "connected") {
      webrtcRef.current?.close();
      webrtcRef.current = null;
      webrtcSessionRef.current = null;
      setWebrtcStream(null);
      setWebrtcStatus("idle");
      return;
    }
    if (!state.session?.session_id) {
      return;
    }
    const shouldRequestWebrtc =
      state.session?.capabilities?.supports_frames ||
      state.session?.adapter_id === "playwright_native" ||
      state.session?.web_bridge === "playwright_native";
    if (!shouldRequestWebrtc) {
      return;
    }
    if (!webrtcRef.current) {
      webrtcRef.current = new WebRTCClient(
        (message) => socketRef.current?.send(message as AnyClientEnvelope),
        (status) => {
          setWebrtcStatus(status);
          if (status === "failed" || status === "closed" || status === "disconnected") {
            setWebrtcStream(null);
            webrtcSessionRef.current = null;
            setWebrtcRetryNonce((value) => value + 1);
          }
        },
        (stream) => setWebrtcStream(stream),
      );
    }
    if (webrtcSessionRef.current === state.session.session_id) {
      return;
    }
    webrtcSessionRef.current = state.session.session_id;
    setWebrtcStatus("connecting");
    webrtcRef.current.requestOffer();
  }, [isReviewMode, state.connectionState, state.session?.adapter_id, state.session?.capabilities?.supports_frames, state.session?.session_id, state.session?.web_bridge, webrtcRetryNonce]);

  useEffect(() => {
    if (REPLAY_MODE || isReviewMode) {
      return;
    }
    if (webrtcStatus !== "connecting" || webrtcStream) {
      return;
    }
    const timeoutId = window.setTimeout(() => {
      webrtcRef.current?.close();
      webrtcSessionRef.current = null;
      setWebrtcRetryNonce((value) => value + 1);
    }, 4000);
    return () => window.clearTimeout(timeoutId);
  }, [isReviewMode, webrtcStatus, webrtcStream]);

  const handleStageReady = useCallback((ready: boolean) => {
    dispatch({ type: "stage_ready", payload: ready });
    engineRef.current?.setStageReady(ready);
  }, []);

  const reviewSteps = useMemo(() => (reviewData ? deriveReviewSteps(reviewData) : []), [reviewData]);
  const reviewSelectionDetails = useMemo(
    () => (reviewData ? resolveReviewSelection(reviewData, reviewSteps, reviewSelection) : null),
    [reviewData, reviewSelection, reviewSteps],
  );
  const reviewSummary = useMemo(
    () =>
      reviewData && reviewSelectionDetails
        ? buildReviewStepSummary(reviewData.artifact, reviewSelectionDetails)
        : null,
    [reviewData, reviewSelectionDetails],
  );
  const reviewBrowserContext = reviewSelectionDetails?.browserContext ?? reviewData?.artifact.browser_context ?? null;

  const renderedSnapshot = useMemo(() => {
    if (!isReviewMode || !reviewData || !reviewSelectionDetails) {
      return snapshot;
    }
    return buildReviewSnapshot(
      reviewData,
      reviewSelection,
      reviewSelectionDetails.linkedEvent,
      reviewSelectionDetails.linkedIntervention,
      backendOrigin,
      spriteFamily,
    );
  }, [backendOrigin, isReviewMode, reviewData, reviewSelectionDetails, snapshot, spriteFamily]);

  const sessionLikeState: SessionStoreState = useMemo(() => {
    if (!isReviewMode || !reviewData) {
      return state;
    }
    return {
      ...state,
      session: {
        session_id: reviewData.artifact.session_id,
        adapter_id: reviewData.artifact.adapter_id as "opencode" | "playwright_native",
        adapter_run_id: reviewData.artifact.adapter_run_id,
        observer_mode: reviewData.artifact.observer_mode,
        web_mode: null,
        web_bridge: null,
        run_mode: "live",
        state:
          reviewData.artifact.status === "failed"
            ? "failed"
            : reviewData.artifact.status === "completed"
              ? "completed"
              : reviewData.artifact.status === "stopped"
                ? "stopped"
                : "running",
        interaction_mode: "watch",
        active_checkpoint_id: null,
        task_text: reviewData.artifact.task_text,
        viewport: { width: 1280, height: 800 },
        capabilities: {
          supports_pause: false,
          supports_approval: false,
          supports_takeover: false,
          supports_frames: Boolean(reviewData.artifact.keyframes.length),
        },
      },
      browserContext: reviewBrowserContext,
      pageVisits: reviewData.artifact.pages_visited.map((page) => ({
        url: page.url,
        domain: page.domain,
        title: page.title ?? null,
        environmentType: page.environment_type,
        firstSeenAt: page.first_seen_at,
        lastSeenAt: page.last_seen_at,
      })),
      browserCommands: [],
      interventions: reviewData.artifact.interventions,
      activeIntervention: previewState.activeIntervention,
      timeline: state.timeline,
      taskResult:
        reviewData.artifact.completed_at || reviewData.artifact.summary_text
          ? {
              session_id: reviewData.artifact.session_id,
              status:
                reviewData.artifact.status === "failed"
                  ? "failed"
                  : reviewData.artifact.status === "stopped"
                    ? "stopped"
                    : "completed",
              summary_text: reviewData.artifact.summary_text ?? "Run complete.",
              task_text: reviewData.artifact.task_text,
              adapter_id: reviewData.artifact.adapter_id as "opencode" | "playwright_native",
              adapter_run_id: reviewData.artifact.adapter_run_id,
            }
          : null,
      connectionState: "connected",
    };
  }, [isReviewMode, previewState.activeIntervention, reviewBrowserContext, reviewData, state]);

  const selectReviewStep = useCallback((key: ReviewSelectionKey) => {
    if (key) {
      setReviewSelection(key);
    }
  }, []);

  const goToPreviousStep = useCallback(() => {
    setReviewSelection((current) => getAdjacentReviewSelection(reviewSteps, current, -1));
  }, [reviewSteps]);

  const goToNextStep = useCallback(() => {
    setReviewSelection((current) => getAdjacentReviewSelection(reviewSteps, current, 1));
  }, [reviewSteps]);

  const jumpToNextIntervention = useCallback(() => {
    setReviewSelection((current) => jumpToNextReviewStep(reviewSteps, current, (step) => step.isIntervention));
  }, [reviewSteps]);

  const jumpToNextPageChange = useCallback(() => {
    setReviewSelection((current) => jumpToNextReviewStep(reviewSteps, current, (step) => step.isPageTransition));
  }, [reviewSteps]);

  return (
    <div className="app-shell">
      <StatusBar
        state={sessionLikeState}
        leftRailCollapsed={leftRailCollapsed}
        onToggleLeftRail={() => setLeftRailCollapsed((value) => !value)}
        spriteFamily={spriteFamily}
        onSpriteFamilyChange={setSpriteFamily}
      />
      <main className={`main-grid${leftRailCollapsed ? " left-collapsed" : ""}`}>
        <section className="stage-workspace">
          {isReviewMode && reviewData && reviewSelectionDetails && reviewSummary ? (
            <div className="review-overlay">
              <div className="review-header-strip">
                <div className="review-header-copy">
                  <span className={`review-outcome-pill is-${reviewData.artifact.status}`}>{reviewData.artifact.status}</span>
                  <div className="review-header-text">
                    <strong>{reviewBrowserContext?.title || reviewBrowserContext?.domain || summarizeOutcome(reviewData.artifact)}</strong>
                    <span>
                      Step {reviewSelectionDetails.selectedIndex + 1} of {reviewSelectionDetails.totalSteps}
                      {reviewBrowserContext?.domain ? ` · ${reviewBrowserContext.domain}` : ""}
                    </span>
                  </div>
                </div>
                <div className="review-header-actions">
                  <button type="button" className="review-nav-button" onClick={goToPreviousStep}>
                    Prev
                  </button>
                  <button type="button" className="review-nav-button" onClick={goToNextStep}>
                    Next
                  </button>
                  <button type="button" className="review-nav-button" onClick={jumpToNextIntervention}>
                    Next intervention
                  </button>
                  <button type="button" className="review-nav-button" onClick={jumpToNextPageChange}>
                    Next page
                  </button>
                  <button
                    type="button"
                    className={`review-nav-button${reviewMetricsVisible ? " is-active" : ""}`}
                    onClick={() => setReviewMetricsVisible((value) => !value)}
                  >
                    Local alpha summary
                  </button>
                </div>
              </div>
              <div className="review-step-summary">
                <span className="review-step-kicker">{reviewSummary.kicker}</span>
                <strong>{reviewSummary.headline}</strong>
                <p>{reviewSummary.detail}</p>
                <div className="review-step-meta">
                  <span>{reviewSummary.location}</span>
                  {reviewSummary.target ? <span>{reviewSummary.target}</span> : null}
                  {reviewSummary.outcome ? <span>{reviewSummary.outcome}</span> : null}
                </div>
              </div>
              {reviewMetricsVisible ? <ReviewMetricsSummary metrics={reviewData.artifact.metrics} /> : null}
            </div>
          ) : null}
          <div
            className={`rail-stack rail-stack-left rail-stack-overlay rail-stack-overlay-left${leftRailCollapsed ? " is-collapsed" : ""}`}
          >
            <TimelinePanel
              state={sessionLikeState}
              reviewArtifact={reviewData?.artifact ?? null}
              reviewEvents={reviewData?.events ?? []}
              reviewCommands={reviewData?.commands ?? []}
              reviewLoading={reviewLoading}
              reviewError={reviewError}
              selectedReviewKey={reviewSelection}
              onSelectReviewKey={selectReviewStep}
            />
          </div>
          <LiveStage
            snapshot={renderedSnapshot}
            adapterId={sessionLikeState.activeAdapterId}
            taskText={sessionLikeState.session?.task_text ?? "Watching your current task"}
            supportsFrames={sessionLikeState.session?.capabilities.supports_frames ?? Boolean(renderedSnapshot.frameSrc)}
            videoStream={WEBRTC_ENABLED ? webrtcStream : null}
            videoStatus={webrtcStatus}
            frameFps={frameFps}
            activeIntervention={previewState.activeIntervention ?? sessionLikeState.activeIntervention}
            browserContext={sessionLikeState.browserContext}
            capabilities={sessionLikeState.session?.capabilities ?? null}
            interactionMode={previewState.interactionModeOverride ?? sessionLikeState.session?.interaction_mode ?? "watch"}
            observerMode={Boolean(sessionLikeState.session?.observer_mode)}
            reviewMode={isReviewMode}
            onCommand={sendCommand}
            sessionStatus={sessionLikeState.session?.state}
            onStageReady={handleStageReady}
          />
        </section>
      </main>
    </div>
  );
}
