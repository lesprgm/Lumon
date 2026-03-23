# Tester Onboarding

## Who This Is For
This is the shortest path for first local alpha testers.

It assumes:
- you are comfortable with terminal-based developer tools
- you already use or can install OpenCode
- you can install local dependencies on your machine

## Requirements
Before setup, the tester machine needs:
- Python `3.11+`
- Node.js and `npm`
- OpenCode installed and `opencode` available on `PATH`

Lumon setup will install:
- backend Python dependencies
- Playwright Chromium
- frontend dependencies
- project-local OpenCode plugin dependencies

## Install
From the repo root:

```bash
cd <repo-root>
./lumon setup
./lumon doctor
```

Expected result from `./lumon doctor`:
- backend venv: ok
- backend imports: ok
- frontend install: ok
- project plugin: ok
- opencode plugin tool api: ok
- playwright browser: ok
- opencode cli: ok
- npm: ok

If `opencode cli` is missing:
- install OpenCode or add it to `PATH`

If `npm` is missing:
- install Node.js first

## Normal Use
The intended product workflow is:

```bash
cd <repo-root>
opencode .
```

Lumon should:
- attach silently in the background
- stay quiet during non-browser work
- open only when browser activity or intervention becomes relevant

## Recovery
If backend/frontend/plugin state drifts after code changes or repeated runs:

```bash
cd <repo-root>
./lumon restart
```

If plugin behavior still looks wrong, collect a debug bundle:

```bash
cd <repo-root>
./lumon triage
```

## Practical Readiness Assessment
Installation is reasonably simple for technical testers:
- one setup command
- one doctor command
- one normal run command

It is not yet “consumer easy.”

Current friction points:
- OpenCode must already exist on the tester machine
- Node/npm and Python must already be installed
- Playwright Chromium install adds setup time
- this is still a local-alpha workflow, not a packaged app installer
