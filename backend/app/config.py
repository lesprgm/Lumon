from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel


PROTOCOL_VERSION = "1.3.1"
_RUNTIME_CONTRACT_PATH = Path(__file__).resolve().parents[2] / "lumon_runtime_contract.json"
_DEFAULT_RUNTIME_VERSION = "2026-03-22-reliability-v1"
_DEFAULT_BACKEND_RUNTIME_FEATURES = {
    "ui_telemetry": True,
    "ui_ready_handshake": True,
    "live_artifact_persistence": True,
}
_DEFAULT_FRONTEND_RUNTIME_FEATURES = {
    "ui_telemetry": True,
    "ui_ready_handshake": True,
}


def _load_runtime_contract() -> dict[str, object]:
    try:
        return json.loads(_RUNTIME_CONTRACT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


_RUNTIME_CONTRACT = _load_runtime_contract()
RUNTIME_VERSION = str(_RUNTIME_CONTRACT.get("runtime_version") or _DEFAULT_RUNTIME_VERSION)
BACKEND_RUNTIME_FEATURES = {
    key: bool(value)
    for key, value in (
        _RUNTIME_CONTRACT.get("backend_runtime_features")
        or _DEFAULT_BACKEND_RUNTIME_FEATURES
    ).items()
}
FRONTEND_RUNTIME_FEATURES = {
    key: bool(value)
    for key, value in (
        _RUNTIME_CONTRACT.get("frontend_features")
        or _DEFAULT_FRONTEND_RUNTIME_FEATURES
    ).items()
}
DEFAULT_ADAPTER_ID = "playwright_native"
VIEWPORT_WIDTH = 1920
VIEWPORT_HEIGHT = 1080
TYPE_FALLBACK_TARGET_WIDTH = 220
TYPE_FALLBACK_TARGET_HEIGHT = 40
DEFAULT_ALLOWED_ORIGINS = (
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
    "http://127.0.0.1:4174",
    "http://localhost:4174",
)


class Settings(BaseModel):
    allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS
    enable_docs: bool = False


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
        allowed_origins=_parse_csv(
            os.getenv("LUMON_ALLOWED_ORIGINS"), default=DEFAULT_ALLOWED_ORIGINS
        ),
        enable_docs=_parse_bool(os.getenv("LUMON_ENABLE_DOCS"), default=False),
    )


def clear_settings_cache() -> None:
    get_settings.cache_clear()
