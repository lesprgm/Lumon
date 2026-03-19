# Component Inventory

## Top-Level Entry Points
- `/Users/leslie/Documents/Lumon/lumon`: primary wrapper command
- `/Users/leslie/Documents/Lumon/README.md`: product-facing repo entry
- `/Users/leslie/Documents/Lumon/APP_OVERVIEW.md`: current app overview

## Scripts
- `/Users/leslie/Documents/Lumon/scripts/lumon_setup.py`: one-time install/bootstrap
- `/Users/leslie/Documents/Lumon/scripts/lumon_doctor.py`: local readiness checks
- `/Users/leslie/Documents/Lumon/scripts/lumon_restart.py`: clean backend/frontend restart for stale local state
- `/Users/leslie/Documents/Lumon/scripts/lumon_app.py`: support launcher for backend + frontend
- `/Users/leslie/Documents/Lumon/scripts/lumon_opencode.py`: wrapper/debug path around OpenCode
- `/Users/leslie/Documents/Lumon/scripts/start_demo_backend.sh`: backend startup script
- `/Users/leslie/Documents/Lumon/scripts/start_demo_frontend.sh`: frontend startup script

## OpenCode Plugin
- `/Users/leslie/Documents/Lumon/.opencode/plugins/lumon.js`: plugin export for OpenCode
- `/Users/leslie/Documents/Lumon/.opencode/lib/lumonPluginCore.js`: plugin config, attach/open controller, `lumon_browser` tool, auto-start logic
- `/Users/leslie/Documents/Lumon/.opencode/tests/lumonPluginCore.test.js`: plugin runtime tests

## Backend
### App shell
- `/Users/leslie/Documents/Lumon/backend/app/main.py`: FastAPI entry and HTTP/WebSocket routes
- `/Users/leslie/Documents/Lumon/backend/app/config.py`: settings, allowed origins, runtime version

### Session system
- `/Users/leslie/Documents/Lumon/backend/app/session/manager.py`: session manager and runtime orchestration
- `/Users/leslie/Documents/Lumon/backend/app/session/artifacts.py`: artifact recorder and keyframe/page tracking
- `/Users/leslie/Documents/Lumon/backend/app/session/opencode_attach.py`: OpenCode attach helper/service
- `/Users/leslie/Documents/Lumon/backend/app/session/state_machine.py`: session-state transition rules

### Adapters
- `/Users/leslie/Documents/Lumon/backend/app/adapters/opencode.py`: OpenCode observer + bridge connector
- `/Users/leslie/Documents/Lumon/backend/app/adapters/playwright_native.py`: delegated browser connector
- `/Users/leslie/Documents/Lumon/backend/app/adapters/registry.py`: connector factory
- `/Users/leslie/Documents/Lumon/backend/app/adapters/base.py`: adapter base contract

### Browser support
- `/Users/leslie/Documents/Lumon/backend/app/browser/actions.py`: browser action layer and evidence capture helpers
- `/Users/leslie/Documents/Lumon/backend/app/browser/screencast.py`: CDP/screenshot streaming helpers
- `/Users/leslie/Documents/Lumon/backend/app/browser/demo_pages.py`: deterministic demo HTML
- `/Users/leslie/Documents/Lumon/backend/app/streaming/webrtc.py`: aiortc session and frame queue used for live stage transport

### Protocol and normalization
- `/Users/leslie/Documents/Lumon/backend/app/protocol/models.py`: strict payload and artifact models
- `/Users/leslie/Documents/Lumon/backend/app/protocol/enums.py`: state/action/error enums
- `/Users/leslie/Documents/Lumon/backend/app/protocol/validation.py`: client/server envelope validation
- `/Users/leslie/Documents/Lumon/backend/app/protocol/normalizer.py`: external event normalization

### Optional integrations
- `/Users/leslie/Documents/Lumon/backend/app/optional/langsmith_bridge.py`: optional trace bridge mapper

## Frontend
### App shell
- `/Users/leslie/Documents/Lumon/frontend/src/App.tsx`: live mode + review mode composition
- `/Users/leslie/Documents/Lumon/frontend/src/styles.css`: shared shell and stage styling

### Components
- `/Users/leslie/Documents/Lumon/frontend/src/components/LiveStage.tsx`: main browser stage
- `/Users/leslie/Documents/Lumon/frontend/src/components/TimelinePanel.tsx`: activity/review drawer
- `/Users/leslie/Documents/Lumon/frontend/src/components/StatusBar.tsx`: top bar and mascot selector
- `/Users/leslie/Documents/Lumon/frontend/src/components/ReviewMetricsSummary.tsx`: review metrics card

### State and protocol
- `/Users/leslie/Documents/Lumon/frontend/src/store/sessionStore.ts`: websocket state reducer
- `/Users/leslie/Documents/Lumon/frontend/src/protocol/types.ts`: frontend protocol types
- `/Users/leslie/Documents/Lumon/frontend/src/lib/sessionBootstrap.ts`: bootstrap URL/session parsing
- `/Users/leslie/Documents/Lumon/frontend/src/lib/sessionSocket.ts`: websocket client and reconnect policy
- `/Users/leslie/Documents/Lumon/frontend/src/lib/webrtcClient.ts`: WebRTC offer/answer handling for the live stage
- `/Users/leslie/Documents/Lumon/frontend/src/lib/reviewMode.ts`: review-step derivation and navigation

### Overlay and sprite system
- `/Users/leslie/Documents/Lumon/frontend/src/overlay/engine/overlayEngine.ts`: scene snapshot and hotspot/sprite placement
- `/Users/leslie/Documents/Lumon/frontend/src/overlay/sprites/`: sprite catalogs, manifests, and animation player
- `/Users/leslie/Documents/Lumon/frontend/public/assets/`: runtime sprite assets

## Artifacts and Output
- `/Users/leslie/Documents/Lumon/output/sessions/`: per-session artifacts
- `/Users/leslie/Documents/Lumon/output/metrics/sessions.ndjson`: local alpha metric rollup
- `/Users/leslie/Documents/Lumon/output/playwright/`: browser recordings/posters when generated
