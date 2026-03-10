from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.browser.actions as actions_module
from app.browser.actions import BrowserActionLayer
from app.protocol.normalizer import normalize_external_event


class FakeLocator:
    def __init__(self, box: dict[str, float] | None = None) -> None:
        self._box = box
        self.clicked = False
        self.filled: str | None = None

    @property
    def first(self) -> "FakeLocator":
        return self

    async def bounding_box(self) -> dict[str, float] | None:
        return self._box

    async def click(self) -> None:
        self.clicked = True

    async def fill(self, value: str) -> None:
        self.filled = value


class SlowLocator(FakeLocator):
    async def bounding_box(self) -> dict[str, float] | None:
        await asyncio.sleep(0.05)
        return self._box


class FakeMouse:
    def __init__(self) -> None:
        self.last_delta = 0

    async def wheel(self, _x: int, delta_y: int) -> None:
        self.last_delta = delta_y


class FakePage:
    def __init__(self) -> None:
        self.locators = {
            "#button": FakeLocator({"x": 100, "y": 200, "width": 80, "height": 40}),
            "#input": FakeLocator({"x": 300, "y": 250, "width": 220, "height": 44}),
            "body": FakeLocator({"x": 0, "y": 0, "width": 1280, "height": 800}),
        }
        self.mouse = FakeMouse()
        self.last_goto: str | None = None
        self.evaluate_calls: list[tuple[str, dict]] = []

    def locator(self, selector: str) -> FakeLocator:
        return self.locators.setdefault(selector, FakeLocator())

    async def goto(self, url: str, wait_until: str = "load") -> None:
        self.last_goto = f"{url}|{wait_until}"

    async def evaluate(self, script: str, payload: dict) -> None:
        self.evaluate_calls.append((script, payload))


@pytest.mark.asyncio
async def test_wrapper_emits_dom_backed_click_event() -> None:
    events: list[dict] = []
    workers: list[dict] = []
    page = FakePage()
    layer = BrowserActionLayer(
        session_id="sess_1",
        adapter_id="playwright_native",
        adapter_run_id="run_1",
        page=page,
        emit_event=lambda payload: _append(events, payload),
        emit_worker_update=lambda payload: _append(workers, payload),
        event_seq_supplier=iter(range(1, 100)).__next__,
        gate_check=lambda: asyncio.sleep(0),
    )

    await layer.click("#button", "Clicking CTA", "Open the results")

    assert events[0]["summary_text"] == "Clicking CTA"
    assert events[0]["cursor"] == {"x": 140, "y": 220}
    assert events[0]["target_rect"] == {"x": 100, "y": 200, "width": 80, "height": 40}
    assert events[0]["meta"]["wrapper_sequence"][0] == "gate_check"
    assert page.locator("#button").clicked is True


@pytest.mark.asyncio
async def test_type_text_masks_values() -> None:
    events: list[dict] = []
    page = FakePage()
    layer = BrowserActionLayer(
        session_id="sess_1",
        adapter_id="playwright_native",
        adapter_run_id="run_1",
        page=page,
        emit_event=lambda payload: _append(events, payload),
        emit_worker_update=lambda payload: _append([], payload),
        event_seq_supplier=iter(range(1, 100)).__next__,
        gate_check=lambda: asyncio.sleep(0),
    )

    await layer.type_text("#input", "secret-value", "Typing input", "Fill secure field")

    assert events[0]["meta"]["masked"] is True
    assert events[0]["meta"]["text_mask"] == "***"
    assert "secret-value" not in str(events[0])
    assert page.locator("#input").filled == "secret-value"


