import { appendFileSync } from "node:fs";
import { tool } from "@opencode-ai/plugin";

const DEFAULT_BACKEND_ORIGIN = "http://127.0.0.1:8000";
const DEFAULT_FRONTEND_ORIGIN = DEFAULT_BACKEND_ORIGIN;
const EXPECTED_RUNTIME_VERSION = "2026-03-22-reliability-v1";
const DEFAULT_WEB_MODE = "observe_only";
const DEFAULT_OPEN_POLICY = "browser_or_intervention";
const DEFAULT_FORCE_DELEGATE_ON_BROWSER_SIGNAL = false;
const DEFAULT_BROWSER_EPISODE_GAP_MS = 25000;
const DEFAULT_INTERVENTION_EPISODE_GAP_MS = 10000;
const DEFAULT_REOPEN_COOLDOWN_MS = 20000;
const DEFAULT_ENABLE_PROMPT_STEERING = true;
const DEFAULT_BROWSER_COMMAND_TIMEOUT_MS = 90000;
const ACTIVE_BROWSER_TASK_WINDOW_MS = 180000;
const OPEN_SIGNAL_DEDUPE_WINDOW_MS = 1000;
const BROWSER_TOKENS = ["browser", "webfetch", "open_url", "open-url", "navigate", "visit", "goto", "search", "playwright", "chrome", "site"];
const INTERVENTION_TOKENS = ["approval", "intervention", "takeover", "permission", "confirm", "sensitive", "blocked"];
const INTERACTIVE_BROWSER_VERBS = ["open", "click", "type", "scroll", "fill", "submit", "press", "select", "stop before", "check the page", "inspect the page"];
const INTERACTIVE_BROWSER_CONTEXT_HINTS = [
  "browser",
  "page",
  "site",
  "website",
  "tab",
  "search box",
  "search field",
  "input",
  "form",
  "submit button",
  "approval page",
  "trace page",
  "local page",
];
const ATTACH_EVENT_PREFIXES = ["session.", "message.", "tool.", "permission."];

function debugTrace(message, extra) {
  try {
    const suffix = extra === undefined ? "" : ` ${JSON.stringify(extra)}`;
    appendFileSync("/tmp/lumon-plugin-debug.log", `[${new Date().toISOString()}] ${message}${suffix}\n`);
  } catch {
    // best-effort debug only
  }
}

export function loadPluginConfig(env = process.env) {
  const webMode = env.LUMON_PLUGIN_WEB_MODE === "delegate_playwright" ? "delegate_playwright" : DEFAULT_WEB_MODE;
  return {
    backendOrigin: env.LUMON_PLUGIN_BACKEND_ORIGIN || DEFAULT_BACKEND_ORIGIN,
    frontendOrigin: env.LUMON_PLUGIN_FRONTEND_ORIGIN || env.LUMON_PLUGIN_BACKEND_ORIGIN || DEFAULT_FRONTEND_ORIGIN,
    webMode,
    autoDelegate: env.LUMON_PLUGIN_AUTO_DELEGATE === "1" || env.LUMON_PLUGIN_AUTO_DELEGATE === "true",
    openPolicy: env.LUMON_PLUGIN_OPEN_POLICY || DEFAULT_OPEN_POLICY,
    forceDelegateOnBrowserSignal:
      env.LUMON_PLUGIN_FORCE_DELEGATE_ON_BROWSER_SIGNAL == null
        ? DEFAULT_FORCE_DELEGATE_ON_BROWSER_SIGNAL
        : env.LUMON_PLUGIN_FORCE_DELEGATE_ON_BROWSER_SIGNAL === "1" || env.LUMON_PLUGIN_FORCE_DELEGATE_ON_BROWSER_SIGNAL === "true",
    disableAutoStart: env.LUMON_PLUGIN_DISABLE_AUTO_START === "1" || env.LUMON_PLUGIN_DISABLE_AUTO_START === "true",
    startupTimeoutMs: Number(env.LUMON_PLUGIN_STARTUP_TIMEOUT_MS || 20000),
    browserEpisodeGapMs: Number(env.LUMON_PLUGIN_BROWSER_EPISODE_GAP_MS || DEFAULT_BROWSER_EPISODE_GAP_MS),
    interventionEpisodeGapMs: Number(env.LUMON_PLUGIN_INTERVENTION_EPISODE_GAP_MS || DEFAULT_INTERVENTION_EPISODE_GAP_MS),
    reopenCooldownMs: Number(env.LUMON_PLUGIN_REOPEN_COOLDOWN_MS || DEFAULT_REOPEN_COOLDOWN_MS),
    browserCommandTimeoutMs: Number(env.LUMON_PLUGIN_BROWSER_COMMAND_TIMEOUT_MS || DEFAULT_BROWSER_COMMAND_TIMEOUT_MS),
    enablePromptSteering:
      env.LUMON_PLUGIN_ENABLE_PROMPT_STEERING == null
        ? DEFAULT_ENABLE_PROMPT_STEERING
        : env.LUMON_PLUGIN_ENABLE_PROMPT_STEERING === "1" || env.LUMON_PLUGIN_ENABLE_PROMPT_STEERING === "true",
  };
}

export function eventTypeOf(event) {
  if (!event || typeof event !== "object") return "";
  return String(event.type || event.name || event.event || "");
}

function firstNonEmptyString(...candidates) {
  for (const value of candidates) {
    if (typeof value === "string" && value.trim().length > 0) {
      return value.trim();
    }
  }
  return null;
}

