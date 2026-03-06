from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.config import PROTOCOL_VERSION
from app.protocol.models import AttachObserverPayload, LocalObserveOpenCodeRequest


class OpenCodeAttachService:
    def __init__(self) -> None:
        self._session_map: dict[str, str] = {}

    def prepare_runtime(
        self,
        payload: LocalObserveOpenCodeRequest,
        sessions: dict[str, Any],
        create_runtime: Callable[[], Any],
    ) -> tuple[Any, bool]:
        observed_session_id = payload.observed_session_id
        runtime = self.runtime_for_observed_session(sessions, observed_session_id) if observed_session_id else None
        already_attached = runtime is not None

        if runtime is None:
            runtime = create_runtime()
            sessions[runtime.session_id] = runtime
            if observed_session_id:
                self._session_map[observed_session_id] = runtime.session_id

        return runtime, already_attached

    async def attach_runtime(
        self,
        runtime: Any,
        payload: LocalObserveOpenCodeRequest,
        *,
        bridge_for_web_mode: Callable[[str | None], str | None],
    ) -> None:
        await runtime.attach_observer(
            AttachObserverPayload(
                task_text="OpenCode interactive session",
                adapter_id="opencode",
                web_mode=payload.web_mode,
                web_bridge=bridge_for_web_mode(payload.web_mode),
                auto_delegate=payload.auto_delegate,
                observed_session_id=payload.observed_session_id,
            )
        )

    def build_attach_response(
        self,
        *,
        runtime: Any,
        frontend_origin: str,
        build_frontend_open_url: Callable[[str, Any], str],
        already_attached: bool,
    ) -> dict[str, Any]:
        return {
            "session_id": runtime.session_id,
            "ws_token": runtime.join_token,
            "ws_path": "/ws/session",
            "protocol_version": PROTOCOL_VERSION,
            "open_url": build_frontend_open_url(frontend_origin, runtime),
            "already_attached": already_attached,
        }

    def rollback_prepared_runtime(self, payload: LocalObserveOpenCodeRequest, sessions: dict[str, Any], runtime: Any) -> None:
        sessions.pop(runtime.session_id, None)
        observed_session_id = payload.observed_session_id
        if observed_session_id and self._session_map.get(observed_session_id) == runtime.session_id:
            self._session_map.pop(observed_session_id, None)

    def runtime_for_observed_session(self, sessions: dict[str, Any], observed_session_id: str | None) -> Any | None:
        if not observed_session_id:
            return None
        session_id = self._session_map.get(observed_session_id)
        if session_id is None:
            return None
        runtime = sessions.get(session_id)
        if runtime is None or runtime.is_terminal():
            self._session_map.pop(observed_session_id, None)
            return None
        return runtime

    def prune_runtime(self, runtime: Any) -> None:
        observed_session_id = getattr(runtime._connector, "observed_session_id", None)
        if observed_session_id:
            self._session_map.pop(str(observed_session_id), None)
