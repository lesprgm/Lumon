from __future__ import annotations

from typing import Any

from app.adapters.opencode import OpenCodeConnector
from app.adapters.playwright_native import PlaywrightNativeConnector


def create_connector(runtime: Any, adapter_id: str):
    if adapter_id == "opencode":
        return OpenCodeConnector(runtime)
    return PlaywrightNativeConnector(runtime)
