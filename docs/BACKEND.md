# Backend

## Purpose
The backend is the session runtime, browser delegate entry point, and artifact writer.

## Main Entry
`/Users/leslie/Documents/Lumon/backend/app/main.py`

Key routes:
- `GET /healthz`: liveness + protocol/runtime version
- `POST /api/local/observe/opencode`: local OpenCode session attach
- `POST /api/local/opencode/browser/command`: interactive browser command execution
- `GET /api/session-artifacts/{session_id}`: review payload
- `GET /api/session-artifacts/{session_id}/keyframes/{filename}`: review keyframes
- `WS /ws/session`: live session stream to frontend

## SessionManager and SessionRuntime
`SessionManager` creates and owns runtimes. `SessionRuntime` is the core state container.

Main responsibilities in `/Users/leslie/Documents/Lumon/backend/app/session/manager.py`:
- create/join sessions
- enforce websocket token/origin checks
- handle client messages (`start_task`, `attach_observer`, `ui_ready`, approval actions)
- broadcast validated server messages
- emit session state, frames, browser context, agent events, approvals, and errors
- attach a local OpenCode observer
- execute local OpenCode browser commands
- finalize session artifacts on terminal states

Important runtime state fields:
- `adapter_id`
- `run_mode`
- `observer_mode`
- `web_mode`
- `web_bridge`
- `active_checkpoint_id`
- current connector instance
- artifact recorder

## Connector Model
Connectors are selected through `/Users/leslie/Documents/Lumon/backend/app/adapters/registry.py`.

Two important connectors:
- `OpenCodeConnector`
- `PlaywrightNativeConnector`

### OpenCodeConnector
Responsibilities:
- observe or run OpenCode
- normalize streamed OpenCode events into Lumon agent events
- optionally create a delegated Playwright bridge for browser work
- proxy bridge-originated frames, browser context, approvals, and command artifacts back into the parent Lumon runtime

Important bridge rule:
- the runtime proxy must forward both `latest_frame_generation` and `latest_command_frame_generation`
- if `latest_command_frame_generation` is missing, browser commands can degrade into `partial/frame_missing` even while frames are visibly flowing

Signal-first routing behavior:
- routing decisions now rely on structured classifier tiers from `backend/app/opencode_signals.py`
- tier `A` and `B` signals may trigger browser delegate launch
- tier `C` text-only heuristic matches are fallback-only and do not auto-launch browser delegate
- each launch decision is recorded as an artifact event with `reason_code`, classifier tier, confidence, and launch outcome

### PlaywrightNativeConnector
Responsibilities:
- launch and own the browser runtime
- keep track of current page and page version
- expose generic command execution
- emit snapshot frames and browser context updates
- enforce idempotency, busy locking, stale target rejection, and approval blocking

## Browser Command Path
Models live in `/Users/leslie/Documents/Lumon/backend/app/protocol/models.py`.

Main types:
- `BrowserCommandRequest`
- `BrowserCommandResult`
- `BrowserElementRef`
- `BrowserEvidence`
- `BrowserCommandRecord`

Current supported commands:
- `begin_task`
- `status`
- `inspect`
- `open`
- `click`
- `type`
- `scroll`
- `wait`
- `stop`

Design constraints:
- `command_id` is the idempotency key
- `click` and `type` should prefer `element_id`
- `success` requires evidence
- risky actions should block instead of pretending to succeed

## Artifact Recorder
`/Users/leslie/Documents/Lumon/backend/app/session/artifacts.py`

Responsibilities:
- record event log
- record browser commands
- track page visits and browser context
- store intervention lifecycle
- capture milestone keyframes
- compute and persist session metrics
- write artifact files on finalize

Finalize behavior:
- `session.json`, `interventions.json`, `events.ndjson`, and `commands.ndjson` are written when the session finalizes
- the live review endpoint can still expose in-memory artifact state before those files exist on disk

Routing telemetry now appears in `events.ndjson` as `routing_decision` events, including:
- `signal` and `tier`
- `confidence`
- `reason_code` and `classifier_reason_code`
- `selected_web_mode` and `selected_web_bridge`
- `should_launch_bridge`

Current metric fields include:
- attach/open timestamps and latencies
- browser episode count
- intervention count
- reconnect count
- duplicate attach prevented
- browser command quality counts
- stale target count
- artifact written

## Validation and Protocol Discipline
The backend validates both incoming and outgoing envelopes.

Relevant files:
- `/Users/leslie/Documents/Lumon/backend/app/protocol/validation.py`
- `/Users/leslie/Documents/Lumon/backend/app/protocol/models.py`
- `/Users/leslie/Documents/Lumon/backend/app/protocol/enums.py`

This matters because Lumon's frontend and artifact pipeline assume the normalized contract is trustworthy.

## Failure Modes the Backend Owns
- stale backend runtime version
- local-only access enforcement
- duplicate attach to the same OpenCode session
- stale `element_id` after navigation
- concurrent browser-command contention
- delegated browser startup timeout
- browser command with missing evidence
- bridge/runtime counter mismatches that break command-scoped frame verification
- artifact finalization and keyframe persistence
