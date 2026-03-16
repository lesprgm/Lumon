from __future__ import annotations

import json
import os
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def bootstrap_session(backend_url: str, frontend_origin: str) -> dict[str, str]:
    request = Request(
        f"{backend_url}/api/bootstrap",
        headers={
            "Origin": frontend_origin,
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=5) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def build_ws_url(base_ws_url: str, session_id: str, ws_token: str) -> str:
    return f"{base_ws_url}?{urlencode({'session_id': session_id, 'token': ws_token})}"


def build_ws_url_with_params(base_ws_url: str, params: dict[str, str]) -> str:
    return f"{base_ws_url}?{urlencode(params)}"


def attach_local_opencode_session(
    backend_url: str,
    *,
    project_directory: str,
    frontend_origin: str,
    web_mode: str,
    auto_delegate: bool,
    observed_session_id: str | None = None,
) -> dict[str, str | bool]:
    payload = {
        "project_directory": project_directory,
        "frontend_origin": frontend_origin,
        "web_mode": web_mode,
        "auto_delegate": auto_delegate,
        "observed_session_id": observed_session_id,
    }
    request = Request(
        f"{backend_url}/api/local/observe/opencode",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:  # pragma: no cover - passthrough for callers
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Local observe attach failed with status {exc.code}: {detail}") from exc


def ensure_recording_enabled() -> None:
    if os.getenv("LUMON_RECORDING_ENABLED", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        raise SystemExit("Set LUMON_RECORDING_ENABLED=1 before capturing videos or screenshots.")
