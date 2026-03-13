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
          ui_open_latency_ms: null,
          browser_episode_count: 2,
          intervention_count: 1,
          reconnect_count: 0,
          duplicate_attach_prevented: 3,
          session_completed: true,
          artifact_written: false,
        }}
      />,
    );

    expect(markup).toContain("Local alpha summary");
    expect(markup).toContain("Attach latency");
    expect(markup).toContain("820 ms");
    expect(markup).toContain("Browser open latency");
    expect(markup).toContain("not recorded");
    expect(markup).toContain("Duplicate attaches prevented");
  });
});