function buildOpenSignalKey(event, signal, fallbackUrl = "") {
  const identity = firstNonEmptyString(
    event?.checkpoint_id,
    event?.payload?.checkpoint_id,
    event?.event_id,
    event?.payload?.event_id,
    event?.source_event_id,
    event?.payload?.source_event_id,
    event?.tool?.name && event?.tool?.url ? `${event.tool.name}:${event.tool.url}` : null,
    event?.payload?.tool?.name && event?.payload?.tool?.url
      ? `${event.payload.tool.name}:${event.payload.tool.url}`
      : null,
    event?.tool?.name,
    event?.payload?.tool?.name,
    event?.url,
    event?.payload?.url,
    event?.message,
    event?.payload?.message,
    event?.summary,
    event?.payload?.summary_text,
    extractSessionId(event),
    fallbackUrl,
  );
  return `${signal}:${eventTypeOf(event)}:${identity || fallbackUrl || "unknown"}`;
}

function findNestedByKey(value, keys, depth = 0) {
  if (depth > 5 || value == null || typeof value !== "object") return null;
  if (Array.isArray(value)) {
    for (const entry of value) {
      const found = findNestedByKey(entry, keys, depth + 1);
      if (found) return found;
    }
    return null;
  }
  for (const [key, entry] of Object.entries(value)) {
    if (keys.has(key) && (typeof entry === "string" || typeof entry === "number") && `${entry}`.length > 0) {
      return String(entry);
    }
    const found = findNestedByKey(entry, keys, depth + 1);
    if (found) return found;
  }
  return null;
}

function collectMessageText(parts = []) {
  const chunks = [];
  for (const part of parts) {
    if (part && typeof part === "object" && part.type === "text" && typeof part.text === "string") {
      chunks.push(part.text);
    }
  }
  return chunks.join(" ").trim();
}

function looksInteractiveBrowserPrompt(text) {
  const lowered = String(text || "").toLowerCase();
  if (!lowered) return false;
  const hasUrl = /https?:\/\/|(?:^|\s)(?:[a-z0-9-]+\.)+[a-z]{2,}(?:\/|\b)/i.test(lowered);
  const hasInteractiveVerb = INTERACTIVE_BROWSER_VERBS.some((token) => lowered.includes(token));
  const hasContextHint = INTERACTIVE_BROWSER_CONTEXT_HINTS.some((token) => lowered.includes(token));
  const hasReadOnlyIntent =
    /\b(fetch|summarize|summarise|read-only|show me the source|source code|what does the page say)\b/i.test(lowered) &&
    !/\bclick|type|fill|submit|scroll|stop before|open .* and\b/i.test(lowered);
  return !hasReadOnlyIntent && hasInteractiveVerb && (hasUrl || hasContextHint);
}

export function extractSessionId(event) {
  if (!event || typeof event !== "object") return null;
  const directCandidates = [
    event.session_id,
    event.sessionId,
    event.session?.id,
    event.properties?.sessionID,
    event.properties?.info?.sessionID,
    event.properties?.part?.sessionID,
    event.properties?.info?.id,
    event.payload?.session_id,
    event.payload?.sessionId,
    event.payload?.session?.id,
    event.properties?.session_id,
    event.properties?.sessionId,
    event.properties?.session?.id,
    event.message?.session_id,
    event.message?.sessionId,
    event.message?.session?.id,
  ];
  for (const value of directCandidates) {
    if (value != null && `${value}`.length > 0) {
      return String(value);
    }
  }
  return findNestedByKey(event, new Set(["session_id", "sessionId"]));
}

export function extractProjectDirectory(event, fallbackDirectory) {
  return (
    findNestedByKey(event, new Set(["project_directory", "projectDirectory", "cwd", "directory", "path"])) ||
    fallbackDirectory ||
    process.cwd()
  );
}

function resolveDirectory(...candidates) {
  for (const value of candidates) {
    if (typeof value === "string" && value.trim().length > 0) {
      return value;
    }
  }
  const cwd = process.cwd();
  if (typeof cwd === "string" && cwd.trim().length > 0) {
    return cwd;
  }
  return "/";
}

function makeFallbackId(prefix) {
  const timestamp = Date.now().toString(36);
  const random = Math.random().toString(36).slice(2, 10);
  return `${prefix}_${timestamp}_${random}`;
}

function resolveSessionIdFromContext(context, fallbackId) {
  const candidates = [context?.sessionID, context?.sessionId, context?.session_id];
  for (const value of candidates) {
    if (typeof value === "string" && value.trim().length > 0) {
      return value;
    }
  }
  const nested = findNestedByKey(context, new Set(["sessionID", "sessionId", "session_id"]));
  if (typeof nested === "string" && nested.trim().length > 0) {
    return nested;
  }
  if (typeof fallbackId === "string" && fallbackId.trim().length > 0) {
    return fallbackId;
  }
  return makeFallbackId("sess_fallback");
}

function resolveSessionIdForPromptSteering(input, output) {
  const direct = [
    input?.sessionID,
    input?.sessionId,
    input?.session_id,
    output?.message?.sessionID,
    output?.message?.sessionId,
    output?.message?.session_id,
  ];
  for (const value of direct) {
    if (typeof value === "string" && value.trim().length > 0) {
      return value;
    }
  }
  const partMatch = Array.isArray(output?.parts)
    ? output.parts.find((part) =>
        typeof part?.sessionID === "string" ||
        typeof part?.sessionId === "string" ||
        typeof part?.session_id === "string",
      )
    : null;
  if (partMatch) {
    return String(partMatch.sessionID || partMatch.sessionId || partMatch.session_id);
  }
  return findNestedByKey({ input, output }, new Set(["sessionID", "sessionId", "session_id"]));
}

