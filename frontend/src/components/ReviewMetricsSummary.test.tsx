import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ReviewMetricsSummary } from "./ReviewMetricsSummary";

describe("ReviewMetricsSummary", () => {
  it("renders compact local alpha metrics with missing values", () => {
    const markup = renderToStaticMarkup(
      <ReviewMetricsSummary
        metrics={{
          attach_requested_at: null,
          attached_at: null,
          first_browser_event_at: null,
          ui_open_requested_at: null,
          ui_ready_at: null,
          attach_latency_ms: 820,
          first_frame_latency_ms: 1330,
          ui_open_latency_ms: null,
          browser_episode_count: 2,
          intervention_count: 1,
          reconnect_count: 0,
          duplicate_attach_prevented: 3,
          open_reason_counts: { open: 1 },
          open_suppression_reason_counts: { duplicate_signal: 2, already_visible: 1 },
          session_completed: true,
          artifact_written: false,
        }}
      />,
    );

    expect(markup).toContain("Local alpha summary");
    expect(markup).toContain("Attach latency");
    expect(markup).toContain("820 ms");
    expect(markup).toContain("First browser frame");
    expect(markup).toContain("1330 ms");
    expect(markup).toContain("Browser open latency");
    expect(markup).toContain("not recorded");
    expect(markup).toContain("Duplicate attaches prevented");
    expect(markup).toContain("Open reasons");
    expect(markup).toContain("open x1");
    expect(markup).toContain("Suppression reasons");
    expect(markup).toContain("duplicate_signal x2, already_visible x1");
  });
});
