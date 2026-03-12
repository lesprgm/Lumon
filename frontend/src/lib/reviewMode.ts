import type {
  ActionType,
  AgentEventPayload,
  BrowserContextPayload,
  InterventionRecord,
  SessionArtifact,
  SessionArtifactResponse,
  SessionMetrics,
} from "../protocol/types";

export type ReviewSelectionKey = string | null;

export interface ReviewStep {
  key: string;
  kind: "page" | "action" | "intervention" | "outcome";
  timestamp: string;
  headline: string;
  detail: string;
  domain: string | null;
  title: string | null;
  environmentType: BrowserContextPayload["environment_type"] | null;
  event: AgentEventPayload | null;
  intervention: InterventionRecord | null;
  browserContext: BrowserContextPayload | null;
  isPageTransition: boolean;
  isIntervention: boolean;
}

export interface ReviewSelectionDetails {
  selectedStep: ReviewStep | null;
  selectedIndex: number;
  totalSteps: number;
  linkedEvent: AgentEventPayload | null;
  linkedIntervention: InterventionRecord | null;
  browserContext: BrowserContextPayload | null;
}

function keyFor(kind: ReviewStep["kind"], id: string): string {
  return `${kind}:${id}`;
}

export function parseReviewSelectionKey(key: ReviewSelectionKey): { kind: string; id: string } | null {
  if (!key) {
    return null;
  }
  const separatorIndex = key.indexOf(":");
  if (separatorIndex <= 0 || separatorIndex === key.length - 1) {
    return null;
  }
  const kind = key.slice(0, separatorIndex);
  const id = key.slice(separatorIndex + 1);
  if (!kind || !id) {
    return null;
  }
  return { kind, id };
}

function actionLabel(actionType: ActionType): string {
  switch (actionType) {
    case "navigate":
      return "opened page";
    case "click":
      return "clicked";
    case "type":
      return "typed";
    case "scroll":
      return "scrolled";
    case "read":
      return "looked through results";
    case "spawn_subagent":
      return "asked a helper to check";
    case "subagent_result":
      return "received helper findings";
    case "wait":
      return "waited";
    case "complete":
      return "finished";
    case "error":
      return "hit a problem";
    default:
      return actionType;
  }
}

export function summarizeOutcome(artifact: SessionArtifact): string {
  if (artifact.summary_text) {
    return artifact.summary_text;
  }
  if (artifact.status === "completed") {
    return "The run finished.";
  }
  if (artifact.status === "failed") {
    return "The run stopped after an error.";
  }
  if (artifact.status === "stopped") {
    return "The run stopped early.";
  }
  return "The run is still in progress.";
}

export function environmentLabel(environmentType: BrowserContextPayload["environment_type"] | null | undefined): string {
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

export function interventionOutcomeLabel(resolution: InterventionRecord["resolution"] | null | undefined): string {
  switch (resolution) {
    case "approved":
      return "Approved";
    case "denied":
      return "Denied";
    case "taken_over":
      return "Taken over";
    case "dismissed":
      return "Dismissed";
    case "expired":
      return "Expired";
    default:
      return "Unresolved";
  }
}

function resolveContextAtTimestamp(
  contexts: BrowserContextPayload[],
  timestamp: string,
  artifactContext: BrowserContextPayload | null | undefined,
): BrowserContextPayload | null {
  const matching = contexts
    .filter((context) => Date.parse(context.timestamp) <= Date.parse(timestamp))
    .sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp));
  return matching.at(-1) ?? artifactContext ?? null;
}

function browserContextFromMeta(event: AgentEventPayload): BrowserContextPayload | null {
  const url = typeof event.meta.url === "string" ? event.meta.url : null;
  if (!url) {
    return null;
  }
  try {
    const parsed = new URL(url);
    return {
      session_id: event.session_id,
      adapter_id: event.adapter_id,
      adapter_run_id: event.adapter_run_id,
      url,
      title: null,
      domain: parsed.hostname || "unknown",
      environment_type: parsed.protocol === "file:" ? "local" : "external",
      timestamp: event.timestamp,
    };
  } catch {
    return {
      session_id: event.session_id,
      adapter_id: event.adapter_id,
      adapter_run_id: event.adapter_run_id,
      url,
      title: null,
      domain: "unknown",
      environment_type: "external",
      timestamp: event.timestamp,
    };
  }
}

