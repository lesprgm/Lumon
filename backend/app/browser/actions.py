from __future__ import annotations

import asyncio
import contextlib
import urllib.parse
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import VIEWPORT_HEIGHT, VIEWPORT_WIDTH
from app.protocol.enums import (
    ActionType,
    AgentKind,
    AgentRuntimeState,
    RiskLevel,
    SubagentSource,
    VisibilityMode,
)
from app.utils.ids import new_id, utc_timestamp

EmitEvent = Callable[[dict[str, Any]], Awaitable[None]]
EmitWorker = Callable[[dict[str, Any]], Awaitable[None]]
EmitBrowserContext = Callable[[dict[str, Any]], Awaitable[None]]
GateCheck = Callable[[], Awaitable[None]]

PRE_ACTION_DELAY_SECONDS: dict[ActionType, float] = {
    ActionType.NAVIGATE: 0.8,
    ActionType.CLICK: 0.8,
    ActionType.TYPE: 0.6,
    ActionType.SCROLL: 0.4,
    ActionType.READ: 0.14,
    ActionType.SPAWN_SUBAGENT: 0.12,
    ActionType.SUBAGENT_RESULT: 0.12,
}

POST_ACTION_DELAY_SECONDS: dict[ActionType, float] = {
    ActionType.NAVIGATE: 1.2,
    ActionType.CLICK: 0.5,
    ActionType.TYPE: 0.5,
    ActionType.SCROLL: 0.4,
    ActionType.READ: 0.42,
    ActionType.SPAWN_SUBAGENT: 0.2,
    ActionType.SUBAGENT_RESULT: 0.26,
}

TARGET_RESOLUTION_TIMEOUT_SECONDS = 0.5
TYPE_ACTION_TIMEOUT_SECONDS = 3.0
VALUE_READ_TIMEOUT_SECONDS = 0.5


async def _noop_emit_browser_context(_payload: dict[str, Any]) -> None:
    return None


