import { describe, expect, it } from "vitest";

import {
  buildReviewMetricItems,
  buildReviewStepSummary,
  defaultReviewSelection,
  deriveReviewSteps,
  jumpToNextReviewStep,
  parseReviewSelectionKey,
  resolveReviewSelection,
} from "./reviewMode";
import { resolveReviewKeyframePath } from "./reviewKeyframes";
import type { SessionArtifactResponse } from "../protocol/types";

const response: SessionArtifactResponse = {
  artifact: {
    session_id: "sess_review_001",
    adapter_id: "playwright_native",
    adapter_run_id: "run_review_001",
    task_text: "Research browser instrumentation",
    observer_mode: true,
    status: "completed",
    started_at: "2026-03-12T10:00:00.000Z",
    completed_at: "2026-03-12T10:01:00.000Z",
    summary_text: "The run finished after checking the docs.",
    browser_context: {
      session_id: "sess_review_001",
      adapter_id: "playwright_native",
      adapter_run_id: "run_review_001",
      url: "https://docs.example.com/reference",
      title: "Reference",
      domain: "docs.example.com",
      environment_type: "docs",
      timestamp: "2026-03-12T10:00:05.000Z",
    },
    pages_visited: [
      {
        url: "https://docs.example.com/reference",
        domain: "docs.example.com",
        title: "Reference",
        environment_type: "docs",
        first_seen_at: "2026-03-12T10:00:05.000Z",
        last_seen_at: "2026-03-12T10:00:45.000Z",
        keyframe_path: "output/sessions/sess_review_001/keyframes/reference.png",
      },
    ],
    interventions: [
      {
        intervention_id: "intv_review_001",
        kind: "approval",
        headline: "Needs your approval",
        reason_text: "The next step will submit the saved selection.",
        source_url: "https://docs.example.com/reference",
        target_summary: "Submit the selected example",
        recommended_action: "approve",
        started_at: "2026-03-12T10:00:20.000Z",
        resolved_at: "2026-03-12T10:00:24.000Z",
        resolution: "approved",
        checkpoint_id: "chk_review_001",
        source_event_id: "evt_review_001",
        keyframe_path: "output/sessions/sess_review_001/keyframes/approved.png",
        domain: "docs.example.com",
      },
    ],
    keyframes: ["output/sessions/sess_review_001/keyframes/approved.png"],
    metrics: {
      attach_requested_at: "2026-03-12T10:00:00.000Z",
      attached_at: "2026-03-12T10:00:01.000Z",
      first_browser_event_at: "2026-03-12T10:00:05.000Z",
      ui_open_requested_at: "2026-03-12T10:00:05.300Z",
      ui_ready_at: null,
      attach_latency_ms: 1000,
      ui_open_latency_ms: null,
      browser_episode_count: 1,
      intervention_count: 1,
      reconnect_count: 0,
      duplicate_attach_prevented: 1,
      session_completed: true,
      artifact_written: true,
    },
  },
  events: [
    {
      type: "browser_context_update",
      payload: {
        session_id: "sess_review_001",
        adapter_id: "playwright_native",
        adapter_run_id: "run_review_001",
        url: "https://docs.example.com/reference",
        title: "Reference",
        domain: "docs.example.com",
        environment_type: "docs",
        timestamp: "2026-03-12T10:00:05.000Z",
      },
    },
    {
      type: "agent_event",
      payload: {
        event_seq: 2,
        event_id: "evt_review_001",
        source_event_id: "src_review_001",
        timestamp: "2026-03-12T10:00:12.000Z",
        session_id: "sess_review_001",
        adapter_id: "playwright_native",
        adapter_run_id: "run_review_001",
        agent_id: "main_001",
        parent_agent_id: null,
        agent_kind: "main",
        environment_id: "env_docs",
        visibility_mode: "foreground",
        action_type: "read",
        state: "running",
        summary_text: "Checked the reference page",
        intent: "Look through the API reference before continuing",
        risk_level: "none",
        subagent_source: null,
        cursor: { x: 400, y: 240 },
        target_rect: { x: 360, y: 200, width: 180, height: 64 },
        target_summary: "Reference example",
        confidence: 0.88,
        meta: {},
      },
    },
  ],
};

describe("reviewMode helpers", () => {
  it("derives review steps and resolves intervention-linked events", () => {
    const steps = deriveReviewSteps(response);

    expect(steps.map((step) => step.kind)).toEqual(["page", "action", "intervention", "outcome"]);

    const selection = resolveReviewSelection(response, steps, defaultReviewSelection(steps));

    expect(selection.selectedStep?.kind).toBe("intervention");
    expect(selection.linkedEvent?.event_id).toBe("evt_review_001");
    expect(selection.browserContext?.domain).toBe("docs.example.com");

    const summary = buildReviewStepSummary(response.artifact, selection);
    expect(summary.kicker).toBe("Intervention");
    expect(summary.outcome).toBe("Approved");
    expect(summary.target).toBe("Submit the selected example");
  });

  it("jumps to the next page change or intervention without resetting the selection", () => {
    const steps = deriveReviewSteps(response);

    expect(jumpToNextReviewStep(steps, "page:2026-03-12T10:00:05.000Z", (step) => step.isIntervention)).toBe(
      "intervention:intv_review_001",
    );
    expect(jumpToNextReviewStep(steps, "intervention:intv_review_001", (step) => step.isPageTransition)).toBe(
      "intervention:intv_review_001",
    );
  });

  it("marks missing metrics as not recorded", () => {
    const items = buildReviewMetricItems(response.artifact.metrics);

    expect(items.find((item) => item.label === "Attach latency")?.value).toBe("1000 ms");
    expect(items.find((item) => item.label === "Browser open latency")?.value).toBe("not recorded");
    expect(items.find((item) => item.label === "Artifact written")?.value).toBe("yes");
  });

  it("labels missing intervention outcomes as unresolved", () => {
    const unresolved = {
      ...response,
      artifact: {
        ...response.artifact,
        interventions: [
          {
            ...response.artifact.interventions[0],
            resolution: null,
            resolved_at: null,
          },
        ],
      },
    } satisfies SessionArtifactResponse;

    const steps = deriveReviewSteps(unresolved);
    const selection = resolveReviewSelection(unresolved, steps, "intervention:intv_review_001");
    const summary = buildReviewStepSummary(unresolved.artifact, selection);

    expect(summary.outcome).toBe("Unresolved");
  });

  it("uses the page-linked keyframe when a page step is selected", () => {
    const withFinalKeyframe = {
      ...response,
      artifact: {
        ...response.artifact,
        keyframes: [
          "output/sessions/sess_review_001/keyframes/reference.png",
          "output/sessions/sess_review_001/keyframes/final.png",
        ],
      },
    } satisfies SessionArtifactResponse;

    const keyframePath = resolveReviewKeyframePath(withFinalKeyframe, "page:2026-03-12T10:00:05.000Z", null, null);

    expect(keyframePath).toBe("output/sessions/sess_review_001/keyframes/reference.png");
  });

  it("parses review selection keys without truncating ISO timestamps", () => {
    expect(parseReviewSelectionKey("page:2026-03-12T10:00:05.000Z")).toEqual({
      kind: "page",
      id: "2026-03-12T10:00:05.000Z",
    });
  });
});
