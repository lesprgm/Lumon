from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "lumon_opencode.py"
if str(SCRIPT_PATH.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_PATH.parent))

_SPEC = importlib.util.spec_from_file_location("lumon_opencode_script", SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
lumon_opencode = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(lumon_opencode)


def test_parse_args_defaults_to_observe_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["lumon_opencode.py", "--", "."])

    args = lumon_opencode.parse_args()

    assert args.web_mode == "observe_only"


def test_parse_args_legacy_web_bridge_maps_to_delegate_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["lumon_opencode.py", "--web-bridge", "playwright_native", "--", "."])

    args = lumon_opencode.parse_args()

    assert args.web_mode == "delegate_playwright"


def test_parse_args_supports_auto_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["lumon_opencode.py", "--web-mode", "delegate_playwright", "--auto-delegate", "--", "."])

    args = lumon_opencode.parse_args()

    assert args.web_mode == "delegate_playwright"
    assert args.auto_delegate is True


@pytest.mark.asyncio
async def test_stream_observed_parts_uses_adaptive_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded_sleeps: list[float] = []

    async def fake_sleep(duration: float) -> None:
        recorded_sleeps.append(round(duration, 3))

    monkeypatch.setattr(lumon_opencode.asyncio, "sleep", fake_sleep)

    process = SimpleNamespace(returncode=None)

    class FakeObserver:
        def __init__(self) -> None:
            self.calls = 0

        def load_parts(self, session_id: str, *, after_rowid: int = 0):
            self.calls += 1
            if self.calls <= 2:
                return []
            if self.calls == 3:
                process.returncode = 0
                return [SimpleNamespace(rowid=4)]
            return []

        def part_to_observer_event(self, observed_part):
            return {
                "source_event_id": "part_001",
                "event_type": "tool_start",
                "state": "thinking",
                "summary_text": "Observed first tool event",
                "intent": "Observe the first tool event",
                "meta": {"part_type": "tool"},
            }

    sent_messages: list[dict] = []

    class FakeWebSocket:
        async def send(self, payload: str) -> None:
            sent_messages.append(json.loads(payload))

    await lumon_opencode.stream_observed_parts(
        FakeWebSocket(),
        FakeObserver(),
        "ses_observed_001",
        min_poll_interval=0.1,
        max_poll_interval=0.6,
        process=process,
    )

    assert recorded_sleeps[:2] == [0.1, 0.15]
    assert sent_messages == [
        {
            "type": "observer_event",
            "payload": {
                "source_event_id": "part_001",
                "event_type": "tool_start",
                "state": "thinking",
                "summary_text": "Observed first tool event",
                "intent": "Observe the first tool event",
                "meta": {"part_type": "tool"},
            },
        }
    ]