@pytest.mark.asyncio
async def test_click_falls_back_when_target_box_missing() -> None:
    events: list[dict] = []
    page = FakePage()
    page.locators["#missing"] = FakeLocator(None)
    layer = BrowserActionLayer(
        session_id="sess_1",
        adapter_id="playwright_native",
        adapter_run_id="run_1",
        page=page,
        emit_event=lambda payload: _append(events, payload),
        emit_worker_update=lambda payload: _append([], payload),
        event_seq_supplier=iter(range(1, 100)).__next__,
        gate_check=lambda: asyncio.sleep(0),
    )

    await layer.click("#missing", "Clicking fallback target", "Attempt fallback click")

    assert events[0]["cursor"] == {"x": 640, "y": 400}
    assert events[0]["target_rect"] is None
    assert events[0]["meta"]["fallback_cursor"] is True
    assert page.locator("#missing").clicked is True


@pytest.mark.asyncio
async def test_target_box_is_clamped_to_viewport() -> None:
    events: list[dict] = []
    page = FakePage()
    page.locators["#offscreen"] = FakeLocator(
        {"x": -20, "y": 790, "width": 2000, "height": 50}
    )
    layer = BrowserActionLayer(
        session_id="sess_1",
        adapter_id="playwright_native",
        adapter_run_id="run_1",
        page=page,
        emit_event=lambda payload: _append(events, payload),
        emit_worker_update=lambda payload: _append([], payload),
        event_seq_supplier=iter(range(1, 100)).__next__,
        gate_check=lambda: asyncio.sleep(0),
    )

    await layer.read_region(
        "#offscreen", "Inspecting offscreen region", "Clamp the selection"
    )

    assert events[0]["target_rect"] == {"x": 0, "y": 790, "width": 1280, "height": 50}
    assert events[0]["cursor"] == {"x": 640, "y": 800}


@pytest.mark.asyncio
async def test_target_resolution_timeout_falls_back_quickly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict] = []
    page = FakePage()
    page.locators["#slow"] = SlowLocator(
        {"x": 120, "y": 160, "width": 80, "height": 40}
    )
    monkeypatch.setattr(actions_module, "TARGET_RESOLUTION_TIMEOUT_SECONDS", 0.01)
    layer = BrowserActionLayer(
        session_id="sess_1",
        adapter_id="playwright_native",
        adapter_run_id="run_1",
        page=page,
        emit_event=lambda payload: _append(events, payload),
        emit_worker_update=lambda payload: _append([], payload),
        event_seq_supplier=iter(range(1, 100)).__next__,
        gate_check=lambda: asyncio.sleep(0),
    )

    await layer.read_region(
        "#slow", "Inspecting slow region", "Avoid waiting on bounding box forever"
    )

    assert events[0]["target_rect"] is None
    assert events[0]["cursor"] == {"x": 640, "y": 400}
    assert events[0]["meta"]["fallback_cursor"] is True
    assert events[0]["meta"]["target_resolution_error"] == "timeout"


def test_normalize_external_event_preserves_contract_fields() -> None:
    normalized = normalize_external_event(
        {
            "event_type": "tool_start",
            "state": "thinking",
            "summary_text": "Reading page",
            "intent": "Inspect current page",
            "cursor": {"x": 10, "y": 20},
            "target_rect": {"x": 0, "y": 0, "width": 20, "height": 20},
            "subagent": False,
        },
        session_id="sess_1",
        adapter_id="claude_code",
        adapter_run_id="run_1",
        event_seq=7,
    )

    assert normalized["adapter_id"] == "claude_code"
    assert normalized["adapter_run_id"] == "run_1"
    assert normalized["event_seq"] == 7
    assert normalized["summary_text"] == "Reading page"


def test_normalize_external_event_defaults_unknown_values() -> None:
    normalized = normalize_external_event(
        {
            "event_type": "something_new",
            "state": "mystery",
            "label": "Adapter fallback label",
            "risk_level": "severe",
        },
        session_id="sess_1",
        adapter_id="opencode",
        adapter_run_id="run_1",
        event_seq=8,
    )

    assert normalized["action_type"] == "read"
    assert normalized["state"] == "thinking"
    assert normalized["summary_text"] == "Adapter fallback label"
    assert normalized["risk_level"] == "none"


async def _append(collection: list[dict], payload: dict) -> None:
    collection.append(payload)