function resolveCommandId(commandId, commandName) {
  if (typeof commandId === "string" && commandId.trim().length > 0) {
    return commandId;
  }
  const commandToken = typeof commandName === "string" && commandName.trim().length > 0 ? commandName : "unknown";
  return makeFallbackId(`cmd_${commandToken}`);
}

function assertNonEmptyStringField(field, value) {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new Error(`lumon_browser payload requires non-empty string field: ${field}`);
  }
}

export function isAttachRelevantEvent(event) {
  const eventType = eventTypeOf(event);
  return ATTACH_EVENT_PREFIXES.some((prefix) => eventType.startsWith(prefix));
}

function hasStructuredIntervention(event) {
  if (!event || typeof event !== "object") return false;
  const eventType = eventTypeOf(event).toLowerCase();
  if (eventType === "approval_required" || eventType === "bridge_offer") {
    return true;
  }
  if (typeof event.intervention_id === "string" && event.intervention_id.length > 0) {
    return true;
  }
  if (typeof event.checkpoint_id === "string" && event.checkpoint_id.length > 0) {
    return true;
  }
  const payload = event.payload;
  if (payload && typeof payload === "object") {
    if (typeof payload.intervention_id === "string" && payload.intervention_id.length > 0) {
      return true;
    }
    if (typeof payload.checkpoint_id === "string" && payload.checkpoint_id.length > 0) {
      return true;
    }
  }
  return false;
}

function hasStructuredBrowserSignal(event) {
  if (!event || typeof event !== "object") return false;
  const eventType = eventTypeOf(event).toLowerCase();

  if (eventType.includes("browser") || eventType.includes("webfetch")) {
    return true;
  }

  const toolCandidates = [
    event.tool?.name,
    event.payload?.tool?.name,
    event.payload?.tool_name,
    event.payload?.name,
  ];
  for (const candidate of toolCandidates) {
    if (typeof candidate === "string" && BROWSER_TOKENS.some((token) => candidate.toLowerCase().includes(token))) {
      return true;
    }
  }

  const commandCandidates = [
    event.command,
    event.command_name,
    event.payload?.command,
    event.payload?.command_name,
    event.payload?.browser_command,
  ];
  for (const candidate of commandCandidates) {
    if (typeof candidate === "string" && INTERACTIVE_BROWSER_VERBS.some((token) => candidate.toLowerCase().includes(token))) {
      return true;
    }
  }

  const urlCandidates = [
    event.open_url,
    event.source_url,
    event.url,
    event.payload?.open_url,
    event.payload?.source_url,
    event.payload?.url,
    event.browser_context?.url,
    event.payload?.browser_context?.url,
  ];
  for (const candidate of urlCandidates) {
    if (typeof candidate === "string" && candidate.trim().length > 0 && candidate !== "about:blank") {
      return true;
    }
  }

  if (Array.isArray(event.actionable_elements) && event.actionable_elements.length > 0) {
    return true;
  }
  if (Array.isArray(event.payload?.actionable_elements) && event.payload.actionable_elements.length > 0) {
    return true;
  }
  if (event.evidence?.frame_emitted === true || event.payload?.evidence?.frame_emitted === true) {
    return true;
  }

  return false;
}

export function shouldOpenForEvent(event, openPolicy = DEFAULT_OPEN_POLICY) {
  if (openPolicy === "never") return false;
  if (openPolicy === "always") return true;

  if (openPolicy === "browser_or_intervention") {
    return classifyOpenSignal(event) !== null;
  }

  const eventType = eventTypeOf(event).toLowerCase();

  if (hasStructuredIntervention(event)) {
    return true;
  }
  return hasStructuredBrowserSignal(event) || BROWSER_TOKENS.some((token) => eventType.includes(token));
}

export function classifyOpenSignal(event) {
  const eventType = eventTypeOf(event).toLowerCase();

  if (hasStructuredIntervention(event)) {
    return "intervention";
  }
  if (hasStructuredBrowserSignal(event)) {
    return "browser";
  }
  if (BROWSER_TOKENS.some((token) => eventType.includes(token))) {
    return "browser";
  }
  return null;
}
function shouldOpenForBrowserCommandResult(result) {
  if (!result || typeof result !== "object") return false;
  if (result.intervention_id || result.status === "blocked") {
    return true;
  }
  if (result.command === "begin_task") {
    return Boolean(result.evidence?.frame_emitted || result.evidence?.verified);
  }
  return Boolean(result.evidence?.frame_emitted);
}

export function isTerminalEvent(event) {
  const eventType = eventTypeOf(event).toLowerCase();
  if (/(^|\.)(completed|finished|stopped|failed)$/.test(eventType)) {
    return true;
  }

  if (!eventType.startsWith("session.")) {
    return false;
  }

  const terminalSessionStates = [
    event.session?.state,
    event.session?.status,
    event.payload?.session?.state,
    event.payload?.session?.status,
  ];
  return terminalSessionStates.some(
    (candidate) => typeof candidate === "string" && ["completed", "finished", "stopped", "failed"].includes(candidate.toLowerCase()),
  );
}

