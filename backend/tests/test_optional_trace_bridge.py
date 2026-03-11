from __future__ import annotations

import pytest

from app.optional.langsmith_bridge import OptionalTraceBridgeMapper, normalize_optional_trace


def test_normalize_optional_trace_rejects_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LUMON_OPTIONAL_TRACING", raising=False)

    with pytest.raises(RuntimeError, match="disabled"):
        normalize_optional_trace(
            {
                "provider": "langchain",
                "run_id": "run_trace_001",
                "event_type": "tool_start",
                "summary_text": "Read docs",
            },
            session_id="sess_demo_001",
            adapter_id="opencode",
            adapter_run_id="run_demo_001",
            event_seq=1,
        )


def test_mapper_assigns_stable_agent_ids_for_repeated_run_ids() -> None:
    mapper = OptionalTraceBridgeMapper()

    first = mapper.normalize_trace(
        {
            "provider": "langchain",
            "trace_id": "trace_001",
            "run_id": "run_alpha",
            "event_type": "tool_start",
            "summary_text": "Inspecting docs",
        },
        session_id="sess_demo_001",
        adapter_id="opencode",
        adapter_run_id="run_demo_001",
        event_seq=1,
        allow_visual_coordinates=True,
        now_ms=0,
    )
    second = mapper.normalize_trace(
        {
            "provider": "langchain",
            "trace_id": "trace_002",
            "run_id": "run_alpha",
            "event_type": "tool_complete",
            "summary_text": "Docs inspected",
        },
        session_id="sess_demo_001",
        adapter_id="opencode",
        adapter_run_id="run_demo_001",
        event_seq=2,
        allow_visual_coordinates=True,
        now_ms=400,
    )

    assert first is not None
    assert second is not None
    assert first.kind == "agent_event"
    assert second.kind == "agent_event"
    assert first.payload["agent_id"] == second.payload["agent_id"]


def test_mapper_returns_background_worker_update_for_hidden_subagents() -> None:
    mapper = OptionalTraceBridgeMapper()

    normalized = mapper.normalize_trace(
        {
            "provider": "langsmith",
            "trace_id": "trace_003",
            "run_id": "run_worker",
            "parent_run_id": "run_alpha",
            "event_type": "subagent",
            "state": "thinking",
            "summary_text": "Background comparer running",
            "subagent": True,
            "meta": {},
        },
        session_id="sess_demo_001",
        adapter_id="opencode",
        adapter_run_id="run_demo_001",
        event_seq=3,
        allow_visual_coordinates=True,
        now_ms=0,
    )

    assert normalized is not None
    assert normalized.kind == "background_worker_update"
    assert normalized.payload["agent_id"].startswith("trace_worker_")


def test_mapper_returns_same_scene_subagent_when_explicitly_visible() -> None:
    mapper = OptionalTraceBridgeMapper()
    parent = mapper.normalize_trace(
        {
            "provider": "langchain",
            "trace_id": "trace_parent",
            "run_id": "run_parent",
            "event_type": "tool_start",
            "summary_text": "Parent run",
        },
        session_id="sess_demo_001",
        adapter_id="opencode",
        adapter_run_id="run_demo_001",
        event_seq=1,
        allow_visual_coordinates=True,
        now_ms=0,
    )

    normalized = mapper.normalize_trace(
        {
            "provider": "langchain",
            "trace_id": "trace_sub",
            "run_id": "run_visible_sub",
            "parent_run_id": "run_parent",
            "event_type": "subagent",
            "state": "thinking",
            "summary_text": "Visible helper",
            "subagent": True,
            "meta": {"same_scene_visible": True},
        },
        session_id="sess_demo_001",
        adapter_id="opencode",
        adapter_run_id="run_demo_001",
        event_seq=2,
        allow_visual_coordinates=True,
        now_ms=500,
    )

    assert parent is not None
    assert normalized is not None
    assert normalized.kind == "agent_event"
    assert normalized.payload["agent_kind"] == "same_scene_subagent"
    assert normalized.payload["parent_agent_id"] == parent.payload["agent_id"]


