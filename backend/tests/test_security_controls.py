from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketState

from app.config import clear_settings_cache
from app.main import create_app
from app.protocol.enums import SessionState
from app.session.manager import SessionManager


class FakeWebSocket:
    def __init__(self, *, session_id: str, token: str, origin: str) -> None:
        self.query_params = {"session_id": session_id, "token": token}
        self.headers = {"origin": origin}
        self.application_state = WebSocketState.CONNECTED
        self.accepted = False
        self.closed: tuple[int, str] | None = None
        self.sent: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)


@pytest.fixture(autouse=True)
def reset_settings_cache() -> Iterator[None]:
    clear_settings_cache()
    yield
    clear_settings_cache()


def test_create_app_disables_docs_and_uses_explicit_cors() -> None:
    app = create_app()

    route_paths = {route.path for route in app.routes}
    assert "/docs" not in route_paths
    assert "/openapi.json" not in route_paths

    cors = next(
        middleware
        for middleware in app.user_middleware
        if middleware.cls is CORSMiddleware
    )
    assert cors.kwargs["allow_origins"]
    assert cors.kwargs["allow_origins"] != ["*"]
    assert cors.kwargs["allow_credentials"] is False


def test_bootstrap_requires_allowed_origin_and_returns_no_store() -> None:
    app = create_app()
    with TestClient(app) as client:
        rejected = client.get(
            "/api/bootstrap", headers={"Origin": "http://evil.example"}
        )
        assert rejected.status_code == 403

        accepted = client.get(
            "/api/bootstrap", headers={"Origin": "http://127.0.0.1:8000"}
        )
        assert accepted.status_code == 200
        payload = accepted.json()
        assert payload["session_id"].startswith("sess_")
        assert payload["ws_token"].startswith("ws_")
        assert accepted.headers["cache-control"] == "no-store"

        local_without_origin = client.get("/api/bootstrap")
        assert local_without_origin.status_code == 200


def test_local_harness_pages_are_local_only() -> None:
    app = create_app()
    with TestClient(app) as client:
        search = client.get("/__lumon_harness__/search")
        approval = client.get("/__lumon_harness__/approval")

    assert search.status_code == 200
    assert "Search Wikipedia" in search.text
    assert approval.status_code == 200
    assert "Submit order" in approval.text


def test_backend_serves_built_frontend_shell() -> None:
    app = create_app()
    with TestClient(app) as client:
        root = client.get("/")
        ready = client.get("/__lumon_frontend_ready__")

    assert root.status_code == 200
    assert 'id="root"' in root.text or "id='root'" in root.text
    assert ready.status_code == 200
    assert ready.json()["frontend"] == "static"


def test_backend_rejects_stale_frontend_shell_at_root_and_catchall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.main as main_module

    monkeypatch.setattr(main_module, "RUNTIME_VERSION", "stale-runtime-for-test")
    app = create_app()
    with TestClient(app) as client:
        root = client.get("/")
        catchall = client.get("/landing")

    assert root.status_code == 503
    assert "frontend build is stale" in root.json()["detail"]
    assert catchall.status_code == 503
    assert "frontend build is stale" in catchall.json()["detail"]


def test_local_approval_endpoints_are_local_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.session.manager as session_manager

    async def fake_resolve(
        self, session_id: str, checkpoint_id: str, *, approve: bool
    ) -> dict:
        return {
            "artifact": {
                "session_id": session_id,
                "interventions": [
                    {
                        "checkpoint_id": checkpoint_id,
                        "resolution": "approved" if approve else "denied",
                    }
                ],
            },
            "events": [],
            "commands": [],
        }

    monkeypatch.setattr(
        session_manager.SessionManager, "resolve_local_checkpoint", fake_resolve
    )
    app = create_app()

    with TestClient(app) as client:
        approve = client.post(
            "/api/local/session/sess_test/approve", json={"checkpoint_id": "chk_1"}
        )
        reject = client.post(
            "/api/local/session/sess_test/reject", json={"checkpoint_id": "chk_1"}
        )

    assert approve.status_code == 200
    assert approve.json()["artifact"]["interventions"][0]["resolution"] == "approved"
    assert reject.status_code == 200
    assert reject.json()["artifact"]["interventions"][0]["resolution"] == "denied"


