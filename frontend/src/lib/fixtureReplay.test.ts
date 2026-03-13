import { describe, expect, it, vi } from "vitest";

import { startFixtureReplay } from "./fixtureReplay";
import type { TimedMessage } from "../fixtures/demoTimeline";

const PLAYWRIGHT_CAPABILITIES = {
  supports_pause: true,
  supports_approval: true,
  supports_takeover: true,
  supports_frames: true,
};

describe("startFixtureReplay", () => {
  it("replays messages in order and allows cancellation", () => {
    vi.useFakeTimers();
    const messages: TimedMessage[] = [
      {
        delayMs: 10,
        message: {
          type: "session_state",
          payload: {
            session_id: "sess_1",
            adapter_id: "playwright_native",
            adapter_run_id: "run_1",
            state: "running",
            interaction_mode: "watch",
            active_checkpoint_id: null,
            task_text: "task",
            viewport: { width: 1280, height: 800 },
            capabilities: PLAYWRIGHT_CAPABILITIES,
          },
        },
      },
      {
        delayMs: 15,
        message: {
          type: "task_result",
          payload: {
            session_id: "sess_1",
            status: "completed",
            summary_text: "done",
            task_text: "task",
            adapter_id: "playwright_native",
            adapter_run_id: "run_1",
          },
        },
      },
    ];
    const seen: string[] = [];

    const stop = startFixtureReplay(messages, (message) => {
      seen.push(message.type);
    });

    vi.advanceTimersByTime(18);
    expect(seen).toEqual(["session_state"]);

    stop();
    vi.advanceTimersByTime(20);
    expect(seen).toEqual(["session_state"]);
    vi.useRealTimers();
  });
});