export function buildAttachPayload({ event, config, directory }) {
  return {
    observed_session_id: extractSessionId(event),
    project_directory: extractProjectDirectory(event, directory),
    web_mode: config.webMode,
    auto_delegate: config.autoDelegate,
    frontend_origin: config.frontendOrigin,
  };
}

function isRecoverableStartupError(message) {
  return /ECONNREFUSED|fetch failed|Failed to fetch|timed out|NetworkError|Connection refused|health|Unable to connect|access the url|attach failed \((502|503|504)\)|browser command failed \((502|503|504)\)/i.test(
    String(message || ""),
  );
}

export async function attachWithAutoStart({ attach, startApp, waitForHealth, payload, config, log = () => {}, onAutoStartComplete = null }) {
  debugTrace("attachWithAutoStart.begin", { observedSessionId: payload?.observed_session_id, webMode: config?.webMode });
  try {
    return await attach(payload);
  } catch (error) {
    const message = String(error?.message || error || "");
    debugTrace("attachWithAutoStart.attach_error", { message });
    const recoverable = isRecoverableStartupError(message);
    if (!recoverable || config.disableAutoStart) {
      debugTrace("attachWithAutoStart.nonrecoverable", { recoverable, disableAutoStart: config.disableAutoStart });
      throw error;
    }
    await log("Lumon backend unavailable; starting Lumon services.");
    try {
      const startupStartedAt = Date.now();
      debugTrace("attachWithAutoStart.starting_app");
      await startApp();
      debugTrace("attachWithAutoStart.waiting_for_health");
      await waitForHealth();
      if (typeof onAutoStartComplete === "function") {
        await onAutoStartComplete({ startupLatencyMs: Date.now() - startupStartedAt });
      }
      debugTrace("attachWithAutoStart.retry_attach");
      return await attach(payload);
    } catch (startupError) {
      await log("Lumon could not start itself. Run `./lumon setup` once, then try `opencode .` again.");
      debugTrace("attachWithAutoStart.startup_error", { message: String(startupError?.message || startupError || "") });
      throw startupError;
    }
  }
}

export async function browserCommandWithAutoStart({ command, startApp, waitForHealth, payload, config, log = () => {}, onAutoStartComplete = null }) {
  debugTrace("browserCommandWithAutoStart.begin", { command: payload?.command, commandId: payload?.command_id });
  try {
    return await command(payload);
  } catch (error) {
    const message = String(error?.message || error || "");
    debugTrace("browserCommandWithAutoStart.command_error", { message });
    const recoverable = isRecoverableStartupError(message);
    if (!recoverable || config.disableAutoStart) {
      throw error;
    }
    await log("Lumon backend unavailable; starting Lumon services.");
    const startupStartedAt = Date.now();
    await startApp();
    await waitForHealth();
    if (typeof onAutoStartComplete === "function") {
      await onAutoStartComplete({ startupLatencyMs: Date.now() - startupStartedAt });
    }
    return await command(payload);
  }
}

