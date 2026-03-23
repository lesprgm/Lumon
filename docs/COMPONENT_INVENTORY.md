# Component Inventory

## Top-Level Entry Points
- `lumon`: primary wrapper command
- `README.md`: product-facing repo entry
- `APP_OVERVIEW.md`: current app overview

## Scripts
- `scripts/lumon_setup.py`: one-time install/bootstrap
- `scripts/lumon_doctor.py`: local readiness checks
- `scripts/lumon_restart.py`: clean backend/frontend restart for stale local state
- `scripts/lumon_app.py`: support launcher for backend + frontend
- `scripts/lumon_opencode.py`: wrapper/debug path around OpenCode
- `scripts/start_demo_backend.sh`: backend startup script
- `scripts/start_demo_frontend.sh`: frontend startup script

## OpenCode Plugin
- `.opencode/plugins/lumon.js`: plugin export for OpenCode
- `.opencode/lib/lumonPluginCore.js`: plugin config, attach/open controller, `lumon_browser` tool, auto-start logic
- `.opencode/tests/lumonPluginCore.test.js`: plugin runtime tests

## Backend
### App shell
- `backend/app/main.py`: FastAPI entry and HTTP/WebSocket routes
- `backend/app/config.py`: settings, allowed origins, runtime version

### Session system
- `backend/app/session/manager.py`: session manager and runtime orchestration
- `backend/app/session/artifacts.py`: artifact recorder and keyframe/page tracking
- `backend/app/session/opencode_attach.py`: OpenCode attach helper/service
- `backend/app/session/state_machine.py`: session-state transition rules

### Adapters
- `backend/app/adapters/opencode.py`: OpenCode observer + bridge connector
- `backend/app/adapters/playwright_native.py`: delegated browser connector
- `backend/app/adapters/registry.py`: connector factory
- `backend/app/adapters/base.py`: adapter base contract

### Browser support
- `backend/app/browser/actions.py`: browser action layer and evidence capture helpers
- `backend/app/browser/screencast.py`: CDP/screenshot streaming helpers
- `backend/app/browser/demo_pages.py`: deterministic demo HTML
- `backend/app/streaming/webrtc.py`: aiortc session and frame queue used for live stage transport

### Protocol and normalization
- `backend/app/protocol/models.py`: strict payload and artifact models
- `backend/app/protocol/enums.py`: state/action/error enums
- `backend/app/protocol/validation.py`: client/server envelope validation
- `backend/app/protocol/normalizer.py`: external event normalization

### Optional integrations
- `backend/app/optional/langsmith_bridge.py`: optional trace bridge mapper

## Frontend
### App shell
- `frontend/src/App.tsx`: live mode + review mode composition
- `frontend/src/styles.css`: shared shell and stage styling

### Components
- `frontend/src/components/LiveStage.tsx`: main browser stage
- `frontend/src/components/TimelinePanel.tsx`: activity/review drawer
- `frontend/src/components/StatusBar.tsx`: top bar and mascot selector
- `frontend/src/components/ReviewMetricsSummary.tsx`: review metrics card

### State and protocol
- `frontend/src/store/sessionStore.ts`: websocket state reducer
- `frontend/src/protocol/types.ts`: frontend protocol types
- `frontend/src/lib/sessionBootstrap.ts`: bootstrap URL/session parsing
- `frontend/src/lib/sessionSocket.ts`: websocket client and reconnect policy
- `frontend/src/lib/webrtcClient.ts`: WebRTC offer/answer handling for the live stage
- `frontend/src/lib/reviewMode.ts`: review-step derivation and navigation

### Overlay and sprite system
- `frontend/src/overlay/engine/overlayEngine.ts`: scene snapshot and hotspot/sprite placement
- `frontend/src/overlay/sprites/`: sprite catalogs, manifests, and animation player
- `frontend/public/assets/`: runtime sprite assets

## Artifacts and Output
- `output/sessions/`: per-session artifacts
- `output/metrics/sessions.ndjson`: local alpha metric rollup
- `output/playwright/`: browser recordings/posters when generated
