from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_OPENCODE_DB_PATH = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
_BROWSERISH_TOOL_TOKENS = (
    "browser",
    "playwright",
    "chrome",
    "navigate",
    "goto",
    "go_to_url",
    "open_url",
    "open-url",
    "search",
    "web",
    "site",
    "internet",
    "click",
    "type",
    "fill",
    "input",
    "scroll",
    "screenshot",
)


@dataclass(slots=True)
class ObservedSession:
    session_id: str
    directory: str
    title: str | None
    parent_id: str | None
    time_created_ms: int
    time_updated_ms: int


@dataclass(slots=True)
class ObservedPart:
    rowid: int
    part_id: str
    session_id: str
    role: str | None
    time_created_ms: int
    time_updated_ms: int
    part: dict[str, Any]
    message: dict[str, Any]


class OpenCodeSQLiteObserver:
    def __init__(self, db_path: str | Path = DEFAULT_OPENCODE_DB_PATH) -> None:
        self.db_path = Path(db_path)

    def list_sessions(self, *, since_ms: int | None = None, directory: str | None = None) -> list[ObservedSession]:
        query = """
            SELECT id, directory, title, parent_id, time_created, time_updated
            FROM session
            WHERE time_archived IS NULL
        """
        params: list[Any] = []
        if directory is not None:
            query += " AND directory = ?"
            params.append(str(Path(directory).resolve()))
        if since_ms is not None:
            query += " AND time_updated >= ?"
            params.append(int(since_ms))
        query += " ORDER BY time_updated DESC, rowid DESC"
        rows = self._execute(query, tuple(params))
        return [self._build_session(row) for row in rows]

    def get_session(self, session_id: str) -> ObservedSession | None:
        rows = self._execute(
            """
            SELECT id, directory, title, parent_id, time_created, time_updated
            FROM session
            WHERE id = ? AND time_archived IS NULL
            LIMIT 1
            """,
            (session_id,),
        )
        if not rows:
            return None
        return self._build_session(rows[0])

    def baseline_session_ids(self, directory: str) -> set[str]:
        target_directory = str(Path(directory).resolve())
        rows = self._execute(
            """
            SELECT id
            FROM session
            WHERE directory = ?
            """,
            (target_directory,),
        )
        return {str(row["id"]) for row in rows}

    def find_session(
        self,
        directory: str,
        *,
        since_ms: int,
        exclude_session_ids: set[str] | None = None,
        preferred_session_id: str | None = None,
    ) -> ObservedSession | None:
        target_directory = str(Path(directory).resolve())
        excluded = exclude_session_ids or set()
        rows = self._execute(
            """
            SELECT id, directory, title, parent_id, time_created, time_updated
            FROM session
            WHERE directory = ?
            ORDER BY time_updated DESC, rowid DESC
            """,
            (target_directory,),
        )
        for row in rows:
            session_id = str(row["id"])
            if preferred_session_id and session_id == preferred_session_id:
                return self._build_session(row)
            if session_id in excluded:
                continue
            if int(row["time_updated"]) >= since_ms or int(row["time_created"]) >= since_ms:
                return self._build_session(row)
        return None

    def load_parts(self, session_id: str, *, after_rowid: int = 0) -> list[ObservedPart]:
        rows = self._execute(
            """
            SELECT
                part.rowid AS rowid,
                part.id AS part_id,
                part.session_id AS session_id,
                part.time_created AS part_time_created,
                part.time_updated AS part_time_updated,
                part.data AS part_data,
                message.data AS message_data
            FROM part
            JOIN message ON message.id = part.message_id
            WHERE part.session_id = ? AND part.rowid > ?
            ORDER BY part.rowid ASC
            """,
            (session_id, after_rowid),
        )
        observed_parts: list[ObservedPart] = []
        for row in rows:
            part = self._decode_json(row["part_data"])
            message = self._decode_json(row["message_data"])
            observed_parts.append(
                ObservedPart(
                    rowid=int(row["rowid"]),
                    part_id=str(row["part_id"]),
                    session_id=str(row["session_id"]),
                    role=message.get("role"),
                    time_created_ms=int(row["part_time_created"]),
                    time_updated_ms=int(row["part_time_updated"]),
                    part=part,
                    message=message,
                )
            )
        return observed_parts

    def part_to_observer_event(self, observed_part: ObservedPart) -> dict[str, Any] | None:
        part_type = str(observed_part.part.get("type") or "").lower()
        if part_type == "text":
            return self._text_event(observed_part)
        if part_type == "tool":
            return self._tool_event(observed_part)
        if part_type == "reasoning":
            return self._reasoning_event(observed_part)
        if part_type == "patch":
            return self._patch_event(observed_part)
        if part_type == "step-start":
            return self._step_event(observed_part, summary_text="OpenCode started a step", state="thinking")
        if part_type == "step-finish":
            return self._step_event(observed_part, summary_text="OpenCode finished a step", state="done")
        return None

    def _text_event(self, observed_part: ObservedPart) -> dict[str, Any] | None:
        text = str(observed_part.part.get("text") or "").strip()
        if not text:
            return None
        if observed_part.role == "user":
            return {
                "source_event_id": observed_part.part_id,
                "event_type": "tool_start",
                "state": "thinking",
                "summary_text": f"OpenCode received: {self._trim(text, 96)}",
                "intent": text,
                "task_text": self._trim(text, 180),
                "meta": {"part_type": "text", "role": "user"},
            }
        return None

    def _tool_event(self, observed_part: ObservedPart) -> dict[str, Any]:
        state = observed_part.part.get("state") or {}
        tool_name = str(observed_part.part.get("tool") or "tool")
        tool_status = str(state.get("status") or "running").lower()
        title = str(state.get("title") or "") or None
        summary = title or f"OpenCode ran {tool_name}"
        output_preview = None
        metadata = state.get("metadata")
        if isinstance(metadata, dict):
            output_preview = metadata.get("preview") or metadata.get("output")
        if not isinstance(output_preview, str):
            output_preview = state.get("output") if isinstance(state.get("output"), str) else None
        return {
            "source_event_id": observed_part.part_id,
            "event_type": self._tool_event_type(tool_name, title),
            "state": self._tool_state(tool_name, tool_status),
            "summary_text": self._trim(summary, 120),
            "intent": title or summary,
            "task_text": None,
            "meta": {
                "part_type": "tool",
                "role": observed_part.role,
                "tool_name": tool_name,
                "tool_status": tool_status,
                "tool_title": title,
                "browser_candidate": self._looks_browserish(tool_name, title, output_preview),
                "output_preview": self._trim(output_preview, 220) if output_preview else None,
            },
        }

    def _reasoning_event(self, observed_part: ObservedPart) -> dict[str, Any] | None:
        text = str(observed_part.part.get("text") or "").strip()
        if not text:
            return None
        return {
            "source_event_id": observed_part.part_id,
            "event_type": "wait",
            "state": "thinking",
            "summary_text": "OpenCode is reasoning about the next step",
            "intent": self._trim(text, 220),
            "meta": {"part_type": "reasoning", "role": observed_part.role},
        }

    def _patch_event(self, observed_part: ObservedPart) -> dict[str, Any]:
        return {
            "source_event_id": observed_part.part_id,
            "event_type": "tool_start",
            "state": "typing",
            "summary_text": "OpenCode prepared a patch",
            "intent": "Apply a code change",
            "meta": {"part_type": "patch", "role": observed_part.role},
        }

    def _step_event(self, observed_part: ObservedPart, *, summary_text: str, state: str) -> dict[str, Any]:
        return {
            "source_event_id": observed_part.part_id,
            "event_type": "wait",
            "state": state,
            "summary_text": summary_text,
            "intent": summary_text,
            "meta": {"part_type": observed_part.part.get("type"), "role": observed_part.role},
        }

    def _tool_event_type(self, tool_name: str, title: str | None) -> str:
        haystack = f"{tool_name} {title or ''}".lower()
        if any(token in haystack for token in ("click", "tap", "submit")):
            return "click"
        if any(token in haystack for token in ("type", "write", "fill", "input")):
            return "type"
        if "scroll" in haystack:
            return "scroll"
        if any(token in haystack for token in ("navigate", "goto", "open_url", "open-url", "visit", "browser", "web", "search", "internet", "site")):
            return "navigate"
        if any(token in haystack for token in ("read", "grep", "glob", "question", "skill", "patch")):
            return "read"
        return "tool_start"

    def _tool_state(self, tool_name: str, tool_status: str) -> str:
        if tool_status in {"error", "failed"}:
            return "error"
        if tool_status in {"completed", "done"}:
            return "done"
        lowered = tool_name.lower()
        if any(token in lowered for token in ("click", "tap")):
            return "clicking"
        if any(token in lowered for token in ("type", "write", "fill", "input")):
            return "typing"
        if "scroll" in lowered:
            return "scrolling"
        if any(token in lowered for token in ("navigate", "goto", "open_url", "open-url", "visit", "browser", "web", "search")):
            return "navigating"
        if any(token in lowered for token in ("read", "grep", "glob", "question", "skill")):
            return "reading"
        return "thinking"

    def _looks_browserish(self, tool_name: str, title: str | None, output_preview: str | None) -> bool:
        haystack = " ".join(part for part in (tool_name, title or "", output_preview or "") if part).lower()
        return any(token in haystack for token in _BROWSERISH_TOOL_TOKENS)

    def _build_session(self, row: sqlite3.Row) -> ObservedSession:
        return ObservedSession(
            session_id=str(row["id"]),
            directory=str(row["directory"]),
            title=str(row["title"]) if row["title"] is not None else None,
            parent_id=str(row["parent_id"]) if row["parent_id"] is not None else None,
            time_created_ms=int(row["time_created"]),
            time_updated_ms=int(row["time_updated"]),
        )

    def _execute(self, query: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
        if not self.db_path.exists():
            return []
        connection = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            return connection.execute(query, params).fetchall()
        finally:
            connection.close()

    def _decode_json(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, str):
            return {}
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}

    def _trim(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 1].rstrip() + "…"
