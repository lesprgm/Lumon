import type {
  AgentEventPayload,
  AnyServerEnvelope,
  ApprovalRequiredPayload,
  BackgroundWorkerUpdatePayload,
  BrowserCommandRecord,
  BrowserContextPayload,
  BridgeOfferPayload,
  ErrorPayload,
  FramePayload,
  InterventionRecord,
  SessionStatePayload,
  TaskResultPayload,
} from "../protocol/types";

export type ConnectionState = "disconnected" | "connecting" | "connected" | "error";

export interface ActiveIntervention {
  interventionId: string;
  kind: "approval" | "live_browser_view" | "manual_control";
  headline: string;
  reasonText: string;
  sourceUrl: string | null;
  targetSummary: string | null;
  recommendedAction: string | null;
  summaryText: string;
  intent: string;
  checkpointId: string | null;
  sourceEventId: string | null;
  riskReason: string | null;
}

export interface TimelineRow {
  id: string;
  eventSeq: number;
  actorId: string;
  actorKind: string;
  actionType: string;
  riskLevel: string;
  summaryText: string;
  intent: string;
  timestamp: string;
  targetSummary: string | null;
  sourceUrl: string | null;
  domain: string | null;
}

export interface PageVisitSummary {
  url: string;
  domain: string;
  title: string | null;
  environmentType: "local" | "docs" | "app" | "external";
  firstSeenAt: string;
  lastSeenAt: string;
}

export interface SessionStoreState {
  connectionState: ConnectionState;
  stageReady: boolean;
  session: SessionStatePayload | null;
  activeIntervention: ActiveIntervention | null;
  interventions: InterventionRecord[];
  browserContext: BrowserContextPayload | null;
  pageVisits: PageVisitSummary[];
  browserCommands: BrowserCommandRecord[];
  activeAdapterId: string;
  adapterRunId: string;
  agents: Record<string, AgentEventPayload>;
  workers: Record<string, BackgroundWorkerUpdatePayload>;
  timeline: TimelineRow[];
  lastError: ErrorPayload | null;
  lastEventSeq: number;
  lastFrameSeq: number;
  latestFrame: FramePayload | null;
  taskResult: TaskResultPayload | null;
}

export const initialSessionStoreState: SessionStoreState = {
  connectionState: "disconnected",
  stageReady: false,
  session: null,
  activeIntervention: null,
  interventions: [],
  browserContext: null,
  pageVisits: [],
  browserCommands: [],
  activeAdapterId: "opencode",
  adapterRunId: "",
  agents: {},
  workers: {},
  timeline: [],
  lastError: null,
  lastEventSeq: 0,
  lastFrameSeq: 0,
  latestFrame: null,
  taskResult: null,
};

export type SessionStoreAction =
  | { type: "connection_state"; payload: ConnectionState }
  | { type: "stage_ready"; payload: boolean }
  | { type: "live_reset" }
  | { type: "resolve_intervention_local"; payload: { resolution: InterventionRecord["resolution"] } }
  | { type: "server_message"; payload: AnyServerEnvelope };

function browserCommandStoreKey(command: Pick<BrowserCommandRecord, "command_id" | "command">): string {
  return `${command.command}:${command.command_id}`;
}

function mergePageVisit(
  pages: PageVisitSummary[],
  payload: BrowserContextPayload,
): PageVisitSummary[] {
  const last = pages.at(-1);
  if (!last || last.url !== payload.url) {
    return [
      ...pages,
      {
        url: payload.url,
        domain: payload.domain,
        title: payload.title,
        environmentType: payload.environment_type,
        firstSeenAt: payload.timestamp,
        lastSeenAt: payload.timestamp,
      },
    ];
  }
  const next = [...pages];
  next[next.length - 1] = {
    ...last,
    title: payload.title ?? last.title,
    domain: payload.domain,
    environmentType: payload.environment_type,
    lastSeenAt: payload.timestamp,
  };
  return next;
}

function appendOrUpdateIntervention(
  interventions: InterventionRecord[],
  record: InterventionRecord,
): InterventionRecord[] {
  const index = interventions.findIndex((item) => item.intervention_id === record.intervention_id);
  if (index === -1) {
    return [...interventions, record];
  }
  const next = [...interventions];
  next[index] = record;
  return next;
}

function resolveIntervention(
  interventions: InterventionRecord[],
  interventionId: string | null | undefined,
  resolution: InterventionRecord["resolution"],
): InterventionRecord[] {
  if (!interventionId) {
    return interventions;
  }
  return interventions.map((item) =>
    item.intervention_id === interventionId
      ? {
          ...item,
          resolution,
          resolved_at: item.resolved_at ?? new Date().toISOString(),
        }
      : item,
  );
}