function importantDetail(event: AgentEventPayload): string {
  if (event.target_summary) {
    return event.target_summary;
  }
  if (event.intent) {
    return event.intent;
  }
  return actionLabel(event.action_type);
}

export function deriveReviewSteps(response: SessionArtifactResponse): ReviewStep[] {
  const browserContexts = response.events
    .filter((event): event is { type: "browser_context_update"; payload: BrowserContextPayload } =>
      event.type === "browser_context_update",
    )
    .map((event) => event.payload)
    .sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp));

  const steps: ReviewStep[] = [];
  const eventById = new Map<string, AgentEventPayload>();

  for (const event of response.events) {
    if (event.type === "browser_context_update") {
      const payload = event.payload as BrowserContextPayload;
      steps.push({
        key: keyFor("page", payload.timestamp),
        kind: "page",
        timestamp: payload.timestamp,
        headline: payload.title || payload.domain,
        detail: `${environmentLabel(payload.environment_type)} opened`,
        domain: payload.domain,
        title: payload.title,
        environmentType: payload.environment_type,
        event: null,
        intervention: null,
        browserContext: payload,
        isPageTransition: true,
        isIntervention: false,
      });
      continue;
    }

    if (event.type === "agent_event") {
      const payload = event.payload as AgentEventPayload;
      eventById.set(payload.event_id, payload);
      const inferredContext = resolveContextAtTimestamp(browserContexts, payload.timestamp, response.artifact.browser_context) ?? browserContextFromMeta(payload);
      steps.push({
        key: keyFor("action", payload.event_id),
        kind: "action",
        timestamp: payload.timestamp,
        headline: payload.summary_text,
        detail: importantDetail(payload),
        domain: inferredContext?.domain ?? null,
        title: inferredContext?.title ?? null,
        environmentType: inferredContext?.environment_type ?? null,
        event: payload,
        intervention: null,
        browserContext: inferredContext,
        isPageTransition: payload.action_type === "navigate",
        isIntervention: false,
      });
    }
  }

  for (const intervention of response.artifact.interventions) {
    const linkedEvent = intervention.source_event_id ? eventById.get(intervention.source_event_id) ?? null : null;
    const inferredContext =
      linkedEvent
        ? resolveContextAtTimestamp(browserContexts, linkedEvent.timestamp, response.artifact.browser_context) ?? browserContextFromMeta(linkedEvent)
        : resolveContextAtTimestamp(browserContexts, intervention.started_at, response.artifact.browser_context);
    steps.push({
      key: keyFor("intervention", intervention.intervention_id),
      kind: "intervention",
      timestamp: intervention.started_at,
      headline: intervention.headline,
      detail: intervention.reason_text,
      domain: intervention.domain ?? inferredContext?.domain ?? null,
      title: inferredContext?.title ?? null,
      environmentType: inferredContext?.environment_type ?? null,
      event: linkedEvent,
      intervention,
      browserContext: inferredContext,
      isPageTransition: false,
      isIntervention: true,
    });
  }

  const outcomeTimestamp = response.artifact.completed_at ?? response.artifact.started_at;
  steps.push({
    key: keyFor("outcome", response.artifact.session_id),
    kind: "outcome",
    timestamp: outcomeTimestamp,
    headline: summarizeOutcome(response.artifact),
    detail: response.artifact.status === "completed" ? "finished" : response.artifact.status === "failed" ? "failed" : "stopped",
    domain: response.artifact.browser_context?.domain ?? null,
    title: response.artifact.browser_context?.title ?? null,
    environmentType: response.artifact.browser_context?.environment_type ?? null,
    event: null,
    intervention: null,
    browserContext: response.artifact.browser_context ?? null,
    isPageTransition: false,
    isIntervention: false,
  });

  return steps.sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp));
}

