#!/bin/zsh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -f "$REPO_ROOT/output/runtime/lumon_backend.env" ]]; then
	set -a
	source "$REPO_ROOT/output/runtime/lumon_backend.env"
	set +a
fi

cd "$REPO_ROOT/frontend"
export VITE_LUMON_REPLAY=false
export CI=1
exec </dev/null

runtime_mode="${LUMON_FRONTEND_RUNTIME_MODE:-preview}"

needs_build=0
if [[ "$runtime_mode" == "preview" ]]; then
	if [[ ! -f dist/index.html ]]; then
		needs_build=1
	else
		latest_source_mtime="$(
			{
				find src public -type f -print0 2>/dev/null
				find . -maxdepth 1 \( -name 'index.html' -o -name 'package.json' -o -name 'vite.config.ts' -o -name 'tsconfig*.json' \) -type f -print0 2>/dev/null
			} \
				| xargs -0 stat -f '%m' 2>/dev/null \
				| sort -nr \
				| head -n 1
		)"
		dist_mtime="$(stat -f '%m' dist/index.html 2>/dev/null || echo 0)"
		if [[ -n "$latest_source_mtime" && "$latest_source_mtime" -gt "$dist_mtime" ]]; then
			needs_build=1
		fi
	fi
	if [[ "$needs_build" -eq 1 ]]; then
		npm run build
	fi
	exec npm run preview -- --host 127.0.0.1 --port 5173 --strictPort
fi

exec npm run dev -- --host 127.0.0.1 --port 5173 --strictPort --clearScreen false
