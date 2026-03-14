import { describe, expect, it } from "vitest";

import { initialSessionStoreState, sessionStoreReducer } from "./sessionStore";
import type { AnyServerEnvelope } from "../protocol/types";

const PLAYWRIGHT_CAPABILITIES = {
  supports_pause: true,
  supports_approval: true,
  supports_takeover: true,
  supports_frames: true,
};

const OPENCODE_CAPABILITIES = {
  supports_pause: false,
  supports_approval: false,
  supports_takeover: false,
  supports_frames: false,
};

const runningState: AnyServerEnvelope = {
  type: "session_state",
  payload: {
    session_id: "sess_demo_001",
    adapter_id: "opencode",
    adapter_run_id: "run_demo_001",
    run_mode: "live",
    observer_mode: true,
    web_mode: "observe_only",
    web_bridge: null,
    state: "running",
    interaction_mode: "watch",
    active_checkpoint_id: null,
    task_text: "Inspect the repo",
    viewport: { width: 1280, height: 800 },
    capabilities: OPENCODE_CAPABILITIES,
  },
};

describe("sessionStoreReducer", () => {
  it("tracks the active attached session state", () => {
    const next = sessionStoreReducer(initialSessionStoreState, {
      type: "server_message",
      payload: runningState,
    });

    expect(next.session?.adapter_id).toBe("opencode");
    expect(next.activeAdapterId).toBe("opencode");
    expect(next.adapterRunId).toBe("run_demo_001");
  });

  it("tracks only the newest agent event", () => {
    const first = sessionStoreReducer(initialSessionStoreState, {
      type: "server_message",
      payload: runningState,
    });
    const second = sessionStoreReducer(first, {
      type: "server_message",
      payload: {
        type: "agent_event",
        payload: {
          event_seq: 2,
          event_id: "evt_2",
          source_event_id: "src_2",
          timestamp: new Date().toISOString(),
          session_id: "sess_demo_001",
          adapter_id: "opencode",
          adapter_run_id: "run_demo_001",
          agent_id: "main_001",
          parent_agent_id: null,
          agent_kind: "main",
          environment_id: "env_opencode_main",
          visibility_mode: "foreground",
          action_type: "read",
          state: "thinking",
          summary_text: "Inspecting runtime logic",
          intent: "Find the observer lifecycle path",
          risk_level: "none",
          subagent_source: null,
          cursor: null,
          target_rect: null,
          meta: {},
        },
      },
    });
    const stale = sessionStoreReducer(second, {
      type: "server_message",
      payload: {
        type: "agent_event",
        payload: {
          ...second.agents.main_001,
          event_seq: 1,
          event_id: "evt_1",
          source_event_id: "src_1",
        },
      },
    });

    expect(stale.lastEventSeq).toBe(2);
    expect(stale.timeline).toHaveLength(1);
  });

  it("ignores stale frames", () => {
    const withNewFrame = sessionStoreReducer(initialSessionStoreState, {
      type: "server_message",
      payload: {
        type: "frame",
        payload: {
          mime_type: "image/jpeg",
          data_base64: "newer",
          frame_seq: 5,
        },
      },
    });
    const withStaleFrame = sessionStoreReducer(withNewFrame, {
      type: "server_message",
      payload: {
        type: "frame",
        payload: {
          mime_type: "image/jpeg",
          data_base64: "older",
          frame_seq: 4,
        },
      },
    });

    expect(withStaleFrame.latestFrame?.data_base64).toBe("newer");
    expect(withStaleFrame.lastFrameSeq).toBe(5);
  });

  it("clears approval once the session leaves approval mode", () => {
    const withApprovalState = sessionStoreReducer(initialSessionStoreState, {
      type: "server_message",
      payload: {
        ...runningState,
        payload: {
          ...runningState.payload,
          state: "waiting_for_approval",
          interaction_mode: "approval",
          active_checkpoint_id: "chk_001",
          capabilities: PLAYWRIGHT_CAPABILITIES,
          adapter_id: "playwright_native",
        },
      },
    });
    const withApproval = sessionStoreReducer(withApprovalState, {
      type: "server_message",
      payload: {
        type: "approval_required",
        payload: {
          intervention_id: "intv_approval_001",
          session_id: "sess_demo_001",
          checkpoint_id: "chk_001",
          event_id: "evt_submit_001",
          action_type: "click",
          source_url: "https://example.com/submit",
          target_summary: "Submit shortlist",
          headline: "About to submit the shortlist",
          reason_text: "This action will submit the final shortlist.",
          recommended_action: "approve",
          summary_text: "Ready to submit",
          intent: "Submit shortlist",
          risk_level: "high",
          risk_reason: "Final irreversible transition",
          adapter_id: "playwright_native",
          adapter_run_id: "run_demo_001",
        },
      },
    });
    const backToRunning = sessionStoreReducer(withApproval, {
      type: "server_message",
      payload: {
        ...runningState,
        payload: {
          ...runningState.payload,
          adapter_id: "playwright_native",
          capabilities: PLAYWRIGHT_CAPABILITIES,
        },
      },
    });

    expect(withApproval.activeIntervention?.checkpointId).toBe("chk_001");
    expect(backToRunning.activeIntervention).toBeNull();
  });

  it("tracks and clears bridge offers", () => {
    const withOffer = sessionStoreReducer(initialSessionStoreState, {
      type: "server_message",
      payload: {
        ...runningState,
        payload: {
          ...runningState.payload,
          web_mode: "delegate_playwright",
          web_bridge: "playwright_native",
        },
      },
    });
    const withBridgeOffer = sessionStoreReducer(withOffer, {
      type: "server_message",
      payload: {
        type: "bridge_offer",
        payload: {
          intervention_id: "intv_bridge_001",
          session_id: "sess_demo_001",
          adapter_id: "opencode",
          adapter_run_id: "run_demo_001",
          web_mode: "delegate_playwright",
          web_bridge: "playwright_native",
          source_event_id: "src_offer_001",
          source_url: "https://example.com/search",
          target_summary: "Open this result in a live browser view",
          headline: "Live browser view",
          reason_text: "Lumon can open this step so you can watch it live.",
          recommended_action: "open_live_browser_view",
          summary_text: "Open a visible browser view",
          intent: "Watch the agent continue on a live page",
        },
      },
    });
    const cleared = sessionStoreReducer(withBridgeOffer, {
      type: "resolve_intervention_local",
      payload: { resolution: "dismissed" },
    });

    expect(withBridgeOffer.activeIntervention?.kind).toBe("live_browser_view");
    expect(cleared.activeIntervention).toBeNull();
  });

  it("stores worker updates separately from main agent events", () => {
    const next = sessionStoreReducer(initialSessionStoreState, {
      type: "server_message",
      payload: {
        type: "background_worker_update",
        payload: {
          session_id: "sess_demo_001",
          adapter_id: "langchain",
          adapter_run_id: "run_trace_001",
          agent_id: "worker_001",
          summary_text: "Summarizing repo telemetry",
          state: "running",
          timestamp: new Date().toISOString(),
        },
      },
    });

    expect(next.workers.worker_001?.summary_text).toBe("Summarizing repo telemetry");
  });

  it("tracks browser commands and resets live browser state on begin_task", () => {
    const withFrame = sessionStoreReducer(initialSessionStoreState, {
      type: "server_message",
      payload: {
        type: "frame",
        payload: {
          mime_type: "image/png",
          data_base64: "older-frame",
          frame_seq: 3,
        },
      },
    });
    const withTimeline = sessionStoreReducer(withFrame, {
      type: "server_message",
      payload: {
        type: "agent_event",
        payload: {
          event_seq: 4,
          event_id: "evt_old",
          source_event_id: "src_old",
          timestamp: new Date().toISOString(),
          session_id: "sess_demo_001",
          adapter_id: "opencode",
          adapter_run_id: "run_demo_001",
          agent_id: "main_001",
          parent_agent_id: null,
          agent_kind: "main",
          environment_id: "env_opencode_main",
          visibility_mode: "foreground",
          action_type: "read",
          state: "thinking",
          summary_text: "Old activity",
          intent: "Old activity",
          risk_level: "none",
          subagent_source: null,
          cursor: null,
          target_rect: null,
          meta: {},
        },
      },
    });
    const withCommand = sessionStoreReducer(withTimeline, {
      type: "server_message",
      payload: {
        type: "browser_command",
        payload: {
          command_id: "cmd_begin",
          command: "begin_task",
          status: "partial",
          summary_text: "Lumon prepared the live browser delegate for this task.",
          timestamp: new Date().toISOString(),
          reason: "awaiting_first_navigation",
          source_url: null,
          domain: null,
          page_version: 0,
          evidence: null,
          actionable_elements: [],
          intervention_id: null,
          checkpoint_id: null,
          meta: {},
        },
      },
    });

    expect(withCommand.browserCommands).toHaveLength(1);
    expect(withCommand.latestFrame).toBeNull();
    expect(withCommand.lastFrameSeq).toBe(0);
    expect(withCommand.pageVisits).toHaveLength(0);
    expect(withCommand.timeline).toHaveLength(0);
    expect(withCommand.taskResult).toBeNull();
  });

  it("preserves revisits when the session returns to a previously seen page", () => {
    const withFirstVisit = sessionStoreReducer(initialSessionStoreState, {
      type: "server_message",
      payload: {
        type: "browser_context_update",
        payload: {
          session_id: "sess_demo_001",
          adapter_id: "playwright_native",
          adapter_run_id: "run_demo_001",
          timestamp: "2026-03-19T01:00:00Z",
          url: "https://example.com/a",
          domain: "example.com",
          title: "A",
          environment_type: "external",
        },
      },
    });
    const withSecondVisit = sessionStoreReducer(withFirstVisit, {
      type: "server_message",
      payload: {
        type: "browser_context_update",
        payload: {
          session_id: "sess_demo_001",
          adapter_id: "playwright_native",
          adapter_run_id: "run_demo_001",
          timestamp: "2026-03-19T01:00:01Z",
          url: "https://example.com/b",
          domain: "example.com",
          title: "B",
          environment_type: "external",
        },
      },
    });
    const withRevisit = sessionStoreReducer(withSecondVisit, {
      type: "server_message",
      payload: {
        type: "browser_context_update",
        payload: {
          session_id: "sess_demo_001",
          adapter_id: "playwright_native",
          adapter_run_id: "run_demo_001",
          timestamp: "2026-03-19T01:00:02Z",
          url: "https://example.com/a",
          domain: "example.com",
          title: "A again",
          environment_type: "external",
        },
      },
    });

    expect(withRevisit.pageVisits.map((page) => page.url)).toEqual([
      "https://example.com/a",
      "https://example.com/b",
      "https://example.com/a",
    ]);
    expect(withRevisit.pageVisits[2].title).toBe("A again");
  });

  it("keeps distinct commands even when they share the same command id", () => {
    const first = sessionStoreReducer(initialSessionStoreState, {
      type: "server_message",
      payload: {
        type: "browser_command",
        payload: {
          command_id: "cmd_same",
          command: "begin_task",
          status: "partial",
          summary_text: "Prepared task.",
          timestamp: new Date().toISOString(),
          reason: "awaiting_first_navigation",
          source_url: null,
          domain: null,
          page_version: 0,
          evidence: null,
          actionable_elements: [],
          intervention_id: null,
          checkpoint_id: null,
          meta: {},
        },
      },
    });
    const second = sessionStoreReducer(first, {
      type: "server_message",
      payload: {
        type: "browser_command",
        payload: {
          command_id: "cmd_same",
          command: "open",
          status: "success",
          summary_text: "Opened the page.",
          timestamp: new Date().toISOString(),
          reason: null,
          source_url: "https://example.com",
          domain: "example.com",
          page_version: 1,
          evidence: { verified: true, details: {}, frame_emitted: true },
          actionable_elements: [],
          intervention_id: null,
          checkpoint_id: null,
          meta: {},
        },
      },
    });

    expect(second.browserCommands).toHaveLength(2);
    expect(second.browserCommands.map((command) => `${command.command}:${command.command_id}`)).toEqual([
      "begin_task:cmd_same",
      "open:cmd_same",
    ]);
  });
});
