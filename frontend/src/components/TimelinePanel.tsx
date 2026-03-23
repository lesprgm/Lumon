import { useMemo, useState } from "react";

import { environmentLabel, interventionOutcomeLabel, summarizeOutcome } from "../lib/reviewMode";
import type { BrowserCommandRecord, SessionArtifact, SessionArtifactResponse } from "../protocol/types";
import type { SessionStoreState, TimelineRow } from "../store/sessionStore";

type ReviewPage = SessionArtifact["pages_visited"][number];
type LivePage = SessionStoreState["pageVisits"][number];

function actorLabel(actorKind: string): string {
  if (actorKind === "same_scene_subagent") return "helper";
  if (actorKind === "background_worker") return "background";
  return "agent";
}

function actionLabel(actionType: string): string {
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
      return "asked for help";
    case "subagent_result":
      return "helper finished";
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

function filterBrowserCommands(
  commands: BrowserCommandRecord[],
  query: string,
  interventionsOnly: boolean,
): BrowserCommandRecord[] {
  const commandKey = (command: BrowserCommandRecord) => `${command.command}:${command.command_id}`;
  const deduped = Array.from(
    commands.reduce((map, command) => {
      map.set(commandKey(command), command);
      return map;
    }, new Map<string, BrowserCommandRecord>()).values(),
  );
  const lowered = query.trim().toLowerCase();
  return deduped.filter((command) => {
    if (interventionsOnly && command.status !== "blocked") {
      return false;
    }
    if (!lowered) {
      return true;
    }
    return [
      command.summary_text,
      command.command,
      command.domain ?? "",
      command.source_url ?? "",
      command.reason ?? "",
    ]
      .join(" ")
      .toLowerCase()
      .includes(lowered);
  });
}

function filterPages<T extends { url: string; domain: string; title?: string | null }>(
  pages: T[],
  query: string,
): T[] {
  const lowered = query.trim().toLowerCase();
  if (!lowered) {
    return pages;
  }
  return pages.filter((page) =>
    [page.url, page.domain, page.title ?? ""].join(" ").toLowerCase().includes(lowered),
  );
}

function filterInterventions<T extends { headline: string; reason_text?: string; source_url?: string | null; resolution?: string | null }>(
  interventions: T[],
  query: string,
): T[] {
  const lowered = query.trim().toLowerCase();
  if (!lowered) {
    return interventions;
  }
  return interventions.filter((record) =>
    [record.headline, record.reason_text ?? "", record.source_url ?? "", record.resolution ?? ""]
      .join(" ")
      .toLowerCase()
      .includes(lowered),
  );
}

function reviewRowClass(baseActive: boolean, variant: "page" | "intervention" | "outcome" | "action"): string {
  const classes = ["timeline-row"];
  if (baseActive) {
    classes.push("active");
  }
  classes.push(`is-${variant}`);
  classes.push("is-clickable");
  return classes.join(" ");
}

function commandStatusLabel(status: BrowserCommandRecord["status"]): string {
  switch (status) {
    case "success":
      return "verified";
    case "blocked":
      return "blocked";
    case "partial":
      return "partial";
    case "failed":
      return "failed";
    case "unsupported":
      return "unsupported";
    default:
      return status;
  }
}

export function TimelinePanel({
  state,
  reviewArtifact,
  reviewEvents,
  reviewCommands,
  reviewLoading,
  reviewError,
  selectedReviewKey,
  onSelectReviewKey,
}: {
  state: SessionStoreState;
  reviewArtifact?: SessionArtifact | null;
  reviewEvents?: SessionArtifactResponse["events"];
  reviewCommands?: BrowserCommandRecord[];
  reviewLoading?: boolean;
  reviewError?: string | null;
  selectedReviewKey?: string | null;
  onSelectReviewKey?: (key: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [interventionsOnly, setInterventionsOnly] = useState(false);
  const isReviewMode = Boolean(reviewArtifact);

  const filteredBrowserCommands = useMemo(
    () => filterBrowserCommands(state.browserCommands, query, interventionsOnly),
    [interventionsOnly, query, state.browserCommands],
  );
  const filteredReviewPages = useMemo(
    () => filterPages<ReviewPage>(reviewArtifact?.pages_visited ?? [], query),
    [query, reviewArtifact?.pages_visited],
  );
  const filteredLivePages = useMemo(
    () => filterPages<LivePage>(state.pageVisits, query),
    [query, state.pageVisits],
  );
  const filteredInterventions = useMemo(
    () => filterInterventions(isReviewMode ? reviewArtifact?.interventions ?? [] : state.interventions, query),
    [isReviewMode, query, reviewArtifact?.interventions, state.interventions],
  );
  const importantEvents = useMemo(
    () =>
      (reviewEvents ?? []).filter(
        (event): event is { type: "agent_event"; payload: TimelineRow & { event_id: string; action_type: string; risk_level: string } } =>
          event.type === "agent_event" && typeof (event.payload as { event_id?: unknown }).event_id === "string",
      ).filter((event) => {
        const payload = event.payload as unknown as { summary_text: string; action_type: string; risk_level: string; target_summary?: string | null };
        if (interventionsOnly && payload.risk_level === "none") {
          return false;
        }
        const lowered = query.trim().toLowerCase();
        if (!lowered) {
          return true;
        }
        return [payload.summary_text, payload.action_type, payload.target_summary ?? "", payload.risk_level]
          .join(" ")
          .toLowerCase()
          .includes(lowered);
      }),
    [interventionsOnly, query, reviewEvents],
  );

  return (
    <aside className="panel timeline-panel">
      <div className="panel-header">
        <div className="panel-heading">
          <h2>{isReviewMode ? "Review" : "Activity"}</h2>
          <p>{isReviewMode ? "Step through what happened." : "What Lumon thinks matters right now."}</p>
        </div>
        <span>{isReviewMode ? reviewArtifact?.pages_visited.length ?? 0 : state.timeline.length}</span>
      </div>

      <div className="activity-controls">
        <input
          className="activity-search"
          type="search"
          placeholder={isReviewMode ? "Search this run" : "Search activity"}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button
          type="button"
          className={`activity-filter-toggle${interventionsOnly ? " is-active" : ""}`}
          onClick={() => setInterventionsOnly((value) => !value)}
        >
          interventions only
        </button>
      </div>

      <div className="timeline-list">
        {reviewLoading ? <div className="panel-empty">Loading this run…</div> : null}
        {reviewError ? <div className="panel-empty">{reviewError}</div> : null}

        {isReviewMode && reviewArtifact ? (
          <>
            <section className="activity-section">
              <div className="activity-section-heading">Summary</div>
              <article className="timeline-row active is-outcome">
                <div className="timeline-summary">{reviewArtifact.task_text}</div>
                <div className="timeline-meta">
                  <span>{reviewArtifact.status}</span>
                  <span>{summarizeOutcome(reviewArtifact)}</span>
                </div>
              </article>
            </section>

            <section className="activity-section">
              <div className="activity-section-heading">Pages visited</div>
              {filteredReviewPages.length === 0 ? <div className="panel-empty">No pages captured for this run.</div> : null}
              {filteredReviewPages.map((page) => (
                <article
                  key={`${page.first_seen_at}:${page.url}`}
                  className={reviewRowClass(selectedReviewKey === `page:${page.first_seen_at}`, "page")}
                  onClick={() => onSelectReviewKey?.(`page:${page.first_seen_at}`)}
                >
                  <div className="timeline-summary">{page.title || page.domain}</div>
                  <div className="timeline-meta">
                    <span>{page.domain}</span>
                    <span>{environmentLabel(page.environment_type)}</span>
                  </div>
                </article>
              ))}
            </section>

            <section className="activity-section">
              <div className="activity-section-heading">Important actions</div>
              {importantEvents.length === 0 ? <div className="panel-empty">No action events captured.</div> : null}
              {importantEvents.map((event) => {
                const payload = event.payload as unknown as { event_id: string; summary_text: string; action_type: string; risk_level: string; target_summary?: string | null };
                const key = `action:${payload.event_id}`;
                return (
                  <article
                    key={key}
                    className={reviewRowClass(selectedReviewKey === key, "action")}
                    onClick={() => onSelectReviewKey?.(key)}
                  >
                    <div className="timeline-summary">{payload.summary_text}</div>
                    <div className="timeline-meta">
                      <span>{actionLabel(payload.action_type)}</span>
                      {payload.target_summary ? <span>{payload.target_summary}</span> : null}
                      {payload.risk_level !== "none" ? <span className={`risk-${payload.risk_level}`}>needs approval</span> : null}
                    </div>
                  </article>
                );
              })}
            </section>

            <section className="activity-section">
              <div className="activity-section-heading">Interventions</div>
              {filteredInterventions.length === 0 ? <div className="panel-empty">No interventions were needed.</div> : null}
              {filteredInterventions.map((record) => {
                const key = `intervention:${record.intervention_id}`;
                return (
                  <article
                    key={key}
                    className={reviewRowClass(selectedReviewKey === key, "intervention")}
                    onClick={() => onSelectReviewKey?.(key)}
                  >
                    <div className="timeline-summary">{record.headline}</div>
                    <div className="timeline-meta">
                      <span>{record.domain || "current page"}</span>
                      <span>{interventionOutcomeLabel(record.resolution)}</span>
                    </div>
                  </article>
                );
              })}
            </section>

            <section className="activity-section">
              <div className="activity-section-heading">Browser commands</div>
              {!filterBrowserCommands(reviewCommands ?? [], query, interventionsOnly).length ? (
                <div className="panel-empty">No browser commands were recorded.</div>
              ) : null}
              {filterBrowserCommands(reviewCommands ?? [], query, interventionsOnly).map((command) => (
                <article key={`command:${command.command}:${command.command_id}`} className={`timeline-row is-action ${command.status === 'blocked' || command.status === 'failed' ? 'is-blocked' : ''}`}>
                  <div className="timeline-summary" title={command.summary_text}>{command.summary_text}</div>
                  <div className="timeline-meta">
                    <span>{command.command}</span>
                    <span>{command.domain || "current page"}</span>
                    <span>{commandStatusLabel(command.status)}</span>
                  </div>
                </article>
              ))}
            </section>

            <section className="activity-section">
              <div className="activity-section-heading">Outcome</div>
              <article
                className={reviewRowClass(selectedReviewKey === `outcome:${reviewArtifact.session_id}`, "outcome")}
                onClick={() => onSelectReviewKey?.(`outcome:${reviewArtifact.session_id}`)}
              >
                <div className="timeline-summary">{summarizeOutcome(reviewArtifact)}</div>
                <div className="timeline-meta">
                  <span>{reviewArtifact.pages_visited.length} pages</span>
                  <span>{reviewArtifact.interventions.length} interventions</span>
                  <span>{reviewArtifact.status}</span>
                </div>
              </article>
            </section>
          </>
        ) : null}

        {!isReviewMode ? (
          <>
            <section className="activity-section">
              <div className="activity-section-heading">What&apos;s happening</div>
              {filteredBrowserCommands.length === 0 ? <div className="panel-empty">No verified browser steps yet.</div> : null}
              {filteredBrowserCommands.map((command) => (
                <article
                  key={`command:${command.command}:${command.command_id}`}
                  className={`${command === filteredBrowserCommands[filteredBrowserCommands.length - 1] ? "timeline-row active is-action" : "timeline-row is-action"} ${command.status === 'blocked' || command.status === 'failed' ? 'is-blocked' : ''}`}
                >
                  <div className="timeline-summary" title={command.summary_text}>{command.summary_text}</div>
                  <div className="timeline-meta">
                    <span>{command.command}</span>
                    <span>{command.domain || "current page"}</span>
                    <span>{commandStatusLabel(command.status)}</span>
                    {command.reason ? <span>{command.reason.replace(/_/g, " ")}</span> : null}
                  </div>
                </article>
              ))}
            </section>

            <section className="activity-section">
              <div className="activity-section-heading">Pages</div>
              {filteredLivePages.length === 0 ? <div className="panel-empty">Lumon will list pages here once online work begins.</div> : null}
              {filteredLivePages.map((page) => (
                <article key={`${page.firstSeenAt}:${page.url}`} className="timeline-row is-page">
                  <div className="timeline-summary">{page.title || page.domain}</div>
                  <div className="timeline-meta">
                    <span>{page.domain}</span>
                    <span>{environmentLabel(page.environmentType)}</span>
                  </div>
                </article>
              ))}
            </section>

            <section className="activity-section">
              <div className="activity-section-heading">Interventions</div>
              {filteredInterventions.length === 0 ? <div className="panel-empty">Lumon will pause here when a decision needs you.</div> : null}
              {filteredInterventions.map((record) => (
                <article key={record.intervention_id} className="timeline-row is-intervention">
                  <div className="timeline-summary">{record.headline}</div>
                  <div className="timeline-meta">
                    <span>{record.domain || "current page"}</span>
                    {record.resolution ? <span>{interventionOutcomeLabel(record.resolution)}</span> : <span>active</span>}
                  </div>
                </article>
              ))}
            </section>

            <section className="activity-section">
              <div className="activity-section-heading">Helpers</div>
              {Object.values(state.workers).length === 0 ? <div className="panel-empty">No helpers are working in the background.</div> : null}
              {Object.values(state.workers).map((worker) => (
                <article key={worker.agent_id} className="timeline-row">
                  <div className="timeline-summary">{worker.summary_text}</div>
                  <div className="timeline-meta">
                    <span>background</span>
                    <span>{worker.state}</span>
                  </div>
                </article>
              ))}
            </section>
          </>
        ) : null}
      </div>
    </aside>
  );
}
