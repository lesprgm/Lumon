import type { AgentEventPayload, InterventionRecord, SessionArtifactResponse } from "../protocol/types";
import type { ReviewSelectionKey } from "./reviewMode";

function reviewStepTimestamp(
  selectedEvent: AgentEventPayload | null,
  selectedIntervention: InterventionRecord | null,
  response: SessionArtifactResponse,
): string {
  if (selectedIntervention?.started_at) {
    return selectedIntervention.started_at;
  }
  if (selectedEvent?.timestamp) {
    return selectedEvent.timestamp;
  }
  return response.artifact.completed_at ?? response.artifact.started_at;
}

function parseSelectionKey(key: ReviewSelectionKey): { kind: string; id: string } | null {
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

export function resolveReviewKeyframePath(
  response: SessionArtifactResponse,
  selectedKey: ReviewSelectionKey,
  selectedEvent: AgentEventPayload | null,
  selectedIntervention: InterventionRecord | null,
): string | null {
  if (selectedIntervention?.keyframe_path) {
    return selectedIntervention.keyframe_path;
  }

  const parsedKey = parseSelectionKey(selectedKey);
  const selectedPage =
    parsedKey?.kind === "page"
      ? response.artifact.pages_visited.find((record) => record.first_seen_at === parsedKey.id) ?? null
      : null;
  if (selectedPage?.keyframe_path) {
    return selectedPage.keyframe_path;
  }

  const selectedAt = Date.parse(reviewStepTimestamp(selectedEvent, selectedIntervention, response));
  const selectedUrl = selectedPage?.url ?? selectedIntervention?.source_url ?? null;

  const commandCandidates = [...(response.commands ?? [])]
    .filter((command) => Boolean(command.evidence?.keyframe_path))
    .filter((command) => Date.parse(command.timestamp) <= selectedAt)
    .filter((command) => !selectedUrl || command.source_url === selectedUrl)
    .sort((left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp));
  if (commandCandidates[0]?.evidence?.keyframe_path) {
    return commandCandidates[0].evidence.keyframe_path;
  }

  const interventionCandidates = [...response.artifact.interventions]
    .filter((record) => Boolean(record.keyframe_path))
    .filter((record) => Date.parse(record.started_at) <= selectedAt)
    .filter((record) => !selectedUrl || record.source_url === selectedUrl)
    .sort((left, right) => Date.parse(right.started_at) - Date.parse(left.started_at));
  if (interventionCandidates[0]?.keyframe_path) {
    return interventionCandidates[0].keyframe_path;
  }

  if (selectedPage) {
    return response.artifact.keyframes[0] ?? null;
  }
  return selectedEvent ? null : response.artifact.keyframes.at(-1) ?? null;
}