class BrowserActionLayer:
    def __init__(
        self,
        *,
        session_id: str,
        adapter_id: str,
        adapter_run_id: str,
        page: Any,
        emit_event: EmitEvent,
        emit_worker_update: EmitWorker,
        emit_browser_context: EmitBrowserContext = _noop_emit_browser_context,
        event_seq_supplier: Callable[[], int],
        gate_check: GateCheck,
        frame_sync: Callable[[], asyncio.Event] | None = None,
    ) -> None:
        self.session_id = session_id
        self.adapter_id = adapter_id
        self.adapter_run_id = adapter_run_id
        self.page = page
        self.emit_event = emit_event
        self.emit_worker_update = emit_worker_update
        self.emit_browser_context = emit_browser_context
        self.next_event_seq = event_seq_supplier
        self.gate_check = gate_check
        self._frame_sync = frame_sync

    async def navigate(
        self,
        url: str,
        *,
        html_content: str | None = None,
        summary_text: str = "Opening travel site",
        intent: str | None = None,
        fast: bool = False,
    ) -> None:
        await self.gate_check()
        cursor, target_rect, meta = await self._target_for_selector("body")
        await self._emit_event(
            agent_id="main_001",
            agent_kind=AgentKind.MAIN,
            visibility_mode=VisibilityMode.FOREGROUND,
            action_type=ActionType.NAVIGATE,
            state=AgentRuntimeState.NAVIGATING,
            summary_text=summary_text,
            intent=intent or f"Navigate to {url}",
            cursor=cursor,
            target_rect=target_rect,
            meta={**meta, "url": url, "fast": fast},
        )
        if not fast:
            await self._pause_before_action(ActionType.NAVIGATE)
        wait_until = "domcontentloaded" if fast else "load"
        if html_content is not None:
            encoded = urllib.parse.quote(html_content)
            await self.page.goto(
                f"data:text/html;charset=utf-8,{encoded}", wait_until=wait_until
            )
        else:
            await self.page.goto(url, wait_until=wait_until)
        await self.refresh_browser_context()
        if not fast:
            await self._pause_after_action(ActionType.NAVIGATE)
            await self._wait_for_frame()

    async def click(
        self, selector: str, summary_text: str, intent: str, risky: bool = False
    ) -> dict[str, Any]:
        await self.gate_check()
        locator = self.page.locator(selector).first
        before_url = str(getattr(self.page, "url", ""))
        before_focus = await self._active_element_summary()
        cursor, target_rect, meta = await self._target_for_selector(selector)
        await self._emit_event(
            agent_id="main_001",
            agent_kind=AgentKind.MAIN,
            visibility_mode=VisibilityMode.FOREGROUND,
            action_type=ActionType.CLICK,
            state=AgentRuntimeState.CLICKING,
            summary_text=summary_text,
            intent=intent,
            cursor=cursor,
            target_rect=target_rect,
            risk_level=RiskLevel.HIGH if risky else RiskLevel.NONE,
            meta=meta,
        )
        await self._pause_before_action(ActionType.CLICK, selector=selector)
        await locator.click(force=True, timeout=5000)
        await self._pause_after_action(ActionType.CLICK)
        await self._wait_for_frame()
        return {
            "before_url": before_url,
            "after_url": str(getattr(self.page, "url", "")),
            "focus_after": await self._active_element_summary(),
            "focus_changed": before_focus != await self._active_element_summary(),
        }

    async def type_text(
        self,
        selector: str,
        value: str,
        summary_text: str,
        intent: str,
        masked: bool = True,
    ) -> dict[str, Any]:
        await self.gate_check()
        locator = self.page.locator(selector).first
        cursor, target_rect, meta = await self._target_for_selector(selector)
        await self._emit_event(
            agent_id="main_001",
            agent_kind=AgentKind.MAIN,
            visibility_mode=VisibilityMode.FOREGROUND,
            action_type=ActionType.TYPE,
            state=AgentRuntimeState.TYPING,
            summary_text=summary_text,
            intent=intent,
            cursor=cursor,
            target_rect=target_rect,
            meta={
                **meta,
                "masked": masked,
                "text_mask": "***" if masked else None,
            },
        )
        await self._pause_before_action(ActionType.TYPE, selector=selector)
        await self._type_value(locator, value)
        await self._pause_after_action(ActionType.TYPE)
        value_after = None
        with contextlib.suppress(Exception):
            if hasattr(locator, "input_value"):
                value_after = await asyncio.wait_for(
                    locator.input_value(), timeout=VALUE_READ_TIMEOUT_SECONDS
                )
            elif hasattr(locator, "evaluate"):
                value_after = await asyncio.wait_for(
                    locator.evaluate("(node) => ('value' in node ? node.value : null)"),
                    timeout=VALUE_READ_TIMEOUT_SECONDS,
                )
        return {"value_after": value_after}

    async def scroll_by(
        self, delta_y: int, summary_text: str, intent: str
    ) -> dict[str, Any]:
        await self.gate_check()
        before_scroll = await self._scroll_position()
        cursor = {"x": VIEWPORT_WIDTH // 2, "y": VIEWPORT_HEIGHT // 2}
        await self._emit_event(
            agent_id="main_001",
            agent_kind=AgentKind.MAIN,
            visibility_mode=VisibilityMode.FOREGROUND,
            action_type=ActionType.SCROLL,
            state=AgentRuntimeState.SCROLLING,
            summary_text=summary_text,
            intent=intent,
            cursor=cursor,
            target_rect=None,
            meta={
                "delta_y": delta_y,
                "selector": None,
                "wrapper_sequence": self._wrapper_sequence(),
                "fallback_cursor": False,
            },
        )
        await self._pause_before_action(ActionType.SCROLL)
        await self.page.mouse.wheel(0, delta_y)
        await self._pause_after_action(ActionType.SCROLL)
        return {
            "before_scroll_y": before_scroll,
            "after_scroll_y": await self._scroll_position(),
        }

    async def read_region(
        self, selector: str, summary_text: str, intent: str
    ) -> dict[str, Any]:
        await self.gate_check()
        cursor, target_rect, meta = await self._target_for_selector(selector)
        await self._emit_event(
            agent_id="main_001",
            agent_kind=AgentKind.MAIN,
            visibility_mode=VisibilityMode.FOREGROUND,
            action_type=ActionType.READ,
            state=AgentRuntimeState.READING,
            summary_text=summary_text,
            intent=intent,
            cursor=cursor,
            target_rect=target_rect,
            meta=meta,
        )
        await self._pause_after_action(ActionType.READ)
        return {"target_rect": target_rect, "cursor": cursor}

    async def spawn_same_scene_subagent(self) -> None:
        await self._emit_event(
            agent_id="subagent_001",
            parent_agent_id="main_001",
            agent_kind=AgentKind.SAME_SCENE_SUBAGENT,
            visibility_mode=VisibilityMode.SAME_SCENE_VISIBLE,
            action_type=ActionType.SPAWN_SUBAGENT,
            state=AgentRuntimeState.HANDOFF,
            summary_text="Helper checks hotel ratings",
            intent="Verify rating quality",
            cursor={"x": 920, "y": 420},
            target_rect={"x": 860, "y": 380, "width": 140, "height": 60},
            subagent_source=SubagentSource.SIMULATED,
            meta={"wrapper_sequence": self._wrapper_sequence()},
        )
        await self._pause_after_action(ActionType.SPAWN_SUBAGENT)

    async def complete_same_scene_subagent(self) -> None:
        await self._emit_event(
            agent_id="subagent_001",
            parent_agent_id="main_001",
            agent_kind=AgentKind.SAME_SCENE_SUBAGENT,
            visibility_mode=VisibilityMode.SAME_SCENE_VISIBLE,
            action_type=ActionType.SUBAGENT_RESULT,
            state=AgentRuntimeState.DONE,
            summary_text="Helper returned rating summary",
            intent="Return quality findings",
            cursor={"x": 920, "y": 420},
            target_rect={"x": 860, "y": 380, "width": 140, "height": 60},
            subagent_source=SubagentSource.SIMULATED,
            meta={"wrapper_sequence": self._wrapper_sequence()},
        )
        await self._pause_after_action(ActionType.SUBAGENT_RESULT)

    async def spawn_background_worker(self) -> None:
        await self.emit_worker_update(
            {
                "session_id": self.session_id,
                "adapter_id": self.adapter_id,
                "adapter_run_id": self.adapter_run_id,
                "agent_id": "worker_001",
                "summary_text": "Comparing hotel ratings in the background",
                "state": "running",
                "timestamp": utc_timestamp(),
            }
        )

    async def refresh_browser_context(self) -> dict[str, Any]:
        current_url = str(
            getattr(self.page, "url", None) or getattr(self.page, "last_goto", "") or ""
        )
        parsed = urllib.parse.urlparse(current_url)
        domain = parsed.hostname or "unknown"
        environment_type = self._environment_type_for_domain(domain)
        title = None
        with contextlib.suppress(Exception):
            if hasattr(self.page, "title"):
                title = await self.page.title()
        payload = {
            "session_id": self.session_id,
            "adapter_id": self.adapter_id,
            "adapter_run_id": self.adapter_run_id,
            "url": current_url,
            "title": title or None,
            "domain": domain,
            "environment_type": environment_type,
            "timestamp": utc_timestamp(),
        }
        await self.emit_browser_context(payload)
        return payload

    async def inspect_actionable_elements(
        self, *, limit: int = 12
    ) -> list[dict[str, Any]]:
        if not hasattr(self.page, "evaluate"):
            return []
        result = await self.page.evaluate(
            """
            ({ limit }) => {
              function visible(el) {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.visibility !== "hidden" &&
                  style.display !== "none" &&
                  rect.width > 4 &&
                  rect.height > 4 &&
                  rect.bottom >= 0 &&
                  rect.right >= 0 &&
                  rect.top <= window.innerHeight &&
                  rect.left <= window.innerWidth;
              }
              function escapeAttr(value) {
                return String(value).replace(/\\\\/g, "\\\\\\\\").replace(/"/g, '\\"');
              }
              function selectorFor(el) {
                if (el.id) return `#${CSS.escape(el.id)}`;
                const tag = el.tagName.toLowerCase();
                const name = el.getAttribute("name");
                if (name) return `${tag}[name="${escapeAttr(name)}"]`;
                const aria = el.getAttribute("aria-label");
                if (aria) return `${tag}[aria-label="${escapeAttr(aria)}"]`;
                const placeholder = el.getAttribute("placeholder");
                if (placeholder) return `${tag}[placeholder="${escapeAttr(placeholder)}"]`;
                if (tag === "a") {
                  const href = el.getAttribute("href");
                  if (href) return `a[href="${escapeAttr(href)}"]`;
                }
                const siblings = el.parentElement ? Array.from(el.parentElement.children).filter((child) => child.tagName === el.tagName) : [el];
                const index = Math.max(0, siblings.indexOf(el)) + 1;
                return `${tag}:nth-of-type(${index})`;
              }
              function labelFor(el) {
                return (
                  el.getAttribute("aria-label") ||
                  el.getAttribute("placeholder") ||
                  el.getAttribute("name") ||
                  el.innerText ||
                  el.textContent ||
                  el.value ||
                  el.id ||
                  el.tagName.toLowerCase()
                ).trim().slice(0, 120);
              }
              const nodes = Array.from(document.querySelectorAll('a,button,input,textarea,select,[role="button"],[contenteditable="true"]'))
                .filter(visible)
                .slice(0, limit)
                .map((el) => {
                  const rect = el.getBoundingClientRect();
                  const tag = el.tagName.toLowerCase();
                  const inputType = tag === "input" ? (el.getAttribute("type") || "text").toLowerCase() : null;
                  const clickable = tag === "a" || tag === "button" || el.getAttribute("role") === "button" || tag === "input";
                  const typeable = tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
                  const isSensitive = ["password", "email"].includes(inputType || "") || /password|token|secret/i.test(labelFor(el));
                  return {
                    label: labelFor(el),
                    role: el.getAttribute("role") || tag,
                    selector: selectorFor(el),
                    typeable,
                    clickable,
                    input_type: inputType,
                    sensitive: isSensitive,
                    value_preview: typeable && !isSensitive && typeof el.value === "string" ? String(el.value).slice(0, 80) : null,
                    bbox: {
                      x: Math.round(rect.x),
                      y: Math.round(rect.y),
                      width: Math.round(rect.width),
                      height: Math.round(rect.height),
                    },
                  };
                });
              return nodes;
            }
            """,
            {"limit": limit},
        )
        return list(result or [])

    async def current_status(self) -> dict[str, Any]:
        context = await self.refresh_browser_context()
        return {
            "browser_context": context,
            "active_element": await self._active_element_summary(),
            "scroll_y": await self._scroll_position(),
        }

    async def complete_background_worker(self) -> None:
        await self.emit_worker_update(
            {
                "session_id": self.session_id,
                "adapter_id": self.adapter_id,
                "adapter_run_id": self.adapter_run_id,
                "agent_id": "worker_001",
                "summary_text": "Background comparison complete",
                "state": "completed",
                "timestamp": utc_timestamp(),
            }
        )

    def _environment_type_for_domain(self, domain: str) -> str:
        if domain in {"127.0.0.1", "localhost"} or domain.endswith(".local"):
            return "local"
        if domain.endswith("docs") or "docs" in domain:
            return "docs"
        if any(token in domain for token in ("app", "dashboard", "admin", "studio")):
            return "app"
        return "external"

    async def _target_for_selector(
        self, selector: str
    ) -> tuple[dict[str, int], dict[str, int] | None, dict[str, Any]]:
        locator = self.page.locator(selector).first
        target_resolution_error: str | None = None
        try:
            box = await asyncio.wait_for(
                locator.bounding_box(), timeout=TARGET_RESOLUTION_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            box = None
            target_resolution_error = "timeout"
        except Exception as exc:
            box = None
            target_resolution_error = str(exc)
        if box is None:
            fallback = {"x": VIEWPORT_WIDTH // 2, "y": VIEWPORT_HEIGHT // 2}
            meta = {
                "selector": selector,
                "wrapper_sequence": self._wrapper_sequence(),
                "fallback_cursor": True,
            }
            if target_resolution_error is not None:
                meta["target_resolution_error"] = target_resolution_error
            return (
                fallback,
                None,
                meta,
            )
        rect = {
            "x": max(0, min(VIEWPORT_WIDTH, int(round(box["x"])))),
            "y": max(0, min(VIEWPORT_HEIGHT, int(round(box["y"])))),
            "width": max(1, min(VIEWPORT_WIDTH, int(round(box["width"])))),
            "height": max(1, min(VIEWPORT_HEIGHT, int(round(box["height"])))),
        }
        cursor = {
            "x": max(0, min(VIEWPORT_WIDTH, rect["x"] + rect["width"] // 2)),
            "y": max(0, min(VIEWPORT_HEIGHT, rect["y"] + rect["height"] // 2)),
        }
        return (
            cursor,
            rect,
            {
                "selector": selector,
                "wrapper_sequence": self._wrapper_sequence(),
                "fallback_cursor": False,
            },
        )

    def _wrapper_sequence(self) -> list[str]:
        return [
            "gate_check",
            "target_resolution",
            "bbox/cursor_derivation",
            "pre_event",
            "playwright_action",
            "post_event",
        ]

    async def _emit_event(
        self,
        *,
        agent_id: str,
        agent_kind: AgentKind,
        visibility_mode: VisibilityMode,
        action_type: ActionType,
        state: AgentRuntimeState,
        summary_text: str,
        intent: str,
        cursor: dict[str, int] | None = None,
        target_rect: dict[str, int] | None = None,
        risk_level: RiskLevel = RiskLevel.NONE,
        parent_agent_id: str | None = None,
        subagent_source: SubagentSource | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "event_seq": self.next_event_seq(),
            "event_id": new_id("evt"),
            "source_event_id": new_id("src"),
            "timestamp": utc_timestamp(),
            "session_id": self.session_id,
            "adapter_id": self.adapter_id,
            "adapter_run_id": self.adapter_run_id,
            "agent_id": agent_id,
            "parent_agent_id": parent_agent_id,
            "agent_kind": agent_kind.value,
            "environment_id": "env_browser_main",
            "visibility_mode": visibility_mode.value,
            "action_type": action_type.value,
            "state": state.value,
            "summary_text": summary_text,
            "intent": intent,
            "risk_level": risk_level.value,
            "subagent_source": subagent_source.value if subagent_source else None,
            "cursor": cursor,
            "target_rect": target_rect,
            "meta": meta or {},
        }
        await self.emit_event(payload)

    async def _active_element_summary(self) -> dict[str, Any] | None:
        if not hasattr(self.page, "evaluate"):
            return None
        with contextlib.suppress(Exception):
            return await self.page.evaluate(
                """
                () => {
                  const el = document.activeElement;
                  if (!el) return null;
                  return {
                    tag: el.tagName ? el.tagName.toLowerCase() : null,
                    id: el.id || null,
                    name: el.getAttribute?.("name") || null,
                    ariaLabel: el.getAttribute?.("aria-label") || null,
                  };
                }
                """,
            )
        return None

    async def _scroll_position(self) -> int:
        if not hasattr(self.page, "evaluate"):
            return 0
        with contextlib.suppress(Exception):
            value = await self.page.evaluate("() => Math.round(window.scrollY || 0)")
            return int(value)
        return 0

    async def _pause_before_action(
        self, action_type: ActionType, selector: str | None = None
    ) -> None:
        if selector and hasattr(self.page, "evaluate"):
            with contextlib.suppress(Exception):
                await self.page.evaluate(
                    """
                    (selector) => {
                      const el = document.querySelector(selector);
                      if (!el) return;
                      const originalStyle = el.style.cssText;
                      el.style.outline = '4px solid #ff4444';
                      el.style.outlineOffset = '2px';
                      el.style.backgroundColor = 'rgba(255, 68, 68, 0.1)';
                      el.style.transition = 'all 0.2s ease-in-out';
                      setTimeout(() => {
                        el.style.cssText = originalStyle;
                      }, 1200);
                    }
                    """,
                    selector,
                )
        delay = PRE_ACTION_DELAY_SECONDS.get(action_type, 0.0)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _pause_after_action(self, action_type: ActionType) -> None:
        delay = POST_ACTION_DELAY_SECONDS.get(action_type, 0.0)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _wait_for_frame(self) -> None:
        if self._frame_sync is None:
            return
        frame_event = self._frame_sync()
        if frame_event is None:
            return
        try:
            await asyncio.wait_for(frame_event.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

    async def _type_value(self, locator: Any, value: str) -> None:
        if hasattr(locator, "press_sequentially"):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    locator.fill(""), timeout=TYPE_ACTION_TIMEOUT_SECONDS
                )
            await asyncio.wait_for(
                locator.press_sequentially(value, delay=85),
                timeout=TYPE_ACTION_TIMEOUT_SECONDS,
            )
            return
        if hasattr(locator, "type"):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    locator.fill(""), timeout=TYPE_ACTION_TIMEOUT_SECONDS
                )
            await asyncio.wait_for(
                locator.type(value, delay=85), timeout=TYPE_ACTION_TIMEOUT_SECONDS
            )
            return
        await asyncio.wait_for(locator.fill(value), timeout=TYPE_ACTION_TIMEOUT_SECONDS)
