# Frontend

## Purpose
The frontend is the supervision surface. It should stay quiet by default, render a live stage when real page evidence exists, and load artifact-backed review mode for completed sessions.

## Main Entry
`frontend/src/App.tsx`

`App.tsx` is responsible for:
- deciding between live mode and review mode
- bootstrapping websocket sessions
- coordinating the overlay engine
- persisting mascot choice
- loading review artifacts
- exposing review navigation and metrics summary state

## Main UI Components
### LiveStage
`frontend/src/components/LiveStage.tsx`

Responsibilities:
- render the current page frame or review keyframe
- render target point / target rect
- render the sprite with offset placement
- show quiet browser chrome (domain/title/environment)
- display intervention UI and captions

### TimelinePanel
`frontend/src/components/TimelinePanel.tsx`

Responsibilities:
- live activity drawer
- review navigator
- page transitions, interventions, and browser-command rows
- filters for text/domain/interventions

### StatusBar
`frontend/src/components/StatusBar.tsx`

Responsibilities:
- top status chrome
- session state summary
- mascot selector (`Lobster` / `Dog`)

### ReviewMetricsSummary
`frontend/src/components/ReviewMetricsSummary.tsx`

Responsibilities:
- compact developer-facing review metrics
- hidden by default
- review mode only

## State Model
`frontend/src/store/sessionStore.ts`

The reducer owns:
- current websocket connection state
- stage readiness
- live session state payload
- browser context
- page visits
- browser commands
- interventions and active intervention
- latest frame
- agent timeline rows
- task result

The stage should prefer:
1. active WebRTC stream
2. websocket frame fallback
3. quiet placeholder only when no verified frame exists yet

Important design rule:
- live UI should key off verified browser-command and browser-context signals, not broad observer chatter

## Review Mode
Helper logic lives in `frontend/src/lib/reviewMode.ts`.

Responsibilities:
- derive review steps from artifact data
- compute default selection
- move previous/next through key review events
- build plain-language summaries
- jump to interventions or page changes

Review mode is milestone-based, not full video scrubbing.

## WebSocket and WebRTC Clients
Files:
- `frontend/src/lib/sessionSocket.ts`
- `frontend/src/lib/webrtcClient.ts`

Responsibilities:
- `SessionSocket` carries the control/state channel and fallback frame transport
- `WebRTCClient` negotiates the live media stream when the backend/runtime supports it

Important reconnect rule:
- websocket close code `1008` means stale or invalid session credentials
- the frontend should not reconnect forever on that path

## Overlay Engine and Sprites
Overlay code lives under `frontend/src/overlay/`.

Key pieces:
- `engine/overlayEngine.ts`: derives scene snapshots, hotspot resolution, sprite targets
- `sprites/`: sprite catalog, manifests, animation player, family selection support

Current sprite families:
- `lobster`
- `dog`

The browser page should remain the truth layer. Sprite and markers are explanatory overlays, not the main content.

## Frontend Failure Modes
Current frontend-specific weak spots to watch:
- stage opens before first real frame
- stale frame sequence handling causing real snapshot frames to be dropped
- activity drawer showing raw observer noise instead of browser-command rows
- stale tabs reconnecting with dead credentials unless `1008` is treated as terminal
- preview/runtime drift if the frontend process is restarted without reloading an old tab
- review mode selecting the wrong event/intervention when artifact data is sparse