def test_mapper_strips_coordinates_when_visual_source_is_authoritative() -> None:
    mapper = OptionalTraceBridgeMapper()

    normalized = mapper.normalize_trace(
        {
            "provider": "langchain",
            "trace_id": "trace_004",
            "run_id": "run_visual",
            "event_type": "click",
            "state": "clicking",
            "summary_text": "Clicking CTA",
            "cursor": {"x": 320, "y": 240},
            "target_rect": {"x": 300, "y": 220, "width": 40, "height": 20},
        },
        session_id="sess_demo_001",
        adapter_id="playwright_native",
        adapter_run_id="run_demo_001",
        event_seq=4,
        allow_visual_coordinates=False,
        now_ms=0,
    )

    assert normalized is not None
    assert normalized.kind == "agent_event"
    assert normalized.payload["cursor"] is None
    assert normalized.payload["target_rect"] is None


def test_mapper_deduplicates_identical_bursts() -> None:
    mapper = OptionalTraceBridgeMapper()
    first = mapper.normalize_trace(
        {
            "provider": "langchain",
            "trace_id": "trace_dup",
            "run_id": "run_dup",
            "event_type": "read",
            "state": "reading",
            "summary_text": "Reading docs",
        },
        session_id="sess_demo_001",
        adapter_id="opencode",
        adapter_run_id="run_demo_001",
        event_seq=1,
        allow_visual_coordinates=True,
        now_ms=1000,
    )
    second = mapper.normalize_trace(
        {
            "provider": "langchain",
            "trace_id": "trace_dup",
            "run_id": "run_dup",
            "event_type": "read",
            "state": "reading",
            "summary_text": "Reading docs",
        },
        session_id="sess_demo_001",
        adapter_id="opencode",
        adapter_run_id="run_demo_001",
        event_seq=2,
        allow_visual_coordinates=True,
        now_ms=1100,
    )

    assert first is not None
    assert second is None


def test_mapper_rejects_unknown_provider() -> None:
    mapper = OptionalTraceBridgeMapper()

    with pytest.raises(ValueError, match="Unsupported"):
        mapper.normalize_trace(
            {
                "provider": "other",
                "run_id": "run_invalid",
                "event_type": "tool_start",
            },
            session_id="sess_demo_001",
            adapter_id="opencode",
            adapter_run_id="run_demo_001",
            event_seq=1,
            allow_visual_coordinates=True,
            now_ms=0,
        )


def test_mapper_handles_orphan_parent_run_id_without_crashing() -> None:
    mapper = OptionalTraceBridgeMapper()

    normalized = mapper.normalize_trace(
        {
            "provider": "langchain",
            "trace_id": "trace_orphan",
            "run_id": "run_child",
            "parent_run_id": "missing_parent",
            "event_type": "subagent",
            "summary_text": "Visible helper",
            "subagent": True,
            "meta": {"same_scene_visible": True},
        },
        session_id="sess_demo_001",
        adapter_id="opencode",
        adapter_run_id="run_demo_001",
        event_seq=1,
        allow_visual_coordinates=True,
        now_ms=0,
    )

    assert normalized is not None
    assert normalized.kind == "agent_event"
    assert normalized.payload["parent_agent_id"] is None


def test_mapper_falls_back_for_missing_summary_and_unknown_state() -> None:
    mapper = OptionalTraceBridgeMapper()

    normalized = mapper.normalize_trace(
        {
            "provider": "langsmith",
            "trace_id": "trace_fallback",
            "run_id": "run_fallback",
            "event_type": "unexpected_event",
            "state": "mystery_state",
            "name": "Trace step",
        },
        session_id="sess_demo_001",
        adapter_id="opencode",
        adapter_run_id="run_demo_001",
        event_seq=1,
        allow_visual_coordinates=True,
        now_ms=0,
    )

    assert normalized is not None
    assert normalized.kind == "agent_event"
    assert normalized.payload["summary_text"] == "Trace step"
    assert normalized.payload["state"] == "thinking"
    assert normalized.payload["action_type"] == "read"