export function defaultReviewSelection(steps: ReviewStep[]): ReviewSelectionKey {
  const preferred = [...steps].reverse().find((step) => step.kind === "intervention" || step.kind === "action");
  return preferred?.key ?? steps.at(-1)?.key ?? null;
}

export function resolveReviewSelection(
  response: SessionArtifactResponse,
  steps: ReviewStep[],
  selectionKey: ReviewSelectionKey,
): ReviewSelectionDetails {
  const selectedIndex = selectionKey ? steps.findIndex((step) => step.key === selectionKey) : -1;
  const selectedStep = selectedIndex >= 0 ? steps[selectedIndex] : steps.at(-1) ?? null;
  const linkedIntervention = selectedStep?.intervention ?? null;
  const linkedEvent =
    selectedStep?.event ??
    (linkedIntervention?.source_event_id
      ? response.events.find(
          (event): event is { type: "agent_event"; payload: AgentEventPayload } =>
            event.type === "agent_event" &&
            (event.payload as AgentEventPayload).event_id === linkedIntervention.source_event_id,
        )?.payload ?? null
      : null);

  return {
    selectedStep,
    selectedIndex: selectedStep ? Math.max(selectedIndex, 0) : -1,
    totalSteps: steps.length,
    linkedEvent,
    linkedIntervention,
    browserContext: selectedStep?.browserContext ?? response.artifact.browser_context ?? null,
  };
}

export function getAdjacentReviewSelection(steps: ReviewStep[], selectionKey: ReviewSelectionKey, direction: -1 | 1): ReviewSelectionKey {
  const currentIndex = selectionKey ? steps.findIndex((step) => step.key === selectionKey) : -1;
  const targetIndex = currentIndex === -1 ? (direction > 0 ? 0 : steps.length - 1) : currentIndex + direction;
  if (targetIndex < 0 || targetIndex >= steps.length) {
    return selectionKey;
  }
  return steps[targetIndex]?.key ?? selectionKey;
}

export function jumpToNextReviewStep(
  steps: ReviewStep[],
  selectionKey: ReviewSelectionKey,
  predicate: (step: ReviewStep) => boolean,
): ReviewSelectionKey {
  if (steps.length === 0) {
    return selectionKey;
  }
  const currentIndex = selectionKey ? steps.findIndex((step) => step.key === selectionKey) : -1;
  for (let index = currentIndex + 1; index < steps.length; index += 1) {
    if (predicate(steps[index]!)) {
      return steps[index]!.key;
    }
  }
  return selectionKey;
}

export function buildReviewStepSummary(
  artifact: SessionArtifact,
  selection: ReviewSelectionDetails,
): {
  kicker: string;
  headline: string;
  detail: string;
  location: string;
  outcome: string | null;
  target: string | null;
} {
  const step = selection.selectedStep;
  if (!step) {
    return {
      kicker: "Review",
      headline: summarizeOutcome(artifact),
      detail: "No milestone was selected.",
      location: "No page context recorded",
      outcome: artifact.status,
      target: null,
    };
  }

  if (step.kind === "intervention" && step.intervention) {
    return {
      kicker: "Intervention",
      headline: step.intervention.headline,
      detail: step.intervention.reason_text,
      location: `${environmentLabel(step.environmentType)}${step.domain ? ` · ${step.domain}` : ""}`,
      outcome: interventionOutcomeLabel(step.intervention.resolution),
      target: step.intervention.target_summary ?? selection.linkedEvent?.target_summary ?? null,
    };
  }

  if (step.kind === "page") {
    return {
      kicker: "Page change",
      headline: step.headline,
      detail: `${environmentLabel(step.environmentType)} opened.`,
      location: step.domain ?? "Current page",
      outcome: null,
      target: null,
    };
  }

  if (step.kind === "outcome") {
    return {
      kicker: "Outcome",
      headline: summarizeOutcome(artifact),
      detail: artifact.status === "completed" ? "The task finished successfully." : artifact.status === "failed" ? "The task stopped after a failure." : "The run ended before completion.",
      location: `${environmentLabel(step.environmentType)}${step.domain ? ` · ${step.domain}` : ""}`,
      outcome: artifact.status,
      target: null,
    };
  }

  const event = selection.linkedEvent ?? step.event;
  return {
    kicker: "Important action",
    headline: step.headline,
    detail: event?.intent || step.detail,
    location: `${environmentLabel(step.environmentType)}${step.domain ? ` · ${step.domain}` : ""}`,
    outcome: event?.risk_level && event.risk_level !== "none" ? "Needs care" : null,
    target: event?.target_summary ?? null,
  };
}