function approvalToIntervention(payload: ApprovalRequiredPayload): ActiveIntervention {
  return {
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
  };
}

function bridgeToIntervention(payload: BridgeOfferPayload): ActiveIntervention {
  return {
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
  };
}

function manualControlIntervention(state: SessionStoreState): ActiveIntervention {
  return {
    interventionId: `manual_${state.session?.session_id ?? "active"}`,
    kind: "manual_control",
    headline: "You are in control",
    reasonText: "The agent is paused until you return control.",
    sourceUrl: state.browserContext?.url ?? null,
    targetSummary: state.browserContext?.title ?? null,
    recommendedAction: "return_control",
    summaryText: "Manual control is active.",
    intent: "Take over the page until you are ready to return control.",
    checkpointId: null,
    sourceEventId: null,
    riskReason: null,
  };
}

function applySessionState(state: SessionStoreState, payload: SessionStatePayload): SessionStoreState {
  const sessionChanged = state.session?.session_id != null && state.session.session_id !== payload.session_id;
  const nextState: SessionStoreState = {
    ...state,
    session: payload,
    activeAdapterId: payload.adapter_id,
    adapterRunId: payload.adapter_run_id,
    ...(sessionChanged
      ? {
          agents: {},
          workers: {},
          timeline: [],
          browserCommands: [],
          browserContext: null,
          pageVisits: [],
          interventions: [],
          activeIntervention: null,
          latestFrame: null,
          lastEventSeq: 0,
          lastFrameSeq: 0,
          taskResult: null,
        }
      : {}),
  };

  if (payload.interaction_mode === "takeover") {
    const manualIntervention = manualControlIntervention(nextState);
    return {
      ...nextState,
      activeIntervention: manualIntervention,
      interventions: appendOrUpdateIntervention(nextState.interventions, {
        intervention_id: manualIntervention.interventionId,
        kind: "manual_control",
        headline: manualIntervention.headline,
        reason_text: manualIntervention.reasonText,
        source_url: manualIntervention.sourceUrl,
        target_summary: manualIntervention.targetSummary,
        recommended_action: manualIntervention.recommendedAction,
        started_at: new Date().toISOString(),
        resolution: null,
        resolved_at: null,
        checkpoint_id: null,
        source_event_id: null,
        keyframe_path: null,
        domain: nextState.browserContext?.domain ?? null,
      }),
    };
  }

  if (state.activeIntervention?.kind === "manual_control") {
    return {
      ...nextState,
      activeIntervention: null,
      interventions: resolveIntervention(
        nextState.interventions,
        state.activeIntervention.interventionId,
        "taken_over",
      ),
    };
  }

  if (payload.state !== "waiting_for_approval" && state.activeIntervention?.kind === "approval") {
    return {
      ...nextState,
      activeIntervention: null,
    };
  }

  if (
    state.activeIntervention?.kind === "live_browser_view" &&
    (payload.state === "completed" || payload.state === "failed" || payload.state === "stopped")
  ) {
    return {
      ...nextState,
      activeIntervention: null,
    };
  }

  return nextState;
}

function applyFrame(state: SessionStoreState, payload: FramePayload): SessionStoreState {
  if (payload.frame_seq <= state.lastFrameSeq) {
    return state;
  }
  return {
    ...state,
    latestFrame: payload,
    lastFrameSeq: payload.frame_seq,
  };
}

function applyAgentEvent(state: SessionStoreState, payload: AgentEventPayload): SessionStoreState {
  if (payload.event_seq <= state.lastEventSeq) {
    return state;
  }
  const row: TimelineRow = {
    id: payload.event_id,
    eventSeq: payload.event_seq,
    actorId: payload.agent_id,
    actorKind: payload.agent_kind,
    actionType: payload.action_type,
    riskLevel: payload.risk_level,
    summaryText: payload.summary_text,
    intent: payload.intent,
    timestamp: payload.timestamp,
    targetSummary: payload.target_summary ?? null,
    sourceUrl: state.browserContext?.url ?? null,
    domain: state.browserContext?.domain ?? null,
  };
  return {
    ...state,
    agents: { ...state.agents, [payload.agent_id]: payload },
    timeline: [...state.timeline, row].slice(-200),
    lastEventSeq: payload.event_seq,
    activeAdapterId: payload.adapter_id,
    adapterRunId: payload.adapter_run_id,
  };
}

