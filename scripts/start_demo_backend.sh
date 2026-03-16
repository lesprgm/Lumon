#!/bin/zsh
set -euo pipefail

if [[ "${ZSH_EVAL_CONTEXT:-}" == *:file ]]; then
  print -u2 "Do not source this file. Run ./scripts/start_demo_backend.sh or zsh scripts/start_demo_backend.sh"
  return 2 2>/dev/null || exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

port_is_listening() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

choose_backend_port() {
  local requested_port="$1"
  local max_probe="${LUMON_BACKEND_PORT_MAX_PROBE:-10}"
  local candidate="$requested_port"
  local probe_count=0

  if ! port_is_listening "$requested_port"; then
    print -r -- "$requested_port"
    return 0
  fi

  if [[ "${LUMON_BACKEND_PORT_STRICT:-0}" == "1" ]]; then
    print -u2 "Port ${requested_port} is already in use and strict mode is enabled."
    return 1
  fi

  while (( probe_count < max_probe )); do
    candidate=$(( requested_port + probe_count + 1 ))
    if ! port_is_listening "$candidate"; then
      print -r -- "$candidate"
      return 0
    fi
    probe_count=$(( probe_count + 1 ))
  done

  print -u2 "No free backend port found from ${requested_port} to $(( requested_port + max_probe ))."
  return 1
}

set -a
source "$REPO_ROOT/.env" 2>/dev/null || true
set +a
cd "$REPO_ROOT/backend"
PYTHON_BIN="./.venv/bin/python"
if [[ -x "$PYTHON_BIN" ]]; then
  :
else
  PYTHON_BIN="python3"
fi

REQUESTED_PORT="${LUMON_BACKEND_PORT:-8000}"
PREFLIGHT_SCRIPT="$SCRIPT_DIR/lumon_preflight_port.sh"

if [[ -x "$PREFLIGHT_SCRIPT" ]]; then
  PREFLIGHT_ARGS=(--port "$REQUESTED_PORT")
  if [[ "${LUMON_BACKEND_KILL_PORT_OWNER:-0}" == "1" ]]; then
    PREFLIGHT_ARGS+=(--kill)
  fi

  set +e
  "$PREFLIGHT_SCRIPT" "${PREFLIGHT_ARGS[@]}"
  PREFLIGHT_STATUS=$?
  set -e

  if (( PREFLIGHT_STATUS != 0 && PREFLIGHT_STATUS != 10 )); then
    exit "$PREFLIGHT_STATUS"
  fi
fi

SELECTED_PORT="$(choose_backend_port "$REQUESTED_PORT")"

if [[ "$SELECTED_PORT" != "$REQUESTED_PORT" ]]; then
  print -u2 "Port ${REQUESTED_PORT} is busy; starting backend on ${SELECTED_PORT}."
fi

RUNTIME_DIR="$REPO_ROOT/output/runtime"
RUNTIME_ENV_FILE="$RUNTIME_DIR/lumon_backend.env"
mkdir -p "$RUNTIME_DIR"
{
  print -r -- "LUMON_BACKEND_PORT=${SELECTED_PORT}"
  print -r -- "VITE_LUMON_BACKEND_ORIGIN=http://127.0.0.1:${SELECTED_PORT}"
} > "$RUNTIME_ENV_FILE"

export LUMON_BACKEND_PORT="$SELECTED_PORT"
export VITE_LUMON_BACKEND_ORIGIN="http://127.0.0.1:${SELECTED_PORT}"

UVICORN_ARGS=(app.main:app --host 127.0.0.1 --port "$SELECTED_PORT")

# Plugin-first local alpha flow should be stable and watcher-free by default.
# Opt back into reload explicitly for manual backend-only development.
if [[ "${LUMON_BACKEND_RELOAD:-0}" == "1" ]]; then
  UVICORN_ARGS+=(--reload)
fi

exec "$PYTHON_BIN" -m uvicorn "${UVICORN_ARGS[@]}"
