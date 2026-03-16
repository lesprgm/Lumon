#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/../frontend"
export VITE_LUMON_REPLAY=true
exec npm run dev
