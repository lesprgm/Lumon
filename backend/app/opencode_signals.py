from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_BROWSER_TOKENS = (
    "browser",
    "webfetch",
    "open_url",
    "open-url",
    "navigate",
    "visit",
    "goto",
    "search",
    "site",
    "url",
    "http",
    "https",
    "playwright",
    "chrome",
)

_INTERVENTION_TOKENS = (
    "approval",
    "intervention",
    "takeover",
    "permission",
    "confirm",
    "sensitive",
    "blocked",
)


def task_mentions_browser(task_text: str) -> bool:
    lowered = task_text.lower()
    return any(token in lowered for token in _BROWSER_TOKENS)


def classify_signal_detailed(payload: Mapping[str, Any]) -> dict[str, Any]:
    meta = payload.get("meta")
    event_type = str(
        payload.get("event_type")
        or payload.get("type")
        or payload.get("event")
        or payload.get("kind")
        or payload.get("name")
        or ""
    ).lower()
    # Tier A: authoritative runtime signals
    if event_type in {"approval_required", "bridge_offer"}:
        return {"signal": "intervention", "tier": "A", "confidence": 1.0, "reason_code": "authoritative_intervention_event"}
    if isinstance(payload.get("intervention_id"), str) and payload.get("intervention_id"):
        return {"signal": "intervention", "tier": "A", "confidence": 1.0, "reason_code": "authoritative_intervention_id"}
    if isinstance(payload.get("checkpoint_id"), str) and payload.get("checkpoint_id"):
        return {"signal": "intervention", "tier": "A", "confidence": 0.95, "reason_code": "authoritative_checkpoint"}

    if isinstance(meta, Mapping):
        tool_name = str(meta.get("tool_name") or "").lower()
        if tool_name == "lumon_browser":
            return {"signal": "browser", "tier": "A", "confidence": 1.0, "reason_code": "authoritative_lumon_browser_tool"}
        if meta.get("tool_mode") == "commands":
            return {"signal": "browser", "tier": "A", "confidence": 0.95, "reason_code": "authoritative_command_mode"}

    # Tier B: structured metadata and event typing
    if isinstance(meta, Mapping):
        if meta.get("intervention_candidate") is True:
            return {"signal": "intervention", "tier": "B", "confidence": 0.9, "reason_code": "structured_intervention_candidate"}
        if meta.get("browser_candidate") is True:
            return {"signal": "browser", "tier": "B", "confidence": 0.9, "reason_code": "structured_browser_candidate"}

    if event_type:
        if any(token in event_type for token in _INTERVENTION_TOKENS):
            return {"signal": "intervention", "tier": "B", "confidence": 0.8, "reason_code": "typed_intervention_token"}
        if any(token in event_type for token in _BROWSER_TOKENS):
            return {"signal": "browser", "tier": "B", "confidence": 0.8, "reason_code": "typed_browser_token"}

    return {"signal": "none", "tier": "none", "confidence": 0.0, "reason_code": "no_signal"}
