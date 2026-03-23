# Lumon OpenCode Plugin

This folder contains the project-local OpenCode plugin for Lumon.

This is the primary local-alpha integration path.

Normal setup:

```bash
cd <repo-root>
./lumon setup
./lumon doctor
```

Prerequisites on the tester machine:
- Python `3.11+`
- Node.js / `npm`
- OpenCode installed and `opencode` available on `PATH`

If the plugin starts opening stale or blank sessions after local code changes, run:

```bash
cd <repo-root>
./lumon restart
```

What it does:
- OpenCode loads `.opencode/plugins/lumon.js`
- the plugin watches OpenCode session/message/tool events
- the plugin registers a real `lumon_browser` tool for interactive browser actions
- on first relevant session activity it calls Lumon's local attach API
- if Lumon is not running yet, it starts the Lumon backend and frontend directly
- it opens the Lumon UI only when online work or intervention-like events appear
- it reopens the Lumon UI for later browser/intervention episodes in the same OpenCode session after a cooldown

`lumon_browser` is the evidence-backed path for:
- opening a page
- inspecting actionable elements
- clicking
- typing
- scrolling
- waiting
- stopping before a risky step

It should be used for interactive browser work. Read-only fetch/summarize tasks should stay on normal OpenCode web tools.

Defaults:
- observation first
- `auto_delegate = false`
- open only on browser/intervention

Optional env overrides:
- `LUMON_PLUGIN_BACKEND_ORIGIN`
- `LUMON_PLUGIN_FRONTEND_ORIGIN`
- `LUMON_PLUGIN_WEB_MODE`
- `LUMON_PLUGIN_AUTO_DELEGATE`
- `LUMON_PLUGIN_OPEN_POLICY`
- `LUMON_PLUGIN_DISABLE_AUTO_START`
- `LUMON_PLUGIN_BROWSER_EPISODE_GAP_MS`
- `LUMON_PLUGIN_INTERVENTION_EPISODE_GAP_MS`
- `LUMON_PLUGIN_REOPEN_COOLDOWN_MS`