export function createLumonController({
  config,
  attach,
  command,
  startApp,
  waitForHealth,
  openUrl,
  recordUiTelemetry = async () => {},
  log = async () => {},
  commandActivity = new Map(),
  pendingPromptSteering = new Map(),
  activeBrowserTasks = new Map(),
}) {
  const sessions = new Map();
  const inflight = new Map();
  let startupPromise = null;
  let recentDelegateFailures = 0;

  const fireUiTelemetry = ({ sessionId, event, meta = {} }) => {
    if (typeof recordUiTelemetry === "function") {
      return recordUiTelemetry({ sessionId, event, meta, source: "plugin" });
    }
    return Promise.resolve();
  };

  async function ensureStarted() {
    if (!startupPromise) {
      startupPromise = (async () => {
        await startApp();
        await waitForHealth();
      })().finally(() => {
        startupPromise = null;
      });
    }
    return startupPromise;
  }

  async function ensureAttached(event, directory, { forceRefresh = false } = {}) {
    const observedSessionId = extractSessionId(event);
    debugTrace("ensureAttached", { eventType: eventTypeOf(event), observedSessionId });
    if (!observedSessionId) return null;
    if (!forceRefresh && sessions.has(observedSessionId)) return sessions.get(observedSessionId);
    if (inflight.has(observedSessionId)) return inflight.get(observedSessionId);

    const payload = buildAttachPayload({ event, config, directory });
    const previousSession = sessions.get(observedSessionId) || null;
    let autoStartLatencyMs = null;
    const promise = attachWithAutoStart({
      attach,
      startApp: ensureStarted,
      waitForHealth,
      payload,
      config,
      log,
      onAutoStartComplete: ({ startupLatencyMs }) => {
        autoStartLatencyMs = startupLatencyMs;
      },
    }).then((response) => {
      const session = {
        observedSessionId,
        lumonSessionId: response.session_id,
        openUrl: response.open_url,
        alreadyAttached: Boolean(response.already_attached),
        uiConnected: response.ui_connected === true,
        uiReadyAt: response.ui_ready_at || null,
        lastOpenedAt: previousSession?.lastOpenedAt || 0,
        openInProgress: previousSession?.openInProgress || false,
        lastRelevantBrowserAt: previousSession?.lastRelevantBrowserAt || 0,
        lastRelevantInterventionAt: previousSession?.lastRelevantInterventionAt || 0,
        delegatePrimed: previousSession?.delegatePrimed || false,
        lastDelegatePrimeAt: previousSession?.lastDelegatePrimeAt || 0,
        attachedAt: Date.now(),
        lastOpenSignalKey: previousSession?.lastOpenSignalKey || null,
        lastOpenSignalAt: previousSession?.lastOpenSignalAt || 0,
      };
      sessions.set(observedSessionId, session);
      if (typeof autoStartLatencyMs === "number") {
        void fireUiTelemetry({
          sessionId: response.session_id,
          event: "auto_start_completed",
          meta: { startup_latency_ms: autoStartLatencyMs, phase: "attach" },
        });
      }
      inflight.delete(observedSessionId);
      return session;
    }).catch((error) => {
      inflight.delete(observedSessionId);
      throw error;
    });

    inflight.set(observedSessionId, promise);
    return promise;
  }

  async function handleEvent(event, directory) {
    const now = Date.now();
    const observedSessionId = extractSessionId(event);
    const relevant = isAttachRelevantEvent(event);
    const signal = classifyOpenSignal(event);
    const activeBrowserTask = observedSessionId ? activeBrowserTasks.get(observedSessionId) : null;
    const browserTaskActive = Boolean(activeBrowserTask && typeof activeBrowserTask.expiresAt === "number" && activeBrowserTask.expiresAt > now);
    if (observedSessionId && activeBrowserTask && !browserTaskActive) {
      activeBrowserTasks.delete(observedSessionId);
    }
    debugTrace("handleEvent", { eventType: eventTypeOf(event), observedSessionId, relevant });
    if (browserTaskActive && signal !== null) {
      debugTrace("openSignal.suppressed_active_browser_task", {
        observedSessionId,
        signal,
        eventType: eventTypeOf(event),
        expiresAt: activeBrowserTask.expiresAt,
      });
      const runtimeSessionId = sessions.get(observedSessionId)?.lumonSessionId;
      if (runtimeSessionId) {
        void fireUiTelemetry({
          sessionId: runtimeSessionId,
          event: "open_suppressed",
          meta: { reason_code: "active_browser_task", signal, event_type: eventTypeOf(event) },
        });
      }
      return;
    }
    if (relevant && observedSessionId && (signal !== null || sessions.has(observedSessionId))) {
      await ensureAttached(event, directory);
    }

    if (!observedSessionId) return;
    const session = sessions.get(observedSessionId);
    if (session) {
      const previousBrowserAt = session.lastRelevantBrowserAt;
      const previousInterventionAt = session.lastRelevantInterventionAt;
      const isBrowserSignal = signal === "browser";
      const isInterventionSignal = signal === "intervention";
      const toolActivity = commandActivity.get(observedSessionId);
      const pendingPromptUntil = pendingPromptSteering.get(observedSessionId) || 0;
      const pendingPromptActive = pendingPromptUntil > now;
      const recentToolBrowserActivity =
        toolActivity &&
        typeof toolActivity.lastCommandAt === "number" &&
        now - toolActivity.lastCommandAt < Math.max(config.reopenCooldownMs, config.browserEpisodeGapMs, 60000);
      if (signal === "browser") {
        session.lastRelevantBrowserAt = now;
      } else if (signal === "intervention") {
        session.lastRelevantInterventionAt = now;
      }

      if (isBrowserSignal && config.forceDelegateOnBrowserSignal && !session.delegatePrimed && now - session.lastDelegatePrimeAt >= 5000) {
        session.lastDelegatePrimeAt = now;
        const eventTaskText = (() => {
          const candidates = [
            event?.intent,
            event?.summary,
            event?.message,
            event?.payload?.intent,
            event?.payload?.summary_text,
            event?.payload?.message,
          ];
          for (const candidate of candidates) {
            if (typeof candidate === "string" && candidate.trim()) {
              return candidate.trim();
            }
          }
          return "Open and inspect the requested page in a live browser view.";
        })();
        try {
          let autoStartLatencyMs = null;
          const result = await browserCommandWithAutoStart({
            command,
            startApp: ensureStarted,
            waitForHealth,
            payload: {
              observed_session_id: observedSessionId,
              project_directory: extractProjectDirectory(event, directory),
              command_id: `begin_${observedSessionId}_${now}`,
              command: "begin_task",
              task_text: eventTaskText,
            },
            config,
            log,
            onAutoStartComplete: ({ startupLatencyMs }) => {
              autoStartLatencyMs = startupLatencyMs;
            },
          });
          if (typeof autoStartLatencyMs === "number") {
            void fireUiTelemetry({
              sessionId: session.lumonSessionId,
              event: "auto_start_completed",
              meta: { startup_latency_ms: autoStartLatencyMs, phase: "delegate_prime" },
            });
          }
          const primed =
            result &&
            (result.status === "success" ||
              result.status === "blocked" ||
              result.evidence?.frame_emitted === true);
          if (primed) {
            session.delegatePrimed = true;
            recentDelegateFailures = 0;
          }
          if (result?.open_url && shouldOpenForBrowserCommandResult(result)) {
            session.lastOpenedAt = now;
            void fireUiTelemetry({
              sessionId: session.lumonSessionId,
              event: "open_requested",
              meta: { reason_code: "delegate_prime", signal: "browser", command: "begin_task" },
            });
            try {
              await openUrl(result.open_url);
              void fireUiTelemetry({
                sessionId: session.lumonSessionId,
                event: "open_completed",
                meta: { reason_code: "delegate_prime", signal: "browser", command: "begin_task" },
              });
            } catch (openError) {
              void fireUiTelemetry({
                sessionId: session.lumonSessionId,
                event: "open_failed",
                meta: {
                  reason_code: "delegate_prime",
                  signal: "browser",
                  command: "begin_task",
                  message: String(openError?.message || openError || ""),
                },
              });
              throw openError;
            }
          }
        } catch (error) {
          await log(`Lumon delegate priming failed: ${String(error?.message || error || "unknown error")}`);
          recentDelegateFailures += 1;
          if (recentDelegateFailures >= 2) {
            await log("Lumon has failed to prime the browser delegate repeatedly. Run `./lumon triage` to collect runtime and log state.");
          }
        }
      }

      const canOpenForSignal =
        isInterventionSignal ||
        (isBrowserSignal &&
          !pendingPromptActive &&
          !recentToolBrowserActivity &&
          (session.delegatePrimed || !config.forceDelegateOnBrowserSignal));
      if (canOpenForSignal && shouldOpenForEvent(event, config.openPolicy)) {
        const episodeGapMs = isInterventionSignal ? config.interventionEpisodeGapMs : config.browserEpisodeGapMs;
        const previousRelevantAt = isInterventionSignal ? previousInterventionAt : previousBrowserAt;
        const openSignalKey = buildOpenSignalKey(event, signal, session.openUrl);
        const reopenCooldownMs = isInterventionSignal
          ? Math.min(config.reopenCooldownMs, config.interventionEpisodeGapMs)
          : config.reopenCooldownMs;
        const isNewEpisode =
          previousRelevantAt === 0 ||
          now - previousRelevantAt >= episodeGapMs ||
          now - session.lastOpenedAt >= episodeGapMs;
        const outsideCooldown = session.lastOpenedAt === 0 || now - session.lastOpenedAt >= reopenCooldownMs;
        debugTrace("openSignal", {
          observedSessionId,
          signal,
          eventType: eventTypeOf(event),
          previousRelevantAt,
          lastOpenedAt: session.lastOpenedAt,
          isNewEpisode,
          outsideCooldown,
          episodeGapMs,
          reopenCooldownMs,
        });

        if (
          openSignalKey &&
          session.lastOpenSignalKey === openSignalKey &&
          now - session.lastOpenSignalAt < OPEN_SIGNAL_DEDUPE_WINDOW_MS
        ) {
          debugTrace("openSignal.suppressed_duplicate_signal", {
            observedSessionId,
            signal,
            eventType: eventTypeOf(event),
            url: session.openUrl,
          });
          void fireUiTelemetry({
            sessionId: session.lumonSessionId,
            event: "open_suppressed",
            meta: { reason_code: "duplicate_signal", signal, event_type: eventTypeOf(event) },
          });
          return;
        }

        if ((session.lastOpenedAt === 0 || isNewEpisode) && outsideCooldown && !session.openInProgress) {
          const shouldRefreshAttachment =
            typeof session.attachedAt !== "number" ||
            now - session.attachedAt >= Math.max(config.reopenCooldownMs, 15000);
          const refreshedSession = shouldRefreshAttachment
            ? await ensureAttached(event, directory, { forceRefresh: true })
            : null;
          const openTarget = refreshedSession || session;
          if (openTarget.uiConnected === true && openTarget.openUrl === session.openUrl) {
            void fireUiTelemetry({
              sessionId: openTarget.lumonSessionId,
              event: "open_suppressed",
              meta: { reason_code: "already_visible", signal, event_type: eventTypeOf(event) },
            });
            return;
          }
          session.lastOpenedAt = now;
          openTarget.lastOpenedAt = now;
          session.lastOpenSignalKey = openSignalKey;
          session.lastOpenSignalAt = now;
          openTarget.lastOpenSignalKey = openSignalKey;
          openTarget.lastOpenSignalAt = now;
          session.openInProgress = true;
          openTarget.openInProgress = true;
          debugTrace("openSignal.opening", { observedSessionId, signal, url: openTarget.openUrl });
          void fireUiTelemetry({
            sessionId: openTarget.lumonSessionId,
            event: "open_requested",
            meta: { reason_code: signal, signal, event_type: eventTypeOf(event) },
          });
          try {
            await openUrl(openTarget.openUrl);
            void fireUiTelemetry({
              sessionId: openTarget.lumonSessionId,
              event: "open_completed",
              meta: { reason_code: signal, signal, event_type: eventTypeOf(event) },
            });
          } catch (error) {
            debugTrace("openSignal.open_failed", {
              observedSessionId,
              signal,
              url: openTarget.openUrl,
              message: String(error?.message || error || ""),
            });
            void fireUiTelemetry({
              sessionId: openTarget.lumonSessionId,
              event: "open_failed",
              meta: {
                reason_code: signal,
                signal,
                event_type: eventTypeOf(event),
                message: String(error?.message || error || ""),
              },
            });
            await log(`Lumon observed browser work but could not open the UI automatically. Open Lumon manually if needed: ${openTarget.openUrl}`);
          } finally {
            session.openInProgress = false;
            openTarget.openInProgress = false;
          }
        } else if ((session.lastOpenedAt === 0 || isNewEpisode) && outsideCooldown && session.openInProgress) {
          debugTrace("openSignal.suppressed_open_in_progress", {
            observedSessionId,
            signal,
            eventType: eventTypeOf(event),
          });
          void fireUiTelemetry({
            sessionId: session.lumonSessionId,
            event: "open_suppressed",
            meta: { reason_code: "open_in_progress", signal, event_type: eventTypeOf(event) },
          });
        } else if (!outsideCooldown) {
          void fireUiTelemetry({
            sessionId: session.lumonSessionId,
            event: "open_suppressed",
            meta: { reason_code: "cooldown", signal, event_type: eventTypeOf(event) },
          });
        } else if (session.lastOpenedAt !== 0 && !isNewEpisode) {
          void fireUiTelemetry({
            sessionId: session.lumonSessionId,
            event: "open_suppressed",
            meta: { reason_code: "same_episode", signal, event_type: eventTypeOf(event) },
          });
        }
      } else if (isBrowserSignal && recentToolBrowserActivity) {
        debugTrace("openSignal.suppressed_tool_active", {
          observedSessionId,
          eventType: eventTypeOf(event),
          lastCommandAt: toolActivity.lastCommandAt,
        });
        void fireUiTelemetry({
          sessionId: session.lumonSessionId,
          event: "open_suppressed",
          meta: { reason_code: "tool_active", signal: "browser", event_type: eventTypeOf(event) },
        });
      } else if (isBrowserSignal && pendingPromptActive) {
        debugTrace("openSignal.suppressed_pending_tool", {
          observedSessionId,
          eventType: eventTypeOf(event),
          pendingPromptUntil,
        });
        void fireUiTelemetry({
          sessionId: session.lumonSessionId,
          event: "open_suppressed",
          meta: { reason_code: "pending_tool", signal: "browser", event_type: eventTypeOf(event) },
        });
      }
    }

    if (session && isTerminalEvent(event)) {
      sessions.delete(observedSessionId);
      inflight.delete(observedSessionId);
      commandActivity.delete(observedSessionId);
      pendingPromptSteering.delete(observedSessionId);
    }
  }

  return {
    sessions,
    handleEvent,
  };
}

