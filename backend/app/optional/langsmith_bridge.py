from __future__ import annotations

import os
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal

from app.protocol.normalizer import normalize_external_event
from app.utils.ids import new_id, utc_timestamp

ALLOWED_PROVIDERS = {"langchain", "langsmith"}
DEDUPLICATION_WINDOW_MS = 240.0
MAX_RECENT_FINGERPRINTS = 64
MAX_SUMMARY_LENGTH = 120


def optional_tracing_enabled() -> bool:
    return os.getenv("LUMON_OPTIONAL_TRACING", "0") == "1"


def _compact_text(value: Any, fallback: str) -> str:
    text = str(value or fallback)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        text = fallback
    if len(text) <= MAX_SUMMARY_LENGTH:
        return text
    return f"{text[:MAX_SUMMARY_LENGTH - 1].rstrip()}…"


@dataclass(frozen=True)
class NormalizedOptionalTrace:
    kind: Literal["agent_event", "background_worker_update"]
    payload: dict[str, Any]


class OptionalTraceBridgeMapper:
    def __init__(self) -> None:
        self._run_agent_ids: dict[str, str] = {}
        self._main_counter = 0
        self._subagent_counter = 0
        self._worker_counter = 0
        self._recent_fingerprints: deque[tuple[tuple[Any, ...], float]] = deque(maxlen=MAX_RECENT_FINGERPRINTS)

    def normalize_trace(
        self,
        trace: dict[str, Any],
        *,
        session_id: str,
        adapter_id: str,
        adapter_run_id: str,
        event_seq: int,
        allow_visual_coordinates: bool,
        now_ms: float | None = None,
    ) -> NormalizedOptionalTrace | None:
        provider = str(trace.get("provider") or "langchain").lower()
        if provider not in ALLOWED_PROVIDERS:
            raise ValueError(f"Unsupported optional trace provider: {provider}")

        run_id = str(trace.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("Optional trace run_id is required")

        meta = dict(trace.get("meta") or {})
        same_scene_visible = bool(meta.get("same_scene_visible")) or meta.get("visibility_mode") == "same_scene_visible"
        subagent = bool(trace.get("subagent"))
        trace_id = str(trace.get("trace_id") or new_id("trace"))
        event_type = str(trace.get("event_type") or "tool_start")
        state = str(trace.get("state") or "thinking")
        summary = _compact_text(trace.get("summary_text"), trace.get("name") or trace.get("label") or "Trace step")
        intent = _compact_text(trace.get("intent"), summary)
        agent_id = self._agent_id_for_trace(
            run_id,
            explicit_agent_id=trace.get("agent_id"),
            subagent=subagent,
            same_scene_visible=same_scene_visible,
        )
        parent_agent_id = self._parent_agent_id(trace.get("parent_agent_id"), trace.get("parent_run_id"))

        if subagent and not same_scene_visible:
            payload = {
                "session_id": session_id,
                "adapter_id": adapter_id,
                "adapter_run_id": adapter_run_id,
                "agent_id": agent_id,
                "summary_text": summary,
                "state": state,
                "timestamp": trace.get("timestamp") or utc_timestamp(),
            }
            if self._is_duplicate(
                ("background_worker_update", agent_id, state, summary, intent, trace_id),
                now_ms=now_ms,
            ):
                return None
            return NormalizedOptionalTrace(kind="background_worker_update", payload=payload)

        normalized = normalize_external_event(
            {
                "event_type": event_type,
                "state": state,
                "summary_text": summary,
                "intent": intent,
                "risk_level": trace.get("risk_level", "none"),
                "cursor": trace.get("cursor") if allow_visual_coordinates else None,
                "target_rect": trace.get("target_rect") if allow_visual_coordinates else None,
                "meta": {
                    **meta,
                    "optional_trace": True,
                    "provider": provider,
                    "trace_id": trace_id,
                    "run_id": run_id,
                    "parent_run_id": trace.get("parent_run_id"),
                },
                "subagent": subagent and same_scene_visible,
                "agent_id": agent_id,
                "parent_agent_id": parent_agent_id,
                "timestamp": trace.get("timestamp") or utc_timestamp(),
            },
            session_id=session_id,
            adapter_id=adapter_id,
            adapter_run_id=adapter_run_id,
            event_seq=event_seq,
        )

        if self._is_duplicate(
            (
                "agent_event",
                normalized["agent_id"],
                normalized["action_type"],
                normalized["state"],
                normalized["summary_text"],
                normalized["intent"],
                normalized.get("cursor", {}).get("x") if isinstance(normalized.get("cursor"), dict) else None,
                normalized.get("cursor", {}).get("y") if isinstance(normalized.get("cursor"), dict) else None,
                trace_id,
            ),
            now_ms=now_ms,
        ):
            return None

        return NormalizedOptionalTrace(kind="agent_event", payload=normalized)

    def _agent_id_for_trace(
        self,
        run_id: str,
        *,
        explicit_agent_id: Any,
        subagent: bool,
        same_scene_visible: bool,
    ) -> str:
        explicit = str(explicit_agent_id or "").strip()
        if explicit:
            self._run_agent_ids[run_id] = explicit
            return explicit
        if run_id in self._run_agent_ids:
            return self._run_agent_ids[run_id]

        if subagent and same_scene_visible:
            self._subagent_counter += 1
            agent_id = f"trace_subagent_{self._subagent_counter:03d}"
        elif subagent:
            self._worker_counter += 1
            agent_id = f"trace_worker_{self._worker_counter:03d}"
        else:
            self._main_counter += 1
            agent_id = f"trace_main_{self._main_counter:03d}"
        self._run_agent_ids[run_id] = agent_id
        return agent_id

    def _parent_agent_id(self, explicit_parent_agent_id: Any, parent_run_id: Any) -> str | None:
        explicit = str(explicit_parent_agent_id or "").strip()
        if explicit:
            return explicit
        parent_run = str(parent_run_id or "").strip()
        if not parent_run:
            return None
        return self._run_agent_ids.get(parent_run)

    def _is_duplicate(self, fingerprint: tuple[Any, ...], *, now_ms: float | None) -> bool:
        observed_at = now_ms if now_ms is not None else time.monotonic() * 1000
        while self._recent_fingerprints and observed_at - self._recent_fingerprints[0][1] > DEDUPLICATION_WINDOW_MS:
            self._recent_fingerprints.popleft()
        if any(existing == fingerprint for existing, _ in self._recent_fingerprints):
            return True
        self._recent_fingerprints.append((fingerprint, observed_at))
        return False


def normalize_optional_trace(
    trace: dict[str, Any],
    *,
    session_id: str,
    adapter_id: str,
    adapter_run_id: str,
    event_seq: int,
    allow_visual_coordinates: bool = True,
) -> dict[str, Any]:
    if not optional_tracing_enabled():
        raise RuntimeError("Optional tracing integration is disabled")

    normalized = OptionalTraceBridgeMapper().normalize_trace(
        trace,
        session_id=session_id,
        adapter_id=adapter_id,
        adapter_run_id=adapter_run_id,
        event_seq=event_seq,
        allow_visual_coordinates=allow_visual_coordinates,
    )
    if normalized is None:
        raise RuntimeError("Optional trace was deduplicated")
    return normalized.payload
