from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.protocol.models import (
    BrowserCommandRecord,
    BrowserContextPayload,
    InterventionRecord,
    PageVisitRecord,
    SessionArtifact,
    SessionMetrics,
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _output_root() -> Path:
    return _project_root() / "output"


def _iso_to_ms(value: str | None) -> int | None:
    if not value:
        return None
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return None


def environment_type_for_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host:
        return "external"
    if host in {"127.0.0.1", "localhost"} or host.endswith(".local"):
        return "local"
    if host.endswith("docs") or "docs" in host:
        return "docs"
    if any(token in host for token in ("app", "dashboard", "admin", "studio")):
        return "app"
    return "external"


class SessionArtifactRecorder:
    def __init__(
        self,
        *,
        session_id: str,
        adapter_id: str,
        adapter_run_id: str,
        task_text: str,
        observer_mode: bool,
        started_at: str,
    ) -> None:
        self.session_id = session_id
        self.adapter_id = adapter_id
        self.adapter_run_id = adapter_run_id
        self.task_text = task_text
        self.observer_mode = observer_mode
        self.started_at = started_at
        self.metrics = SessionMetrics()
        self.events: list[dict[str, Any]] = []
        self.interventions: list[InterventionRecord] = []
        self._intervention_index: dict[str, int] = {}
        self.commands: list[BrowserCommandRecord] = []
        self._page_visits: list[PageVisitRecord] = []
        self.current_browser_context: BrowserContextPayload | None = None
        self.latest_frame: tuple[str, bytes] | None = None
        self.keyframes: list[str] = []
        self._keyframe_counter = 1
        self._artifact_written = False
        self._last_browser_episode_ms: int | None = None
        self._session_dir = _output_root() / "sessions" / self.session_id
        self._keyframe_dir = self._session_dir / "keyframes"

    @property
    def session_dir(self) -> Path:
        return self._session_dir

    def update_session_identity(self, *, adapter_id: str, adapter_run_id: str, task_text: str, observer_mode: bool) -> None:
        self.adapter_id = adapter_id
        self.adapter_run_id = adapter_run_id
        self.task_text = task_text
        self.observer_mode = observer_mode

    def note_attach_requested(self, timestamp: str) -> None:
        if self.metrics.attach_requested_at is None:
            self.metrics.attach_requested_at = timestamp

    def note_attached(self, timestamp: str) -> None:
        if self.metrics.attached_at is None:
            self.metrics.attached_at = timestamp
        self._update_attach_latency()

    def note_duplicate_attach_prevented(self) -> None:
        self.metrics.duplicate_attach_prevented += 1

    def note_reconnect(self) -> None:
        self.metrics.reconnect_count += 1

    def note_ui_open_requested(self, timestamp: str) -> None:
        if self.metrics.ui_open_requested_at is None:
            self.metrics.ui_open_requested_at = timestamp
        self._update_ui_open_latency()

    def note_ui_ready(self, timestamp: str) -> None:
        if self.metrics.ui_ready_at is None:
            self.metrics.ui_ready_at = timestamp
        self._update_ui_open_latency()

    def note_browser_episode(self, timestamp: str) -> None:
        if self.metrics.first_browser_event_at is None:
            self.metrics.first_browser_event_at = timestamp
        timestamp_ms = _iso_to_ms(timestamp)
        if timestamp_ms is None:
            if self._last_browser_episode_ms is None:
                self.metrics.browser_episode_count += 1
                self._last_browser_episode_ms = 0
            return
        if self._last_browser_episode_ms is None or timestamp_ms - self._last_browser_episode_ms >= 20_000:
            self.metrics.browser_episode_count += 1
            self._last_browser_episode_ms = timestamp_ms

    def record_frame(self, mime_type: str, data_base64: str) -> None:
        try:
            raw = base64.b64decode(data_base64)
        except Exception:
            return
        extension = ".png" if mime_type == "image/png" else ".jpg"
        self.latest_frame = (extension, raw)

    def append_event(self, payload: dict[str, Any]) -> None:
        self.events.append(payload)

    def append_command(self, record: BrowserCommandRecord) -> None:
        self.commands.append(record)
        self.metrics.browser_command_count += 1
        if record.status == "success" and record.evidence and record.evidence.verified:
            self.metrics.verified_browser_action_count += 1
        elif record.status == "blocked":
            self.metrics.browser_blocked_count += 1
        elif record.status == "partial":
            self.metrics.browser_partial_count += 1
        if record.reason == "stale_target":
            self.metrics.stale_target_count += 1

    def record_browser_context(self, payload: BrowserContextPayload, *, capture_keyframe: bool = False) -> None:
        previous_url = self.current_browser_context.url if self.current_browser_context else None
        self.current_browser_context = payload
        keyframe_path = self.capture_keyframe(reason="browser_context") if capture_keyframe and payload.url != previous_url else None
        if self._page_visits and self._page_visits[-1].url == payload.url:
            existing = self._page_visits[-1]
            self._page_visits[-1] = existing.model_copy(
                update={
                    "title": payload.title or existing.title,
                    "last_seen_at": payload.timestamp,
                    "environment_type": payload.environment_type,
                    "keyframe_path": existing.keyframe_path or keyframe_path,
                }
            )
        else:
            self._page_visits.append(
                PageVisitRecord(
                    url=payload.url,
                    domain=payload.domain,
                    title=payload.title,
                    environment_type=payload.environment_type,
                    first_seen_at=payload.timestamp,
                    last_seen_at=payload.timestamp,
                    keyframe_path=keyframe_path,
                )
            )

    def start_intervention(
        self,
        *,
        intervention_id: str,
        kind: str,
        headline: str,
        reason_text: str,
        started_at: str,
        source_url: str | None,
        target_summary: str | None,
        recommended_action: str | None,
        checkpoint_id: str | None = None,
        source_event_id: str | None = None,
    ) -> None:
        if intervention_id in self._intervention_index:
            return
        record = InterventionRecord(
            intervention_id=intervention_id,
            kind=kind,
            headline=headline,
            reason_text=reason_text,
            source_url=source_url,
            target_summary=target_summary,
            recommended_action=recommended_action,
            started_at=started_at,
            checkpoint_id=checkpoint_id,
            source_event_id=source_event_id,
            domain=(urlparse(source_url).hostname if source_url else None),
            keyframe_path=self.capture_keyframe(reason=f"intervention_{kind}"),
        )
        self._intervention_index[intervention_id] = len(self.interventions)
        self.interventions.append(record)
        self.metrics.intervention_count += 1

    def resolve_intervention(self, intervention_id: str, *, resolution: str, resolved_at: str) -> None:
        index = self._intervention_index.get(intervention_id)
        if index is None:
            return
        self.interventions[index] = self.interventions[index].model_copy(
            update={"resolution": resolution, "resolved_at": resolved_at}
        )

    def capture_keyframe(self, *, reason: str) -> str | None:
        if self.latest_frame is None:
            return None
        extension, raw = self.latest_frame
        self._keyframe_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{self._keyframe_counter:03d}_{reason}{extension}"
        self._keyframe_counter += 1
        path = self._keyframe_dir / filename
        path.write_bytes(raw)
        relative_path = f"keyframes/{filename}"
        self.keyframes.append(relative_path)
        return relative_path

    def finalize(self, *, status: str, completed_at: str, summary_text: str | None) -> SessionArtifact:
        self.capture_keyframe(reason=status)
        self.metrics.artifact_written = True
        browser_commands = [BrowserCommandRecord.model_validate(command) for command in self.read_commands()]
        artifact = SessionArtifact(
            session_id=self.session_id,
            adapter_id=self.adapter_id,
            adapter_run_id=self.adapter_run_id,
            task_text=self.task_text,
            observer_mode=self.observer_mode,
            status=status,
            started_at=self.started_at,
            completed_at=completed_at,
            summary_text=summary_text,
            browser_context=self.current_browser_context,
            pages_visited=list(self._page_visits),
            interventions=self.interventions,
            browser_commands=browser_commands,
            keyframes=self.keyframes,
            metrics=self.metrics.model_copy(update={"session_completed": True, "artifact_written": True}),
        )

        self._session_dir.mkdir(parents=True, exist_ok=True)
        (self._session_dir / "session.json").write_text(
            json.dumps(artifact.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        (self._session_dir / "interventions.json").write_text(
            json.dumps([record.model_dump(mode="json") for record in self.interventions], indent=2),
            encoding="utf-8",
        )
        with (self._session_dir / "events.ndjson").open("w", encoding="utf-8") as handle:
            for event in self.events:
                handle.write(json.dumps(event))
                handle.write("\n")
        with (self._session_dir / "commands.ndjson").open("w", encoding="utf-8") as handle:
            for command in self.commands:
                handle.write(json.dumps(command.model_dump(mode="json")))
                handle.write("\n")

        metrics_dir = _output_root() / "metrics"
        metrics_dir.mkdir(parents=True, exist_ok=True)
        with (metrics_dir / "sessions.ndjson").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(artifact.model_dump(mode="json")))
            handle.write("\n")
        self._artifact_written = True
        return artifact

    def current_artifact(self, *, status: str = "running", summary_text: str | None = None) -> SessionArtifact:
        browser_commands = [BrowserCommandRecord.model_validate(command) for command in self.read_commands()]
        return SessionArtifact(
            session_id=self.session_id,
            adapter_id=self.adapter_id,
            adapter_run_id=self.adapter_run_id,
            task_text=self.task_text,
            observer_mode=self.observer_mode,
            status=status,
            started_at=self.started_at,
            summary_text=summary_text,
            browser_context=self.current_browser_context,
            pages_visited=list(self._page_visits),
            interventions=self.interventions,
            browser_commands=browser_commands,
            keyframes=self.keyframes,
            metrics=self.metrics,
        )

    def read_events(self) -> list[dict[str, Any]]:
        return list(self.events)

    def read_commands(self) -> list[dict[str, Any]]:
        deduped: dict[str, BrowserCommandRecord] = {}
        for command in self.commands:
            deduped[f"{command.command}:{command.command_id}"] = command
        return [command.model_dump(mode="json") for command in deduped.values()]

    def _update_attach_latency(self) -> None:
        requested_ms = _iso_to_ms(self.metrics.attach_requested_at)
        attached_ms = _iso_to_ms(self.metrics.attached_at)
        if requested_ms is not None and attached_ms is not None:
            self.metrics.attach_latency_ms = max(attached_ms - requested_ms, 0)

    def _update_ui_open_latency(self) -> None:
        requested_ms = _iso_to_ms(self.metrics.ui_open_requested_at)
        ready_ms = _iso_to_ms(self.metrics.ui_ready_at)
        if requested_ms is not None and ready_ms is not None:
            self.metrics.ui_open_latency_ms = max(ready_ms - requested_ms, 0)
