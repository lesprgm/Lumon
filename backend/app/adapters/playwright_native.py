from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import re
import sys
import time
import traceback
import urllib.parse
from itertools import count
from typing import Any, Literal

from app.adapters.base import AdapterConnector
from app.browser.actions import BrowserActionLayer
from app.browser.demo_pages import backup_demo_html, primary_demo_html
from app.browser.screencast import CDPScreencastStreamer, ScreenshotPollStreamer
from app.config import DEFAULT_ADAPTER_ID, VIEWPORT_HEIGHT, VIEWPORT_WIDTH
from app.protocol.enums import ErrorCode, RiskLevel, SessionState
from app.protocol.models import BrowserCommandRecord, BrowserCommandRequest, BrowserCommandResult, BrowserElementRef, BrowserEvidence
from app.utils.ids import new_id

try:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
except Exception:  # pragma: no cover
    Browser = BrowserContext = Page = Playwright = object  # type: ignore[assignment]
    async_playwright = None


StreamMode = Literal["live", "option_a"]
DemoVariant = Literal["primary", "backup"]
_URL_PATTERN = re.compile(r"(https?://[^\s)>\"]+|(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s)]*)?)", re.IGNORECASE)
COMMAND_READY_TIMEOUT_SECONDS = float(os.getenv("LUMON_COMMAND_READY_TIMEOUT_SECONDS", "45"))


