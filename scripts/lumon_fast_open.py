from __future__ import annotations

import os
import sys
from pathlib import Path

FAST_OPEN_ENV = {
    "LUMON_PLUGIN_BROWSER_EPISODE_GAP_MS": "5000",
    "LUMON_PLUGIN_INTERVENTION_EPISODE_GAP_MS": "2000",
    "LUMON_PLUGIN_REOPEN_COOLDOWN_MS": "3000",
}


def main() -> int:
    for key, value in FAST_OPEN_ENV.items():
        os.environ[key] = value

    script_path = Path(__file__).resolve().parent / "lumon_opencode.py"
    args = [sys.executable, str(script_path), *sys.argv[1:]]
    os.execvpe(sys.executable, args, os.environ)


if __name__ == "__main__":
    raise SystemExit(main())
