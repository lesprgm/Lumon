// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionArtifactResponse } from "./protocol/types";

const REVIEW_RESPONSE: SessionArtifactResponse = {
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
      first_frame_latency_ms: 1800,
      ui_open_latency_ms: null,
      browser_episode_count: 1,
      intervention_count: 1,
      reconnect_count: 0,
      duplicate_attach_prevented: 1,
      open_reason_counts: { browser: 1, open: 2 },
      open_suppression_reason_counts: { already_visible: 3, duplicate_signal: 1 },
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
  commands: [],
};

describe("App review entry and playback", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.resetModules();
    vi.unstubAllEnvs();
    window.history.pushState({}, "", "/");
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 0));
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    window.history.pushState({}, "", "/");
  });

  it("shows a Review run entrypoint after the fixture replay completes", async () => {
    vi.stubEnv("VITE_LUMON_REPLAY", "true");
    const { default: App } = await import("./App");

    render(<App />);

    await act(async () => {
      vi.runAllTimers();
    });

    expect(screen.getByRole("button", { name: "Review run" })).toBeTruthy();
  });

  it("replays review steps from the start and advances through the run", async () => {
    window.history.pushState({}, "", "/?review_session=sess_review_001");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => REVIEW_RESPONSE,
      })) as unknown as typeof fetch,
    );

    const { default: App } = await import("./App");

    render(<App />);

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByRole("button", { name: "Replay" })).toBeTruthy();
    expect(screen.getByText(/Step 4 of 4/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Exit review" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Replay" }));

    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
    expect(screen.getByText(/Step 1 of 4/)).toBeTruthy();

    await act(async () => {
      vi.advanceTimersByTime(1500);
    });

    expect(screen.getByText(/Step 2 of 4/)).toBeTruthy();
  });

  it("shows terminal review caption instead of the last action text for completed runs", async () => {
    window.history.pushState({}, "", "/?review_session=sess_review_001");
    const completedResponse: SessionArtifactResponse = {
      ...REVIEW_RESPONSE,
      events: REVIEW_RESPONSE.events.map((event) =>
        event.type === "agent_event"
          ? {
              ...event,
              payload: {
                ...event.payload,
                summary_text: "OpenCode is reasoning about the next step",
              },
            }
          : event,
      ),
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => completedResponse,
      })) as unknown as typeof fetch,
    );

    const { default: App } = await import("./App");
    const { container } = render(<App />);

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByText(/Step 4 of 4/)).toBeTruthy();
    expect(container.querySelector(".caption-bubble")?.textContent).toBe("The run finished after checking the docs.");
  });

  it("switches into takeover UI immediately after clicking Take over", async () => {
    vi.stubEnv("VITE_LUMON_WEBRTC", "false");
    window.history.pushState({}, "", "/?session_id=sess_live_001&ws_token=token_live_001");

    class MockWebSocket {
      static readonly OPEN = 1;
      static lastInstance: MockWebSocket | null = null;

      readonly url: string;
      readyState = 0;
      onopen: ((event: Event) => void) | null = null;
      onclose: ((event: CloseEvent) => void) | null = null;
      onerror: (() => void) | null = null;
      onmessage: ((event: MessageEvent<string>) => void) | null = null;
      sent: string[] = [];

      constructor(url: string) {
        this.url = url;
        MockWebSocket.lastInstance = this;
      }

      send(data: string) {
        this.sent.push(data);
      }

      close() {
        this.readyState = 3;
        this.onclose?.({ code: 1000 } as CloseEvent);
      }

      emitOpen() {
        this.readyState = MockWebSocket.OPEN;
        this.onopen?.(new Event("open"));
      }

      emitMessage(payload: unknown) {
        this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>);
      }
    }

    vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket);

    const { default: App } = await import("./App");

    render(<App />);

    const socket = MockWebSocket.lastInstance;
    expect(socket).not.toBeNull();

    await act(async () => {
      socket?.emitOpen();
      socket?.emitMessage({
        type: "session_state",
        payload: {
          session_id: "sess_live_001",
          adapter_id: "playwright_native",
          adapter_run_id: "run_live_001",
          observer_mode: false,
          web_mode: null,
          web_bridge: null,
          run_mode: "live",
          state: "running",
          interaction_mode: "watch",
          active_checkpoint_id: null,
          task_text: "Open Wikipedia and inspect the page",
          viewport: { width: 1280, height: 800 },
          capabilities: {
            supports_pause: true,
            supports_approval: true,
            supports_takeover: true,
            supports_frames: true,
          },
        },
      });
      await Promise.resolve();
    });

    fireEvent.click(screen.getByRole("button", { name: "Take over" }));

    expect(screen.getByRole("button", { name: "Collapse manual control card" })).toBeTruthy();
    expect(document.querySelector(".status-idle-sprite")?.getAttribute("data-status-sprite-mode")).toBe("takeover");
    expect(socket?.sent.some((message) => message.includes('"type":"start_takeover"'))).toBe(true);
  });
});
