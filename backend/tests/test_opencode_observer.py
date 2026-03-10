from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.opencode_observer import OpenCodeSQLiteObserver


def _seed_observer_db(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE session (
                id TEXT PRIMARY KEY,
                directory TEXT,
                title TEXT,
                parent_id TEXT,
                time_created INTEGER,
                time_updated INTEGER
            );
            CREATE TABLE message (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                data TEXT
            );
            CREATE TABLE part (
                id TEXT PRIMARY KEY,
                message_id TEXT,
                session_id TEXT,
                time_created INTEGER,
                time_updated INTEGER,
                data TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO session (id, directory, title, parent_id, time_created, time_updated) VALUES (?, ?, ?, ?, ?, ?)",
            ("ses_new_001", str(Path("/tmp/example").resolve()), "Search the docs", None, 100, 150),
        )
        connection.execute(
            "INSERT INTO message (id, session_id, data) VALUES (?, ?, ?)",
            ("msg_user_001", "ses_new_001", json.dumps({"role": "user"})),
        )
        connection.execute(
            "INSERT INTO message (id, session_id, data) VALUES (?, ?, ?)",
            ("msg_assistant_001", "ses_new_001", json.dumps({"role": "assistant"})),
        )
        connection.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
            ("prt_user_001", "msg_user_001", "ses_new_001", 110, 110, json.dumps({"type": "text", "text": "Search the web for Lumon docs"})),
        )
        connection.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "prt_tool_001",
                "msg_assistant_001",
                "ses_new_001",
                120,
                120,
                json.dumps(
                    {
                        "type": "tool",
                        "tool": "open_url",
                        "state": {
                            "status": "running",
                            "title": "Search the web for Lumon docs",
                            "metadata": {"preview": "Opening browser and running a search"},
                        },
                    }
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def test_observer_discovers_new_session_and_parts(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    _seed_observer_db(db_path)
    observer = OpenCodeSQLiteObserver(db_path)

    session = observer.find_session(str(Path("/tmp/example")), since_ms=90, exclude_session_ids=set())
    assert session is not None
    assert session.session_id == "ses_new_001"
    assert session.title == "Search the docs"

    parts = observer.load_parts(session.session_id)
    assert [part.part_id for part in parts] == ["prt_user_001", "prt_tool_001"]


def test_observer_maps_user_prompt_and_browser_tool(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    _seed_observer_db(db_path)
    observer = OpenCodeSQLiteObserver(db_path)
    parts = observer.load_parts("ses_new_001")

    user_event = observer.part_to_observer_event(parts[0])
    assert user_event == {
        "source_event_id": "prt_user_001",
        "event_type": "tool_start",
        "state": "thinking",
        "summary_text": "OpenCode received: Search the web for Lumon docs",
        "intent": "Search the web for Lumon docs",
        "task_text": "Search the web for Lumon docs",
        "meta": {"part_type": "text", "role": "user"},
    }

    tool_event = observer.part_to_observer_event(parts[1])
    assert tool_event is not None
    assert tool_event["event_type"] == "navigate"
    assert tool_event["state"] == "navigating"
    assert tool_event["meta"]["tool_name"] == "open_url"
    assert tool_event["meta"]["browser_candidate"] is True


def test_observer_prefers_explicit_session_id(tmp_path: Path) -> None:
    db_path = tmp_path / "opencode.db"
    _seed_observer_db(db_path)
    observer = OpenCodeSQLiteObserver(db_path)

    session = observer.find_session(
        str(Path("/tmp/example")),
        since_ms=999999,
        exclude_session_ids={"ses_new_001"},
        preferred_session_id="ses_new_001",
    )

    assert session is not None
    assert session.session_id == "ses_new_001"