class PlaywrightNativeConnector(AdapterConnector):
    adapter_id = DEFAULT_ADAPTER_ID
    capabilities = {
        "supports_pause": True,
        "supports_approval": True,
        "supports_takeover": True,
        "supports_frames": True,
    }

    def __init__(
        self,
        runtime: "SessionRuntimeProtocol",
    ) -> None:
        self.runtime = runtime
        self.adapter_run_id = new_id("run")
        self.event_seq = count(1)
        self.run_task: asyncio.Task[None] | None = None
        self.approval_future: asyncio.Future[bool] | None = None
        self.resume_event = asyncio.Event()
        self.resume_event.set()
        self.suspended_checkpoint_id: str | None = None
        self.latest_checkpoint_id: str | None = None
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.cdp_session: Any | None = None
        self.live_streamer: CDPScreencastStreamer | None = None
        self.live_stream_health_task: asyncio.Task[None] | None = None
        self.option_a_streamer: ScreenshotPollStreamer | None = None
        self.stream_mode: StreamMode = self._configured_stream_mode()
        self.webrtc_primary = self._configured_webrtc_primary()
        self.demo_variant: DemoVariant = self._configured_demo_variant()
        self.action_layer: BrowserActionLayer | None = None
        self.bridge_context: dict[str, Any] = {}
        self.command_mode = False
        self.command_ready = asyncio.Event()
        self.command_stop_event = asyncio.Event()
        self.command_lock = asyncio.Lock()
        self.command_inflight_id: str | None = None
        self.command_results: dict[str, dict[str, Any]] = {}
        self.pending_browser_commands: dict[str, dict[str, Any]] = {}
        self.page_version = 0
        self.current_page_url: str | None = None
        self.element_refs: dict[str, dict[str, Any]] = {}
        self.snapshot_frame_seq = count(1)
        self.command_delegate_error: str | None = None
        self.last_snapshot_error: str | None = None
        self.last_begin_task_open_url: str | None = None
        self.last_begin_task_opened_at = 0.0

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
        _ = (web_mode, web_bridge, auto_delegate, observer_mode, observed_session_id)
        self.runtime.task_text = task_text
        self.runtime.adapter_run_id = self.adapter_run_id
        self.bridge_context = dict(bridge_context or {})
        self.command_mode = self.bridge_context.get("tool_mode") == "commands"
        self.command_ready.clear()
        self.command_stop_event.clear()
        self.command_results.clear()
        self.pending_browser_commands.clear()
        self.command_inflight_id = None
        self.latest_checkpoint_id = None
        self.suspended_checkpoint_id = None
        self.command_delegate_error = None
        self.page_version = 0
        self.current_page_url = None
        self.element_refs.clear()
        await self.runtime.transition_to(SessionState.STARTING)
        if self.command_mode:
            self.run_task = asyncio.create_task(self._run_command_delegate())
        else:
            self.run_task = asyncio.create_task(self._run_flow(task_text, demo_mode=demo_mode))

    async def _run_flow(self, task_text: str, demo_mode: bool = False) -> None:
        try:
            await self._launch_browser()
            await self._start_stream_transport()
            await self.runtime.transition_to(SessionState.RUNNING)
            if demo_mode:
                html_content = primary_demo_html(task_text) if self.demo_variant == "primary" else backup_demo_html(task_text)
                await self.action_layer.navigate(
                    "https://lumon.local/demo",
                    html_content=html_content,
                    summary_text="Opening demo travel site" if self.demo_variant == "primary" else "Opening backup travel site",
                    intent="Load the deterministic demo experience",
                )

                if self.demo_variant == "primary":
                    await self._run_primary_flow()
                else:
                    await self._run_backup_flow()
            else:
                await self._run_live_bridge_flow(task_text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - runtime safety path
            await self.runtime.emit_error(ErrorCode.INVALID_STATE, f"Runtime failed: {exc}")
            await self.runtime.transition_to(SessionState.FAILED)
        finally:
            await self._shutdown_browser()

    async def _run_command_delegate(self) -> None:
        try:
            await self._launch_browser()
            self.command_delegate_error = None
            self.command_ready.set()
            await self.runtime.transition_to(SessionState.RUNNING)
            await self._start_stream_transport()
            await self.command_stop_event.wait()
            await self.runtime.complete_task(status="completed", summary_text="Live browser view closed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - runtime safety path
            self.command_delegate_error = str(exc)
            self.command_ready.set()
            await self.runtime.emit_error(ErrorCode.INVALID_STATE, f"Browser delegate failed: {exc}")
            await self.runtime.complete_task(status="failed", summary_text="Live browser view failed")
        finally:
            self.command_ready.clear()
            await self._shutdown_browser()

    async def _run_live_bridge_flow(self, task_text: str) -> None:
        assert self.action_layer is not None
        bridge_url = str(self.bridge_context.get("source_url") or "").strip()
        if bridge_url:
            await self.action_layer.navigate(
                bridge_url,
                summary_text=f"Opening {bridge_url}",
                intent=f"Open {bridge_url} in the browser",
            )
            await self._emit_snapshot_frame()
            await self.runtime.complete_task(status="completed", summary_text=f"Opened {bridge_url} in the live browser view")
            return

        await self._emit_snapshot_frame()
        await self.runtime.complete_task(status="completed", summary_text="Live browser view opened without a page target")

    async def execute_browser_command(self, request: BrowserCommandRequest | dict[str, Any]) -> dict[str, Any]:
        normalized = request if isinstance(request, BrowserCommandRequest) else BrowserCommandRequest.model_validate(request)
        command_id = normalized.command_id
        command_key = self._command_cache_key(normalized.command, command_id)
        approval_granted = False

        cached = self.command_results.get(command_key)
        if cached is not None:
            return cached

        pending = self.pending_browser_commands.get(command_key)
        if pending is not None:
            state = pending["state"]
            if state == "awaiting_approval":
                return pending["result"]
            if state == "denied":
                result = pending["result"]
                self.command_results[command_key] = result
                self.pending_browser_commands.pop(command_key, None)
                return result
            if state == "approved":
                normalized = BrowserCommandRequest.model_validate(pending["request"])
                approval_granted = True
                command_key = self._command_cache_key(normalized.command, normalized.command_id)
                self.pending_browser_commands.pop(command_key, None)

        if self.command_lock.locked() and self.command_inflight_id != command_key:
            return self._result(
                normalized,
                status="blocked",
                summary_text="Lumon is still finishing the previous browser step.",
                reason="busy",
            )

        async with self.command_lock:
            self.command_inflight_id = command_key
            try:
                return await self._execute_browser_command_locked(
                    normalized,
                    command_key=command_key,
                    approval_granted=approval_granted,
                )
            finally:
                self.command_inflight_id = None

    async def _execute_browser_command_locked(
        self,
        request: BrowserCommandRequest,
        *,
        command_key: str,
        approval_granted: bool,
    ) -> dict[str, Any]:
        if not self._bridge_is_alive():
            result = self._result(
                request,
                status="failed",
                summary_text="Lumon lost the live browser delegate.",
                reason=self.command_delegate_error or "delegate_unavailable",
            )
            self.command_results[command_key] = result
            self._record_command_artifact(result)
            return result
        try:
            await asyncio.wait_for(self.command_ready.wait(), timeout=COMMAND_READY_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            result = self._result(
                request,
                status="failed",
                summary_text="Lumon did not finish preparing the live browser delegate in time.",
                reason="delegate_start_timeout",
            )
            self.command_results[command_key] = result
            self._record_command_artifact(result)
            return result
        await self._maybe_switch_to_foreground_page()
        try:
            result = await self._execute_browser_command(request, approval_granted=approval_granted)
        except Exception as exc:
            reason, summary_text = self._classify_command_exception(exc)
            if reason == "delegate_crashed":
                self.command_delegate_error = str(exc)
            self._log_command_exception(request, exc, reason=reason)
            result = self._result(
                request,
                status="failed",
                summary_text=summary_text,
                reason=reason,
                source_url=self.current_page_url,
                domain=urllib.parse.urlparse(self.current_page_url or "").hostname,
                page_version=self.page_version,
                meta={"error": str(exc), "exception_type": type(exc).__name__},
            )
        if result["status"] == "blocked" and result.get("reason") == "awaiting_approval":
            self.pending_browser_commands[command_key] = {
                "state": "awaiting_approval",
                "request": request.model_dump(mode="json"),
                "result": result,
                "checkpoint_id": result.get("checkpoint_id"),
            }
        else:
            self.command_results[command_key] = result
        self._record_command_artifact(result)
        return result

    async def _execute_browser_command(self, request: BrowserCommandRequest, *, approval_granted: bool = False) -> dict[str, Any]:
        if request.command == "begin_task":
            self._reset_command_task_state()
            clear_interventions = getattr(self.runtime, "clear_active_interventions", None)
            if clear_interventions is not None:
                clear_interventions(resolution="expired")
            if self.runtime.state != SessionState.RUNNING:
                await self.runtime.transition_to(SessionState.RUNNING, checkpoint_id=None)
            if request.task_text:
                self.runtime.task_text = request.task_text
            inferred_url = self._infer_url_from_task_text(request.task_text)
            if inferred_url:
                assert self.action_layer is not None
                await self.action_layer.navigate(
                    inferred_url,
                    summary_text=f"Opening {inferred_url}",
                    intent=f"Open {inferred_url}",
                )
                self.last_begin_task_open_url = inferred_url
                self.last_begin_task_opened_at = time.monotonic()
                await self._sync_page_version(force=False)
                frame_emitted, keyframe_path = await self._capture_command_frame("command_begin_task")
                context = await self._browser_status_context()
                evidence = BrowserEvidence(
                    verified=bool(frame_emitted and context["url"]),
                    final_url=context["url"],
                    title=context["title"],
                    domain=context["domain"],
                    page_version=self.page_version,
                    frame_emitted=frame_emitted,
                    keyframe_path=keyframe_path,
                    details={"auto_opened_url": inferred_url},
                ).model_dump(mode="json")
                return self._result(
                    request,
                    status="success" if evidence["verified"] else "partial",
                    summary_text=(
                        f"Lumon prepared the task and opened {context['domain']}."
                        if evidence["verified"]
                        else f"Lumon opened {context['domain']}, but no fresh visible frame was captured yet."
                    ),
                    reason=None if evidence["verified"] else "frame_missing",
                    evidence=evidence,
                    source_url=context["url"],
                    domain=context["domain"],
                    page_version=self.page_version,
                    meta={
                        "auto_opened_url": inferred_url,
                        "snapshot_error": self.last_snapshot_error,
                        "recovery_hint": None
                        if evidence["verified"]
                        else "Use status once to re-check the browser delegate. Do not loop open/inspect blindly.",
                    },
                )
            return self._result(
                request,
                status="partial",
                summary_text="Lumon prepared the live browser delegate for this task.",
                reason="awaiting_first_navigation",
                page_version=self.page_version,
                meta={"recovery_hint": "Issue open with a concrete URL or inspect the page after navigation starts."},
            )

        if request.command == "status":
            frame_emitted, keyframe_path = await self._capture_command_frame("command_status")
            context = await self._browser_status_context()
            evidence = BrowserEvidence(
                verified=bool(frame_emitted and context["url"]),
                final_url=context["url"],
                title=context["title"],
                domain=context["domain"],
                page_version=self.page_version,
                frame_emitted=frame_emitted,
                keyframe_path=keyframe_path,
                details={"active_element": context["active_element"], "scroll_y": context["scroll_y"]},
            ).model_dump(mode="json")
            return self._result(
                request,
                status="success" if evidence["verified"] else "partial",
                summary_text=(
                    f"Browser is ready on {context['domain']}."
                    if evidence["verified"]
                    else f"Lumon can reach {context['domain']}, but no fresh visible frame was captured yet."
                ),
                reason=None if evidence["verified"] else "frame_missing",
                evidence=evidence,
                source_url=context["url"],
                domain=context["domain"],
                page_version=self.page_version,
                meta={
                    "snapshot_error": self.last_snapshot_error,
                    "recovery_hint": None
                    if evidence["verified"]
                    else "Do not repeat open in a loop. Retry status once or report that the live browser frame is unavailable.",
                },
            )

        if request.command == "open":
            assert self.action_layer is not None
            requested_url = request.url or ""
            skip_navigation = (
                bool(requested_url)
                and requested_url == self.current_page_url
                and requested_url == self.last_begin_task_open_url
                and (time.monotonic() - self.last_begin_task_opened_at) <= 15.0
            )
            if not skip_navigation:
                await self.action_layer.navigate(
                    requested_url,
                    summary_text=f"Opening {request.url}",
                    intent=f"Open {request.url}",
                )
            await self._sync_page_version(force=False)
            frame_emitted, keyframe_path = await self._capture_command_frame("command_open")
            context = await self._browser_status_context()
            evidence = BrowserEvidence(
                verified=bool(frame_emitted and context["url"]),
                final_url=context["url"],
                title=context["title"],
                domain=context["domain"],
                page_version=self.page_version,
                frame_emitted=frame_emitted,
                keyframe_path=keyframe_path,
            ).model_dump(mode="json")
            status = "success" if evidence["verified"] else "partial"
            return self._result(
                request,
                status=status,
                summary_text=(
                    f"{'Already on' if skip_navigation else 'Opened'} {context['domain']}."
                    if status == "success"
                    else f"{'Already on' if skip_navigation else 'Opened'} {context['domain']}, but Lumon did not capture a fresh visible frame yet."
                ),
                reason=None if status == "success" else "frame_missing",
                evidence=evidence,
                source_url=context["url"],
                domain=context["domain"],
                page_version=self.page_version,
                meta={
                    "snapshot_error": self.last_snapshot_error,
                    "recovery_hint": None
                    if status == "success"
                    else "Use status once to confirm the delegate. Do not repeat open in a loop.",
                },
            )

        if request.command == "inspect":
            assert self.action_layer is not None
            await self.action_layer.read_region("body", "Inspecting page", "Review the current page before acting")
            raw_elements = await self.action_layer.inspect_actionable_elements(limit=12)
            frame_emitted, keyframe_path = await self._capture_command_frame("command_inspect")
            context = await self._browser_status_context()
            actionable_elements = self._register_element_refs(raw_elements)
            evidence = BrowserEvidence(
                verified=bool(frame_emitted and context["url"]),
                final_url=context["url"],
                title=context["title"],
                domain=context["domain"],
                page_version=self.page_version,
                frame_emitted=frame_emitted,
                keyframe_path=keyframe_path,
                details={"element_count": len(actionable_elements)},
            ).model_dump(mode="json")
            status = "success" if evidence["verified"] else "partial"
            return self._result(
                request,
                status=status,
                summary_text=(
                    f"Found {len(actionable_elements)} actionable elements on {context['domain']}."
                    if status == "success"
                    else f"Inspected {context['domain']}, but Lumon did not capture a fresh visible frame yet."
                ),
                reason=None if status == "success" else "frame_missing",
                evidence=evidence,
                actionable_elements=actionable_elements,
                source_url=context["url"],
                domain=context["domain"],
                page_version=self.page_version,
                meta={
                    "snapshot_error": self.last_snapshot_error,
                    "recovery_hint": None
                    if status == "success"
                    else "Use status once if you need to confirm the visible frame. Avoid repeated inspect loops.",
                },
            )

        if request.command == "scroll":
            assert self.action_layer is not None
            command_risk = None if approval_granted else self._command_risk(request, target=None)
            if command_risk is not None:
                if command_risk == "File upload workflows are not supported.":
                    return self._result(
                        request,
                        status="unsupported",
                        summary_text=command_risk,
                        reason="unsupported_file_upload",
                        source_url=self.current_page_url,
                        domain=urllib.parse.urlparse(self.current_page_url or "").hostname,
                        page_version=self.page_version,
                    )
                return await self._blocked_for_approval(request, summary_text="Scrolling here needs approval.", risk_reason=command_risk)
            outcome = await self.action_layer.scroll_by(
                request.delta_y or 0,
                "Scrolling page",
                f"Scroll by {request.delta_y or 0} pixels",
            )
            frame_emitted, keyframe_path = await self._capture_command_frame("command_scroll")
            context = await self._browser_status_context()
            viewport_changed = outcome["before_scroll_y"] != outcome["after_scroll_y"]
            evidence = BrowserEvidence(
                verified=bool(viewport_changed),
                final_url=context["url"],
                title=context["title"],
                domain=context["domain"],
                page_version=self.page_version,
                frame_emitted=frame_emitted,
                keyframe_path=keyframe_path,
                viewport_changed=viewport_changed,
                details=outcome,
            ).model_dump(mode="json")
            status = "success" if viewport_changed else "failed"
            return self._result(
                request,
                status=status,
                summary_text="Scrolled the page.",
                reason=None if status == "success" else "viewport_unchanged",
                evidence=evidence,
                source_url=context["url"],
                domain=context["domain"],
                page_version=self.page_version,
            )

        if request.command == "wait":
            completed = await self._wait_for_condition(request)
            frame_emitted, keyframe_path = await self._capture_command_frame("command_wait")
            context = await self._browser_status_context()
            evidence = BrowserEvidence(
                verified=completed,
                final_url=context["url"],
                title=context["title"],
                domain=context["domain"],
                page_version=self.page_version,
                frame_emitted=frame_emitted,
                keyframe_path=keyframe_path,
                details={
                    "wait_for_selector": request.wait_for_selector,
                    "wait_for_text": request.wait_for_text,
                    "timeout_ms": request.timeout_ms,
                },
            ).model_dump(mode="json")
            return self._result(
                request,
                status="success" if completed else "failed",
                summary_text="Wait condition completed." if completed else "Wait condition did not complete in time.",
                reason=None if completed else "timeout",
                evidence=evidence,
                source_url=context["url"],
                domain=context["domain"],
                page_version=self.page_version,
            )

        if request.command in {"click", "type"}:
            target = self._resolve_target(request)
            if target is None:
                return self._result(
                    request,
                    status="failed",
                    summary_text="Lumon could not resolve that page target.",
                    reason="stale_target" if request.element_id else "target_not_found",
                )
            if target["page_version"] != self.page_version:
                return self._result(
                    request,
                    status="failed",
                    summary_text="That page target is stale. Inspect the page again before acting.",
                    reason="stale_target",
                    source_url=self.current_page_url,
                    page_version=self.page_version,
                )

            unsupported_reason = self._unsupported_command_reason(request, target=target)
            if unsupported_reason is not None:
                return self._result(
                    request,
                    status="unsupported",
                    summary_text=unsupported_reason,
                    reason="unsupported_file_upload",
                    source_url=self.current_page_url,
                    domain=urllib.parse.urlparse(self.current_page_url or "").hostname,
                    page_version=self.page_version,
                )

            command_risk = None if approval_granted else self._command_risk(request, target=target)
            if command_risk is not None:
                return await self._blocked_for_approval(
                    request,
                    summary_text="Lumon stopped before a risky browser action.",
                    risk_reason=command_risk,
                    target=target,
                )

            assert self.action_layer is not None
            before_url = self.current_page_url or ""
            if request.command == "click":
                outcome = await self.action_layer.click(
                    target["selector"],
                    f"Clicking {target['label']}",
                    f"Click {target['label']}",
                    risky=False,
                )
            else:
                outcome = await self.action_layer.type_text(
                    target["selector"],
                    request.text or "",
                    f"Typing into {target['label']}",
                    f"Type into {target['label']}",
                    masked=bool(target.get("sensitive", False)),
                )
            await self._sync_page_version(force=False)
            frame_emitted, keyframe_path = await self._capture_command_frame(f"command_{request.command}")
            context = await self._browser_status_context()
            url_changed = before_url != context["url"]
            focus_changed = bool(outcome.get("focus_changed"))
            value_after = outcome.get("value_after")
            sensitive_target = bool(target.get("sensitive", False))
            verified = False
            reason: str | None = None
            if request.command == "click":
                verified = bool(url_changed or focus_changed or frame_emitted)
                if not verified:
                    reason = "no_post_action_evidence"
            else:
                verified = value_after == (request.text or "")
                if not verified:
                    reason = "value_mismatch"
            evidence = BrowserEvidence(
                verified=verified,
                final_url=context["url"],
                title=context["title"],
                domain=context["domain"],
                page_version=self.page_version,
                frame_emitted=frame_emitted,
                keyframe_path=keyframe_path,
                element_id=target["element_id"],
                value_after=None if sensitive_target else value_after,
                value_redacted=sensitive_target if request.command == "type" else None,
                focus_changed=focus_changed,
                url_changed=url_changed,
                details={**outcome, "value_after": None if sensitive_target else value_after},
            ).model_dump(mode="json")
            return self._result(
                request,
                status="success" if verified else "partial",
                summary_text=(
                    f"Clicked {target['label']}."
                    if request.command == "click"
                    else f"Typed into {target['label']}."
                ),
                reason=reason,
                evidence=evidence,
                source_url=context["url"],
                domain=context["domain"],
                page_version=self.page_version,
            )

        if request.command == "stop":
            frame_emitted, keyframe_path = await self._capture_command_frame("command_stop")
            context = await self._browser_status_context()
            evidence = BrowserEvidence(
                verified=bool(context["url"]),
                final_url=context["url"],
                title=context["title"],
                domain=context["domain"],
                page_version=self.page_version,
                frame_emitted=frame_emitted,
                keyframe_path=keyframe_path,
            ).model_dump(mode="json")
            return self._result(
                request,
                status="success",
                summary_text="Lumon stopped before the next irreversible step.",
                evidence=evidence,
                source_url=context["url"],
                domain=context["domain"],
                page_version=self.page_version,
            )

        return self._result(
            request,
            status="unsupported",
            summary_text=f"{request.command} is not supported by the browser delegate.",
            reason="unsupported_command",
        )

    async def _run_primary_flow(self) -> None:
        assert self.action_layer is not None
        await self._wait_for_run_permission()
        await self.action_layer.click("#destination", "Opening destination input", "Focus destination field")
        await self._wait_for_run_permission()
        await self.action_layer.type_text("#destination", "NYC", "Entering destination", "Type destination city")
        await self._wait_for_run_permission()
        await self.action_layer.type_text("#dates", "Apr 18 - Apr 20", "Entering travel dates", "Type travel dates")
        await self._wait_for_run_permission()
        await self.action_layer.click("#search-button", "Searching hotel results", "Run the hotel search")
        await self._wait_for_run_permission()
        await self.action_layer.spawn_background_worker()
        await self._wait_for_run_permission()
        await self.action_layer.read_region("#results-list", "Reviewing shortlist", "Inspect the top hotel results")
        await self._wait_for_run_permission()
        await self.action_layer.scroll_by(320, "Scanning lower results", "Scroll the result grid")
        approved = await self._approval_gate(
            summary_text="Ready to create shortlist",
            intent="Create the final shortlist from the filtered results",
            risk_reason="Final irreversible transition",
            action_type="click",
        )
        if not approved:
            await self.runtime.emit_agent_event(
                {
                    "event_seq": next(self.event_seq),
                    "event_id": new_id("evt"),
                    "source_event_id": new_id("src"),
                    "timestamp": self.runtime.timestamp(),
                    "session_id": self.runtime.session_id,
                    "adapter_id": self.adapter_id,
                    "adapter_run_id": self.adapter_run_id,
                    "agent_id": "main_001",
                    "parent_agent_id": None,
                    "agent_kind": "main",
                    "environment_id": "env_browser_main",
                    "visibility_mode": "foreground",
                    "action_type": "wait",
                    "state": "waiting",
                    "summary_text": "Shortlist creation rejected",
                    "intent": "Abort the risky transition",
                    "risk_level": RiskLevel.HIGH.value,
                    "subagent_source": None,
                    "cursor": {"x": 700, "y": 650},
                    "target_rect": {"x": 620, "y": 610, "width": 160, "height": 52},
                    "meta": {"rejected": True},
                }
            )
            await self.runtime.complete_task(status="stopped", summary_text="Operator rejected the shortlist step")
            return

        await self.action_layer.click("#shortlist-button", "Creating shortlist", "Submit the shortlist step", risky=True)
        await self._wait_for_run_permission()
        await self.action_layer.spawn_same_scene_subagent()
        await self._wait_for_run_permission()
        await self.action_layer.complete_same_scene_subagent()
        await self.action_layer.complete_background_worker()
        await self.action_layer.read_region("#shortlist-status", "Reading completion notice", "Confirm shortlist success")
        await self.runtime.complete_task(status="completed", summary_text="Shortlisted three hotels under budget")

    async def _run_backup_flow(self) -> None:
        assert self.action_layer is not None
        await self._wait_for_run_permission()
        await self.action_layer.click("#backup-destination", "Opening backup destination field", "Focus backup destination field")
        await self._wait_for_run_permission()
        await self.action_layer.type_text("#backup-destination", "NYC", "Entering backup destination", "Type destination city")
        await self._wait_for_run_permission()
        await self.action_layer.type_text("#backup-dates", "Apr 18 - Apr 20", "Entering backup travel dates", "Type travel dates")
        await self._wait_for_run_permission()
        await self.action_layer.click("#backup-search", "Running backup search", "Execute the backup hotel search")
        approved = await self._approval_gate(
            summary_text="Ready to approve backup shortlist",
            intent="Approve the fallback shortlist transition",
            risk_reason="Final shortlist confirmation",
            action_type="click",
        )
        if approved:
            await self.action_layer.click("#backup-shortlist", "Approving backup shortlist", "Commit the backup shortlist", risky=True)
            await self.runtime.complete_task(status="completed", summary_text="Backup shortlist complete")
        else:
            await self.runtime.complete_task(status="stopped", summary_text="Backup shortlist rejected")

    async def _adopt_page(self, page: Page) -> None:
        if self.context is None:
            return
        if self.page is page:
            return
        self.page = page
        if self.action_layer is not None:
            self.action_layer.page = page
        await self._stop_webrtc_capture_loop()
        if self.stream_mode == "option_a":
            if self.live_streamer is not None:
                await self.live_streamer.stop()
                self.live_streamer = None
            if self.option_a_streamer is not None:
                await self.option_a_streamer.stop()
            self.option_a_streamer = ScreenshotPollStreamer(page, self.runtime.emit_frame)
            await self.option_a_streamer.start()
        else:
            if self.option_a_streamer is not None:
                await self.option_a_streamer.stop()
                self.option_a_streamer = None
            if self.live_streamer is not None:
                await self.live_streamer.stop()
            self.cdp_session = await self.context.new_cdp_session(page)
            self.live_streamer = CDPScreencastStreamer(self.cdp_session, self.runtime.emit_frame)
            await self.live_streamer.start()
            asyncio.create_task(self._watch_live_stream_health())
        await self._sync_page_version(force=True)
        if self.action_layer is not None:
            await self.action_layer.refresh_browser_context()

    async def _maybe_switch_to_foreground_page(self) -> None:
        if self.context is None:
            return
        pages = getattr(self.context, "pages", None)
        if not pages:
            return
        latest_page = pages[-1]
        if latest_page is not self.page:
            await self._adopt_page(latest_page)

    async def _sync_page_version(self, *, force: bool) -> bool:
        await self._maybe_switch_to_foreground_page()
        current_url = str(getattr(self.page, "url", "") or "")
        if force or current_url != self.current_page_url:
            self.page_version += 1
            self.current_page_url = current_url
            self.element_refs.clear()
            return True
        return False

    async def _emit_snapshot_frame(self, *, command_snapshot: bool = False) -> bool:
        if self.page is None or not hasattr(self.page, "screenshot"):
            self.last_snapshot_error = "page_unavailable"
            return False
        try:
            raw = await self.page.screenshot(type="png")
        except Exception as exc:
            self.last_snapshot_error = str(exc)
            return False
        self.last_snapshot_error = None
        await self.runtime.emit_frame(
            {
                "mime_type": "image/png",
                "data_base64": base64.b64encode(raw).decode("ascii"),
                "frame_seq": next(self.snapshot_frame_seq),
                "__skip_webrtc": True,
                "__command_snapshot": command_snapshot,
            }
        )
        return True

    async def _emit_snapshot_frame_with_retry(
        self,
        *,
        attempts: int = 5,
        delay_seconds: float = 0.2,
        command_snapshot: bool = False,
    ) -> bool:
        for attempt in range(attempts):
            if await self._emit_snapshot_frame(command_snapshot=command_snapshot):
                return True
            if attempt < attempts - 1:
                await asyncio.sleep(delay_seconds)
        return False

    async def _wait_for_fresh_frame(self, previous_generation: int, *, timeout_seconds: float = 1.2) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            if getattr(self.runtime, "latest_frame_generation", 0) > previous_generation:
                return True
            await asyncio.sleep(0.05)
        return getattr(self.runtime, "latest_frame_generation", 0) > previous_generation

    async def _wait_for_fresh_command_frame(self, previous_generation: int, *, timeout_seconds: float = 1.2) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            if getattr(self.runtime, "latest_command_frame_generation", 0) > previous_generation:
                return True
            await asyncio.sleep(0.05)
        return getattr(self.runtime, "latest_command_frame_generation", 0) > previous_generation

    async def _capture_live_keyframe(self, reason: str) -> str | None:
        capture = getattr(self.runtime, "capture_live_keyframe", None)
        if capture is None:
            return None
        return await capture(reason)

    async def _capture_command_frame(self, reason: str) -> tuple[bool, str | None]:
        previous_generation = getattr(self.runtime, "latest_command_frame_generation", 0)
        emitted = await self._emit_snapshot_frame_with_retry(command_snapshot=True)
        frame_emitted = await self._wait_for_fresh_command_frame(previous_generation) if emitted else False
        if not frame_emitted:
            frame_emitted = await self._wait_for_fresh_command_frame(previous_generation, timeout_seconds=1.6)
        keyframe_path = await self._capture_live_keyframe(reason) if frame_emitted else None
        return frame_emitted, keyframe_path

    async def _browser_status_context(self) -> dict[str, Any]:
        assert self.action_layer is not None
        status = await self.action_layer.current_status()
        browser_context = status["browser_context"]
        await self._sync_page_version(force=False)
        return {
            "url": browser_context["url"],
            "title": browser_context.get("title"),
            "domain": browser_context["domain"],
            "environment_type": browser_context["environment_type"],
            "active_element": status.get("active_element"),
            "scroll_y": status.get("scroll_y"),
        }

    def _reset_command_task_state(self) -> None:
        self.command_results.clear()
        self.pending_browser_commands.clear()
        self.latest_checkpoint_id = None
        self.suspended_checkpoint_id = None
        self.page_version = 0
        self.current_page_url = None
        self.element_refs.clear()
        self.snapshot_frame_seq = count(1)
        self.command_delegate_error = None
        self.last_snapshot_error = None
        self.last_begin_task_open_url = None
        self.last_begin_task_opened_at = 0.0

    def _infer_url_from_task_text(self, task_text: str | None) -> str | None:
        if not task_text:
            return None
        match = _URL_PATTERN.search(task_text)
        if not match:
            return None
        candidate = match.group(0).rstrip(".,)")
        if candidate.lower().startswith(("http://", "https://")):
            return candidate
        return f"https://{candidate}"

    def _bridge_is_alive(self) -> bool:
        if self.command_mode and self.command_stop_event.is_set():
            return False
        if self.browser is None or self.context is None or self.page is None:
            return False
        with contextlib.suppress(Exception):
            if hasattr(self.page, "is_closed") and self.page.is_closed():
                return False
        return True

    def _register_element_refs(self, raw_elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        elements: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_elements, start=1):
            element_id = f"el_{self.page_version}_{index:02d}"
            sensitive = bool(raw.get("sensitive") or raw.get("input_type") in {"password", "file"})
            stored = {
                "element_id": element_id,
                "label": str(raw.get("label") or f"element {index}"),
                "role": str(raw.get("role") or "element"),
                "selector": str(raw.get("selector") or ""),
                "typeable": bool(raw.get("typeable")),
                "clickable": bool(raw.get("clickable")),
                "input_type": raw.get("input_type"),
                "page_version": self.page_version,
                "bbox": raw.get("bbox"),
                "sensitive": sensitive,
            }
            self.element_refs[element_id] = stored
            elements.append(
                BrowserElementRef(
                    element_id=element_id,
                    label=stored["label"],
                    role=stored["role"],
                    typeable=stored["typeable"],
                    clickable=stored["clickable"],
                    input_type=stored["input_type"],
                    value_preview=None if sensitive else raw.get("value_preview"),
                    bbox=raw.get("bbox"),
                    page_version=self.page_version,
                    sensitive=sensitive,
                ).model_dump(mode="json")
            )
        return elements

    def _resolve_target(self, request: BrowserCommandRequest) -> dict[str, Any] | None:
        if request.element_id:
            return self.element_refs.get(request.element_id)
        if request.selector:
            return {
                "element_id": request.selector,
                "label": request.selector,
                "role": "selector",
                "selector": request.selector,
                "typeable": True,
                "clickable": True,
                "input_type": None,
                "page_version": self.page_version,
                "bbox": None,
                "sensitive": False,
            }
        return None

    def _unsupported_command_reason(self, request: BrowserCommandRequest, *, target: dict[str, Any] | None) -> str | None:
        input_type = str((target or {}).get("input_type") or "").lower()
        if request.command == "type" and input_type == "file":
            return "File upload workflows are not supported."
        return None

    @staticmethod
    def _command_cache_key(command: str, command_id: str) -> str:
        return f"{command}:{command_id}"

    def _command_risk(self, request: BrowserCommandRequest, *, target: dict[str, Any] | None) -> str | None:
        url = (self.current_page_url or "").lower()
        label = str((target or {}).get("label") or "").lower()
        selector = str((target or {}).get("selector") or "").lower()
        input_type = str((target or {}).get("input_type") or "").lower()
        risk_text = " ".join([url, label, selector, input_type, request.text or ""]).lower()
        if request.command == "type" and (input_type in {"password", "email", "file"} or any(token in risk_text for token in ("password", "token", "secret", "login", "sign in", "auth"))):
            if input_type == "file":
                return "File upload workflows are not supported."
            return "Typing into a sensitive or authenticated field needs approval."
        if request.command == "click" and any(token in risk_text for token in ("submit", "delete", "remove", "purchase", "pay", "checkout", "save", "grant", "allow", "login", "sign in", "settings", "billing", "admin")):
            return "This click could change real state or enter a sensitive flow."
        return None

    def _classify_command_exception(self, exc: Exception) -> tuple[str, str]:
        message = str(exc)
        lowered = message.lower()
        if "bounding_box" in lowered and "timeout" in lowered:
            return (
                "target_resolution_timeout",
                "Lumon could not resolve that page target in time.",
            )
        if isinstance(exc, asyncio.TimeoutError) or "timeout" in lowered:
            return (
                "action_timeout",
                "Lumon could not finish that browser step in time.",
            )
        if not self._bridge_is_alive() or any(
            token in lowered
            for token in (
                "browser has been closed",
                "context closed",
                "page closed",
                "target page, context or browser has been closed",
                "target closed",
                "connection closed",
                "session closed",
            )
        ):
            return (
                "delegate_crashed",
                "Lumon lost the live browser delegate while executing that step.",
            )
        return (
            "action_failed",
            "Lumon could not complete that browser step.",
        )

    def _log_command_exception(self, request: BrowserCommandRequest, exc: Exception, *, reason: str) -> None:
        print(
            (
                "[lumon] browser_command_exception "
                f"command={request.command} "
                f"command_id={request.command_id} "
                f"reason={reason} "
                f"error={exc}"
            ),
            file=sys.stderr,
            flush=True,
        )
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)

    async def _blocked_for_approval(
        self,
        request: BrowserCommandRequest,
        *,
        summary_text: str,
        risk_reason: str,
        target: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        checkpoint_id = new_id("chk")
        event_id = new_id("evt")
        intervention_id = new_id("intv")
        self.latest_checkpoint_id = checkpoint_id
        await self.runtime.transition_to(SessionState.WAITING_FOR_APPROVAL, checkpoint_id=checkpoint_id)
        await self.runtime.emit_approval_required(
            {
                "session_id": self.runtime.session_id,
                "intervention_id": intervention_id,
                "checkpoint_id": checkpoint_id,
                "event_id": event_id,
                "action_type": request.command,
                "summary_text": summary_text,
                "intent": request.task_text or summary_text,
                "risk_level": RiskLevel.HIGH.value,
                "risk_reason": risk_reason,
                "adapter_id": self.adapter_id,
                "adapter_run_id": self.adapter_run_id,
                "source_url": self.current_page_url,
                "target_summary": (target or {}).get("label"),
                "headline": "Needs your approval",
                "reason_text": risk_reason,
            }
        )
        return self._result(
            request,
            status="blocked",
            summary_text=summary_text,
            reason="awaiting_approval",
            source_url=self.current_page_url,
            domain=urllib.parse.urlparse(self.current_page_url or "").hostname,
            page_version=self.page_version,
            checkpoint_id=checkpoint_id,
            intervention_id=intervention_id,
        )

    async def _wait_for_condition(self, request: BrowserCommandRequest) -> bool:
        timeout_ms = request.timeout_ms or 2000
        if request.wait_for_selector and self.page is not None:
            try:
                await self.page.locator(request.wait_for_selector).first.wait_for(state="visible", timeout=timeout_ms)
                return True
            except Exception:
                return False
        if request.wait_for_text and self.page is not None:
            try:
                await self.page.wait_for_function(
                    "(needle) => document.body && document.body.innerText.includes(needle)",
                    request.wait_for_text,
                    timeout=timeout_ms,
                )
                return True
            except Exception:
                return False
        await asyncio.sleep(timeout_ms / 1000)
        return True

    def _record_command_artifact(self, result: dict[str, Any]) -> None:
        record = BrowserCommandRecord(
            command_id=result["command_id"],
            command=result["command"],
            status=result["status"],
            summary_text=result["summary_text"],
            timestamp=self.runtime.timestamp(),
            reason=result.get("reason"),
            source_url=result.get("source_url"),
            domain=result.get("domain"),
            page_version=result.get("page_version"),
            evidence=result.get("evidence"),
            actionable_elements=result.get("actionable_elements", []),
            intervention_id=result.get("intervention_id"),
            checkpoint_id=result.get("checkpoint_id"),
            meta=result.get("meta", {}),
        )
        artifact = getattr(self.runtime, "record_browser_command", None)
        if artifact is not None:
            artifact(record)

    def _result(
        self,
        request: BrowserCommandRequest,
        *,
        status: str,
        summary_text: str,
        reason: str | None = None,
        evidence: dict[str, Any] | None = None,
        actionable_elements: list[dict[str, Any]] | None = None,
        source_url: str | None = None,
        domain: str | None = None,
        page_version: int | None = None,
        checkpoint_id: str | None = None,
        intervention_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return BrowserCommandResult(
            command_id=request.command_id,
            command=request.command,
            status=status,  # type: ignore[arg-type]
            summary_text=summary_text,
            reason=reason,
            session_id=self.runtime.session_id,
            source_url=source_url,
            domain=domain,
            page_version=page_version,
            evidence=evidence,
            actionable_elements=actionable_elements or [],
            intervention_id=intervention_id,
            checkpoint_id=checkpoint_id,
            meta=meta or {},
        ).model_dump(mode="json")

    async def _launch_browser(self) -> None:
        if async_playwright is None:  # pragma: no cover
            raise RuntimeError("Playwright is not installed")
        headless = os.getenv("LUMON_HEADLESS", "1") != "0"
        scale_factor_raw = os.getenv("LUMON_DEVICE_SCALE_FACTOR", "2")
        try:
            scale_factor = float(scale_factor_raw)
        except ValueError:
            scale_factor = 1.0
        if scale_factor <= 0:
            scale_factor = 1.0
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=headless)
        self.context = await self.browser.new_context(
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            device_scale_factor=scale_factor,
        )
        self.page = await self.context.new_page()
        with contextlib.suppress(Exception):
            self.context.on("page", lambda page: asyncio.create_task(self._adopt_page(page)))
        self.cdp_session = await self.context.new_cdp_session(self.page)
        self.action_layer = BrowserActionLayer(
            session_id=self.runtime.session_id,
            adapter_id=self.adapter_id,
            adapter_run_id=self.adapter_run_id,
            page=self.page,
            emit_event=self.runtime.emit_agent_event,
            emit_worker_update=self.runtime.emit_background_worker_update,
            emit_browser_context=self.runtime.emit_browser_context_update,
            event_seq_supplier=lambda: next(self.event_seq),
            gate_check=self._wait_for_run_permission,
        )
        await self._sync_page_version(force=True)

    async def _start_stream_transport(self) -> None:
        assert self.page is not None
        await self._stop_webrtc_capture_loop()
        if self.stream_mode == "option_a":
            if self.live_streamer is not None:
                await self.live_streamer.stop()
                self.live_streamer = None
            interval_raw = os.getenv("LUMON_SCREENSHOT_INTERVAL_SECONDS", "0.2")
            try:
                interval_seconds = float(interval_raw)
            except ValueError:
                interval_seconds = 0.2
            if interval_seconds <= 0:
                interval_seconds = 0.2
            self.option_a_streamer = ScreenshotPollStreamer(
                self.page,
                self.runtime.emit_frame,
                interval_seconds=interval_seconds,
            )
            await self.option_a_streamer.start()
            return

        if self.option_a_streamer is not None:
            await self.option_a_streamer.stop()
            self.option_a_streamer = None
        assert self.cdp_session is not None
        self.live_streamer = CDPScreencastStreamer(self.cdp_session, self.runtime.emit_frame)
        await self.live_streamer.start()
        self.live_stream_health_task = asyncio.create_task(self._watch_live_stream_health())

    async def _watch_live_stream_health(self) -> None:
        if self.live_streamer is None:
            return
        await self.live_streamer.fallback_requested.wait()
        await self._switch_to_option_a()

    async def _switch_to_option_a(self) -> None:
        if self.stream_mode == "option_a":
            return
        self.stream_mode = "option_a"
        if self.live_streamer is not None:
            await self.live_streamer.stop()
            self.live_streamer = None
        if self.page is None:
            return
        self.option_a_streamer = ScreenshotPollStreamer(self.page, self.runtime.emit_frame)
        await self.option_a_streamer.start()

    async def _stop_webrtc_capture_loop(self) -> None:
        if self.live_stream_health_task is None:
            return
        task = self.live_stream_health_task
        self.live_stream_health_task = None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _shutdown_browser(self) -> None:
        await self._stop_webrtc_capture_loop()
        if self.live_streamer is not None:
            await self.live_streamer.stop()
            self.live_streamer = None
        if self.option_a_streamer is not None:
            await self.option_a_streamer.stop()
            self.option_a_streamer = None
        if self.context is not None:
            await self.context.close()
            self.context = None
        if self.browser is not None:
            await self.browser.close()
            self.browser = None
        if self.playwright is not None:
            await self.playwright.stop()
            self.playwright = None
        self.page = None
        self.cdp_session = None

    async def _wait_for_run_permission(self) -> None:
        if self.runtime.state == SessionState.PAUSE_REQUESTED:
            await self.runtime.transition_to(SessionState.PAUSED)
        while self.runtime.state in {SessionState.PAUSED, SessionState.TAKEOVER, SessionState.WAITING_FOR_APPROVAL}:
            self.resume_event.clear()
            await self.resume_event.wait()
        if self.runtime.state == SessionState.PAUSE_REQUESTED:
            await self.runtime.transition_to(SessionState.PAUSED)
            self.resume_event.clear()
            await self.resume_event.wait()

    async def _approval_gate(self, *, summary_text: str, intent: str, risk_reason: str, action_type: str) -> bool:
        checkpoint_id = new_id("chk")
        self.latest_checkpoint_id = checkpoint_id
        self.approval_future = asyncio.get_running_loop().create_future()
        await self.runtime.transition_to(SessionState.WAITING_FOR_APPROVAL, checkpoint_id=checkpoint_id)
        await self.runtime.emit_approval_required(
            {
                "session_id": self.runtime.session_id,
                "checkpoint_id": checkpoint_id,
                "event_id": new_id("evt"),
                "action_type": action_type,
                "summary_text": summary_text,
                "intent": intent,
                "risk_level": RiskLevel.HIGH.value,
                "risk_reason": risk_reason,
                "adapter_id": self.adapter_id,
                "adapter_run_id": self.adapter_run_id,
            }
        )
        approved = await self.approval_future
        self.approval_future = None
        if self.suspended_checkpoint_id == checkpoint_id:
            self.suspended_checkpoint_id = None
            await self._wait_for_run_permission()
            return await self._approval_gate(
                summary_text=summary_text,
                intent=intent,
                risk_reason=risk_reason,
                action_type=action_type,
            )
        await self.runtime.transition_to(SessionState.RUNNING, checkpoint_id=None)
        return approved

    async def pause(self) -> None:
        if self.runtime.state in {SessionState.PAUSED, SessionState.PAUSE_REQUESTED}:
            await self.runtime.emit_session_state()
            return
        await self.runtime.transition_to(SessionState.PAUSE_REQUESTED)

    async def resume(self) -> None:
        if self.runtime.state == SessionState.RUNNING:
            await self.runtime.emit_session_state()
            return
        if self.runtime.state not in {SessionState.PAUSED, SessionState.PAUSE_REQUESTED}:
            await self.runtime.emit_error(ErrorCode.INVALID_STATE, "Cannot resume from current state", command_type="resume")
            return
        await self.runtime.transition_to(SessionState.RUNNING)
        self.resume_event.set()

    async def approve(self, checkpoint_id: str) -> bool:
        if self.command_mode:
            pending_entry = next(
                (
                    (command_key, command)
                    for command_key, command in self.pending_browser_commands.items()
                    if command.get("checkpoint_id") == checkpoint_id and command.get("state") == "awaiting_approval"
                ),
                None,
            )
            pending = pending_entry[1] if pending_entry is not None else None
            if pending is None:
                await self.runtime.emit_error(
                    ErrorCode.CHECKPOINT_STALE,
                    "Checkpoint is stale",
                    command_type="approve",
                    checkpoint_id=checkpoint_id,
                )
                return False
            command_key = pending_entry[0]
            request = BrowserCommandRequest.model_validate(pending["request"])
            self.pending_browser_commands.pop(command_key, None)
            await self.runtime.transition_to(SessionState.RUNNING, checkpoint_id=None)
            async with self.command_lock:
                self.command_inflight_id = command_key
                try:
                    return await self._execute_browser_command_locked(
                        request,
                        command_key=command_key,
                        approval_granted=True,
                    )
                finally:
                    self.command_inflight_id = None
        if self.runtime.state != SessionState.WAITING_FOR_APPROVAL or self.latest_checkpoint_id != checkpoint_id:
            await self.runtime.emit_error(
                ErrorCode.CHECKPOINT_STALE,
                "Checkpoint is stale",
                command_type="approve",
                checkpoint_id=checkpoint_id,
            )
            return False
        if self.approval_future and not self.approval_future.done():
            self.approval_future.set_result(True)
            self.resume_event.set()
        return True

    async def reject(self, checkpoint_id: str) -> bool:
        if self.command_mode:
            pending = next(
                (
                    command
                    for command in self.pending_browser_commands.values()
                    if command.get("checkpoint_id") == checkpoint_id and command.get("state") == "awaiting_approval"
                ),
                None,
            )
            if pending is None:
                await self.runtime.emit_error(
                    ErrorCode.CHECKPOINT_STALE,
                    "Checkpoint is stale",
                    command_type="reject",
                    checkpoint_id=checkpoint_id,
                )
                return False
            pending["state"] = "denied"
            pending["result"] = {
                **pending["result"],
                "status": "blocked",
                "reason": "denied",
                "summary_text": "You denied that browser step.",
            }
            denied_result = pending["result"]
            command_key = self._command_cache_key(str(denied_result["command"]), str(denied_result["command_id"]))
            self.command_results[command_key] = denied_result
            self.pending_browser_commands.pop(command_key, None)
            await self.runtime.transition_to(SessionState.RUNNING, checkpoint_id=None)
            self._record_command_artifact(denied_result)
            return True
        if self.runtime.state != SessionState.WAITING_FOR_APPROVAL or self.latest_checkpoint_id != checkpoint_id:
            await self.runtime.emit_error(
                ErrorCode.CHECKPOINT_STALE,
                "Checkpoint is stale",
                command_type="reject",
                checkpoint_id=checkpoint_id,
            )
            return False
        if self.approval_future and not self.approval_future.done():
            self.approval_future.set_result(False)
            self.resume_event.set()
        return True


    def _can_remote_control(self) -> bool:
        from app.protocol.enums import SessionState
        return self.runtime.state in {
            SessionState.TAKEOVER,
            SessionState.COMPLETED,
            SessionState.STOPPED,
            SessionState.FAILED,
            SessionState.PAUSED,
        }

    async def remote_mouse_move(self, x: float, y: float) -> None:
        if not self._can_remote_control() or not self.page:
            return
        await self.page.mouse.move(x, y)

    async def remote_mouse_down(self, x: float, y: float, button: str = "left") -> None:
        if not self._can_remote_control() or not self.page:
            return
        await self.page.mouse.move(x, y)
        await self.page.mouse.down(button=button)

    async def remote_mouse_up(self, x: float, y: float, button: str = "left") -> None:
        if not self._can_remote_control() or not self.page:
            return
        await self.page.mouse.move(x, y)
        await self.page.mouse.up(button=button)

    async def remote_click(self, x: float, y: float, button: str = "left") -> None:
        if not self._can_remote_control() or not self.page:
            return
        await self.page.mouse.click(x, y, button=button)

    async def remote_scroll(self, delta_x: float, delta_y: float) -> None:
        if not self._can_remote_control() or not self.page:
            return
        await self.page.mouse.wheel(delta_x, delta_y)

    async def remote_key_down(self, key: str) -> None:
        if not self._can_remote_control() or not self.page:
            return
        await self.page.keyboard.down(key)

    async def remote_key_up(self, key: str) -> None:
        if not self._can_remote_control() or not self.page:
            return
        await self.page.keyboard.up(key)

    async def start_takeover(self) -> None:
        if self.runtime.state == SessionState.TAKEOVER:
            await self.runtime.emit_session_state()
            return
        if self.runtime.state not in {
            SessionState.RUNNING,
            SessionState.PAUSE_REQUESTED,
            SessionState.PAUSED,
            SessionState.WAITING_FOR_APPROVAL,
        }:
            await self.runtime.emit_error(ErrorCode.INVALID_STATE, "Cannot enter takeover from current state", command_type="start_takeover")
            return
        if self.runtime.state == SessionState.WAITING_FOR_APPROVAL:
            self.suspended_checkpoint_id = self.latest_checkpoint_id
            if self.approval_future and not self.approval_future.done():
                self.approval_future.set_result(False)
        await self.runtime.transition_to(SessionState.TAKEOVER, checkpoint_id=self.latest_checkpoint_id)

    async def end_takeover(self) -> None:
        if self.runtime.state != SessionState.TAKEOVER:
            await self.runtime.emit_error(ErrorCode.INVALID_STATE, "Cannot end takeover from current state", command_type="end_takeover")
            return
        stale_checkpoint_id = self.suspended_checkpoint_id
        self.suspended_checkpoint_id = None
        self.latest_checkpoint_id = None
        await self.runtime.transition_to(SessionState.PAUSED, checkpoint_id=None)
        if stale_checkpoint_id:
            await self.runtime.emit_error(
                ErrorCode.CHECKPOINT_STALE,
                "Checkpoint invalidated by takeover",
                checkpoint_id=stale_checkpoint_id,
            )

    async def accept_bridge(self) -> bool:
        await self.runtime.emit_error(ErrorCode.INVALID_STATE, "Playwright adapter does not support bridge offers", command_type="accept_bridge")
        return False

    async def decline_bridge(self) -> bool:
        await self.runtime.emit_error(ErrorCode.INVALID_STATE, "Playwright adapter does not support bridge offers", command_type="decline_bridge")
        return False

    async def stop(self) -> None:
        self.command_delegate_error = None
        if self.command_mode:
            self.command_stop_event.set()
            if self.run_task and not self.run_task.done():
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.run_task, timeout=2.0)
                if self.run_task and not self.run_task.done():
                    self.run_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self.run_task
        elif self.run_task and not self.run_task.done():
            self.run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.run_task
        self.resume_event.set()
        await self._shutdown_browser()

    def _configured_stream_mode(self) -> StreamMode:
        mode = os.getenv("LUMON_STREAM_MODE", "live").lower()
        return "option_a" if mode == "option_a" else "live"

    def _configured_webrtc_primary(self) -> bool:
        value = os.getenv("LUMON_WEBRTC_PRIMARY")
        if value is None:
            return True
        return value.strip().lower() in {"1", "true", "yes", "on"}

    def _configured_demo_variant(self) -> DemoVariant:
        variant = os.getenv("LUMON_DEMO_VARIANT", "primary").lower()
        return "backup" if variant == "backup" else "primary"


class SessionRuntimeProtocol:
    session_id: str
    state: SessionState
    task_text: str
    adapter_run_id: str | None
    latest_frame_seq: int | None
    latest_frame_generation: int

    async def emit_agent_event(self, payload: dict[str, Any]) -> None: ...
    async def emit_background_worker_update(self, payload: dict[str, Any]) -> None: ...
    async def emit_approval_required(self, payload: dict[str, Any]) -> None: ...
    async def emit_error(self, code: ErrorCode, message: str, command_type: str | None = None, checkpoint_id: str | None = None) -> None: ...
    async def emit_frame(self, payload: dict[str, Any]) -> None: ...
    def push_webrtc_frame_bytes(self, mime_type: str, data: bytes) -> None: ...
    async def emit_session_state(self) -> None: ...
    async def transition_to(self, state: SessionState, checkpoint_id: str | None = None) -> None: ...
    async def complete_task(self, status: str, summary_text: str) -> None: ...
    async def capture_live_keyframe(self, reason: str) -> str | None: ...
    def record_browser_command(self, record: BrowserCommandRecord) -> None: ...
    def clear_active_interventions(self, *, resolution: str = "expired") -> None: ...
    def timestamp(self) -> str: ...