function applyWorkerUpdate(state: SessionStoreState, payload: BackgroundWorkerUpdatePayload): SessionStoreState {
  return {
    ...state,
    workers: { ...state.workers, [payload.agent_id]: payload },
  };
}

function applyBrowserContext(state: SessionStoreState, payload: BrowserContextPayload): SessionStoreState {
  return {
    ...state,
    browserContext: payload,
    pageVisits: mergePageVisit(state.pageVisits, payload),
  };
}

function applyBrowserCommand(state: SessionStoreState, payload: BrowserCommandRecord): SessionStoreState {
  const upsertCommands = (commands: BrowserCommandRecord[]): BrowserCommandRecord[] => {
    const next = [...commands];
    const existingIndex = next.findIndex((command) => browserCommandStoreKey(command) === browserCommandStoreKey(payload));
    if (existingIndex >= 0) {
      next[existingIndex] = payload;
      return next.slice(-120);
    }
    return [...next, payload].slice(-120);
  };

  if (payload.command === "begin_task") {
    return {
      ...state,
      browserCommands: [payload],
      browserContext: null,
      pageVisits: [],
      latestFrame: null,
      lastFrameSeq: 0,
      activeIntervention: null,
      interventions: [],
      agents: {},
      workers: {},
      timeline: [],
      taskResult: null,
    };
  }
  return {
    ...state,
    browserCommands: upsertCommands(state.browserCommands),
  };
}

export function sessionStoreReducer(state: SessionStoreState, action: SessionStoreAction): SessionStoreState {
  if (action.type === "connection_state") {
    return { ...state, connectionState: action.payload };
  }
  if (action.type === "stage_ready") {
    return { ...state, stageReady: action.payload };
  }
  if (action.type === "live_reset") {
    return {
      ...state,
      stageReady: false,
      browserContext: null,
      latestFrame: null,
      lastFrameSeq: 0,
      agents: {},
      workers: {},
      timeline: [],
      activeIntervention: null,
    };
  }
  if (action.type === "resolve_intervention_local") {
    return {
      ...state,
      activeIntervention: null,
      interventions: resolveIntervention(
        state.interventions,
        state.activeIntervention?.interventionId,
        action.payload.resolution,
      ),
    };
  }

  const message = action.payload;
  switch (message.type) {
    case "session_state":
      return applySessionState(state, message.payload);
    case "browser_context_update":
      return applyBrowserContext(state, message.payload);
    case "frame":
      return applyFrame(state, message.payload);
    case "agent_event":
      return applyAgentEvent(state, message.payload);
    case "browser_command":
      return applyBrowserCommand(state, message.payload);
    case "background_worker_update":
      return applyWorkerUpdate(state, message.payload);
    case "approval_required": {
      const activeIntervention = approvalToIntervention(message.payload);
      return {
        ...state,
        activeIntervention,
        interventions: appendOrUpdateIntervention(state.interventions, {
          intervention_id: activeIntervention.interventionId,
          kind: "approval",
          headline: activeIntervention.headline,
          reason_text: activeIntervention.reasonText,
          source_url: activeIntervention.sourceUrl,
          target_summary: activeIntervention.targetSummary,
          recommended_action: activeIntervention.recommendedAction,
          started_at: new Date().toISOString(),
          resolution: null,
          resolved_at: null,
          checkpoint_id: activeIntervention.checkpointId,
          source_event_id: activeIntervention.sourceEventId,
          keyframe_path: null,
          domain: state.browserContext?.domain ?? null,
        }),
      };
    }
    case "bridge_offer": {
      const activeIntervention = bridgeToIntervention(message.payload);
      return {
        ...state,
        activeIntervention,
        interventions: appendOrUpdateIntervention(state.interventions, {
          intervention_id: activeIntervention.interventionId,
          kind: "live_browser_view",
          headline: activeIntervention.headline,
          reason_text: activeIntervention.reasonText,
          source_url: activeIntervention.sourceUrl,
          target_summary: activeIntervention.targetSummary,
          recommended_action: activeIntervention.recommendedAction,
          started_at: new Date().toISOString(),
          resolution: null,
          resolved_at: null,
          checkpoint_id: null,
          source_event_id: activeIntervention.sourceEventId,
          keyframe_path: null,
          domain: state.browserContext?.domain ?? null,
        }),
      };
    }
    case "task_result":
      return {
        ...state,
        taskResult: message.payload,
        activeIntervention: null,
      };
    case "error":
      return { ...state, lastError: message.payload };
    default:
      return state;
  }
}
