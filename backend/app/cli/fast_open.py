from __future__ import annotations

import os
import sys

from .main import main as cli_main

FAST_OPEN_ENV = {
    "LUMON_PLUGIN_BROWSER_EPISODE_GAP_MS": "5000",
    "LUMON_PLUGIN_INTERVENTION_EPISODE_GAP_MS": "2000",
    "LUMON_PLUGIN_REOPEN_COOLDOWN_MS": "3000",
}


def run() -> int:
    for key, value in FAST_OPEN_ENV.items():
        os.environ[key] = value
    return cli_main(["opencode", *sys.argv[1:]])
