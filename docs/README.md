# Lumon Docs

This folder is the codebase-grounded architecture and operations reference for Lumon.

The docs are split by subsystem so you can read only the layer you need:
- product/runtime shape
- transport and streaming
- backend contracts
- frontend behavior
- OpenCode plugin behavior
- artifacts and review
- operations and troubleshooting

## Doc Map
- `ARCHITECTURE.md`: end-to-end system architecture, runtime boundaries, and main flows
- `TESTER_ONBOARDING.md`: shortest setup and first-run path for local alpha testers
- `COMPONENT_INVENTORY.md`: file-by-file component map for the current repo
- `BACKEND.md`: FastAPI routes, session runtime, adapters, command execution, and artifact writing
- `FRONTEND.md`: app shell, live stage, review mode, websocket/WebRTC clients, and UI state model
- `OPENCODE_PLUGIN.md`: project-local OpenCode plugin, `lumon_browser`, prompt steering, attach/open behavior, and startup logic
- `STREAMING_AND_TRANSPORT.md`: how live frames move from Playwright to the Lumon stage, including WebRTC and fallback behavior
- `ARTIFACTS_AND_REVIEW.md`: artifact bundle structure, file semantics, and how review mode derives its state
- `SCRIPTS_AND_OPERATIONS.md`: `./lumon` commands, startup scripts, restart behavior, logs, and trace tooling
- `TROUBLESHOOTING.md`: symptom-to-log mapping and concrete debugging steps for the current stack
- `EDGE_CASES.md`: current edge-case register, mitigations, and remaining weak spots

## Current Product Shape
Lumon is a local supervision layer for OpenCode sessions.

Normal flow:
1. `./lumon setup`
2. `./lumon doctor`
3. `opencode .`
4. The plugin attaches silently
5. Lumon opens only when there is verified browser work or an intervention

Interactive browser work should go through the real `lumon_browser` tool path. Read-only web work should stay in OpenCode's read-only web tools.

## Reading Order
If you need to understand Lumon quickly, read in this order:
1. `ARCHITECTURE.md`
2. `OPENCODE_PLUGIN.md`
3. `STREAMING_AND_TRANSPORT.md`
4. `ARTIFACTS_AND_REVIEW.md`
5. `TROUBLESHOOTING.md`

If you are changing code in one subsystem, jump straight to the matching subsystem doc and then use `COMPONENT_INVENTORY.md` for the exact files.