function createRuntimeHelpers({ $, directory, client, config }) {
  const runtimeDirectory = resolveDirectory(directory);
  const healthUrl = `${config.backendOrigin}/healthz`;
  const attachUrl = `${config.backendOrigin}/api/local/observe/opencode`;
  const browserCommandUrl = `${config.backendOrigin}/api/local/opencode/browser/command`;
  const frontendUrl = config.frontendOrigin;
  const frontendServedByBackend = frontendUrl.replace(/\/$/, "") === config.backendOrigin.replace(/\/$/, "");
  const frontendReadyUrl = frontendServedByBackend ? `${config.backendOrigin}/__lumon_frontend_ready__` : frontendUrl;
  const uiTelemetryUrl = (sessionId) => `${config.backendOrigin}/api/local/session/${encodeURIComponent(sessionId)}/ui-telemetry`;
  let telemetryCapabilityWarningShown = false;

  const fetchFrontendReady = async () => {
    try {
      const response = await fetch(frontendReadyUrl);
      if (!response.ok) return false;
      if (frontendServedByBackend) {
        const payload = typeof response.json === "function" ? await response.json() : null;
        const frontendRuntimeVersion = payload?.frontend_runtime_version || payload?.runtime_version;
        const frontendFeatures = payload?.frontend_features || {};
        return frontendRuntimeVersion === EXPECTED_RUNTIME_VERSION && frontendFeatures.ui_telemetry === true;
      }
      const text = typeof response.text === "function" ? await response.text() : "";
      const normalized = String(text || "");
      return normalized.includes("Lumon") || normalized.includes('id="root"') || normalized.includes("id='root'");
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

  let lastHealthCheckAt = 0;
  let lastHealthPayload = null;

  const verifyBackendVersion = async () => {
    const now = Date.now();
    if (lastHealthPayload && now - lastHealthCheckAt < 2000) {
      return lastHealthPayload;
    }
    const payload = await fetchJson(healthUrl);
    lastHealthCheckAt = now;
    lastHealthPayload = payload;
    if (payload.runtime_version !== EXPECTED_RUNTIME_VERSION) {
      throw new Error(
        `Stale Lumon backend detected (${payload.runtime_version || "unknown"}). Run \`./lumon restart\` so the plugin and backend are on the same runtime version.`,
      );
    }
    if (payload.runtime_features?.ui_telemetry !== true || payload.runtime_features?.live_artifact_persistence !== true) {
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
    throw new Error(`Lumon backend did not become ready at ${config.backendOrigin}`);
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
    const response = await fetch(attachUrl, {
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
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    const timeoutHandle = controller
      ? setTimeout(() => controller.abort(new Error("browser_command_timeout")), config.browserCommandTimeoutMs)
      : null;
    let response;
    try {
      response = await fetch(browserCommandUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        ...(controller ? { signal: controller.signal } : {}),
      });
    } catch (error) {
      debugTrace("browserCommand.transport_error", {
        command: payload?.command,
        commandId: payload?.command_id,
        message: error instanceof Error ? error.message : String(error),
      });
      throw error;
    } finally {
      if (timeoutHandle) {
        clearTimeout(timeoutHandle);
      }
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

  const startBackend = async () => {
    const command = [
      "cd",
      JSON.stringify(runtimeDirectory),
      "&&",
      `curl -fsS ${JSON.stringify(healthUrl)} >/dev/null 2>&1 ||`,
      "nohup /bin/zsh ./scripts/start_demo_backend.sh >/tmp/lumon-backend.log 2>&1 &",
    ].join(" ");
    if ($) {
      debugTrace("startBackend.exec", { command });
      await $`/bin/zsh -lc ${command}`;
      return true;
    }
    debugTrace("startBackend.no_shell_helper");
    throw new Error("OpenCode shell helper is unavailable; cannot start the Lumon backend.");
  };

  const startFrontend = async () => {
    if (frontendServedByBackend) {
      debugTrace("startFrontend.skipped_backend_served", { frontendUrl, backendOrigin: config.backendOrigin });
      return true;
    }
    const command = [
      "cd",
      JSON.stringify(runtimeDirectory),
      "&&",
      `curl -fsS ${JSON.stringify(frontendUrl)} >/dev/null 2>&1 ||`,
      "nohup /bin/zsh ./scripts/start_demo_frontend.sh >/tmp/lumon-frontend.log 2>&1 &",
    ].join(" ");
    if ($) {
      debugTrace("startFrontend.exec", { command });
      await $`/bin/zsh -lc ${command}`;
      return true;
    }
    debugTrace("startFrontend.no_shell_helper");
    return false;
  };

  const startApp = async () => {
    await startBackend();
    await waitForHealth();
    await startFrontend();
    const frontendReady = await waitForFrontend();
    if (!frontendReady) {
      throw new Error(`Lumon frontend did not become ready at ${frontendUrl}`);
    }
  };

  const openUrl = async (url) => {
    const opener = process.platform === "darwin" ? "open" : "xdg-open";
    debugTrace("openUrl.begin", { url, opener });
    if (url.startsWith(frontendUrl)) {
      const frontendReachable = await fetchFrontendReady();
      if (!frontendReachable) {
        await log("Lumon frontend unavailable; starting it before opening the UI.");
        await startFrontend();
      }
      const frontendReady = await waitForFrontend();
      if (!frontendReady) {
        throw new Error(`Lumon frontend did not become ready at ${frontendUrl}`);
      }
    }
    if ($) {
      const command = `${opener} ${JSON.stringify(url)} >/dev/null 2>&1 || true`;
      debugTrace("openUrl.exec", { command });
      await $`/bin/zsh -lc ${command}`;
      debugTrace("openUrl.done", { url });
      return;
    }
    debugTrace("openUrl.no_shell_helper", { url });
    await log(`Open Lumon manually: ${url}`);
  };

  return { attach, command, startApp, waitForHealth, openUrl, log, recordUiTelemetry, directory: runtimeDirectory };
}

function createLumonBrowserTool({ config, helpers }) {
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
      selector: tool.schema.string().optional().describe("Internal fallback selector when no element_id exists."),
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
        }
        if (helpers.commandActivity) {
          helpers.commandActivity.set(repeatedKey, {
            lastCommandAt: Date.now(),
            lastStatus: result?.status ?? null,
            lastReason: result?.reason ?? null,
          });
        }
        if (args.command === "stop" && result?.status === "success") {
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

export function createLumonPlugin(input) {
  const { $, directory, client } = input;
  const config = loadPluginConfig();
  const helpers = createRuntimeHelpers({ $, directory, client, config });
  const commandActivity = new Map();
  const pendingPromptSteering = new Map();
  const activeBrowserTasks = new Map();
  const controller = createLumonController({ config, ...helpers, commandActivity, pendingPromptSteering, activeBrowserTasks });

  return (async () => {
    debugTrace("plugin.init", { directory, webMode: config.webMode });
    await helpers.log(`plugin ready (${config.webMode})`);
    const plugin = {
      event: async ({ event }) => {
        await controller.handleEvent(event, directory);
      },
      tool: {
        lumon_browser: createLumonBrowserTool({ config, helpers: { ...helpers, commandActivity, pendingPromptSteering, activeBrowserTasks } }),
      },
      "tool.definition": async (input, output) => {
        if (input.toolID !== "lumon_browser") {
          return;
        }
        output.description =
          "Use Lumon for interactive browser work. This tool is required when the user asks to open a page, inspect actionable elements, click, type, scroll, wait for page state, or stop before submitting. Never narrate browser success without this tool returning verified evidence. If the tool returns partial with reason=frame_missing, call status once at most and then report the delegate problem instead of looping open or inspect.";
      },
    };
    if (config.enablePromptSteering) {
      plugin["chat.message"] = async (_input, output) => {
        const promptText = collectMessageText(output.parts);
        if (!looksInteractiveBrowserPrompt(promptText)) {
          return;
        }
        const sessionId = resolveSessionIdForPromptSteering(_input, output);
        if (sessionId) {
          pendingPromptSteering.set(sessionId, Date.now() + 15000);
          activeBrowserTasks.set(sessionId, {
            source: "prompt_steering",
            expiresAt: Date.now() + ACTIVE_BROWSER_TASK_WINDOW_MS,
          });
          debugTrace("chat.message.pending_tool", { sessionId, promptText });
        } else {
          debugTrace("chat.message.pending_tool_missing_session", { promptText });
        }
        output.message.tools = {
          ...(output.message.tools || {}),
          lumon_browser: true,
          webfetch: false,
        };
      };
      plugin["experimental.chat.system.transform"] = async (_input, output) => {
        output.system.push(
          "For interactive browser tasks, use the `lumon_browser` tool. Use read-only web tools only for fetching or summarizing content. Never claim a browser click, type, navigation, or scroll succeeded unless `lumon_browser` returned verified success evidence. If `lumon_browser` returns partial with reason=frame_missing, use `status` once at most and then stop with a clear delegate failure instead of looping open or inspect.",
        );
      };
    }
    return plugin;
  })();
}
