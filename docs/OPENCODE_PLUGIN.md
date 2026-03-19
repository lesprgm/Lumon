# OpenCode Plugin

## Purpose
The plugin is the primary Lumon integration path.

Files:
- `/Users/leslie/Documents/Lumon/.opencode/plugins/lumon.js`
- `/Users/leslie/Documents/Lumon/.opencode/lib/lumonPluginCore.js`

## Main Responsibilities
- load config from environment
- observe OpenCode session/message/tool/permission events
- attach the current OpenCode session to Lumon
- expose the model-visible `lumon_browser` tool
- auto-start backend/frontend if needed
- auto-open Lumon only when browser evidence or interventions justify it
- fail fast when the backend runtime version is stale

## `lumon_browser`
This is the real browser-command path.

It is intentionally separate from read-only web tools.

The plugin tells the OpenCode model:
- use `lumon_browser` for interactive browser work
- keep read-only fetch/summarize on normal OpenCode web tools
- never narrate browser success without `lumon_browser` evidence

Current steering strategy:
- use safe tool/system steering, not synthetic message-part mutation
- keep observer-driven UI opening suppressed while tool-backed browser work is pending or active

## Auto-Start Logic
Runtime helpers in `lumonPluginCore.js` do three things:
- check `/healthz`
- start backend with `./scripts/start_demo_backend.sh` if needed
- start frontend with `./scripts/start_demo_frontend.sh` if needed

The plugin opens the Lumon UI only after the frontend is reachable.

## Open Trigger Rules
Important product rules in the plugin:
- observer chatter should not open Lumon by itself
- structured interventions can open Lumon
- verified browser command results with frame evidence can open Lumon
- `begin_task` alone should not open Lumon
- later successful commands in the same active browser run should not reopen the same Lumon tab

## Stale Runtime Protection
The plugin checks `runtime_version` from `/healthz`.

If the plugin code and backend code drift, it throws a hard error and tells the user to run:
- `./lumon restart`

That prevents subtle mismatches where the plugin expects a newer backend contract than the backend actually serves.

## Current Weak Spots
- OpenCode still decides whether to call the tool; the plugin exposes `lumon_browser`, but the model must still choose it
- plugin code changes still require a full OpenCode restart to reload the updated plugin runtime
- local auto-start still adds noticeable first-use latency on cold boot
- observer event streams are still noisy enough that any relaxation in suppression logic can regress back into premature UI opens
