from __future__ import annotations

import asyncio
import base64
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.session.artifacts as artifacts_module
from app.main import create_app
from app.protocol.models import BrowserCommandRecord, BrowserContextPayload, BrowserEvidence
from app.session.manager import SessionRuntime


@pytest.mark.asyncio
async def test_runtime_writes_artifact_with_browser_context_intervention_and_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(artifacts_module, "_output_root", lambda: tmp_path)

    runtime = SessionRuntime(session_id="sess_artifact_001", join_token="ws_artifact_001")
    messages: list[dict] = []
    runtime.broadcast = _capture_broadcast(messages)  # type: ignore[assignment]

    runtime._artifact.note_attach_requested(runtime.timestamp())
    runtime._artifact.note_attached(runtime.timestamp())
    runtime.note_duplicate_attach_prevented()
    runtime._artifact.note_ui_open_requested(runtime.timestamp())

    await runtime.emit_frame(
        {
            "session_id": runtime.session_id,
            "frame_seq": 1,
            "timestamp": runtime.timestamp(),
            "mime_type": "image/png",
            "data_base64": base64.b64encode(b"fake-png-bytes").decode("ascii"),
        }
    )
    await runtime.emit_browser_context_update(
        {
            "session_id": runtime.session_id,
            "adapter_id": runtime.adapter_id,
            "adapter_run_id": runtime.adapter_run_id or "run_pending",
            "timestamp": runtime.timestamp(),
            "url": "https://docs.example.com/reference",
            "domain": "docs.example.com",
            "title": "Reference",
            "environment_type": "docs",
        }
    )
    await runtime.emit_agent_event(
        {
            "event_seq": 1,
            "event_id": "evt_artifact_001",
            "source_event_id": "src_artifact_001",
            "timestamp": runtime.timestamp(),
            "session_id": runtime.session_id,
            "adapter_id": runtime.adapter_id,
            "adapter_run_id": runtime.adapter_run_id or "run_pending",
            "agent_id": "main_001",
            "parent_agent_id": None,
            "agent_kind": "main",
            "environment_id": "env_browser_main",
            "visibility_mode": "foreground",
            "action_type": "read",
            "state": "reading",
            "summary_text": "Looked through the docs page",
            "intent": "Inspect the docs page",
            "risk_level": "none",
            "subagent_source": None,
            "cursor": {"x": 180, "y": 220},
            "target_rect": {"x": 140, "y": 190, "width": 120, "height": 36},
            "target_summary": "Docs search result",
            "confidence": 0.93,
            "meta": {},
        }
    )
    await runtime.emit_approval_required(
        {
            "checkpoint_id": "chk_artifact_001",
            "event_id": "evt_checkpoint_001",
            "summary_text": "About to submit details",
            "intent": "Submit the request form",
            "risk_reason": "This sends personal information.",
            "source_url": "https://docs.example.com/reference",
            "target_summary": "Submit form",
        }
    )
    runtime.record_browser_command(
        BrowserCommandRecord(
            command_id="cmd_artifact_001",
            command="inspect",
            status="success",
            summary_text="Found 3 actionable elements on docs.example.com.",
            timestamp=runtime.timestamp(),
            source_url="https://docs.example.com/reference",
            domain="docs.example.com",
            page_version=1,
            evidence=BrowserEvidence(
                verified=True,
                final_url="https://docs.example.com/reference",
                title="Reference",
                domain="docs.example.com",
                page_version=1,
                frame_emitted=True,
            ),
            actionable_elements=[],
            meta={},
        )
    )
    async def fake_approve(checkpoint_id: str) -> bool:
        assert checkpoint_id == "chk_artifact_001"
        return True

    runtime._connector.approve = fake_approve  # type: ignore[assignment]
    await runtime.handle_client_message({"type": "ui_ready", "payload": {"ready": True}})
    await runtime.handle_client_message({"type": "approve", "payload": {"checkpoint_id": "chk_artifact_001"}})
    await runtime.complete_task("completed", "Finished reviewing the page")

    session_dir = tmp_path / "sessions" / runtime.session_id
    session_json = session_dir / "session.json"
    interventions_json = session_dir / "interventions.json"
    events_ndjson = session_dir / "events.ndjson"
    commands_ndjson = session_dir / "commands.ndjson"

    assert session_json.exists()
    assert interventions_json.exists()
    assert events_ndjson.exists()
    assert commands_ndjson.exists()

    artifact = json.loads(session_json.read_text(encoding="utf-8"))
    assert artifact["status"] == "completed"
    assert artifact["browser_context"]["domain"] == "docs.example.com"
    assert artifact["pages_visited"][0]["environment_type"] == "docs"
    assert artifact["interventions"][0]["resolution"] == "approved"
    assert artifact["browser_commands"][0]["command"] == "inspect"
    assert artifact["browser_commands"][0]["status"] == "success"
    assert artifact["metrics"]["attach_requested_at"] is not None
    assert artifact["metrics"]["attached_at"] is not None
    assert artifact["metrics"]["ui_open_requested_at"] is not None
    assert artifact["metrics"]["ui_ready_at"] is not None
    assert artifact["metrics"]["browser_episode_count"] == 1
    assert artifact["metrics"]["duplicate_attach_prevented"] == 1
    assert artifact["metrics"]["intervention_count"] == 1
    assert artifact["metrics"]["browser_command_count"] == 1
    assert artifact["metrics"]["verified_browser_action_count"] == 1
    assert artifact["metrics"]["artifact_written"] is True
    assert artifact["metrics"]["session_completed"] is True
    assert artifact["keyframes"]

    events = [json.loads(line) for line in events_ndjson.read_text(encoding="utf-8").splitlines() if line.strip()]
    commands = [json.loads(line) for line in commands_ndjson.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert {event["type"] for event in events} >= {"browser_context_update", "agent_event", "approval_required", "task_result"}
    assert commands[0]["command"] == "inspect"
    assert commands[0]["status"] == "success"

    rollup_path = tmp_path / "metrics" / "sessions.ndjson"
    assert rollup_path.exists()
    rollup_entries = [json.loads(line) for line in rollup_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rollup_entries[-1]["session_id"] == runtime.session_id


def test_session_artifact_routes_return_persisted_artifact_and_keyframe() -> None:
    app = create_app()
    session_id = "sess_route_artifact_001"
    output_root = Path(__file__).resolve().parents[2] / "output" / "sessions" / session_id
    keyframes_dir = output_root / "keyframes"
    try:
        keyframes_dir.mkdir(parents=True, exist_ok=True)
        (output_root / "session.json").write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "adapter_id": "opencode",
                    "adapter_run_id": "run_route_001",
                    "task_text": "Review route artifact",
                    "observer_mode": True,
                    "status": "completed",
                    "started_at": "2026-03-12T00:00:00Z",
                    "completed_at": "2026-03-12T00:00:05Z",
                    "summary_text": "Finished route artifact test",
                    "browser_context": None,
                    "pages_visited": [],
                    "interventions": [],
                    "browser_commands": [],
                    "keyframes": ["keyframes/001_completed.png"],
                    "metrics": {
                        "attach_requested_at": None,
                        "attached_at": None,
                        "first_browser_event_at": None,
                        "ui_open_requested_at": None,
                        "ui_ready_at": None,
                        "attach_latency_ms": None,
                        "ui_open_latency_ms": None,
                        "browser_episode_count": 0,
                        "intervention_count": 0,
                        "reconnect_count": 0,
                        "duplicate_attach_prevented": 0,
                        "browser_command_count": 0,
                        "verified_browser_action_count": 0,
                        "browser_blocked_count": 0,
                        "browser_partial_count": 0,
                        "stale_target_count": 0,
                        "session_completed": True,
                        "artifact_written": True,
                    },
                }
            ),
            encoding="utf-8",
        )
        (output_root / "events.ndjson").write_text(
            json.dumps({"type": "task_result", "payload": {"status": "completed"}}) + "\n",
            encoding="utf-8",
        )
        (output_root / "commands.ndjson").write_text(
            json.dumps(
                {
                    "command_id": "cmd_route_001",
                    "command": "status",
                    "status": "success",
                    "summary_text": "Browser ready",
                    "timestamp": "2026-03-12T00:00:01Z",
                    "actionable_elements": [],
                    "meta": {},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (keyframes_dir / "001_completed.png").write_bytes(b"png-bytes")

        with TestClient(app) as client:
            artifact_response = client.get(f"/api/session-artifacts/{session_id}")
            assert artifact_response.status_code == 200
            payload = artifact_response.json()
            assert payload["artifact"]["session_id"] == session_id
            assert payload["events"][0]["type"] == "task_result"
            assert payload["commands"][0]["command"] == "status"

            keyframe_response = client.get(f"/api/session-artifacts/{session_id}/keyframes/001_completed.png")
            assert keyframe_response.status_code == 200
            assert keyframe_response.content == b"png-bytes"
    finally:
        shutil.rmtree(output_root, ignore_errors=True)


def test_session_artifact_recorder_preserves_revisits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts_module, "_output_root", lambda: tmp_path)
    recorder = artifacts_module.SessionArtifactRecorder(
        session_id="sess_revisit_001",
        adapter_id="playwright_native",
        adapter_run_id="run_revisit_001",
        task_text="Revisit the same page twice",
        observer_mode=False,
        started_at="2026-03-18T00:00:00Z",
    )

    for timestamp, url in [
        ("2026-03-18T00:00:01Z", "https://example.com/a"),
        ("2026-03-18T00:00:02Z", "https://example.com/b"),
        ("2026-03-18T00:00:03Z", "https://example.com/a"),
    ]:
        recorder.record_browser_context(
            artifacts_module.BrowserContextPayload(
                session_id="sess_revisit_001",
                adapter_id="playwright_native",
                adapter_run_id="run_revisit_001",
                url=url,
                title=url.rsplit("/", 1)[-1].upper(),
                domain="example.com",
                environment_type="external",
                timestamp=timestamp,
            )
        )

    artifact = recorder.current_artifact(status="running")
    assert [page.url for page in artifact.pages_visited] == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/a",
    ]


@pytest.mark.asyncio
async def test_browser_episode_count_does_not_increment_for_quick_page_hops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(artifacts_module, "_output_root", lambda: tmp_path)
    runtime = SessionRuntime(session_id="sess_episode_001", join_token="ws_episode_001")

    await runtime.emit_browser_context_update(
        {
            "session_id": runtime.session_id,
            "adapter_id": runtime.adapter_id,
            "adapter_run_id": runtime.adapter_run_id or "run_pending",
            "timestamp": "2026-03-16T00:00:00Z",
            "url": "https://example.com/start",
            "domain": "example.com",
            "title": "Start",
            "environment_type": "external",
        }
    )
    await runtime.emit_browser_context_update(
        {
            "session_id": runtime.session_id,
            "adapter_id": runtime.adapter_id,
            "adapter_run_id": runtime.adapter_run_id or "run_pending",
            "timestamp": "2026-03-16T00:00:05Z",
            "url": "https://example.com/next",
            "domain": "example.com",
            "title": "Next",
            "environment_type": "external",
        }
    )

    assert runtime._artifact.metrics.browser_episode_count == 1


def test_read_commands_keeps_distinct_commands_that_share_a_command_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts_module, "_output_root", lambda: tmp_path)
    runtime = SessionRuntime(session_id="sess_cmd_dedupe_001", join_token="ws_cmd_dedupe_001")
    runtime.record_browser_command(
        BrowserCommandRecord(
            command_id="cmd_same",
            command="click",
            status="blocked",
            summary_text="Awaiting approval.",
            timestamp="2026-03-16T00:00:00Z",
            meta={},
        )
    )
    runtime.record_browser_command(
        BrowserCommandRecord(
            command_id="cmd_same",
            command="click",
            status="blocked",
            summary_text="You denied that browser step.",
            timestamp="2026-03-16T00:00:01Z",
            reason="denied",
            meta={},
        )
    )
    runtime.record_browser_command(
        BrowserCommandRecord(
            command_id="cmd_same",
            command="open",
            status="success",
            summary_text="Opened the page.",
            timestamp="2026-03-16T00:00:02Z",
            source_url="https://example.com",
            domain="example.com",
            page_version=1,
            meta={},
        )
    )

    commands = runtime.current_artifact()["commands"]
    assert len(commands) == 2
    assert [(command["command"], command["command_id"]) for command in commands] == [
        ("click", "cmd_same"),
        ("open", "cmd_same"),
    ]
    assert commands[0]["reason"] == "denied"


