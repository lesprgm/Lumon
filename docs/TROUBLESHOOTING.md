# Troubleshooting

## Purpose
This document maps common Lumon failures to the files and logs that actually explain them.

Current log files:
- `/tmp/lumon-plugin-debug.log`
- `/tmp/lumon-backend.log`
- `/tmp/lumon-frontend.log`

If you need a clean run, start with:
```bash
cd /Users/leslie/Documents/Lumon
./lumon restart
```

If `.opencode` plugin code changed, also fully restart OpenCode.

## First Triage Split
### Symptom: Lumon tab never opens
Check in this order:
1. `/tmp/lumon-plugin-debug.log`
2. `/tmp/lumon-backend.log`
3. `/tmp/lumon-frontend.log`

What to look for:
- `attach.response`
- `browserCommand.response`
- `openUrl.begin`
- frontend preview startup on `127.0.0.1:5173`

### Symptom: Lumon opens but the stage is blank
Most likely causes:
- command result returned `partial/frame_missing`
- no real frame exists yet
- stale observer chatter opened the UI before the tool-backed path produced evidence

Check:
- `/tmp/lumon-plugin-debug.log` for `browserCommand.response`
- `/tmp/lumon-backend.log` for command reasons like `frame_missing`

Specific known case:
- if the OpenCode bridge proxy does not forward `latest_command_frame_generation`, `open` and `status` can stay stuck at `partial/frame_missing` even while the page is live

### Symptom: Safari says it cannot connect to `127.0.0.1:5173`
Check:
- `/tmp/lumon-frontend.log`

Expected frontend runtime today:
- `vite preview --host 127.0.0.1 --port 5173 --strictPort`

Likely causes:
- frontend not started
- port conflict
- stale old frontend process

### Symptom: OpenCode shows `0 tokens spent` and appears hung
Short version:
- a short pause while a tool is running is normal
- a long stall is usually a real tool/runtime problem

Check:
- `/tmp/lumon-plugin-debug.log` for the last `lumon_browser` command that started
- `/tmp/lumon-backend.log` for the matching backend exception or timeout

Recent concrete causes in this repo have included:
- target-resolution timeouts in the `type` path
- command results stuck on `partial/frame_missing`
- plugin observer chatter racing the tool-backed path

### Symptom: Lumon keeps reloading or autorefreshing during a run
Most likely cause:
- the plugin is reopening the Lumon URL during the same active browser session

Check:
- `/tmp/lumon-plugin-debug.log`

What should be true now:
- you should see `openSignal.suppressed_tool_active` for later commands in the same run
- you should **not** see a fresh `openUrl.begin` during `inspect`, `click`, `type`, or `stop` unless there is a new intervention or genuinely new browser episode

### Symptom: Backend log is full of websocket `403` noise
Likely cause:
- a stale old Lumon tab is reconnecting with dead `session_id/ws_token`

Current backend behavior:
- websocket auth failure returns `1008`

Current frontend behavior:
- new frontend code should stop reconnecting forever after a `1008`

If the noise persists:
- close or reload old Lumon tabs
- then rerun with fresh logs

## Clean-Room Debug Procedure
1. `./lumon restart`
2. close existing Lumon tabs
3. if plugin code changed, restart OpenCode
4. run one reproduction only
5. immediately inspect only the fresh logs

Do not debug from append-only stale logs after multiple different runs.

## Telemetry Trace Tool
Use this when plain log tails are not enough:
```bash
cd /Users/leslie/Documents/Lumon
python3 scripts/run_browser_flow_trace.py --mode stack --restart
```

It writes a bundle under:
- `/Users/leslie/Documents/Lumon/output/manual_checks/browser_flow_trace_*`

That bundle includes:
- browser command sequence and timings
- websocket message capture
- session artifact snapshot
- backend/frontend/plugin log tails

## Symptom-to-Owner Mapping
### Plugin issue
Look in:
- `.opencode/lib/lumonPluginCore.js`

Examples:
- tool not chosen or not steered correctly
- Lumon opens too early
- Lumon reopens mid-run
- backend/frontend startup orchestration

### Backend/runtime issue
Look in:
- `backend/app/session/manager.py`
- `backend/app/adapters/opencode.py`
- `backend/app/adapters/playwright_native.py`
- `backend/app/browser/actions.py`

Examples:
- command timeout
- stale target
- `frame_missing`
- approval checkpoint failure
- bridge/runtime counter mismatch

### Frontend issue
Look in:
- `frontend/src/App.tsx`
- `frontend/src/lib/sessionSocket.ts`
- `frontend/src/lib/webrtcClient.ts`
- `frontend/src/components/LiveStage.tsx`
- `frontend/src/store/sessionStore.ts`

Examples:
- stage not updating
- reconnect weirdness
- stale live state after restart
- review selection mismatch
