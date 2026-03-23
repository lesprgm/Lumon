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


FALSE_OPEN_THRESHOLD_MS = 3_000


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _output_root() -> Path:
    return _project_root() / "output"


def _iso_to_ms(value: str | None) -> int | None:
    if not value:
        return None
    try:
        from datetime import datetime

        return int(
            datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000
        )
    except Exception:
        return None


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
        self._pending_open_requested_ms: int | None = None
        self._session_dir = _output_root() / "sessions" / self.session_id
        self._keyframe_dir = self._session_dir / "keyframes"

    @property
    def session_dir(self) -> Path:
        return self._session_dir

    def update_session_identity(
        self,
        *,
        adapter_id: str,
        adapter_run_id: str,
        task_text: str,
        observer_mode: bool,
    ) -> None:
        self.adapter_id = adapter_id
        self.adapter_run_id = adapter_run_id
        self.task_text = task_text
        self.observer_mode = observer_mode
        self._persist_live_snapshot()

    def note_attach_requested(self, timestamp: str) -> None:
        if self.metrics.attach_requested_at is None:
            self.metrics.attach_requested_at = timestamp
            self._persist_live_snapshot()

    def note_attached(self, timestamp: str) -> None:
        if self.metrics.attached_at is None:
            self.metrics.attached_at = timestamp
        self._update_attach_latency()
        self._persist_live_snapshot()

    def note_duplicate_attach_prevented(self) -> None:
        self.metrics.duplicate_attach_prevented += 1
        self._persist_live_snapshot()

    def note_reconnect(self) -> None:
        self.metrics.reconnect_count += 1
        self._persist_live_snapshot()

    def note_ui_open_requested(self, timestamp: str) -> None:
        if self.metrics.ui_open_requested_at is None:
            self.metrics.ui_open_requested_at = timestamp
        self._update_ui_open_latency()
        self._persist_live_snapshot()

    def note_auto_start_completed(
        self, *, timestamp: str, latency_ms: int | None
    ) -> None:
        self.metrics.auto_start_count += 1
        if latency_ms is not None and latency_ms >= 0:
            if self.metrics.startup_latency_ms is None:
                self.metrics.startup_latency_ms = latency_ms
            else:
                self.metrics.startup_latency_ms = min(
                    self.metrics.startup_latency_ms, latency_ms
                )

    def note_ui_ready(self, timestamp: str) -> None:
        if self.metrics.ui_ready_at is None:
            self.metrics.ui_ready_at = timestamp
        self._update_ui_open_latency()
        self._persist_live_snapshot()

    def note_browser_episode(self, timestamp: str) -> None:
        if self.metrics.first_browser_event_at is None:
            self.metrics.first_browser_event_at = timestamp
        timestamp_ms = _iso_to_ms(timestamp)
        if timestamp_ms is None:
            if self._last_browser_episode_ms is None:
                self.metrics.browser_episode_count += 1
                self._last_browser_episode_ms = 0
            return
        if (
            self._last_browser_episode_ms is None
            or timestamp_ms - self._last_browser_episode_ms >= 20_000
        ):
            self.metrics.browser_episode_count += 1
            self._last_browser_episode_ms = timestamp_ms

    def note_first_frame(self, timestamp: str) -> None:
        if self.metrics.first_frame_at is None:
            self.metrics.first_frame_at = timestamp
        self._update_first_frame_latency()

    def record_frame(self, mime_type: str, data_base64: str) -> None:
        try:
            raw = base64.b64decode(data_base64)
        except Exception:
            return
        extension = ".png" if mime_type == "image/png" else ".jpg"
        self.latest_frame = (extension, raw)

    def append_event(self, payload: dict[str, Any]) -> None:
        self.events.append(payload)
        self._persist_live_snapshot()

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
        self._persist_live_snapshot()

    def record_browser_context(
        self, payload: BrowserContextPayload, *, capture_keyframe: bool = False
    ) -> None:
        previous_url = (
            self.current_browser_context.url if self.current_browser_context else None
        )
        self.current_browser_context = payload
        keyframe_path = (
            self.capture_keyframe(reason="browser_context")
            if capture_keyframe and payload.url != previous_url
            else None
        )
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
        self._persist_live_snapshot()

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
        self._persist_live_snapshot()

    def resolve_intervention(
        self, intervention_id: str, *, resolution: str, resolved_at: str
    ) -> None:
        index = self._intervention_index.get(intervention_id)
        if index is None:
            return
        self.interventions[index] = self.interventions[index].model_copy(
            update={"resolution": resolution, "resolved_at": resolved_at}
        )
        self._persist_live_snapshot()

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
        self._persist_live_snapshot()
        return relative_path

    def note_ui_open_attempt(
        self, *, timestamp: str, reason_code: str | None = None
    ) -> None:
        self._resolve_pending_open(timestamp)
        self.metrics.open_attempt_count += 1
        self.note_ui_open_requested(timestamp)
        if reason_code:
            self._increment_reason_counter(self.metrics.open_reason_counts, reason_code)
        timestamp_ms = _iso_to_ms(timestamp)
        if timestamp_ms is not None:
            self._pending_open_requested_ms = timestamp_ms

    def note_ui_open_suppressed(
        self, *, reason_code: str | None = None, noisy_prevented: bool = False
    ) -> None:
        self.metrics.open_suppressed_count += 1
        if noisy_prevented:
            self.metrics.noisy_open_prevented_count += 1
        if reason_code:
            self._increment_reason_counter(
                self.metrics.open_suppression_reason_counts, reason_code
            )

    def note_ui_open_completed(self, *, reason_code: str | None = None) -> None:
        self.metrics.open_completed_count += 1
        self._clear_pending_open()

    def note_ui_open_failed(self, *, reason_code: str | None = None) -> None:
        self.metrics.open_failed_count += 1
        self._clear_pending_open()

    def note_meaningful_frame_visible(self, *, timestamp: str) -> None:
        if self.metrics.first_meaningful_frame_at is not None:
            return
        self.metrics.first_meaningful_frame_at = timestamp
        self._update_meaningful_frame_latency()
        self._update_browser_to_meaningful_frame_latency()
        self._resolve_pending_open(timestamp)

    def note_intervention_visible(self, *, timestamp: str) -> None:
        if self.metrics.intervention_visible_at is not None:
            return
        self.metrics.intervention_visible_at = timestamp
        self._update_intervention_latency()
        self._resolve_pending_open(timestamp)

    def note_clarity_ready(self, *, timestamp: str) -> None:
        if self.metrics.clarity_ready_at is not None:
            return
        self.metrics.clarity_ready_at = timestamp
        self._update_clarity_latency()

    def note_sprite_visible(
        self, *, timestamp: str, delay_ms: int | None = None
    ) -> None:
        if self.metrics.first_sprite_visible_at is not None:
            return
        self.metrics.first_sprite_visible_at = timestamp
        if delay_ms is not None and delay_ms >= 0:
            self.metrics.sprite_after_frame_latency_ms = delay_ms
            return
        self._update_sprite_after_frame_latency()

    def note_video_quality_sample(
        self,
        *,
        width: int | None = None,
        height: int | None = None,
        fps: float | None = None,
    ) -> None:
        self.metrics.video_quality_sample_count += 1
        if isinstance(width, int) and width > 0:
            self.metrics.peak_video_width = max(
                self.metrics.peak_video_width or 0, width
            )
        if isinstance(height, int) and height > 0:
            self.metrics.peak_video_height = max(
                self.metrics.peak_video_height or 0, height
            )
        if isinstance(fps, (int, float)) and fps > 0:
            fps_value = round(float(fps), 1)
            self.metrics.latest_video_fps = fps_value
            self.metrics.peak_video_fps = max(
                self.metrics.peak_video_fps or 0.0, fps_value
            )

    def record_ui_telemetry(
        self, *, event: str, timestamp: str, meta: dict[str, Any] | None = None
    ) -> None:
        details = meta or {}
        if event == "auto_start_completed":
            self.note_auto_start_completed(
                timestamp=timestamp,
                latency_ms=self._coerce_nonnegative_int(
                    details.get("startup_latency_ms")
                ),
            )
            return
        if event == "open_requested":
            self.note_ui_open_attempt(
                timestamp=timestamp,
                reason_code=self._coerce_reason(details.get("reason_code")),
            )
            return
        if event == "open_suppressed":
            reason_code = self._coerce_reason(details.get("reason_code"))
            noisy_prevented = reason_code in {
                "active_browser_task",
                "tool_active",
                "pending_tool",
                "duplicate_signal",
                "active_session",
                "duplicate_intervention",
                "active_intervention_session",
                "already_visible",
                "cooldown",
                "open_in_progress",
            }
            self.note_ui_open_suppressed(
                reason_code=reason_code, noisy_prevented=noisy_prevented
            )
            return
        if event == "open_completed":
            self.note_ui_open_completed(
                reason_code=self._coerce_reason(details.get("reason_code"))
            )
            return
        if event == "open_failed":
            self.note_ui_open_failed(
                reason_code=self._coerce_reason(details.get("reason_code"))
            )
            return
        if event == "meaningful_frame_visible":
            self.note_meaningful_frame_visible(timestamp=timestamp)
            return
        if event == "intervention_visible":
            self.note_intervention_visible(timestamp=timestamp)
            return
        if event == "clarity_ready":
            self.note_clarity_ready(timestamp=timestamp)
            return
        if event == "sprite_visible":
            self.note_sprite_visible(
                timestamp=timestamp,
                delay_ms=self._coerce_nonnegative_int(details.get("delay_ms")),
            )
            return
        if event == "video_quality_sample":
            self.note_video_quality_sample(
                width=self._coerce_nonnegative_int(details.get("width")),
                height=self._coerce_nonnegative_int(details.get("height")),
                fps=self._coerce_positive_float(details.get("fps")),
            )
        self._persist_live_snapshot()

    def finalize(
        self, *, status: str, completed_at: str, summary_text: str | None
    ) -> SessionArtifact:
        self._resolve_pending_open(completed_at)
        self.capture_keyframe(reason=status)
        self.metrics.artifact_written = True
        browser_commands = [
            BrowserCommandRecord.model_validate(command)
            for command in self.read_commands()
        ]
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
            metrics=self.metrics.model_copy(
                update={"session_completed": True, "artifact_written": True}
            ),
        )

        self._write_snapshot_files(artifact)

        metrics_dir = _output_root() / "metrics"
        metrics_dir.mkdir(parents=True, exist_ok=True)
        with (metrics_dir / "sessions.ndjson").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(artifact.model_dump(mode="json")))
            handle.write("\n")
        self._artifact_written = True
        return artifact

    def current_artifact(
        self, *, status: str = "running", summary_text: str | None = None
    ) -> SessionArtifact:
        browser_commands = [
            BrowserCommandRecord.model_validate(command)
            for command in self.read_commands()
        ]
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

    def _persist_live_snapshot(self) -> None:
        if self._artifact_written:
            return
        artifact = self.current_artifact(
            status="running", summary_text=self.task_text or None
        )
        self._write_snapshot_files(artifact)

    def _write_snapshot_files(self, artifact: SessionArtifact) -> None:
        self._session_dir.mkdir(parents=True, exist_ok=True)
        (self._session_dir / "session.json").write_text(
            json.dumps(artifact.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        (self._session_dir / "interventions.json").write_text(
            json.dumps(
                [record.model_dump(mode="json") for record in self.interventions],
                indent=2,
            ),
            encoding="utf-8",
        )
        with (self._session_dir / "events.ndjson").open(
            "w", encoding="utf-8"
        ) as handle:
            for event in self.events:
                handle.write(json.dumps(event))
                handle.write("\n")
        with (self._session_dir / "commands.ndjson").open(
            "w", encoding="utf-8"
        ) as handle:
            for command in self.commands:
                handle.write(json.dumps(command.model_dump(mode="json")))
                handle.write("\n")

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

    def _update_first_frame_latency(self) -> None:
        started_ms = _iso_to_ms(self.started_at)
        frame_ms = _iso_to_ms(self.metrics.first_frame_at)
        if started_ms is not None and frame_ms is not None:
            self.metrics.first_frame_latency_ms = max(frame_ms - started_ms, 0)

    def _update_ui_open_latency(self) -> None:
        requested_ms = _iso_to_ms(self.metrics.ui_open_requested_at)
        ready_ms = _iso_to_ms(self.metrics.ui_ready_at)
        if requested_ms is not None and ready_ms is not None:
            self.metrics.ui_open_latency_ms = max(ready_ms - requested_ms, 0)

    def _update_meaningful_frame_latency(self) -> None:
        requested_ms = _iso_to_ms(self.metrics.ui_open_requested_at)
        frame_ms = _iso_to_ms(self.metrics.first_meaningful_frame_at)
        if requested_ms is not None and frame_ms is not None:
            self.metrics.meaningful_frame_latency_ms = max(frame_ms - requested_ms, 0)

    def _update_browser_to_meaningful_frame_latency(self) -> None:
        browser_ms = _iso_to_ms(self.metrics.first_browser_event_at)
        frame_ms = _iso_to_ms(self.metrics.first_meaningful_frame_at)
        if browser_ms is not None and frame_ms is not None:
            self.metrics.browser_to_meaningful_frame_latency_ms = max(
                frame_ms - browser_ms, 0
            )

    def _update_intervention_latency(self) -> None:
        visible_ms = _iso_to_ms(self.metrics.intervention_visible_at)
        if visible_ms is None:
            return
        started_at = self.interventions[-1].started_at if self.interventions else None
        started_ms = _iso_to_ms(started_at)
        if started_ms is not None:
            self.metrics.intervention_latency_ms = max(visible_ms - started_ms, 0)

    def _update_clarity_latency(self) -> None:
        clarity_ms = _iso_to_ms(self.metrics.clarity_ready_at)
        if clarity_ms is None:
            return
        anchors = [
            _iso_to_ms(self.metrics.ui_open_requested_at),
            _iso_to_ms(self.interventions[-1].started_at)
            if self.interventions
            else None,
        ]
        anchor_ms = max((value for value in anchors if value is not None), default=None)
        if anchor_ms is not None:
            self.metrics.clarity_latency_ms = max(clarity_ms - anchor_ms, 0)
            self.metrics.clarity_within_2s = self.metrics.clarity_latency_ms <= 2_000

    def _update_sprite_after_frame_latency(self) -> None:
        frame_ms = _iso_to_ms(self.metrics.first_meaningful_frame_at)
        sprite_ms = _iso_to_ms(self.metrics.first_sprite_visible_at)
        if frame_ms is not None and sprite_ms is not None:
            self.metrics.sprite_after_frame_latency_ms = max(sprite_ms - frame_ms, 0)

    def _resolve_pending_open(self, timestamp: str) -> None:
        if self._pending_open_requested_ms is None:
            return
        timestamp_ms = _iso_to_ms(timestamp)
        if timestamp_ms is None:
            return
        if timestamp_ms - self._pending_open_requested_ms > FALSE_OPEN_THRESHOLD_MS:
            self.metrics.false_open_count += 1
        self._clear_pending_open()

    def _clear_pending_open(self) -> None:
        self._pending_open_requested_ms = None

    @staticmethod
    def _increment_reason_counter(counter: dict[str, int], reason_code: str) -> None:
        counter[reason_code] = counter.get(reason_code, 0) + 1

    @staticmethod
    def _coerce_nonnegative_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, float):
            return int(value) if value >= 0 else None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _coerce_positive_float(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            parsed = float(value)
            return parsed if parsed > 0 else None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _coerce_reason(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None
