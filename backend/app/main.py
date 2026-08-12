from __future__ import annotations

import json
import logging
import os
import traceback
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app.adapters.browser_pool import init_browser_pool, shutdown_browser_pool
from app.config import (
    BACKEND_RUNTIME_FEATURES,
    FRONTEND_RUNTIME_FEATURES,
    PROTOCOL_VERSION,
    RUNTIME_VERSION,
    get_settings,
)
from app.protocol.models import (
    BrowserCommandRequest,
    BrowserCommandResult,
    CheckpointPayload,
    LocalObserveOpenCodeRequest,
    LocalObserveOpenCodeResponse,
    ResumeIntentAcknowledgeRequest,
    ResumeIntentRequest,
    UiTelemetryPayload,
)
from app.session.manager import SessionManager

_ERROR_LOG_PATH = Path(
    os.getenv("LUMON_ERROR_LOG", str(Path(__file__).resolve().parents[2] / "output" / "lumon_error.log"))
)
_ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

_logger = logging.getLogger("lumon.error")
_handler = logging.FileHandler(str(_ERROR_LOG_PATH), encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
_logger.addHandler(_handler)
_logger.setLevel(logging.ERROR)

DEFAULT_ARTIFACT_EVENT_LIMIT = 1200
DEFAULT_ARTIFACT_COMMAND_LIMIT = 400
MAX_ARTIFACT_LIMIT = 5000


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_browser_pool()
    try:
        yield
    finally:
        try:
            await app.state.session_manager.shutdown()
        finally:
            await shutdown_browser_pool()


def create_app() -> FastAPI:
    settings = get_settings()
    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    frontend_index = frontend_dist / "index.html"
    frontend_runtime_manifest = frontend_dist / "lumon-runtime.json"
    app = FastAPI(
        title="Lumon Backend",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    manager = SessionManager(allowed_origins=settings.allowed_origins)
    app.state.session_manager = manager

    def _require_local_client(request: Request) -> None:
        client_host = request.client.host if request.client is not None else None
        if client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
            raise HTTPException(status_code=403, detail="Local access only")

    def _frontend_ready_response() -> FileResponse | JSONResponse:
        stale_reason = _frontend_staleness_reason()
        if frontend_index.exists():
            if stale_reason is not None:
                return JSONResponse(status_code=503, content={"detail": stale_reason})
            return FileResponse(frontend_index)
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Lumon frontend build is missing. Run `./lumon setup` to build the shipped frontend bundle."
            },
        )

    def _request_origin(request: Request) -> str:
        return str(request.base_url).rstrip("/")

    def _read_frontend_runtime_manifest() -> dict[str, Any]:
        if not frontend_runtime_manifest.exists():
            return {}
        try:
            return json.loads(frontend_runtime_manifest.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _frontend_staleness_reason() -> str | None:
        if not frontend_index.exists():
            return "Lumon frontend build is missing. Run `./lumon setup`."
        frontend_manifest = _read_frontend_runtime_manifest()
        frontend_runtime_version = frontend_manifest.get("runtime_version")
        frontend_features = frontend_manifest.get("features") or {}
        if frontend_runtime_version != RUNTIME_VERSION:
            return "Lumon frontend build is stale. Run `./lumon restart` so the served UI matches the backend runtime."
        missing_frontend_features = [
            key
            for key, expected in FRONTEND_RUNTIME_FEATURES.items()
            if expected is True and frontend_features.get(key) is not True
        ]
        if missing_frontend_features:
            return (
                "Lumon frontend build is missing required runtime features. "
                "Run `./lumon restart` so the served UI matches the backend runtime."
            )
        return None

    def _resolve_limit(raw_value: str | None, *, default: int) -> int:
        if raw_value is None or raw_value == "":
            return default
        try:
            parsed = int(raw_value)
        except ValueError:
            return default
        if parsed <= 0:
            return 0
        return min(parsed, MAX_ARTIFACT_LIMIT)

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        if request.url.path == "/api/bootstrap":
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, HTTPException):
            raise exc
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        _logger.error("Unhandled exception on %s %s\n%s", request.method, request.url.path, tb)
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        frontend_manifest = _read_frontend_runtime_manifest()
        return {
            "status": "ok",
            "protocol_version": PROTOCOL_VERSION,
            "runtime_version": RUNTIME_VERSION,
            "runtime_features": BACKEND_RUNTIME_FEATURES,
            "frontend_runtime_version": frontend_manifest.get("runtime_version"),
            "frontend_features": frontend_manifest.get("features") or {},
        }

    @app.get("/api/bootstrap")
    async def bootstrap_session(request: Request) -> dict[str, str]:
        origin = request.headers.get("origin")
        if origin:
            if origin not in settings.allowed_origins:
                raise HTTPException(status_code=403, detail="Origin not allowed")
        else:
            _require_local_client(request)
        session = manager.create_session()
        return {
            "session_id": session["session_id"],
            "ws_token": session["ws_token"],
            "ws_path": "/ws/session",
            "protocol_version": PROTOCOL_VERSION,
        }

    @app.get("/__lumon_harness__/search", response_class=HTMLResponse)
    async def harness_search_page(request: Request) -> HTMLResponse:
        _require_local_client(request)
        return HTMLResponse(
            """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Lumon Harness Search</title>
  </head>
  <body>
    <main>
      <label for="search">Search Wikipedia</label>
      <input id="search" aria-label="Search Wikipedia" placeholder="Search Wikipedia" />
      <button id="submit">Submit</button>
    </main>
  </body>
</html>
""".strip()
        )

    @app.get("/__lumon_harness__/approval", response_class=HTMLResponse)
    async def harness_approval_page(request: Request) -> HTMLResponse:
        _require_local_client(request)
        return HTMLResponse(
            """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Lumon Harness Approval</title>
  </head>
  <body>
    <main>
      <button id="submit-order" aria-label="Submit order">Submit order</button>
    </main>
  </body>
</html>
""".strip()
        )

    @app.post("/api/local/observe/opencode")
    async def local_observe_opencode(
        payload: LocalObserveOpenCodeRequest,
        request: Request,
    ) -> LocalObserveOpenCodeResponse:
        _require_local_client(request)
        attached = await manager.attach_local_opencode_observer(
            payload,
            frontend_origin=payload.frontend_origin or _request_origin(request),
        )
        return LocalObserveOpenCodeResponse.model_validate(attached)

    @app.post("/api/local/opencode/browser/command")
    async def local_opencode_browser_command(
        payload: BrowserCommandRequest,
        request: Request,
    ) -> BrowserCommandResult:
        _require_local_client(request)
        result = await manager.execute_local_opencode_browser_command(
            payload,
            frontend_origin=payload.frontend_origin or _request_origin(request),
        )
        return BrowserCommandResult.model_validate(result)

    @app.post("/api/local/session/{session_id}/approve")
    async def local_session_approve(
        session_id: str, payload: CheckpointPayload, request: Request
    ) -> dict:
        _require_local_client(request)
        try:
            return await manager.resolve_local_checkpoint(
                session_id, payload.checkpoint_id, approve=True
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc

    @app.post("/api/local/session/{session_id}/reject")
    async def local_session_reject(
        session_id: str, payload: CheckpointPayload, request: Request
    ) -> dict:
        _require_local_client(request)
        try:
            return await manager.resolve_local_checkpoint(
                session_id, payload.checkpoint_id, approve=False
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc

    @app.post("/api/local/session/{session_id}/ui-telemetry")
    async def local_session_ui_telemetry(
        session_id: str, payload: UiTelemetryPayload, request: Request
    ) -> dict:
        _require_local_client(request)
        try:
            return manager.record_local_ui_telemetry(session_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc

    @app.post("/api/local/session/{session_id}/consume-resume-intent")
    async def local_consume_resume_intent(
        session_id: str, payload: ResumeIntentRequest, request: Request
    ) -> dict:
        _require_local_client(request)
        try:
            return (
                manager.consume_local_resume_intent(
                    session_id,
                    after_seq=payload.after_seq,
                )
                if payload.consume
                else manager.read_local_resume_intent(
                    session_id,
                    after_seq=payload.after_seq,
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc

    @app.post("/api/local/session/{session_id}/ack-resume-intent")
    async def local_ack_resume_intent(
        session_id: str, payload: ResumeIntentAcknowledgeRequest, request: Request
    ) -> dict:
        _require_local_client(request)
        try:
            return manager.acknowledge_local_resume_intent(
                session_id,
                resume_intent_seq=payload.resume_intent_seq,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc

    @app.get("/api/session-artifacts/{session_id}")
    async def session_artifact(session_id: str, request: Request) -> dict:
        _require_local_client(request)
        event_limit = _resolve_limit(
            request.query_params.get("event_limit"),
            default=DEFAULT_ARTIFACT_EVENT_LIMIT,
        )
        command_limit = _resolve_limit(
            request.query_params.get("command_limit"),
            default=DEFAULT_ARTIFACT_COMMAND_LIMIT,
        )
        live_artifact = manager.artifact_for_session(session_id)
        if live_artifact is not None:
            return {
                **live_artifact,
                "event_limit": event_limit,
                "command_limit": command_limit,
                "events_truncated": False,
                "commands_truncated": False,
            }

        session_dir = (
            Path(__file__).resolve().parents[2] / "output" / "sessions" / session_id
        )
        session_json = session_dir / "session.json"
        events_ndjson = session_dir / "events.ndjson"
        commands_ndjson = session_dir / "commands.ndjson"
        if not session_json.exists():
            raise HTTPException(status_code=404, detail="Session artifact not found")
        artifact = json.loads(session_json.read_text(encoding="utf-8"))
        if event_limit > 0:
            recent_events: deque[dict[str, Any]] = deque(maxlen=event_limit)
        else:
            recent_events = deque()
        events_truncated = False
        if events_ndjson.exists():
            with events_ndjson.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        parsed_event = json.loads(line)
                        if event_limit > 0:
                            if len(recent_events) == event_limit:
                                events_truncated = True
                            recent_events.append(parsed_event)
                        else:
                            recent_events.append(parsed_event)
        commands: list[dict] = []
        commands_truncated = False
        if commands_ndjson.exists():
            deduped_commands: dict[str, dict] = {}
            with commands_ndjson.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        command = json.loads(line)
                        command_id = command.get("command_id")
                        command_name = command.get("command")
                        if isinstance(command_id, str) and command_id:
                            dedupe_key = (
                                f"{command_name}:{command_id}"
                                if isinstance(command_name, str) and command_name
                                else command_id
                            )
                            if dedupe_key in deduped_commands:
                                deduped_commands.pop(dedupe_key, None)
                            deduped_commands[dedupe_key] = command
                            if (
                                command_limit > 0
                                and len(deduped_commands) > command_limit
                            ):
                                commands_truncated = True
                                oldest_key = next(iter(deduped_commands))
                                deduped_commands.pop(oldest_key, None)
                        else:
                            commands.append(command)
            commands.extend(deduped_commands.values())
        return {
            "artifact": artifact,
            "events": list(recent_events),
            "commands": commands,
            "event_limit": event_limit,
            "command_limit": command_limit,
            "events_truncated": events_truncated,
            "commands_truncated": commands_truncated,
        }

    @app.get("/api/session-artifacts/{session_id}/keyframes/{filename}")
    async def session_keyframe(
        session_id: str, filename: str, request: Request
    ) -> FileResponse:
        _require_local_client(request)
        keyframe_path = (
            Path(__file__).resolve().parents[2]
            / "output"
            / "sessions"
            / session_id
            / "keyframes"
            / filename
        )
        if not keyframe_path.exists() or not keyframe_path.is_file():
            raise HTTPException(status_code=404, detail="Keyframe not found")
        return FileResponse(keyframe_path)

    @app.websocket("/ws/session")
    async def session_ws(websocket: WebSocket) -> None:
        await manager.connect(websocket)
        try:
            while True:
                message = await websocket.receive_json()
                await manager.handle(websocket, message)
        except WebSocketDisconnect:
            await manager.disconnect(websocket)
        except RuntimeError as exc:
            if "accept" in str(exc).lower() and "websocket" in str(exc).lower():
                await manager.disconnect(websocket)
                return
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            _logger.error("WebSocket RuntimeError\n%s", tb)
            raise
        except Exception as exc:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            _logger.error("WebSocket unhandled exception\n%s", tb)
            raise

    @app.get("/", include_in_schema=False)
    async def frontend_root():
        return _frontend_ready_response()

    @app.get("/favicon.ico", include_in_schema=False)
    async def frontend_favicon():
        favicon = frontend_dist / "favicon.ico"
        if favicon.exists():
            return FileResponse(favicon)
        raise HTTPException(status_code=404, detail="Not found")

    @app.get("/__lumon_frontend_ready__", include_in_schema=False)
    async def frontend_ready() -> dict[str, Any]:
        stale_reason = _frontend_staleness_reason()
        if stale_reason is not None:
            raise HTTPException(status_code=503, detail=stale_reason)
        frontend_manifest = _read_frontend_runtime_manifest()
        frontend_runtime_version = frontend_manifest.get("runtime_version")
        frontend_features = frontend_manifest.get("features") or {}
        return {
            "status": "ok",
            "frontend": "static",
            "runtime_version": RUNTIME_VERSION,
            "frontend_runtime_version": frontend_runtime_version,
            "frontend_features": frontend_features,
        }

    @app.get("/assets/{asset_path:path}", include_in_schema=False)
    async def frontend_asset(asset_path: str):
        asset = (frontend_dist / "assets" / asset_path).resolve()
        assets_root = (frontend_dist / "assets").resolve()
        if (
            assets_root not in asset.parents
            or not asset.exists()
            or not asset.is_file()
        ):
            raise HTTPException(status_code=404, detail="Asset not found")
        return FileResponse(asset)

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend_catchall(path: str):
        normalized = path.strip("/")
        if not normalized:
            return _frontend_ready_response()
        if normalized == "healthz" or normalized.startswith(
            ("api/", "ws/", "__lumon_harness__/", "docs", "redoc")
        ):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (frontend_dist / normalized).resolve()
        if (
            frontend_dist.resolve() in candidate.parents
            and candidate.exists()
            and candidate.is_file()
        ):
            return FileResponse(candidate)
        return _frontend_ready_response()

    return app


app = create_app()
