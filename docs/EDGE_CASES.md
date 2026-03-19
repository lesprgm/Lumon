# Edge Cases

This is the current edge-case register for Lumon.

## Already Covered or Partly Covered
### 1. Stale backend/plugin runtime mismatch
Status: covered

Mitigation:
- plugin checks backend `runtime_version`
- hard failure tells the user to run `./lumon restart`

### 2. Backend up, frontend down
Status: covered

Mitigation:
- plugin starts frontend separately if needed before opening the UI
- `./lumon restart` restarts both services cleanly

### 3. Frontend binds to the wrong host or silently drifts ports
Status: covered

Mitigation:
- Vite is bound to `127.0.0.1:5173`
- `strictPort` is enabled

### 4. Duplicate attach for the same OpenCode session
Status: covered

Mitigation:
- one Lumon session per observed OpenCode session
- duplicate attach returns `already_attached`
- metrics count duplicate prevention

### 5. Blank shell opens before first real browser frame
Status: partly covered

Mitigation:
- `begin_task` should not open the UI
- browser-command results should open only with frame evidence

Still weak:
- if observer noise leaks through or the tool path stalls before a frame, UX can still feel empty

### 6. Raw OpenCode chatter pollutes the activity drawer
Status: partly covered

Mitigation:
- live mode now prefers browser-command rows over broad observer noise
- plugin classifier is stricter about what counts as intervention/browser relevance

Still weak:
- session-level activity can still feel noisy if the normalization layer emits too much general context

### 7. Long tool stall in OpenCode with `0 tokens spent`
Status: partly covered

What is normal:
- short pause while a tool is running

What is not normal:
- backend/tool exceptions or delegate startup deadlocks

Mitigation:
- explicit delegate startup timeout
- runtime bug fixes in the browser-command path

Still weak:
- cold-start latency is still high enough that the tool can feel hung even when not actually deadlocked

### 8. Browser command retries duplicate actions
Status: covered

Mitigation:
- `command_id` is the idempotency key
- duplicate commands return cached results

### 9. Concurrent browser commands race each other
Status: covered

Mitigation:
- per-session command lock
- second command returns `blocked` / `busy`

### 10. Stale `element_id` after navigation
Status: covered

Mitigation:
- `inspect` returns page-versioned element refs
- `click`/`type` reject stale targets

### 11. High-risk actions should block instead of silently executing
Status: partly covered

Mitigation:
- approval/intervention model exists
- blocked commands can surface approval state

Still weak:
- approval resume/deny loops should still be hardened and retested live

### 12. User closes Lumon tab during a run
Status: partly covered

Mitigation:
- reconnect metrics exist
- session runtime has disconnect grace handling
- later browser/intervention episodes can reopen Lumon
- stale websocket credentials now terminate reconnect on backend `1008` instead of reconnecting forever

Still weak:
- end-to-end behavior needs more live validation during real OpenCode runs

### 13. Popup/new-tab/redirect navigation during browser commands
Status: partly covered

Mitigation:
- delegated browser runtime tracks active page and page version

Still weak:
- this path needs explicit acceptance coverage in real browser scenarios

### 14. Read-only webfetch is misrepresented as a live browser run
Status: partly covered

Desired behavior:
- read-only fetch should stay in OpenCode and not show a fake live browser shell

Still weak:
- this distinction is now in the design and tool path, but the UX should be tested harder in live OpenCode prompts

### 15. Artifact contamination across sessions or tasks
Status: partly covered

Mitigation:
- artifacts are per-session
- `begin_task` resets browser-task state in the delegate

Still weak:
- reuse inside a long-lived OpenCode session should be watched carefully in acceptance runs

### 16. Sensitive typed values leak into artifacts
Status: partly covered

Desired behavior:
- passwords/tokens should be blocked or masked

Still weak:
- needs a stricter pass over command evidence recording and review rendering

### 17. Unrelated process already owns `8000` or `5173`
Status: covered

Mitigation:
- `./lumon restart` refuses to kill unrelated processes unless `--force`

### 18. OpenCode plugin code changed but desktop app did not reload it
Status: covered operationally

Mitigation:
- full OpenCode restart required after plugin changes
- docs now call this out explicitly

## Edge Cases Still Worth Adding Tests For
These are the next ones worth hardening with deterministic or acceptance tests.

1. Natural-language prompt chooses read-only webfetch even though the task clearly requires interactive browser control
2. `lumon_browser begin_task` succeeds but `open` never arrives
3. `open` succeeds but no frame is emitted within the expected time budget
4. high-risk command is approved, then navigation changes and the original target becomes stale before retry
5. frontend reconnects while a browser command is in flight
6. delegated browser crashes mid-run after one successful command
7. OpenCode starts a second browser task in the same session before the first one is fully finalized
8. review mode loads a session with commands but no keyframes
9. review mode loads a session with interventions but no resolved outcome
10. browser command evidence says success but the visible page never changed enough to be meaningful

## Product Priority
The highest-value edge cases to keep attacking are:
1. blank-shell before first real page
2. noisy activity unrelated to the current browser task
3. cold-start slowness and perceived tool hangs
4. mismatch between model narration and verified browser evidence

### 19. OpenCode bridge forwards live frames but not command-scoped frame counters
Status: covered

Mitigation:
- bridge runtime now forwards both `latest_frame_generation` and `latest_command_frame_generation`
- command evidence checks use the command-scoped counter so `open`/`status` do not get stuck at `partial/frame_missing` while the page is visibly live

### 20. Finalized artifact files are assumed to exist during a running session
Status: covered in docs, partly covered in tooling

Mitigation:
- docs now distinguish live in-memory review payloads from finalized on-disk artifacts
- review/debug work should use the backend review endpoint first for active sessions

Still weak:
- developers can still confuse partial live artifacts with finalized bundles if they inspect `output/sessions/` too early