export interface ReviewMetricItem {
  label: string;
  value: string;
  tone: "success" | "warning" | "missing" | "neutral";
}

function formatMetricDuration(value: number | null | undefined): string {
  if (value == null) {
    return "not recorded";
  }
  return `${value} ms`;
}

function metricTone(value: unknown): ReviewMetricItem["tone"] {
  if (value == null) {
    return "missing";
  }
  if (typeof value === "boolean") {
    return value ? "success" : "warning";
  }
  return "neutral";
}

export function buildReviewMetricItems(metrics: SessionMetrics): ReviewMetricItem[] {
  return [
    {
      label: "Attach latency",
      value: formatMetricDuration(metrics.attach_latency_ms),
      tone: metricTone(metrics.attach_latency_ms),
    },
    {
      label: "Browser open latency",
      value: formatMetricDuration(metrics.ui_open_latency_ms),
      tone: metricTone(metrics.ui_open_latency_ms),
    },
    {
      label: "Browser episodes",
      value: String(metrics.browser_episode_count),
      tone: "neutral",
    },
    {
      label: "Interventions",
      value: String(metrics.intervention_count),
      tone: metrics.intervention_count > 0 ? "warning" : "neutral",
    },
    {
      label: "Reconnects",
      value: String(metrics.reconnect_count),
      tone: metrics.reconnect_count > 0 ? "warning" : "neutral",
    },
    {
      label: "Duplicate attaches prevented",
      value: String(metrics.duplicate_attach_prevented),
      tone: metrics.duplicate_attach_prevented > 0 ? "success" : "neutral",
    },
    {
      label: "Browser commands",
      value: String(metrics.browser_command_count ?? "not recorded"),
      tone: metrics.browser_command_count == null ? "missing" : "neutral",
    },
    {
      label: "Verified browser actions",
      value: String(metrics.verified_browser_action_count ?? "not recorded"),
      tone:
        metrics.verified_browser_action_count == null
          ? "missing"
          : (metrics.verified_browser_action_count ?? 0) > 0
            ? "success"
            : "neutral",
    },
    {
      label: "Blocked browser actions",
      value: String(metrics.browser_blocked_count ?? "not recorded"),
      tone:
        metrics.browser_blocked_count == null
          ? "missing"
          : (metrics.browser_blocked_count ?? 0) > 0
            ? "warning"
            : "neutral",
    },
    {
      label: "Partial browser actions",
      value: String(metrics.browser_partial_count ?? "not recorded"),
      tone:
        metrics.browser_partial_count == null
          ? "missing"
          : (metrics.browser_partial_count ?? 0) > 0
            ? "warning"
            : "neutral",
    },
    {
      label: "Stale targets",
      value: String(metrics.stale_target_count ?? "not recorded"),
      tone:
        metrics.stale_target_count == null
          ? "missing"
          : (metrics.stale_target_count ?? 0) > 0
            ? "warning"
            : "neutral",
    },
    {
      label: "Session completed",
      value: metrics.session_completed ? "yes" : "no",
      tone: metricTone(metrics.session_completed),
    },
    {
      label: "Artifact written",
      value: metrics.artifact_written ? "yes" : "no",
      tone: metricTone(metrics.artifact_written),
    },
  ];
}
