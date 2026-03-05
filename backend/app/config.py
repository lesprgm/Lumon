from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel


PROTOCOL_VERSION = "1.3.1"
RUNTIME_VERSION = "2026-03-16-browser-flow-v3"
DEFAULT_ADAPTER_ID = "playwright_native"
SUPPORTED_ADAPTER_IDS = ("playwright_native", "opencode")
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 800
DEFAULT_ALLOWED_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
    "http://127.0.0.1:4174",
    "http://localhost:4174",
)


class ViewportConfig(BaseModel):
    width: int = VIEWPORT_WIDTH
    height: int = VIEWPORT_HEIGHT


class Settings(BaseModel):
    allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS
    enable_docs: bool = False
    recording_enabled: bool = False


def _parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv(value: str | None, *, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    return items or default


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        allowed_origins=_parse_csv(os.getenv("LUMON_ALLOWED_ORIGINS"), default=DEFAULT_ALLOWED_ORIGINS),
        enable_docs=_parse_bool(os.getenv("LUMON_ENABLE_DOCS"), default=False),
        recording_enabled=_parse_bool(os.getenv("LUMON_RECORDING_ENABLED"), default=False),
    )


def clear_settings_cache() -> None:
    get_settings.cache_clear()
