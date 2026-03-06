from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator

from app.fixtures.build_fixtures import main as build_fixtures
from app.protocol.validation import validate_server_message

TIMELINE_DIR = Path(__file__).resolve().parent / "timelines"


async def replay_timeline(name: str = "happy_path") -> AsyncIterator[dict]:
    build_fixtures()
    timeline_path = TIMELINE_DIR / f"{name}.json"
    entries = json.loads(timeline_path.read_text())
    for entry in entries:
        await asyncio.sleep(entry["delay_ms"] / 1000)
        yield validate_server_message(entry["message"])


def main() -> None:
    build_fixtures()
    for path in sorted(TIMELINE_DIR.glob("*.json")):
        print(path.name)


if __name__ == "__main__":
    main()
