from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import urllib.parse
from collections.abc import Sequence
from collections import deque
from itertools import count
from typing import Any, Literal

from app.adapters.base import AdapterConnector
from app.opencode_signals import classify_signal_detailed, task_mentions_browser
from app.protocol.enums import ErrorCode, SessionState
from app.protocol.models import BrowserCommandRecord, BrowserCommandRequest
from app.protocol.normalizer import normalize_external_event
from app.utils.ids import new_id

WebBridgeId = Literal["playwright_native"]
WebModeId = Literal["observe_only", "delegate_playwright"]
COMMAND_DELEGATE_READY_TIMEOUT_SECONDS = float(os.getenv("LUMON_COMMAND_DELEGATE_READY_TIMEOUT_SECONDS", "45"))

_URL_PATTERN = re.compile(r"https?://[^\s)>\"]+")


class OpenCodeConnector(AdapterConnector):
    adapter_id = "opencode"
    base_capabilities = {
        "supports_pause": False,
        "supports_approval": False,
        "supports_takeover": False,
        "supports_frames": False,
    }

    def __init__(self, runtime: "SessionRuntimeProtocol") -> None:
        self.runtime = runtime
        self.adapter_run_id = new_id("run")
        self.event_seq = count(1)
        self.bridge_frame_seq = count(1)
        self.observer_mode = False
        self.observed_session_id: str | None = None
        self.pending_observer_completion: tuple[str, str] | None = None
        self._observed_source_event_ids: deque[str] = deque(maxlen=2048)
        self._observed_source_event_id_set: set[str] = set()
        self.pending_bridge_offer: dict[str, Any] | None = None
        self.declined_bridge_source_ids: set[str] = set()
        self.run_task: asyncio.Task[None] | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.selected_web_mode: WebModeId = "observe_only"
        self.selected_web_bridge: WebBridgeId | None = None
        self.active_web_bridge: WebBridgeId | None = None
        self.bridge_connector: AdapterConnector | None = None
        self.bridge_runtime: _BridgeRuntimeProxy | None = None
        self.bridge_completion: asyncio.Event | None = None
        self.bridge_result: tuple[str, str] | None = None
        self.auto_delegate = False

    @property
    def capabilities(self) -> dict[str, bool]:
        merged = dict(self.base_capabilities)
        if self.bridge_connector is None:
            return merged
        for key, value in self.bridge_connector.capabilities.items():
            merged[key] = bool(merged.get(key, False) or value)
        return merged

    async def start_task(
        self,
        task_text: str,
        demo_mode: bool = False,
        web_mode: str | None = None,
        web_bridge: str | None = None,
        auto_delegate: bool = False,
        observer_mode: bool = False,
        observed_session_id: str | None = None,
        bridge_context: dict[str, Any] | None = None,
    ) -> None:
        _ = bridge_context
        self.runtime.task_text = task_text
        self.runtime.adapter_run_id = self.adapter_run_id
        self.observer_mode = observer_mode
        self.observed_session_id = observed_session_id
        self.pending_observer_completion = None
        self._observed_source_event_ids.clear()
        self._observed_source_event_id_set.clear()
        self.pending_bridge_offer = None
        self.declined_bridge_source_ids.clear()
        self.auto_delegate = auto_delegate
        self.selected_web_mode = self._coerce_web_mode(web_mode, web_bridge, observer_mode)
        self.selected_web_bridge = self._bridge_for_mode(self.selected_web_mode)
        self.active_web_bridge = None
        self.bridge_connector = None
        self.bridge_runtime = None
        self.bridge_completion = None
        self.bridge_result = None
        await self.runtime.transition_to(SessionState.STARTING)
        if observer_mode:
            await self.runtime.transition_to(SessionState.RUNNING)
            await self.runtime.emit_session_state()
            return
        self.run_task = asyncio.create_task(self._run(task_text, demo_mode=demo_mode))

    async def _run(self, task_text: str, *, demo_mode: bool) -> None:
        try:
            await self.runtime.transition_to(SessionState.RUNNING)
            self._emit_runtime_decision(
                reason_code="run_started",
                summary_text="OpenCode run started",
                severity="info",
                mode="demo" if demo_mode else "live",
                cli_available=bool(shutil.which("opencode")),
            )
            if demo_mode:
                self._emit_runtime_decision(
                    reason_code="run_demo_selected",
                    summary_text="OpenCode demo mode selected",
                    severity="info",
                )
                await self._run_demo(task_text)
            elif shutil.which("opencode") is None:
                self._emit_runtime_decision(
                    reason_code="opencode_cli_missing_live",
                    summary_text="OpenCode CLI missing for live mode",
                    severity="error",
                )
                await self.runtime.emit_error(ErrorCode.INVALID_STATE, "OpenCode run failed: opencode CLI not found in PATH")
                await self.runtime.complete_task(status="failed", summary_text="OpenCode adapter run failed")
            else:
                self._emit_runtime_decision(
                    reason_code="run_live_selected",
                    summary_text="OpenCode live mode selected",
                    severity="info",
                )
                await self._run_live(task_text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - runtime guard
            self._emit_runtime_decision(
                reason_code="unexpected_runtime_exception",
                summary_text="OpenCode runtime raised an unexpected exception",
                severity="error",
                error=str(exc),
            )
            await self.runtime.emit_error(ErrorCode.INVALID_STATE, f"OpenCode runtime failed: {exc}")
            await self.runtime.transition_to(SessionState.FAILED)
            await self.runtime.complete_task(status="failed", summary_text="OpenCode adapter run failed")
        finally:
            await self._stop_bridge()

    async def _run_demo(self, task_text: str) -> None:
        raw_events: list[dict[str, Any]] = [
            {
                "type": "session.started",
                "state": "thinking",
                "summary": "OpenCode planning task execution",
                "intent": f"Plan the task: {task_text}",
            },
            {
                "type": "tool_start",
                "state": "reading",
                "summary": "OpenCode reads repository context",
                "intent": "Inspect relevant project files before acting",
            },
        ]

        if self._task_needs_web(task_text):
            raw_events.append(
                {
                    "type": "browser.search",
                    "state": "running",
                    "summary": (
                        f"OpenCode delegated browser work to {self.selected_web_bridge}"
                        if self._delegation_enabled()
                        else "OpenCode entered browser-capable work"
                    ),
                    "intent": task_text,
                }
            )
        else:
            raw_events.append(
                {
                    "type": "tool_start",
                    "state": "typing",
                    "summary": "OpenCode drafts the next change set",
                    "intent": "Prepare a safe edit sequence",
                }
            )

        for raw in raw_events:
            await self.runtime.emit_agent_event(self._normalize_opencode_event(raw))
            await self._maybe_emit_browser_context(raw)
            if self._should_launch_web_bridge(raw, task_text):
                await self._launch_web_bridge(raw, task_text, demo_mode=True)
                bridge_result = await self._wait_for_bridge_completion()
                if bridge_result and bridge_result[0] != "completed":
                    await self.runtime.complete_task(status=bridge_result[0], summary_text=bridge_result[1])
                    return
            await asyncio.sleep(0.35)

        await self.runtime.emit_agent_event(
            self._normalize_opencode_event(
                {
                    "type": "tool_complete",
                    "state": "done",
                    "summary": "OpenCode finished the adapter demo run",
                    "intent": "Return the synthesized outcome to Lumon",
                }
            )
        )
        await self.runtime.complete_task(
            status="completed",
            summary_text="OpenCode adapter demo completed the requested task flow",
        )

    async def _run_live(self, task_text: str) -> None:
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self._build_run_command(task_text),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            self._emit_runtime_decision(
                reason_code="spawn_filenotfound",
                summary_text="OpenCode CLI executable not found at launch",
                severity="error",
            )
            await self.runtime.emit_error(ErrorCode.INVALID_STATE, "OpenCode run failed: opencode CLI not found in PATH")
            await self.runtime.complete_task(status="failed", summary_text="OpenCode adapter run failed")
            return
        except OSError as exc:
            self._emit_runtime_decision(
                reason_code="spawn_oserror",
                summary_text="OpenCode CLI failed to launch",
                severity="error",
                error=str(exc),
            )
            await self.runtime.emit_error(ErrorCode.INVALID_STATE, f"OpenCode run failed: unable to launch opencode CLI ({exc})")
            await self.runtime.complete_task(status="failed", summary_text="OpenCode adapter run failed")
            return
        assert self.process.stdout is not None
        assert self.process.stderr is not None

        saw_event = False
        saw_error_event = False
        last_error_message: str | None = None
        stderr_tail: list[str] = []
        stderr_task = asyncio.create_task(self._collect_stderr(stderr_tail))

        try:
            async for line in self.process.stdout:
                parsed = self._parse_json_line(line)
                if not parsed:
                    continue
                saw_event = True
                if self._is_error_event(parsed):
                    saw_error_event = True
                    last_error_message = self._error_message_for(parsed)
                await self.runtime.emit_agent_event(self._normalize_opencode_event(parsed))
                await self._maybe_emit_browser_context(parsed)
                if self._should_launch_web_bridge(parsed, task_text):
                    await self._launch_web_bridge(parsed, task_text, demo_mode=False)

            return_code = await self.process.wait()
            await stderr_task
        finally:
            self.process = None

        bridge_result = await self._wait_for_bridge_completion()
        if bridge_result and bridge_result[0] != "completed":
            await self.runtime.complete_task(status=bridge_result[0], summary_text=bridge_result[1])
            return

        if return_code != 0:
            detail = stderr_tail[-1] if stderr_tail else f"OpenCode exited with code {return_code}"
            self._emit_runtime_decision(
                reason_code="process_nonzero_exit",
                summary_text="OpenCode CLI exited with non-zero status",
                severity="error",
                return_code=return_code,
                detail=detail,
            )
            await self.runtime.emit_error(ErrorCode.INVALID_STATE, f"OpenCode run failed: {detail}")
            await self.runtime.complete_task(status="failed", summary_text="OpenCode adapter run failed")
            return

        if saw_error_event:
            detail = last_error_message or "OpenCode emitted a structured error event"
            self._emit_runtime_decision(
                reason_code="structured_error_event",
                summary_text="OpenCode emitted a structured error event",
                severity="error",
                detail=detail,
            )
            await self.runtime.emit_error(ErrorCode.INVALID_STATE, f"OpenCode run failed: {detail}")
            await self.runtime.complete_task(status="failed", summary_text="OpenCode adapter run failed")
            return

        if not saw_event:
            await self.runtime.emit_agent_event(
                self._normalize_opencode_event(
                    {
                        "type": "tool_complete",
                        "state": "done",
                        "summary": "OpenCode completed without streamed JSON events",
                        "intent": task_text,
                    }
                )
            )

        await self.runtime.complete_task(status="completed", summary_text="OpenCode adapter run completed")

    def _emit_runtime_decision(self, *, reason_code: str, summary_text: str, severity: str, **extra: Any) -> None:
        emit = getattr(self.runtime, "emit_routing_decision", None)
        if emit is None:
            return
        emit(
            {
                "timestamp": self.runtime.timestamp(),
                "session_id": self.runtime.session_id,
                "adapter_id": self.adapter_id,
                "adapter_run_id": self.adapter_run_id,
                "trace_id": str(getattr(self.runtime, "trace_id", "")) or None,
                "category": "runtime",
                "reason_code": reason_code,
                "summary_text": summary_text,
                "severity": severity,
                **extra,
            }
        )

    def _emit_observer_decision(self, *, reason_code: str, summary_text: str, severity: str, **extra: Any) -> None:
        emit = getattr(self.runtime, "emit_routing_decision", None)
        if emit is None:
            return
        emit(
            {
                "timestamp": self.runtime.timestamp(),
                "session_id": self.runtime.session_id,
                "adapter_id": self.adapter_id,
                "adapter_run_id": self.adapter_run_id,
                "trace_id": str(getattr(self.runtime, "trace_id", "")) or None,
                "category": "observer",
                "reason_code": reason_code,
                "summary_text": summary_text,
                "severity": severity,
                **extra,
            }
        )

    async def observer_event(
        self,
        source_event_id: str,
        event_type: str,
        state: str = "thinking",
        summary_text: str = "",
        intent: str = "",
        risk_level: str = "none",
        cursor: dict[str, Any] | None = None,
        target_rect: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        subagent: bool = False,
        agent_id: str = "main_001",
        parent_agent_id: str | None = None,
        task_text: str | None = None,
    ) -> None:
        if not self.observer_mode:
            await self.runtime.emit_error(ErrorCode.INVALID_STATE, "OpenCode observer event received without observer mode", command_type="observer_event")
            return
        if source_event_id in self._observed_source_event_id_set:
            self._emit_observer_decision(
                reason_code="observer_event_duplicate_ignored",
                summary_text="Duplicate observer source event ignored",
                severity="info",
                source_event_id=source_event_id,
            )
            return
        self._remember_observed_source_event_id(source_event_id)

        next_task_text = (task_text or "").strip()
        if next_task_text and next_task_text != self.runtime.task_text:
            self.runtime.task_text = next_task_text
            await self.runtime.emit_session_state()

        raw_event = {
            "type": event_type,
            "state": state,
            "summary": summary_text,
            "intent": intent or summary_text,
            "risk_level": risk_level,
            "cursor": cursor,
            "target_rect": target_rect,
            "meta": meta or {},
            "subagent": subagent,
            "agent_id": agent_id,
            "parent_agent_id": parent_agent_id,
            "id": source_event_id,
        }
        await self.runtime.emit_agent_event(self._normalize_opencode_event(raw_event))
        await self._maybe_emit_browser_context(raw_event)
        if self.observer_mode and self._delegation_enabled():
            if self.auto_delegate and self._should_launch_web_bridge(raw_event, self.runtime.task_text):
                await self._launch_web_bridge(raw_event, self.runtime.task_text, demo_mode=False)
            else:
                await self._maybe_offer_bridge(raw_event, self.runtime.task_text)
        elif not self.observer_mode and self._should_launch_web_bridge(raw_event, self.runtime.task_text):
            await self._launch_web_bridge(raw_event, self.runtime.task_text, demo_mode=False)

    def _remember_observed_source_event_id(self, source_event_id: str) -> None:
        if len(self._observed_source_event_ids) == self._observed_source_event_ids.maxlen:
            oldest = self._observed_source_event_ids.popleft()
            self._observed_source_event_id_set.discard(oldest)
        self._observed_source_event_ids.append(source_event_id)
        self._observed_source_event_id_set.add(source_event_id)

    async def observer_complete(self, status: str, summary_text: str) -> None:
        if not self.observer_mode:
            await self.runtime.emit_error(ErrorCode.INVALID_STATE, "OpenCode observer completion received without observer mode", command_type="observer_complete")
            return

        if status != "completed":
            self.pending_bridge_offer = None
            await self._stop_bridge()
            await self.runtime.complete_task(status=status, summary_text=summary_text)
            return

        if self._bridge_is_running():
            self.pending_observer_completion = (status, summary_text)
            return

        self.pending_bridge_offer = None
        await self.runtime.complete_task(status=status, summary_text=summary_text)

    async def accept_bridge(self) -> bool:
        if self.pending_bridge_offer is None:
            await self.runtime.emit_error(ErrorCode.INVALID_STATE, "No pending bridge offer to accept", command_type="accept_bridge")
            return False
        offer = self.pending_bridge_offer
        source_event_id = str(offer["raw_event"].get("id") or "")
        self._emit_observer_decision(
            reason_code="bridge_offer_accepted",
            summary_text="Bridge offer accepted",
            severity="info",
            source_event_id=source_event_id or None,
            web_mode=self.selected_web_mode,
            web_bridge=self.selected_web_bridge,
        )
        self.pending_bridge_offer = None
        await self._launch_web_bridge(offer["raw_event"], offer["task_text"], demo_mode=False)
        return True

    async def decline_bridge(self) -> bool:
        if self.pending_bridge_offer is None:
            await self.runtime.emit_error(ErrorCode.INVALID_STATE, "No pending bridge offer to decline", command_type="decline_bridge")
            return False
        source_event_id = str(self.pending_bridge_offer["raw_event"].get("id") or "")
        self._emit_observer_decision(
            reason_code="bridge_offer_declined",
            summary_text="Bridge offer declined",
            severity="info",
            source_event_id=source_event_id or None,
            web_mode=self.selected_web_mode,
            web_bridge=self.selected_web_bridge,
        )
        if source_event_id:
            self.declined_bridge_source_ids.add(source_event_id)
        self.pending_bridge_offer = None
        await self.runtime.emit_session_state()
        return True

    async def _launch_web_bridge(self, raw: dict[str, Any], task_text: str, *, demo_mode: bool) -> None:
        if not self._delegation_enabled():
            self._emit_observer_decision(
                reason_code="bridge_launch_guard_delegation_disabled",
                summary_text="Bridge launch skipped because delegation is disabled",
                severity="info",
            )
            return
        if self._bridge_is_running():
            self._emit_observer_decision(
                reason_code="bridge_launch_guard_already_running",
                summary_text="Bridge launch skipped because a bridge is already running",
                severity="info",
                web_bridge=self.active_web_bridge,
            )
            return

        bridge_task_text = self._bridge_task_text(raw, task_text)
        bridge_context = self._bridge_context(raw, task_text, bridge_task_text)
        bridge_id = self.selected_web_bridge
        from app.adapters.registry import create_connector

        self.bridge_result = None
        self.bridge_completion = asyncio.Event()
        self.active_web_bridge = bridge_id
        self._emit_observer_decision(
            reason_code="bridge_launch_started",
            summary_text="Bridge launch started",
            severity="info",
            source_event_id=str(raw.get("id") or raw.get("event_id") or raw.get("source_event_id") or "") or None,
            web_mode=self.selected_web_mode,
            web_bridge=bridge_id,
            demo_mode=demo_mode,
        )
        self.bridge_runtime = _BridgeRuntimeProxy(self, bridge_id, bridge_task_text)
        self.bridge_connector = create_connector(self.bridge_runtime, bridge_id)
        await self.runtime.emit_agent_event(
            self._normalize_opencode_event(
                {
                    "type": "browser.bridge",
                    "state": "thinking",
                    "summary": f"Launching delegated browser view via {bridge_id}",
                    "intent": bridge_task_text,
                    "meta": {
                        "bridge_launch": True,
                        "web_bridge": bridge_id,
                        "web_mode": self.selected_web_mode,
                        **bridge_context,
                    },
                }
            )
        )
        await self.runtime.emit_session_state()
        await self.bridge_connector.start_task(
            bridge_task_text,
            demo_mode=demo_mode,
            web_mode=self.selected_web_mode,
            bridge_context=bridge_context,
        )

    async def ensure_browser_delegate(self, *, observed_session_id: str, task_text: str) -> None:
        if self.observed_session_id is None:
            self.observed_session_id = observed_session_id
        self.selected_web_mode = "delegate_playwright"
        self.selected_web_bridge = "playwright_native"
        self.auto_delegate = True
        bridge_alive = bool(getattr(self.bridge_connector, "_bridge_is_alive", lambda: True)())
        if self.bridge_connector is not None and (not getattr(self.bridge_connector, "command_mode", False) or not bridge_alive):
            await self._stop_bridge()
        if not self._bridge_is_running():
            await self._launch_web_bridge(
                {
                    "type": "browser.tool",
                    "summary": "Lumon browser tool activated",
                    "intent": task_text,
                    "id": new_id("src"),
                    "meta": {"tool_mode": "commands"},
                },
                task_text,
                demo_mode=False,
            )
        if self.bridge_connector is not None and hasattr(self.bridge_connector, "command_ready"):
            try:
                await asyncio.wait_for(
                    self.bridge_connector.command_ready.wait(),
                    timeout=COMMAND_DELEGATE_READY_TIMEOUT_SECONDS,
                )  # type: ignore[attr-defined]
            except asyncio.TimeoutError as exc:
                await self._stop_bridge()
                raise RuntimeError("Lumon browser delegate did not become ready in time") from exc

    async def execute_browser_command(self, payload: BrowserCommandRequest | dict[str, Any]) -> dict[str, Any]:
        if not self._bridge_is_running() or self.bridge_connector is None:
            raise RuntimeError("Lumon browser delegate is not running")
        if not bool(getattr(self.bridge_connector, "_bridge_is_alive", lambda: True)()):
            raise RuntimeError("Lumon browser delegate is unavailable")
        execute = getattr(self.bridge_connector, "execute_browser_command", None)
        if execute is None:
            raise RuntimeError("Active browser bridge does not support commands")
        return await execute(payload)

    async def _maybe_offer_bridge(self, raw: dict[str, Any], task_text: str) -> None:
        if not self._delegation_enabled() or self._bridge_is_running():
            return
        if not self._should_launch_web_bridge(raw, task_text):
            return
        source_event_id = str(raw.get("id") or raw.get("event_id") or raw.get("source_event_id") or "")
        if source_event_id and source_event_id in self.declined_bridge_source_ids:
            self._emit_observer_decision(
                reason_code="bridge_offer_declined_cooldown",
                summary_text="Bridge offer suppressed for declined source event",
                severity="info",
                source_event_id=source_event_id,
            )
            return
        if self.pending_bridge_offer is not None:
            self._emit_observer_decision(
                reason_code="bridge_offer_pending",
                summary_text="Bridge offer suppressed because a pending offer already exists",
                severity="info",
                source_event_id=source_event_id or None,
            )
            return
        summary_text = str(raw.get("summary") or raw.get("message") or f"OpenCode can delegate browser control to {self.selected_web_bridge}")
        intent = str(raw.get("intent") or task_text)
        bridge_url = self._bridge_url(raw)
        self.pending_bridge_offer = {"raw_event": dict(raw), "task_text": task_text}
        self._emit_observer_decision(
            reason_code="bridge_offer_emitted",
            summary_text="Bridge offer emitted",
            severity="info",
            source_event_id=source_event_id or None,
            source_url=bridge_url,
            web_mode=self.selected_web_mode,
            web_bridge=self.selected_web_bridge,
        )
        await self.runtime.emit_bridge_offer(
            {
                "intervention_id": new_id("intv"),
                "session_id": self.runtime.session_id,
                "adapter_id": self.adapter_id,
                "adapter_run_id": self.adapter_run_id,
                "web_mode": self.selected_web_mode,
                "web_bridge": self.selected_web_bridge,
                "source_event_id": source_event_id or new_id("src"),
                "source_url": bridge_url,
                "target_summary": summary_text,
                "headline": "Live browser view",
                "reason_text": "Lumon can open a visible browser view for this online step.",
                "recommended_action": "open_live_browser_view",
                "summary_text": summary_text,
                "intent": intent,
            }
        )

    async def _wait_for_bridge_completion(self) -> tuple[str, str] | None:
        if self.bridge_completion is None:
            return None
        if not self.bridge_completion.is_set():
            await self.bridge_completion.wait()
        result = self.bridge_result
        self.bridge_result = None
        self.bridge_completion = None
        self.bridge_connector = None
        self.bridge_runtime = None
        self.active_web_bridge = None
        return result

    def _bridge_is_running(self) -> bool:
        return self.bridge_connector is not None and self.bridge_completion is not None and not self.bridge_completion.is_set()

    async def _on_bridge_complete(self, status: str, summary_text: str) -> None:
        self.bridge_result = (status, summary_text)
        bridge_completion = self.bridge_completion
        self.bridge_connector = None
        self.bridge_runtime = None
        self.active_web_bridge = None

        if status == "completed":
            if self.observer_mode and self.pending_observer_completion is not None:
                pending_status, pending_summary = self.pending_observer_completion
                self.pending_observer_completion = None
                await self.runtime.complete_task(status=pending_status, summary_text=pending_summary)
            elif self.runtime.state in {
                SessionState.PAUSE_REQUESTED,
                SessionState.PAUSED,
                SessionState.WAITING_FOR_APPROVAL,
                SessionState.TAKEOVER,
            }:
                await self.runtime.transition_to(SessionState.RUNNING, checkpoint_id=None)
            else:
                await self.runtime.emit_session_state()
        else:
            self.pending_observer_completion = None
            if self.process is not None and self.process.returncode is None:
                self.process.terminate()
                with contextlib.suppress(ProcessLookupError):
                    await self.process.wait()
            target = SessionState.FAILED if status == "failed" else SessionState.STOPPED
            if self.runtime.state != target:
                await self.runtime.transition_to(target, checkpoint_id=None)
            else:
                await self.runtime.emit_session_state()

        if bridge_completion is not None and not bridge_completion.is_set():
            bridge_completion.set()

    async def _stop_bridge(self) -> None:
        self.pending_bridge_offer = None
        if self.bridge_connector is not None:
            await self.bridge_connector.stop()
        if self.bridge_completion is not None and not self.bridge_completion.is_set():
            self.bridge_result = ("stopped", "OpenCode browser bridge stopped")
            self.bridge_completion.set()
        self.bridge_connector = None
        self.bridge_runtime = None
        self.active_web_bridge = None

    def _coerce_web_bridge(self, value: str | None) -> WebBridgeId | None:
        if value == "playwright_native":
            return value
        return None

    def _coerce_web_mode(self, web_mode: str | None, web_bridge: str | None, observer_mode: bool) -> WebModeId:
        if web_mode in {"observe_only", "delegate_playwright"}:
            return web_mode
        bridge = self._coerce_web_bridge(web_bridge)
        if bridge == "playwright_native":
            return "delegate_playwright"
        if observer_mode:
            return "observe_only"
        return "observe_only"

    def _bridge_for_mode(self, web_mode: WebModeId) -> WebBridgeId | None:
        if web_mode == "delegate_playwright":
            return "playwright_native"
        return None

    def _delegation_enabled(self) -> bool:
        return self.selected_web_mode == "delegate_playwright" and self.selected_web_bridge is not None

    def _task_needs_web(self, task_text: str) -> bool:
        return task_mentions_browser(task_text)

    def _should_launch_web_bridge(self, raw: dict[str, Any], task_text: str) -> bool:
        event_type = str(raw.get("type") or raw.get("event_type") or "")

        def _emit_decision(*, decision: dict[str, Any], should_launch: bool, reason_code: str) -> None:
            emit = getattr(self.runtime, "emit_routing_decision", None)
            if emit is None:
                return
            emit(
                {
                    "timestamp": self.runtime.timestamp(),
                    "session_id": self.runtime.session_id,
                    "adapter_id": self.adapter_id,
                    "adapter_run_id": self.adapter_run_id,
                    "source_event_type": event_type,
                    "signal": decision.get("signal"),
                    "tier": decision.get("tier"),
                    "confidence": decision.get("confidence"),
                    "reason_code": reason_code,
                    "classifier_reason_code": decision.get("reason_code"),
                    "auto_delegate": self.auto_delegate,
                    "selected_web_mode": self.selected_web_mode,
                    "selected_web_bridge": self.selected_web_bridge,
                    "should_launch_bridge": should_launch,
                }
            )

        decision = classify_signal_detailed(raw)

        if not self._delegation_enabled():
            _emit_decision(decision=decision, should_launch=False, reason_code="delegation_disabled")
            return False
        if self._bridge_is_running():
            _emit_decision(decision=decision, should_launch=False, reason_code="bridge_already_running")
            return False
        signal = decision["signal"]
        tier = decision["tier"]
        if signal == "browser":
            if tier == "C":
                _emit_decision(decision=decision, should_launch=False, reason_code="tier_c_text_only")
                return False
            _emit_decision(decision=decision, should_launch=True, reason_code="browser_signal")
            return True
        # Keep the task-text fallback only for synthetic/demo flows where no
        # richer OpenCode event metadata exists yet.
        if signal == "none" and raw.get("type") == "browser.search":
            fallback = self._task_needs_web(task_text)
            _emit_decision(
                decision=decision,
                should_launch=fallback,
                reason_code="synthetic_task_fallback" if fallback else "synthetic_task_no_browser_need",
            )
            return fallback
        _emit_decision(decision=decision, should_launch=False, reason_code="no_browser_signal")
        return False

    def _bridge_task_text(self, raw: dict[str, Any], task_text: str) -> str:
        bridge_url = self._bridge_url(raw)
        if bridge_url:
            return f"Open and inspect this exact URL in the browser: {bridge_url}"
        for field in ("intent", "summary", "message", "text"):
            value = raw.get(field)
            if isinstance(value, str) and value.strip() and self._task_needs_web(value):
                return value.strip()
        return task_text.strip()

    def _bridge_context(self, raw: dict[str, Any], task_text: str, bridge_task_text: str) -> dict[str, Any]:
        meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        output_preview = str(meta.get("output_preview") or "") if meta else ""
        return {
            "source_url": self._bridge_url(raw),
            "source_event_type": str(raw.get("type") or raw.get("event_type") or ""),
            "source_tool_name": str(meta.get("tool_name") or "") if meta else "",
            "source_tool_title": str(meta.get("tool_title") or "") if meta else "",
            "source_output_preview": output_preview[:500],
            "source_summary_text": str(raw.get("summary") or raw.get("message") or "")[:500],
            "source_task_text": task_text[:500],
            "bridge_task_text": bridge_task_text[:500],
            "tool_mode": str(meta.get("tool_mode") or ""),
        }

    def _bridge_url(self, raw: dict[str, Any]) -> str | None:
        meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        candidates: list[str] = []
        for value in (
            raw.get("intent"),
            raw.get("summary"),
            raw.get("message"),
            raw.get("text"),
            meta.get("url") if meta else None,
            meta.get("tool_title") if meta else None,
            meta.get("output_preview") if meta else None,
        ):
            if isinstance(value, str) and value.strip():
                candidates.append(value)
        for value in candidates:
            match = _URL_PATTERN.search(value)
            if match:
                return match.group(0)
        return None

    def _build_run_command(self, task_text: str) -> tuple[str, ...]:
        command: list[str] = ["opencode", "run", "--format", "json"]
        attach_url = os.getenv("OPENCODE_ATTACH_URL")
        model = os.getenv("OPENCODE_MODEL")
        agent = os.getenv("OPENCODE_AGENT")
        variant = os.getenv("OPENCODE_VARIANT")

        if attach_url:
            command.extend(["--attach", attach_url])
        if model:
            command.extend(["--model", model])
        if agent:
            command.extend(["--agent", agent])
        if variant:
            command.extend(["--variant", variant])

        command.append(task_text)
        return tuple(command)

    async def _collect_stderr(self, collection: list[str]) -> None:
        if not self.process or self.process.stderr is None:
            return
        async for line in self.process.stderr:
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                collection.append(text)

    async def _maybe_emit_browser_context(self, raw: dict[str, Any]) -> None:
        if not hasattr(self.runtime, "emit_browser_context_update"):
            return
        url = self._bridge_url(raw)
        if not url:
            return
        meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        title = str(meta.get("tool_title") or meta.get("url_title") or "") or None
        parsed = urllib.parse.urlparse(url)
        domain = parsed.hostname or "unknown"
        await self.runtime.emit_browser_context_update(
            {
                "session_id": self.runtime.session_id,
                "adapter_id": self.adapter_id,
                "adapter_run_id": self.adapter_run_id,
                "url": url,
                "title": title,
                "domain": domain,
                "environment_type": "local" if domain in {"127.0.0.1", "localhost"} or domain.endswith(".local") else "external",
                "timestamp": self.runtime.timestamp(),
            }
        )

    def _parse_json_line(self, line: bytes) -> dict[str, Any] | None:
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _normalize_opencode_event(self, raw: dict[str, Any]) -> dict[str, Any]:
        event_type = (
            raw.get("type")
            or raw.get("event")
            or raw.get("kind")
            or raw.get("event_type")
            or "tool_start"
        )
        summary = (
            raw.get("summary")
            or raw.get("message")
            or raw.get("label")
            or raw.get("text")
            or self._error_message_for(raw)
            or str(event_type).replace(".", " ").replace("_", " ").title()
        )
        state = raw.get("state") or raw.get("status") or ("done" if "complete" in str(event_type) else "thinking")
        meta = {
            "opencode_event_type": event_type,
            "provider": "opencode",
            "raw_kind": raw.get("kind"),
            "web_mode": self.selected_web_mode,
        }
        if self.selected_web_bridge is not None:
            meta["web_bridge"] = self.selected_web_bridge
        return normalize_external_event(
            {
                "event_type": self._map_event_type(str(event_type)),
                "state": state,
                "summary_text": summary,
                "intent": raw.get("intent") or summary,
                "risk_level": raw.get("risk_level", "none"),
                "cursor": raw.get("cursor"),
                "target_rect": raw.get("target_rect"),
                "meta": meta,
                "subagent": bool(raw.get("subagent")),
                "agent_id": raw.get("agent_id", "main_001"),
                "parent_agent_id": raw.get("parent_agent_id"),
                "source_event_id": raw.get("id") or raw.get("event_id") or new_id("src"),
            },
            session_id=self.runtime.session_id,
            adapter_id=self.adapter_id,
            adapter_run_id=self.adapter_run_id,
            event_seq=next(self.event_seq),
        )

    def _map_event_type(self, event_type: str) -> str:
        lowered = event_type.lower()
        if "error" in lowered:
            return "error"
        if "complete" in lowered or "finished" in lowered:
            return "tool_complete"
        if "navigate" in lowered or "open_url" in lowered or "open-url" in lowered or "visit" in lowered or "goto" in lowered:
            return "navigate"
        if "search" in lowered or "browser" in lowered or "web" in lowered:
            return "navigate"
        if "click" in lowered or "submit" in lowered or "tap" in lowered:
            return "click"
        if "type" in lowered or "write" in lowered or "fill" in lowered or "input" in lowered:
            return "type"
        if "scroll" in lowered:
            return "scroll"
        if "subagent" in lowered:
            return "subagent"
        if "wait" in lowered:
            return "wait"
        return "tool_start"

    def _is_error_event(self, raw: dict[str, Any]) -> bool:
        event_type = str(raw.get("type") or raw.get("event") or raw.get("kind") or raw.get("event_type") or "")
        return "error" in event_type.lower() or isinstance(raw.get("error"), dict)

    def _error_message_for(self, raw: dict[str, Any]) -> str | None:
        error = raw.get("error")
        if not isinstance(error, dict):
            return None
        data = error.get("data")
        if isinstance(data, dict):
            message = data.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        name = error.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return None

    async def pause(self) -> None:
        if self.bridge_connector is not None:
            await self.bridge_connector.pause()
            return
        await self.runtime.emit_error(ErrorCode.INVALID_STATE, "OpenCode adapter does not support pause", command_type="pause")

    async def resume(self) -> None:
        if self.bridge_connector is not None:
            await self.bridge_connector.resume()
            return
        await self.runtime.emit_error(ErrorCode.INVALID_STATE, "OpenCode adapter does not support resume", command_type="resume")

    async def approve(self, checkpoint_id: str) -> bool:
        if self.bridge_connector is not None:
            return await self.bridge_connector.approve(checkpoint_id)
        await self.runtime.emit_error(ErrorCode.INVALID_STATE, "OpenCode adapter does not support approval checkpoints", command_type="approve", checkpoint_id=checkpoint_id)
        return False

    async def reject(self, checkpoint_id: str) -> bool:
        if self.bridge_connector is not None:
            return await self.bridge_connector.reject(checkpoint_id)
        await self.runtime.emit_error(ErrorCode.INVALID_STATE, "OpenCode adapter does not support approval checkpoints", command_type="reject", checkpoint_id=checkpoint_id)
        return False

    async def start_takeover(self) -> None:
        if self.bridge_connector is not None:
            await self.bridge_connector.start_takeover()
            return
        await self.runtime.emit_error(ErrorCode.INVALID_STATE, "OpenCode adapter does not support takeover", command_type="start_takeover")

    async def end_takeover(self) -> None:
        if self.bridge_connector is not None:
            await self.bridge_connector.end_takeover()
            return
        await self.runtime.emit_error(ErrorCode.INVALID_STATE, "OpenCode adapter does not support takeover", command_type="end_takeover")

    async def stop(self) -> None:
        await self._stop_bridge()
        if self.process and self.process.returncode is None:
            self.process.terminate()
            with contextlib.suppress(ProcessLookupError):
                await self.process.wait()
        if self.run_task and not self.run_task.done():
            self.run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.run_task


class _BridgeRuntimeProxy:
    def __init__(self, parent: OpenCodeConnector, bridge_id: WebBridgeId, task_text: str) -> None:
        self.parent = parent
        self.bridge_id = bridge_id
        self._task_text = task_text
        self._adapter_run_id: str | None = None

    @property
    def session_id(self) -> str:
        return self.parent.runtime.session_id

    @property
    def state(self) -> SessionState:
        return self.parent.runtime.state

    @state.setter
    def state(self, value: SessionState) -> None:
        _ = value

    @property
    def task_text(self) -> str:
        return self._task_text

    @task_text.setter
    def task_text(self, value: str) -> None:
        self._task_text = value

    @property
    def adapter_run_id(self) -> str | None:
        return self._adapter_run_id

    @adapter_run_id.setter
    def adapter_run_id(self, value: str | None) -> None:
        self._adapter_run_id = value

    @property
    def latest_frame_generation(self) -> int:
        return int(getattr(self.parent.runtime, "latest_frame_generation", 0) or 0)

    @property
    def latest_command_frame_generation(self) -> int:
        return int(getattr(self.parent.runtime, "latest_command_frame_generation", 0) or 0)

    @property
    def latest_frame_seq(self) -> int | None:
        value = getattr(self.parent.runtime, "latest_frame_seq", None)
        return int(value) if isinstance(value, int) else value

    def timestamp(self) -> str:
        return self.parent.runtime.timestamp()

    async def emit_agent_event(self, payload: dict[str, Any]) -> None:
        bridged = dict(payload)
        meta = dict(bridged.get("meta") or {})
        source_adapter = bridged.get("adapter_id")
        if source_adapter and source_adapter != self.parent.adapter_id:
            meta.setdefault("bridge_source_adapter_id", source_adapter)
        meta["web_bridge"] = self.bridge_id
        bridged["session_id"] = self.session_id
        bridged["adapter_id"] = self.parent.adapter_id
        bridged["adapter_run_id"] = self.parent.adapter_run_id
        bridged["event_seq"] = next(self.parent.event_seq)
        bridged["meta"] = meta
        await self.parent.runtime.emit_agent_event(bridged)

    async def emit_background_worker_update(self, payload: dict[str, Any]) -> None:
        bridged = dict(payload)
        bridged["session_id"] = self.session_id
        bridged["adapter_id"] = self.parent.adapter_id
        bridged["adapter_run_id"] = self.parent.adapter_run_id
        await self.parent.runtime.emit_background_worker_update(bridged)

    async def emit_approval_required(self, payload: dict[str, Any]) -> None:
        bridged = dict(payload)
        bridged["session_id"] = self.session_id
        bridged["adapter_id"] = self.parent.adapter_id
        bridged["adapter_run_id"] = self.parent.adapter_run_id
        await self.parent.runtime.emit_approval_required(bridged)

    async def emit_error(
        self,
        code: ErrorCode,
        message: str,
        command_type: str | None = None,
        checkpoint_id: str | None = None,
    ) -> None:
        await self.parent.runtime.emit_error(
            code,
            f"{self.bridge_id} bridge: {message}",
            command_type=command_type,
            checkpoint_id=checkpoint_id,
        )

    async def emit_frame(self, payload: dict[str, Any]) -> None:
        bridged = dict(payload)
        bridged["frame_seq"] = next(self.parent.bridge_frame_seq)
        await self.parent.runtime.emit_frame(bridged)

    async def emit_browser_context_update(self, payload: dict[str, Any]) -> None:
        bridged = dict(payload)
        bridged["session_id"] = self.session_id
        bridged["adapter_id"] = self.parent.adapter_id
        bridged["adapter_run_id"] = self.parent.adapter_run_id
        await self.parent.runtime.emit_browser_context_update(bridged)

    async def emit_session_state(self) -> None:
        await self.parent.runtime.emit_session_state()

    async def transition_to(self, state: SessionState, checkpoint_id: str | None = None) -> None:
        if state == SessionState.STARTING:
            await self.parent.runtime.emit_session_state()
            return
        if state == SessionState.RUNNING:
            if self.parent.runtime.state != SessionState.RUNNING or checkpoint_id is not None:
                await self.parent.runtime.transition_to(SessionState.RUNNING, checkpoint_id=checkpoint_id)
            else:
                await self.parent.runtime.emit_session_state()
            return
        if state in {
            SessionState.PAUSE_REQUESTED,
            SessionState.PAUSED,
            SessionState.WAITING_FOR_APPROVAL,
            SessionState.TAKEOVER,
        }:
            await self.parent.runtime.transition_to(state, checkpoint_id=checkpoint_id)
            return
        await self.parent.runtime.emit_session_state()

    async def complete_task(self, status: str, summary_text: str) -> None:
        await self.parent._on_bridge_complete(status, summary_text)

    async def capture_live_keyframe(self, reason: str) -> str | None:
        return await self.parent.runtime.capture_live_keyframe(reason)

    def record_browser_command(self, record: BrowserCommandRecord) -> None:
        self.parent.runtime.record_browser_command(record)


class SessionRuntimeProtocol:
    session_id: str
    state: SessionState
    task_text: str
    adapter_run_id: str | None

    async def emit_agent_event(self, payload: dict[str, Any]) -> None: ...
    async def emit_background_worker_update(self, payload: dict[str, Any]) -> None: ...
    async def emit_approval_required(self, payload: dict[str, Any]) -> None: ...
    async def emit_bridge_offer(self, payload: dict[str, Any]) -> None: ...
    async def emit_error(self, code: ErrorCode, message: str, command_type: str | None = None, checkpoint_id: str | None = None) -> None: ...
    async def emit_frame(self, payload: dict[str, Any]) -> None: ...
    async def emit_browser_context_update(self, payload: dict[str, Any]) -> None: ...
    async def emit_session_state(self) -> None: ...
    async def transition_to(self, state: SessionState, checkpoint_id: str | None = None) -> None: ...
    async def complete_task(self, status: str, summary_text: str) -> None: ...
    def emit_routing_decision(self, payload: dict[str, Any]) -> None: ...
    def timestamp(self) -> str: ...
