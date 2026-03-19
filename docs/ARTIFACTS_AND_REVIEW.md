# Artifacts and Review

## Purpose
This document explains what Lumon writes to disk, when those files appear, and how review mode reconstructs a run.

## Artifact Root
Per-session artifacts live under:
- `/Users/leslie/Documents/Lumon/output/sessions/<session_id>/`

Metrics rollups live under:
- `/Users/leslie/Documents/Lumon/output/metrics/sessions.ndjson`

## Session Files
### Always conceptually part of the artifact bundle
- `session.json`
- `events.ndjson`
- `commands.ndjson`
- `interventions.json`
- `keyframes/`

### Important caveat
Those files are written on **finalize**, not guaranteed at attach time.

That means:
- a live session can already have in-memory commands, interventions, and keyframes
- the on-disk directory may still be incomplete until the session finishes
- during a running session, the backend review endpoint can serve a live artifact view even if the finalized files do not exist yet

## Writer
File:
- `/Users/leslie/Documents/Lumon/backend/app/session/artifacts.py`

`SessionArtifactRecorder` owns:
- browser context
- event stream
- browser commands
- interventions
- current page visits
- latest frame bytes for milestone capture
- derived metrics

## File Semantics
### `session.json`
The canonical summary artifact.

Contains:
- session identity
- adapter identity
- task text
- observer mode
- status
- started/completed timestamps
- current browser context snapshot
- pages visited
- interventions
- keyframe paths
- metrics

### `events.ndjson`
Normalized event trail for the run.

Contains things like:
- normalized agent events
- routing decisions
- browser context changes
- live state transitions

It is append-only during the run in memory and written to disk on finalize.

### `commands.ndjson`
Browser command trail.

Contains:
- command name
- command id
- status
- reason
- evidence
- target/page metadata

Important detail:
- live review payloads dedupe repeated command ids when served by the backend
- the finalized file itself preserves the command records that were appended during the run

### `interventions.json`
Intervention lifecycle records.

Contains:
- intervention id
- kind
- headline/reason
- checkpoint id
- related keyframe path
- resolution and resolved timestamp when available

A run can have unresolved interventions in live state. Review surfaces must handle that without assuming a resolution exists.

### `keyframes/`
Milestone stills.

These are not full video frames. They are milestone captures such as:
- browser-context changes
- intervention start
- final status capture

Current naming pattern:
- `001_browser_context.jpg`
- `002_intervention_approval.jpg`
- `003_completed.jpg`

## Live Review Endpoint
Backend route:
- `/Users/leslie/Documents/Lumon/backend/app/main.py`
- `GET /api/session-artifacts/{session_id}`

Behavior:
- if the session is still live in memory, return a synthesized current artifact view
- otherwise read the finalized files from disk
- include `events` and `commands` alongside the artifact summary

This is why review mode can still work during or immediately after a live session even before all files are finalized on disk.

## Review Mode Construction
Frontend files:
- `/Users/leslie/Documents/Lumon/frontend/src/lib/reviewMode.ts`
- `/Users/leslie/Documents/Lumon/frontend/src/App.tsx`
- `/Users/leslie/Documents/Lumon/frontend/src/components/TimelinePanel.tsx`
- `/Users/leslie/Documents/Lumon/frontend/src/components/LiveStage.tsx`

Review mode derives:
- page transitions
- important actions
- interventions
- outcome/summary

It is milestone-based, not full video scrubbing.

## Current Constraints
- Commands can exist without keyframes; review mode must not assume every command has a still.
- Interventions can exist without a resolved outcome; review mode must display them as unresolved rather than guessing.
- Repeated visits to the same page should be preserved as distinct visits when they matter to the run narrative.

## Security and Privacy
Sensitive typed values should be:
- blocked before execution when appropriate
- or redacted from evidence and review when execution is allowed

Artifacts should never be treated as safe to share blindly. They can contain:
- visited URLs
- page titles
- screenshots/keyframes
- approval decisions

## Practical Debugging Rules
If a user says:
- “review is empty”
  - check whether the session finalized and whether `session.json` exists
- “commands are missing”
  - compare the live review endpoint response against `commands.ndjson`
- “wrong screenshot in review”
  - check the keyframe list and the event/command timestamps used to select the step