def test_session_artifact_route_keeps_distinct_commands_that_share_a_command_id() -> None:
    app = create_app()
    session_id = "sess_route_cmd_dedupe_001"
    output_root = Path(__file__).resolve().parents[2] / "output" / "sessions" / session_id
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / "session.json").write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "adapter_id": "opencode",
                    "adapter_run_id": "run_route_002",
                    "task_text": "Review route artifact",
                    "observer_mode": True,
                    "status": "completed",
                    "started_at": "2026-03-12T00:00:00Z",
                    "completed_at": "2026-03-12T00:00:05Z",
                    "summary_text": "Finished route artifact test",
                    "browser_context": None,
                    "pages_visited": [],
                    "interventions": [],
                    "keyframes": [],
                    "metrics": {
                        "attach_requested_at": None,
                        "attached_at": None,
                        "first_browser_event_at": None,
                        "ui_open_requested_at": None,
                        "ui_ready_at": None,
                        "attach_latency_ms": None,
                        "ui_open_latency_ms": None,
                        "browser_episode_count": 0,
                        "intervention_count": 0,
                        "reconnect_count": 0,
                        "duplicate_attach_prevented": 0,
                        "browser_command_count": 0,
                        "verified_browser_action_count": 0,
                        "browser_blocked_count": 0,
                        "browser_partial_count": 0,
                        "stale_target_count": 0,
                        "session_completed": True,
                        "artifact_written": True,
                    },
                }
            ),
            encoding="utf-8",
        )
        (output_root / "commands.ndjson").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "command_id": "cmd_same",
                            "command": "begin_task",
                            "status": "partial",
                            "summary_text": "Prepared task.",
                            "timestamp": "2026-03-12T00:00:01Z",
                            "meta": {},
                        }
                    ),
                    json.dumps(
                        {
                            "command_id": "cmd_same",
                            "command": "open",
                            "status": "success",
                            "summary_text": "Opened page.",
                            "timestamp": "2026-03-12T00:00:02Z",
                            "meta": {},
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        with TestClient(app) as client:
            artifact_response = client.get(f"/api/session-artifacts/{session_id}")
            assert artifact_response.status_code == 200
            commands = artifact_response.json()["commands"]
            assert [(command["command"], command["command_id"]) for command in commands] == [
                ("begin_task", "cmd_same"),
                ("open", "cmd_same"),
            ]
    finally:
        shutil.rmtree(output_root, ignore_errors=True)


def test_record_browser_context_attaches_keyframe_to_page_visit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts_module, "_output_root", lambda: tmp_path)
    runtime = SessionRuntime(session_id="sess_page_keyframe_001", join_token="ws_page_keyframe_001")
    runtime._artifact.record_frame(
        "image/png",
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2p0i8AAAAASUVORK5CYII=",
    )

    runtime._artifact.record_browser_context(
        BrowserContextPayload(
            session_id="sess_page_keyframe_001",
            adapter_id="playwright_native",
            adapter_run_id="run_page_keyframe_001",
            url="https://example.com/docs",
            domain="example.com",
            title="Docs",
            environment_type="docs",
            timestamp="2026-03-18T00:00:01Z",
        ),
        capture_keyframe=True,
    )

    artifact = runtime.current_artifact()["artifact"]
    page = artifact["pages_visited"][0]
    assert page["keyframe_path"] == "keyframes/001_browser_context.png"


def _capture_broadcast(messages: list[dict]) -> callable:
    async def _broadcast(message: dict) -> None:
        messages.append(message)

    return _broadcast
