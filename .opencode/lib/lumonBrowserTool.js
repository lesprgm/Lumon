import { tool } from "@opencode-ai/plugin";

import {
  ACTIVE_BROWSER_TASK_WINDOW_MS,
  assertNonEmptyStringField,
  browserCommandWithAutoStart,
  debugTrace,
  isDirectTakeoverActiveContext,
  resolveCommandId,
  resolveDirectory,
  resolveSessionIdFromContext,
  shouldOpenForBrowserCommandResult,
} from "./lumonPluginShared.js";

export function createLumonBrowserTool({ config, helpers }) {
  const sessionOpenState = new Map();
  const repeatedFrameMissing = new Map();
  const runtimeSessionIds = new Map();
  const visibleRuntimeSessions = new Set();

  const fireUiTelemetry = ({ sessionId, event, meta = {} }) => {
    if (typeof helpers.recordUiTelemetry === "function") {
      return helpers.recordUiTelemetry({ sessionId, event, meta, source: "plugin" });
    }
    return Promise.resolve();
  };

  const markBrowserTaskActive = (sessionId) => {
    if (!helpers.activeBrowserTasks || typeof sessionId !== "string" || sessionId.trim().length === 0) {
      return;
    }
    helpers.activeBrowserTasks.set(sessionId, {
      source: "lumon_browser",
      expiresAt: Date.now() + ACTIVE_BROWSER_TASK_WINDOW_MS,
    });
  };

  const clearBrowserTaskActive = (sessionId) => {
    if (!helpers.activeBrowserTasks || typeof sessionId !== "string" || sessionId.trim().length === 0) {
      return;
    }
    helpers.activeBrowserTasks.delete(sessionId);
  };

  const resolveTrackedSession = (stateKey, telemetrySessionId) => {
    if (!helpers.sessions || typeof helpers.sessions.get !== "function") {
      return null;
    }
    if (typeof stateKey === "string" && stateKey.trim().length > 0) {
      const direct = helpers.sessions.get(stateKey);
      if (direct) {
        return direct;
      }
    }
    if (typeof telemetrySessionId === "string" && telemetrySessionId.trim().length > 0) {
      for (const session of helpers.sessions.values()) {
        if (session?.lumonSessionId === telemetrySessionId) {
          return session;
        }
      }
    }
    return null;
  };

  const maybeOpenUrl = async ({ stateKey, telemetrySessionId, result, commandName, uiConnected = false }) => {
    const url = result?.open_url;
    if (typeof stateKey !== "string" || stateKey.trim().length === 0) {
      return;
    }
    if (typeof url !== "string" || url.trim().length === 0) {
      return;
    }
    const resolvedTelemetrySessionId =
      typeof telemetrySessionId === "string" && telemetrySessionId.trim().length > 0
        ? telemetrySessionId
        : stateKey;
    const now = Date.now();
    const isIntervention = Boolean(result?.intervention_id || result?.status === "blocked");
    const state = sessionOpenState.get(stateKey) || {
      taskSequence: 0,
      openedTaskSequence: 0,
      lastOpenedAt: 0,
      lastOpenedUrl: null,
      lastInterventionKey: null,
    };

    if (state.taskSequence === 0) {
      state.taskSequence = 1;
    }

    const trackedSession = resolveTrackedSession(stateKey, resolvedTelemetrySessionId);
    if (isDirectTakeoverActiveContext(result, trackedSession)) {
      void fireUiTelemetry({
        sessionId: resolvedTelemetrySessionId,
        event: "open_suppressed",
        meta: { reason_code: "direct_takeover_mode", command: commandName },
      });
      sessionOpenState.set(stateKey, state);
      return;
    }

    const alreadyVisible = uiConnected || visibleRuntimeSessions.has(resolvedTelemetrySessionId);

    if (!isIntervention && alreadyVisible && state.lastOpenedUrl === url) {
      debugTrace("toolOpen.suppressed_already_visible", {
        stateKey,
        sessionId: resolvedTelemetrySessionId,
        command: commandName,
        url,
      });
      void fireUiTelemetry({
        sessionId: resolvedTelemetrySessionId,
        event: "open_suppressed",
        meta: { reason_code: "already_visible", command: commandName },
      });
      sessionOpenState.set(stateKey, state);
      return;
    }

    if (
      isIntervention &&
      state.openedTaskSequence === state.taskSequence &&
      state.lastOpenedUrl === url &&
      now - state.lastOpenedAt < config.reopenCooldownMs
    ) {
      debugTrace("toolOpen.suppressed_active_intervention_session", {
        sessionId: resolvedTelemetrySessionId,
        command: commandName,
        url,
        taskSequence: state.taskSequence,
      });
      void fireUiTelemetry({
        sessionId: resolvedTelemetrySessionId,
        event: "open_suppressed",
        meta: { reason_code: "active_intervention_session", command: commandName },
      });
      sessionOpenState.set(stateKey, state);
      return;
    }

    if (isIntervention) {
      const interventionKey =
        result?.intervention_id ||
        result?.checkpoint_id ||
        `${commandName}:${result?.reason || "blocked"}`;
      if (
        state.lastInterventionKey === interventionKey &&
        state.lastOpenedUrl === url &&
        now - state.lastOpenedAt < config.reopenCooldownMs
      ) {
        debugTrace("toolOpen.suppressed_duplicate_intervention", {
          sessionId: resolvedTelemetrySessionId,
          command: commandName,
          interventionKey,
          url,
        });
        void fireUiTelemetry({
          sessionId: resolvedTelemetrySessionId,
          event: "open_suppressed",
          meta: { reason_code: "duplicate_intervention", command: commandName },
        });
        sessionOpenState.set(stateKey, state);
        return;
      }
      state.lastInterventionKey = interventionKey;
    } else if (state.openedTaskSequence === state.taskSequence) {
      debugTrace("toolOpen.suppressed_active_session", {
        sessionId: resolvedTelemetrySessionId,
        command: commandName,
        url,
        taskSequence: state.taskSequence,
      });
      void fireUiTelemetry({
        sessionId: resolvedTelemetrySessionId,
        event: "open_suppressed",
        meta: { reason_code: "active_session", command: commandName },
      });
      sessionOpenState.set(stateKey, state);
      return;
    }

    state.lastOpenedAt = now;
    state.lastOpenedUrl = url;
    if (!isIntervention) {
      state.openedTaskSequence = state.taskSequence;
    }
    sessionOpenState.set(stateKey, state);
    try {
      void fireUiTelemetry({
        sessionId: resolvedTelemetrySessionId,
        event: "open_requested",
        meta: { reason_code: isIntervention ? "intervention" : commandName, command: commandName },
      });
      await helpers.openUrl(url);
      void fireUiTelemetry({
        sessionId: resolvedTelemetrySessionId,
        event: "open_completed",
        meta: { reason_code: isIntervention ? "intervention" : commandName, command: commandName },
      });
    } catch (error) {
      debugTrace("toolOpen.open_failed", {
        sessionId: resolvedTelemetrySessionId,
        command: commandName,
        url,
        message: String(error?.message || error || ""),
      });
      void fireUiTelemetry({
        sessionId: resolvedTelemetrySessionId,
        event: "open_failed",
        meta: {
          reason_code: isIntervention ? "intervention" : commandName,
          command: commandName,
          message: String(error?.message || error || ""),
        },
      });
      await helpers.log(
        `Lumon completed the browser step but could not open the UI automatically. Open Lumon manually if needed: ${url}`,
      );
    }
  };
  return tool({
    description:
      "Use Lumon for interactive browser tasks. Use this tool whenever the user asks to open a page, click, type, scroll, inspect actionable elements, wait for page state, or stop before submitting. Do not claim a browser action succeeded unless this tool returned status=success with evidence. Keep using read-only web tools for fetching or summarizing page content without interaction. If Lumon returns partial with reason=frame_missing, call status at most once; do not loop open or inspect repeatedly.",
    args: {
      command_id: tool.schema.string().optional().describe("Stable idempotency key for this exact browser step."),
      command: tool.schema
        .enum(["begin_task", "status", "inspect", "open", "click", "type", "scroll", "wait", "stop"])
        .describe("Browser command to execute."),
      task_text: tool.schema.string().optional().describe("Natural-language task context for this browser step."),
      url: tool.schema.string().optional().describe("URL to open for command=open, or the known starting page for command=begin_task."),
      element_id: tool.schema.string().optional().describe("Element id returned by inspect. Prefer this over selector."),
      selector: tool.schema.string().optional().describe("CSS selector to use when no element_id is available."),
      text: tool.schema.string().optional().describe("Text to type for command=type."),
      delta_y: tool.schema.number().int().optional().describe("Scroll distance in pixels for command=scroll."),
      wait_for_selector: tool.schema.string().optional().describe("CSS selector to wait for."),
      wait_for_text: tool.schema.string().optional().describe("Page text to wait for."),
      timeout_ms: tool.schema.number().int().positive().optional().describe("Timeout in milliseconds for wait."),
    },
    async execute(args, context) {
      const resolvedCommandId = resolveCommandId(args?.command_id, args?.command);
      const resolvedSessionId = resolveSessionIdFromContext(context);
      const resolvedProjectDirectory = resolveDirectory(context?.directory, helpers.directory);
      const repeatedKey = `${resolvedSessionId}`;
      if (args?.command === "begin_task") {
        const state = sessionOpenState.get(resolvedSessionId) || {
          taskSequence: 0,
          openedTaskSequence: 0,
          lastOpenedAt: 0,
          lastOpenedUrl: null,
          lastInterventionKey: null,
        };
        state.taskSequence += 1;
        state.openedTaskSequence = 0;
        state.lastInterventionKey = null;
        sessionOpenState.set(resolvedSessionId, state);
        repeatedFrameMissing.delete(repeatedKey);
      }
      const payload = {
        observed_session_id: resolvedSessionId,
        project_directory: resolvedProjectDirectory,
        frontend_origin: config.frontendOrigin,
        ...args,
        command_id: resolvedCommandId,
      };
      markBrowserTaskActive(resolvedSessionId);
      if (helpers.commandActivity) {
        helpers.commandActivity.set(`${resolvedSessionId}`, {
          lastCommandAt: Date.now(),
          lastStatus: "inflight",
          lastReason: null,
        });
      }
      assertNonEmptyStringField("observed_session_id", payload.observed_session_id);
      assertNonEmptyStringField("project_directory", payload.project_directory);
      assertNonEmptyStringField("command_id", payload.command_id);
      assertNonEmptyStringField("command", payload.command);

      try {
        const result = await browserCommandWithAutoStart({
          command: helpers.command,
          startApp: helpers.startApp,
          waitForHealth: helpers.waitForHealth,
          payload,
          config,
          log: helpers.log,
        });
        const isFrameMissing = result?.status === "partial" && result?.reason === "frame_missing";
        if (isFrameMissing) {
          const now = Date.now();
          const previous = repeatedFrameMissing.get(repeatedKey);
          const withinWindow = previous && now - previous.lastAt <= config.reopenCooldownMs;
          const nextCount = withinWindow ? previous.count + 1 : 1;
          repeatedFrameMissing.set(repeatedKey, { count: nextCount, lastAt: now });
          if (nextCount >= 2) {
            result.status = "failed";
            result.reason = "repeated_frame_missing";
            result.summary_text =
              "Lumon repeatedly failed to capture a visible browser frame. Stop retrying open or inspect and report the delegate problem.";
            result.meta = {
              ...(result.meta || {}),
              forced_terminal_failure: true,
              repeated_frame_missing_count: nextCount,
            };
          }
        } else {
          repeatedFrameMissing.delete(repeatedKey);
        }
        if (typeof result?.session_id === "string" && result.session_id.trim().length > 0) {
          runtimeSessionIds.set(resolvedSessionId, result.session_id);
          if (result.ui_connected === true) {
            visibleRuntimeSessions.add(result.session_id);
          } else if (result.ui_connected === false) {
            visibleRuntimeSessions.delete(result.session_id);
          }
          if (typeof helpers.upsertSessionFromTool === "function") {
            helpers.upsertSessionFromTool({
              observedSessionId: resolvedSessionId,
              lumonSessionId: result.session_id,
              projectDirectory: resolvedProjectDirectory,
              openUrl: result.open_url || null,
              uiConnected: result.ui_connected === true,
              takeoverMode:
                result?.meta?.takeover_mode === "remote" || result?.meta?.takeover_mode === "direct"
                  ? result.meta.takeover_mode
                  : null,
              takeoverUrl:
                typeof result?.meta?.takeover_url === "string" && result.meta.takeover_url.trim().length > 0
                  ? result.meta.takeover_url
                  : null,
            });
          }
        }
        if (helpers.commandActivity) {
          helpers.commandActivity.set(repeatedKey, {
            lastCommandAt: Date.now(),
            lastStatus: result?.status ?? null,
            lastReason: result?.reason ?? null,
          });
        }
        if (args.command === "stop" && result?.status === "success") {
          if (typeof helpers.markSessionStopped === "function") {
            helpers.markSessionStopped(resolvedSessionId);
            if (typeof helpers.stopAutoResumePollingIfIdle === "function") {
              helpers.stopAutoResumePollingIfIdle();
            }
          }
          clearBrowserTaskActive(resolvedSessionId);
        }
        if (result?.open_url && shouldOpenForBrowserCommandResult(result)) {
          await maybeOpenUrl({
            stateKey: resolvedSessionId,
            telemetrySessionId: result?.session_id || runtimeSessionIds.get(resolvedSessionId) || resolvedSessionId,
            result,
            commandName: args.command,
            uiConnected: result?.ui_connected === true,
          });
        }
        if (typeof context?.metadata === "function") {
          context.metadata({
            title: `Lumon browser: ${args.command}`,
            metadata: {
              status: result.status,
              reason: result.reason ?? null,
              domain: result.domain ?? null,
              page_version: result.page_version ?? null,
            },
          });
        }
        return JSON.stringify(
          {
            command_id: result.command_id,
            command: result.command,
            status: result.status,
            summary_text: result.summary_text,
            reason: result.reason ?? null,
            source_url: result.source_url ?? null,
            domain: result.domain ?? null,
            page_version: result.page_version ?? null,
            actionable_elements: result.actionable_elements ?? [],
            checkpoint_id: result.checkpoint_id ?? null,
            intervention_id: result.intervention_id ?? null,
            evidence: result.evidence ?? null,
            meta: result.meta ?? {},
          },
          null,
          2,
        );
      } catch (error) {
        repeatedFrameMissing.delete(repeatedKey);
        if (helpers.commandActivity) {
          helpers.commandActivity.set(repeatedKey, {
            lastCommandAt: Date.now(),
            lastStatus: "failed",
            lastReason: "tool_exception",
          });
        }
        throw error;
      } finally {
        if (helpers.pendingPromptSteering) {
          helpers.pendingPromptSteering.delete(resolvedSessionId);
        }
      }
    },
  });
}
