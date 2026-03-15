#!/bin/zsh
set -euo pipefail

PORT=8000
KILL_OWNER=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      shift
      PORT="${1:-}"
      ;;
    --kill)
      KILL_OWNER=1
      ;;
    *)
      print -u2 "Unknown option: $1"
      print -u2 "Usage: ./scripts/lumon_preflight_port.sh [--port <port>] [--kill]"
      exit 2
      ;;
  esac
  shift

done

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  PIDS=$(lsof -t -nP -iTCP:"$PORT" -sTCP:LISTEN | tr '\n' ' ' | sed 's/[[:space:]]*$//')
  print -u2 "Port $PORT is in use by PID(s): ${PIDS}."
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN | sed -n '2,6p' >&2

  if [[ "$KILL_OWNER" == "1" ]]; then
    print -u2 "Attempting to terminate PID(s): ${PIDS}"
    kill $PIDS
    sleep 1
    if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      print -u2 "Port $PORT is still occupied after kill attempt."
      exit 1
    fi
    print -u2 "Port $PORT is now free."
    exit 0
  fi

  exit 10
fi

print -u2 "Port $PORT is free."
exit 0
