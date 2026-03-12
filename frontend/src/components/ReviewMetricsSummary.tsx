import { buildReviewMetricItems } from "../lib/reviewMode";
import type { SessionMetrics } from "../protocol/types";

export function ReviewMetricsSummary({ metrics }: { metrics: SessionMetrics }) {
  const items = buildReviewMetricItems(metrics);

  return (
    <section className="review-metrics-sheet" aria-label="Local alpha summary">
      <div className="review-metrics-header">
        <div>
          <h3>Local alpha summary</h3>
          <p>Useful for tuning attach, open, and intervention behavior during testing.</p>
        </div>
      </div>
      <div className="review-metrics-grid">
        {items.map((item) => (
          <article key={item.label} className={`review-metric-card tone-${item.tone}`}>
            <span className="review-metric-label">{item.label}</span>
            <strong className="review-metric-value">{item.value}</strong>
          </article>
        ))}
      </div>
    </section>
  );
}
