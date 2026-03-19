# Scripts and Operations

## Primary Commands
Wrapper entry point:
- `/Users/leslie/Documents/Lumon/lumon`

Supported user-facing commands:
- `./lumon setup`
- `./lumon doctor`
- `./lumon triage`
- `./lumon restart`
- `./lumon app`

Internal/debug commands:
- `./lumon opencode`
- `python3 scripts/run_browser_flow_trace.py`
- `node scripts/run_opencode_reliability_harness.mjs`
- `python3 scripts/run_transport_reliability_checks.py`
- `make reliability`

## What Each Command Does
### `./lumon setup`
- creates backend virtualenv
- installs backend dependencies
- installs Playwright Chromium
- installs frontend dependencies
- installs `.opencode` plugin dependencies

### `./lumon doctor`
Checks:
- backend venv
- backend imports
- frontend `node_modules`
- project plugin file
- OpenCode plugin tool API runtime
- Playwright browser install
- `opencode` on PATH
- `npm` on PATH

### `./lumon triage`
Purpose:
- collect a CLI-first debugging bundle when OpenCode + Lumon integration is failing
- prefer direct OpenCode diagnostics over MCP setup for plugin/tool argument issues

Behavior:
- records OpenCode CLI availability and key command outputs
- runs `lumon_doctor` and includes its output
- tails the newest OpenCode log from `~/.local/share/opencode/log/`
- tails Lumon local logs from `/tmp`
- writes a report under `output/manual_checks/opencode_cli_triage_*.md`

Useful flags:
- `--tail-lines N` to control per-log tail size
- `--no-bundle` to print the report without writing a file

### `./lumon restart`
Purpose:
- clear stale local backend/frontend state after local changes
- rotate Lumon logs so the next reproduction is easier to read
- restart the standard backend/frontend pair cleanly

Behavior:
- archives prior `/tmp/lumon-*.log` files into timestamped copies
- kills Lumon-owned backend/frontend/control processes
- refuses unrelated port occupants unless `--force`
- starts backend and waits for `/healthz`
- starts frontend and waits for `127.0.0.1:5173`
- prints new PIDs and log paths

### `python3 scripts/run_browser_flow_trace.py`
Purpose:
- run a deterministic browser-command trace through Lumon
- capture telemetry for the full command pipeline instead of only tailing logs

Behavior:
- can restart backend/frontend first in `stack` mode
- issues a fixed command sequence through `/api/local/opencode/browser/command`
- captures websocket messages from the Lumon session
- reads the session artifact back from the backend
- writes a bundle under `output/manual_checks/browser_flow_trace_*`

Useful flags:
- `--mode stack` to hit the real local backend/frontend
- `--restart` to clear stale state before the run
- `--open-url` to choose the target page for the trace
- `--prompt` to control the `begin_task` text used in the trace

### `node scripts/run_opencode_reliability_harness.mjs`
Purpose:
- run a deterministic repo-local reliability gate for the OpenCode-style browser tool path
- catch lifecycle regressions before manual OpenCode debugging

Behavior:
- drives fixed browser-task scenarios through the real local browser-command stack
- records:
  - command transcript
  - websocket transcript
  - artifact snapshot
  - shell open records
  - plugin/backend/frontend log tails
- writes a bundle under `output/manual_checks/`

Current scenarios:
- `external`
- `local`
- `approval`
- `second-task`

Example:
```bash
cd /Users/leslie/Documents/Lumon
node scripts/run_opencode_reliability_harness.mjs --scenario external,local,approval,second-task
```

### `python3 scripts/run_transport_reliability_checks.py`
Purpose:
- verify the live transport and reconnect path directly against the running backend/frontend
- exercise:
  - command path
  - approval flow
  - reconnect replay
  - stale-token websocket rejection

Behavior:
- drives local browser-command scenarios
- captures transport/reconnect artifacts under `output/manual_checks/`
- can restart Lumon first with `--restart`

Example:
```bash
cd /Users/leslie/Documents/Lumon
python3 scripts/run_transport_reliability_checks.py --restart
```

### `make reliability`
Purpose:
- run the repo-local reliability gate before more manual OpenCode debugging

Behavior:
- backend tests
- frontend tests
- plugin tests
- OpenCode/plugin harness
- transport harness

### `./lumon app`
Support launcher that starts backend and frontend together for a plugin-first local flow.

## Startup Scripts
- `/Users/leslie/Documents/Lumon/scripts/start_demo_backend.sh`
- `/Users/leslie/Documents/Lumon/scripts/start_demo_frontend.sh`

These are the scripts the plugin uses for auto-start as well.

Current frontend runtime behavior:
- default to `vite preview` on `127.0.0.1:5173`
- rebuild when source is newer than `dist/`
- avoid detached `vite dev` by default because it was too unstable for the plugin-first flow

## Logs
Current local log files:
- `/tmp/lumon-plugin-debug.log`
- `/tmp/lumon-backend.log`
- `/tmp/lumon-frontend.log`

## Operational Reality
`./lumon restart` restarts Lumon services. It does not reload plugin code inside an already-running OpenCode desktop process.

If `.opencode` plugin code changed:
1. run `./lumon restart`
2. fully restart OpenCode

That is the cleanest way to avoid stale backend/plugin mismatches.
