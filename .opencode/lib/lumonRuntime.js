import {
  DIRECT_TAKEOVER_ENV,
  EXPECTED_FRONTEND_FEATURES,
  EXPECTED_RUNTIME_VERSION,
  REQUIRED_BACKEND_RUNTIME_FEATURES,
  debugTrace,
  hasRequiredFeatures,
  normalizeOrigin,
  readRuntimeOriginsFromEnvFile,
  resolveDirectory,
} from "./lumonPluginShared.js";

export function createRuntimeHelpers({ $, directory, client, config }) {
  const runtimeDirectory = resolveDirectory(directory);
  const origins = {
    backendOrigin: normalizeOrigin(config.backendOrigin),
    frontendOrigin: normalizeOrigin(config.frontendOrigin),
  };
  const getBackendOrigin = () => origins.backendOrigin;
  const getFrontendOrigin = () => origins.frontendOrigin;
  const frontendServedByBackend = () => getFrontendOrigin() === getBackendOrigin();
  const healthUrl = () => `${getBackendOrigin()}/healthz`;
  const attachUrl = () => `${getBackendOrigin()}/api/local/observe/opencode`;
  const browserCommandUrl = () => `${getBackendOrigin()}/api/local/opencode/browser/command`;
  const consumeResumeIntentUrl = (sessionId) =>
    `${getBackendOrigin()}/api/local/session/${encodeURIComponent(sessionId)}/consume-resume-intent`;
  const frontendReadyUrl = () =>
    frontendServedByBackend()
      ? `${getBackendOrigin()}/__lumon_frontend_ready__`
      : `${getFrontendOrigin()}/lumon-runtime.json`;
  const uiTelemetryUrl = (sessionId) => `${getBackendOrigin()}/api/local/session/${encodeURIComponent(sessionId)}/ui-telemetry`;
  let telemetryCapabilityWarningShown = false;
  let lastHealthCheckAt = 0;
  let lastHealthPayload = null;

  const updateRuntimeOrigins = ({ backendOrigin = null, frontendOrigin = null, reason = "runtime_update" }) => {
    const previousBackendOrigin = getBackendOrigin();
    const previousFrontendOrigin = getFrontendOrigin();
    let changed = false;
    if (typeof backendOrigin === "string" && backendOrigin.trim().length > 0) {
      const normalized = normalizeOrigin(backendOrigin);
      if (normalized && normalized !== previousBackendOrigin) {
        origins.backendOrigin = normalized;
        config.backendOrigin = normalized;
        changed = true;
      }
    }
    if (typeof frontendOrigin === "string" && frontendOrigin.trim().length > 0) {
      const normalized = normalizeOrigin(frontendOrigin);
      if (normalized && normalized !== previousFrontendOrigin) {
        origins.frontendOrigin = normalized;
        config.frontendOrigin = normalized;
        changed = true;
      }
    }
    if (changed) {
      lastHealthCheckAt = 0;
      lastHealthPayload = null;
      debugTrace("runtimeOrigins.updated", {
        reason,
        backendOrigin: getBackendOrigin(),
        frontendOrigin: getFrontendOrigin(),
        previousBackendOrigin,
        previousFrontendOrigin,
      });
    }
    return changed;
  };

  const refreshOriginsFromRuntimeEnv = () => {
    const runtimeOrigins = readRuntimeOriginsFromEnvFile(runtimeDirectory);
    if (!runtimeOrigins) {
      return false;
    }
    const previousBackendOrigin = getBackendOrigin();
    const previousFrontendOrigin = getFrontendOrigin();
    const resolvedBackendOrigin =
      runtimeOrigins.backendOrigin && !config.backendOriginExplicit ? runtimeOrigins.backendOrigin : previousBackendOrigin;
    let resolvedFrontendOrigin = previousFrontendOrigin;
    if (runtimeOrigins.frontendOrigin && !config.frontendOriginExplicit) {
      resolvedFrontendOrigin = runtimeOrigins.frontendOrigin;
    } else if (!config.frontendOriginExplicit && previousFrontendOrigin === previousBackendOrigin) {
      resolvedFrontendOrigin = resolvedBackendOrigin;
    }
    return updateRuntimeOrigins({
      backendOrigin: resolvedBackendOrigin,
      frontendOrigin: resolvedFrontendOrigin,
      reason: "runtime_env",
    });
  };

  const fetchFrontendReady = async () => {
    try {
      const response = await fetch(frontendReadyUrl());
      if (!response.ok) return false;
      const payload = typeof response.json === "function" ? await response.json() : null;
      if (frontendServedByBackend()) {
        const frontendRuntimeVersion = payload?.frontend_runtime_version || payload?.runtime_version;
        const frontendFeatures = payload?.frontend_features || {};
        return frontendRuntimeVersion === EXPECTED_RUNTIME_VERSION && hasRequiredFeatures(frontendFeatures, EXPECTED_FRONTEND_FEATURES);
      }
      const frontendRuntimeVersion = payload?.frontend_runtime_version || payload?.runtime_version;
      const frontendFeatures = payload?.features || payload?.frontend_features || {};
      return frontendRuntimeVersion === EXPECTED_RUNTIME_VERSION && hasRequiredFeatures(frontendFeatures, EXPECTED_FRONTEND_FEATURES);
    } catch {
      return false;
    }
  };

  const fetchJson = async (url) => {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Request failed (${response.status}) for ${url}`);
    }
    return await response.json();
  };

  const verifyBackendVersion = async () => {
    const now = Date.now();
    if (lastHealthPayload && now - lastHealthCheckAt < 2000) {
      return lastHealthPayload;
    }
    const payload = await fetchJson(healthUrl());
    lastHealthCheckAt = now;
    lastHealthPayload = payload;
    if (payload.runtime_version !== EXPECTED_RUNTIME_VERSION) {
      throw new Error(
        `Stale Lumon backend detected (${payload.runtime_version || "unknown"}). Run \`./lumon restart\` so the plugin and backend are on the same runtime version.`,
      );
    }
    if (!hasRequiredFeatures(payload.runtime_features, REQUIRED_BACKEND_RUNTIME_FEATURES)) {
      throw new Error("Stale Lumon backend detected (missing eval capabilities). Run `./lumon restart` so Lumon can measure trust, clarity, and latency correctly.");
    }
    return payload;
  };

  const log = async (message) => {
    debugTrace("plugin.log", { message });
    if (client?.app?.log) {
      await client.app.log(`[lumon] ${message}`);
    }
  };

  const recordUiTelemetry = async ({ sessionId, event, meta = {}, source = "plugin" }) => {
    if (typeof sessionId !== "string" || sessionId.trim().length === 0) {
      return;
    }
    try {
      const response = await fetch(uiTelemetryUrl(sessionId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          event,
          source,
          timestamp: new Date().toISOString(),
          meta,
        }),
      });
      if (!response.ok) {
        const body = typeof response.text === "function" ? await response.text() : "";
        debugTrace("uiTelemetry.error", {
          sessionId,
          event,
          status: response.status,
          body,
        });
        if (!telemetryCapabilityWarningShown) {
          telemetryCapabilityWarningShown = true;
          await log("Lumon runtime is missing telemetry support for this browser session. Run `./lumon restart` before trusting eval data.");
        }
      }
    } catch (error) {
      debugTrace("uiTelemetry.error", {
        sessionId,
        event,
        message: error instanceof Error ? error.message : String(error),
      });
    }
  };

  const waitForHealth = async () => {
    const deadline = Date.now() + config.startupTimeoutMs;
    while (Date.now() < deadline) {
      try {
        await verifyBackendVersion();
        return;
      } catch (error) {
        const message = String(error?.message || error || "");
        if (message.includes("Stale Lumon backend detected")) {
          throw error;
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 400));
    }
    throw new Error(`Lumon backend did not become ready at ${getBackendOrigin()}`);
  };

  const waitForFrontend = async () => {
    const deadline = Date.now() + Math.min(config.startupTimeoutMs, 10000);
    while (Date.now() < deadline) {
      if (await fetchFrontendReady()) return true;
      await new Promise((resolve) => setTimeout(resolve, 400));
    }
    return false;
  };

  const attach = async (payload) => {
    await verifyBackendVersion();
    debugTrace("attach.request", payload);
    const response = await fetch(attachUrl(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    debugTrace("attach.response", { ok: response.ok, status: response.status });
    if (!response.ok) {
      const body = await response.text();
      debugTrace("attach.response_error", { status: response.status, body });
      throw new Error(`attach failed (${response.status}): ${body}`);
    }
    return await response.json();
  };

  const command = async (payload) => {
    await verifyBackendVersion();
    debugTrace("browserCommand.request", { command: payload?.command, commandId: payload?.command_id });
    const controller = new AbortController();
    const timeoutHandle = setTimeout(
      () => controller.abort(new Error("browser_command_timeout")),
      config.browserCommandTimeoutMs,
    );
    let response;
    try {
      response = await fetch(browserCommandUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
    } catch (error) {
      debugTrace("browserCommand.transport_error", {
        command: payload?.command,
        commandId: payload?.command_id,
        message: error instanceof Error ? error.message : String(error),
      });
      throw error;
    } finally {
      clearTimeout(timeoutHandle);
    }
    let bodyText = "";
    let parsed = null;
    if (typeof response.text === "function") {
      bodyText = await response.text();
      try {
        parsed = bodyText ? JSON.parse(bodyText) : null;
      } catch {
        parsed = null;
      }
    } else if (typeof response.json === "function") {
      parsed = await response.json();
      try {
        bodyText = JSON.stringify(parsed);
      } catch {
        bodyText = "";
      }
    }
    debugTrace("browserCommand.response", {
      ok: response.ok,
      status: response.status,
      command: payload?.command,
      resultStatus: parsed?.status ?? null,
      reason: parsed?.reason ?? null,
    });
    if (!response.ok) {
      debugTrace("browserCommand.response_error", { status: response.status, body: bodyText });
      throw new Error(`browser command failed (${response.status}): ${bodyText}`);
    }
    return parsed;
  };

  const consumeResumeIntent = async ({ sessionId, afterSeq = 0, consume = false }) => {
    const response = await fetch(consumeResumeIntentUrl(sessionId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        after_seq: Number(afterSeq || 0),
        consume: consume === true,
      }),
    });
    if (!response.ok) {
      const body = typeof response.text === "function" ? await response.text() : "";
      throw new Error(`consume resume intent failed (${response.status}): ${body}`);
    }
    return await response.json();
  };

  const acknowledgeResumeIntent = async ({ sessionId, resumeIntentSeq }) => {
    const response = await fetch(
      `${getBackendOrigin()}/api/local/session/${encodeURIComponent(sessionId)}/ack-resume-intent`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resume_intent_seq: Number(resumeIntentSeq || 0) }),
      },
    );
    if (!response.ok) {
      const body = typeof response.text === "function" ? await response.text() : "";
      throw new Error(`ack resume intent failed (${response.status}): ${body}`);
    }
    return await response.json();
  };

  const continueSession = async ({ observedSessionId, projectDirectory, reason }) => {
    if (!client?.session || typeof client.session.promptAsync !== "function") {
      throw new Error("OpenCode client does not support session.promptAsync");
    }
    const normalizedReason = reason || "takeover_returned_control";
    await client.session.promptAsync({
      path: { id: observedSessionId },
      query: {
        directory: resolveDirectory(projectDirectory, runtimeDirectory),
      },
      body: {
        tools: {
          lumon_browser: true,
          webfetch: false,
        },
        parts: [
          {
            type: "text",
            text:
              "Auto-resume handoff: you now have control back in Lumon after manual takeover. First run lumon_browser with command=\"status\" exactly once to validate the current page state, then continue from that state without repeating completed actions.",
            metadata: {
              lumon_auto_resume: true,
              reason: normalizedReason,
              lumon_resume_strategy: "status_first",
              lumon_drift_check: "status_first",
            },
          },
        ],
      },
    });
  };

  const continueSessionWithTakeoverHint = async ({ observedSessionId, projectDirectory, reason, takeoverMode }) => {
    if (takeoverMode === "direct") {
      await log(
        `Return control complete for ${observedSessionId}. For zero-lag manual control in future sessions, start OpenCode with ${DIRECT_TAKEOVER_ENV}.`,
      );
    }
    await continueSession({ observedSessionId, projectDirectory, reason });
  };

  const startBackend = async () => {
    const command = [
      "cd",
      JSON.stringify(runtimeDirectory),
      "&&",
      `curl -fsS ${JSON.stringify(healthUrl())} >/dev/null 2>&1 ||`,
      "nohup ./lumon internal-start-backend >/tmp/lumon-backend.log 2>&1 &",
    ].join(" ");
    if ($) {
      debugTrace("startBackend.exec", { command });
      await $`/bin/zsh -lc ${command}`;
      refreshOriginsFromRuntimeEnv();
      return true;
    }
    debugTrace("startBackend.no_shell_helper");
    throw new Error("OpenCode shell helper is unavailable; cannot start the Lumon backend.");
  };

  const startFrontend = async () => {
    if (frontendServedByBackend()) {
      debugTrace("startFrontend.skipped_backend_served", { frontendUrl: getFrontendOrigin(), backendOrigin: getBackendOrigin() });
      return true;
    }
    const command = [
      "cd",
      JSON.stringify(runtimeDirectory),
      "&&",
      `curl -fsS ${JSON.stringify(frontendReadyUrl())} >/dev/null 2>&1 ||`,
      "nohup ./lumon internal-start-frontend >/tmp/lumon-frontend.log 2>&1 &",
    ].join(" ");
    if ($) {
      debugTrace("startFrontend.exec", { command });
      await $`/bin/zsh -lc ${command}`;
      return true;
    }
    debugTrace("startFrontend.no_shell_helper");
    throw new Error("OpenCode shell helper is unavailable; cannot start the Lumon frontend.");
  };

  const startApp = async () => {
    await startBackend();
    await waitForHealth();
    await startFrontend();
    const frontendReady = await waitForFrontend();
    if (!frontendReady) {
      throw new Error(`Lumon frontend did not become ready at ${getFrontendOrigin()}`);
    }
  };

  const openUrl = async (url) => {
    const opener = process.platform === "darwin" ? "open" : "xdg-open";
    debugTrace("openUrl.begin", { url, opener });
    if (url.startsWith(getFrontendOrigin())) {
      const frontendReachable = await fetchFrontendReady();
      if (!frontendReachable) {
        await log("Lumon frontend unavailable; starting it before opening the UI.");
        await startFrontend();
      }
      const frontendReady = await waitForFrontend();
      if (!frontendReady) {
        throw new Error(`Lumon frontend did not become ready at ${getFrontendOrigin()}`);
      }
    }
    if ($) {
      const command = `${opener} ${JSON.stringify(url)} >/dev/null 2>&1`;
      debugTrace("openUrl.exec", { command });
      await $`/bin/zsh -lc ${command}`;
      debugTrace("openUrl.done", { url });
      return;
    }
    debugTrace("openUrl.no_shell_helper", { url });
    throw new Error("OpenCode shell helper is unavailable; cannot open the Lumon UI.");
  };

  refreshOriginsFromRuntimeEnv();

  return {
    attach,
    command,
    consumeResumeIntent,
    acknowledgeResumeIntent,
    continueSession: continueSessionWithTakeoverHint,
    startApp,
    waitForHealth,
    openUrl,
    log,
    recordUiTelemetry,
    directory: runtimeDirectory,
  };
}