def test_local_consume_resume_intent_endpoint_is_local_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.session.manager as session_manager

    def fake_read(
        self, session_id: str, *, after_seq: int = 0, consume: bool = False
    ) -> dict:
        return {
            "session_id": session_id,
            "pending": not consume,
            "resume_intent_seq": after_seq + 1,
            "reason": "takeover_returned_control",
        }

    monkeypatch.setattr(
        session_manager.SessionManager,
        "read_local_resume_intent",
        fake_read,
    )
    app = create_app()

    with TestClient(app) as client:
        response = client.post(
            "/api/local/session/sess_test/consume-resume-intent",
            json={"after_seq": 2},
        )
        consumed = client.post(
            "/api/local/session/sess_test/consume-resume-intent",
            json={"after_seq": 2, "consume": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["pending"] is True
    assert payload["resume_intent_seq"] == 3
    assert consumed.status_code == 200
    consumed_payload = consumed.json()
    assert consumed_payload["pending"] is False


def test_local_ack_resume_intent_endpoint_is_local_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.session.manager as session_manager

    def fake_ack(self, session_id: str, *, resume_intent_seq: int) -> dict:
        return {
            "session_id": session_id,
            "resume_intent_seq": resume_intent_seq,
            "consumed_seq": resume_intent_seq,
            "acknowledged": True,
        }

    monkeypatch.setattr(
        session_manager.SessionManager,
        "acknowledge_local_resume_intent",
        fake_ack,
    )
    app = create_app()

    with TestClient(app) as client:
        response = client.post(
            "/api/local/session/sess_test/ack-resume-intent",
            json={"resume_intent_seq": 7},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["acknowledged"] is True
    assert payload["consumed_seq"] == 7


def test_local_observe_endpoint_is_local_only_and_reuses_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.session.manager as session_manager

    class FakeConnector:
        adapter_id = "opencode"
        capabilities = {
            "supports_pause": False,
            "supports_approval": False,
            "supports_takeover": False,
            "supports_frames": False,
        }

        def __init__(self, runtime) -> None:
            self.runtime = runtime
            self.adapter_run_id = "run_local_api_001"
            self.observed_session_id = None

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            auto_delegate: bool = False,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (
                task_text,
                demo_mode,
                web_mode,
                web_bridge,
                auto_delegate,
                observer_mode,
                bridge_context,
            )
            self.observed_session_id = observed_session_id
            self.runtime.adapter_run_id = self.adapter_run_id
            self.runtime.state = SessionState.RUNNING

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def accept_bridge(self) -> None: ...
        async def decline_bridge(self) -> None: ...
        async def start_takeover(self, mode_preference: str | None = None) -> None:
            _ = mode_preference
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(
        session_manager,
        "create_connector",
        lambda runtime, adapter_id: FakeConnector(runtime),
    )
    app = create_app()
    with TestClient(app) as client:
        first = client.post(
            "/api/local/observe/opencode",
            json={
                "project_directory": "/Users/leslie/Documents/Lumon",
                "observed_session_id": "ses_local_api_001",
                "web_mode": "observe_only",
                "auto_delegate": False,
            },
        )
        assert first.status_code == 200
        second = client.post(
            "/api/local/observe/opencode",
            json={
                "project_directory": "/Users/leslie/Documents/Lumon",
                "observed_session_id": "ses_local_api_001",
                "web_mode": "observe_only",
                "auto_delegate": False,
            },
        )
        assert second.status_code == 200
        assert second.json()["already_attached"] is True
        assert first.json()["session_id"] == second.json()["session_id"]


def test_local_browser_command_endpoint_is_local_only_and_returns_verified_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.session.manager as session_manager

    class FakeConnector:
        adapter_id = "opencode"
        capabilities = {
            "supports_pause": False,
            "supports_approval": True,
            "supports_takeover": True,
            "supports_frames": True,
        }

        def __init__(self, runtime) -> None:
            self.runtime = runtime
            self.adapter_run_id = "run_browser_api_001"
            self.observed_session_id = None
            self.ensure_delegate_calls = 0

        async def start_task(
            self,
            task_text: str,
            demo_mode: bool = True,
            web_mode: str | None = None,
            web_bridge: str | None = None,
            auto_delegate: bool = False,
            observer_mode: bool = False,
            observed_session_id: str | None = None,
            bridge_context: dict | None = None,
        ) -> None:
            _ = (
                task_text,
                demo_mode,
                web_mode,
                web_bridge,
                auto_delegate,
                observer_mode,
                bridge_context,
            )
            self.observed_session_id = observed_session_id
            self.runtime.adapter_run_id = self.adapter_run_id
            self.runtime.state = SessionState.RUNNING

        async def ensure_browser_delegate(
            self, *, observed_session_id: str, task_text: str
        ) -> None:
            _ = task_text
            self.ensure_delegate_calls += 1
            self.observed_session_id = observed_session_id
            self.runtime.adapter_run_id = self.adapter_run_id
            self.runtime.state = SessionState.RUNNING

        async def execute_browser_command(self, payload) -> dict:
            return {
                "command_id": payload.command_id,
                "command": payload.command,
                "status": "success",
                "summary_text": "Opened wikipedia.org.",
                "session_id": self.runtime.session_id,
                "source_url": "https://www.wikipedia.org/",
                "domain": "www.wikipedia.org",
                "page_version": 1,
                "evidence": {
                    "verified": True,
                    "final_url": "https://www.wikipedia.org/",
                    "title": "Wikipedia",
                    "domain": "www.wikipedia.org",
                    "page_version": 1,
                    "frame_emitted": True,
                    "details": {},
                },
                "actionable_elements": [],
                "meta": {},
            }

        async def pause(self) -> None: ...
        async def resume(self) -> None: ...
        async def approve(self, checkpoint_id: str) -> None: ...
        async def reject(self, checkpoint_id: str) -> None: ...
        async def accept_bridge(self) -> None: ...
        async def decline_bridge(self) -> None: ...
        async def start_takeover(self, mode_preference: str | None = None) -> None:
            _ = mode_preference
        async def end_takeover(self) -> None: ...
        async def stop(self) -> None: ...

    monkeypatch.setattr(
        session_manager,
        "create_connector",
        lambda runtime, adapter_id: FakeConnector(runtime),
    )
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/local/opencode/browser/command",
            json={
                "project_directory": "/Users/leslie/Documents/Lumon",
                "observed_session_id": "ses_browser_api_001",
                "command_id": "cmd_001",
                "command": "open",
                "url": "https://www.wikipedia.org/",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "success"
        assert payload["evidence"]["verified"] is True
        assert payload["open_url"].startswith("http://")


@pytest.mark.asyncio
async def test_manager_rejects_invalid_origin_and_invalid_token() -> None:
    manager = SessionManager(allowed_origins=("http://127.0.0.1:5173",))
    session = manager.create_session()

    invalid_origin = FakeWebSocket(
        session_id=session["session_id"],
        token=session["ws_token"],
        origin="http://evil.example",
    )
    invalid_token = FakeWebSocket(
        session_id=session["session_id"],
        token="ws_wrong",
        origin="http://127.0.0.1:5173",
    )

    await manager.connect(invalid_origin)
    await manager.connect(invalid_token)

    assert invalid_origin.accepted is True
    assert invalid_origin.closed == (1008, "WebSocket origin not allowed")
    assert invalid_token.accepted is True
    assert invalid_token.closed == (1008, "Invalid session credentials")


@pytest.mark.asyncio
async def test_session_events_are_isolated_per_runtime() -> None:
    manager = SessionManager(allowed_origins=("http://127.0.0.1:5173",))
    session_a = manager.create_session()
    session_b = manager.create_session()

    ws_a = FakeWebSocket(
        session_id=session_a["session_id"],
        token=session_a["ws_token"],
        origin="http://127.0.0.1:5173",
    )
    ws_b = FakeWebSocket(
        session_id=session_b["session_id"],
        token=session_b["ws_token"],
        origin="http://127.0.0.1:5173",
    )

    await manager.connect(ws_a)
    await manager.connect(ws_b)

    runtime_a = manager._sessions[session_a["session_id"]]
    runtime_b = manager._sessions[session_b["session_id"]]
    runtime_a.state = SessionState.RUNNING
    runtime_b.state = SessionState.RUNNING

    await runtime_a.emit_agent_event(
        {
            "event_seq": 1,
            "event_id": "evt_a",
            "source_event_id": "src_a",
            "timestamp": runtime_a.timestamp(),
            "session_id": runtime_a.session_id,
            "adapter_id": runtime_a.adapter_id,
            "adapter_run_id": "run_a",
            "agent_id": "main_001",
            "parent_agent_id": None,
            "agent_kind": "main",
            "environment_id": "env_browser_main",
            "visibility_mode": "foreground",
            "action_type": "read",
            "state": "thinking",
            "summary_text": "Read page A",
            "intent": "Inspect session A",
            "risk_level": "none",
            "subagent_source": None,
            "cursor": None,
            "target_rect": None,
            "meta": {},
        }
    )

    assert [message["type"] for message in ws_a.sent].count("agent_event") == 1
    assert [message["type"] for message in ws_b.sent].count("agent_event") == 0
