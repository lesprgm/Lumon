from __future__ import annotations

from app.adapters.opencode import OpenCodeConnector
from app.adapters.playwright_native import PlaywrightNativeConnector
from app.config import DEFAULT_ADAPTER_ID


def create_connector(runtime: "SessionRuntimeProtocol", adapter_id: str):
    if adapter_id == "opencode":
        return OpenCodeConnector(runtime)
    return PlaywrightNativeConnector(runtime)


def default_adapter_id() -> str:
    return DEFAULT_ADAPTER_ID
