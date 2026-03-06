from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from typing import Iterator
from uuid import uuid4


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sequence(start: int = 1) -> Iterator[int]:
    return count(start)
