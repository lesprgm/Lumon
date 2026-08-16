import { appendFileSync, readFileSync } from "node:fs";
import { join } from "node:path";

const DEFAULT_BACKEND_ORIGIN = "http://127.0.0.1:8000";
const DEFAULT_FRONTEND_ORIGIN = DEFAULT_BACKEND_ORIGIN;
const DEFAULT_WEB_MODE = "observe_only";
const DEFAULT_OPEN_POLICY = "browser_or_intervention";
const DEFAULT_FORCE_DELEGATE_ON_BROWSER_SIGNAL = false;
const DEFAULT_BROWSER_EPISODE_GAP_MS = 25000;
const DEFAULT_INTERVENTION_EPISODE_GAP_MS = 10000;
const DEFAULT_REOPEN_COOLDOWN_MS = 20000;
const DEFAULT_ENABLE_PROMPT_STEERING = true;
const DEFAULT_BROWSER_COMMAND_TIMEOUT_MS = 90000;
const DEFAULT_RESUME_INTENT_POLL_MS = 1500;
const DEFAULT_AUTO_RESUME_COOLDOWN_MS = 5000;
const DEFAULT_AUTO_RESUME_BUSY_RETRY_MS = 600;
const DEFAULT_AUTO_RESUME_BUSY_MAX_RETRIES = 5;
const DIRECT_TAKEOVER_ENV = "LUMON_HEADLESS=0";
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
const RUNTIME_CONTRACT = loadRuntimeContract();
const EXPECTED_RUNTIME_VERSION = RUNTIME_CONTRACT.runtime_version;
const REQUIRED_BACKEND_RUNTIME_FEATURES = RUNTIME_CONTRACT.backend_runtime_features;
const EXPECTED_FRONTEND_FEATURES = RUNTIME_CONTRACT.frontend_features;

function debugTrace(message, extra) {
  try {
    const suffix = extra === undefined ? "" : ` ${JSON.stringify(extra)}`;
    appendFileSync("/tmp/lumon-plugin-debug.log", `[${new Date().toISOString()}] ${message}${suffix}\n`);
  } catch {
    // best-effort debug only
  }
}

function loadRuntimeContract() {
  const payload = JSON.parse(
    readFileSync(new URL("../../lumon_runtime_contract.json", import.meta.url), "utf8"),
  );
  if (
    typeof payload?.runtime_version !== "string" ||
    !payload.backend_runtime_features ||
    !payload.frontend_features
  ) {
    throw new Error("lumon_runtime_contract.json is incomplete");
  }
  return payload;
}

function normalizeOrigin(origin) {
  return String(origin || "").replace(/\/+$/, "");
}

function parseEnvAssignments(text) {
  const values = {};
  for (const rawLine of String(text || "").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const separator = line.indexOf("=");
    if (separator <= 0) continue;
    const key = line.slice(0, separator).trim();
    let value = line.slice(separator + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}

function readRuntimeOriginsFromEnvFile(runtimeDirectory) {
  try {
    const payload = parseEnvAssignments(readFileSync(join(runtimeDirectory, "output", "runtime", "lumon_backend.env"), "utf8"));
    const backendOrigin = firstNonEmptyString(
      payload.LUMON_PLUGIN_BACKEND_ORIGIN,
      payload.VITE_LUMON_BACKEND_ORIGIN,
      payload.LUMON_BACKEND_ORIGIN,
    );
    const frontendOrigin = firstNonEmptyString(
      payload.LUMON_PLUGIN_FRONTEND_ORIGIN,
      payload.VITE_LUMON_FRONTEND_ORIGIN,
      payload.LUMON_FRONTEND_ORIGIN,
    );
    if (!backendOrigin && !frontendOrigin) {
      return null;
    }
    return {
      backendOrigin: backendOrigin ? normalizeOrigin(backendOrigin) : null,
      frontendOrigin: frontendOrigin ? normalizeOrigin(frontendOrigin) : null,
    };
  } catch (error) {
    if (error?.code === "ENOENT") {
      return null;
    }
    throw error;
  }
}

function hasRequiredFeatures(actual = {}, expected = {}) {
  return Object.entries(expected).every(([key, value]) => {
    if (value !== true) return true;
    return actual?.[key] === true;
  });
}

export function loadPluginConfig(env = process.env) {
  const webMode = env.LUMON_PLUGIN_WEB_MODE === "delegate_playwright" ? "delegate_playwright" : DEFAULT_WEB_MODE;
  const backendOriginExplicit = typeof env.LUMON_PLUGIN_BACKEND_ORIGIN === "string" && env.LUMON_PLUGIN_BACKEND_ORIGIN.trim().length > 0;
  const backendOrigin = env.LUMON_PLUGIN_BACKEND_ORIGIN || DEFAULT_BACKEND_ORIGIN;
  const frontendOriginExplicit = typeof env.LUMON_PLUGIN_FRONTEND_ORIGIN === "string" && env.LUMON_PLUGIN_FRONTEND_ORIGIN.trim().length > 0;
  return {
    backendOrigin,
    backendOriginExplicit,
    frontendOrigin: env.LUMON_PLUGIN_FRONTEND_ORIGIN || backendOrigin || DEFAULT_FRONTEND_ORIGIN,
    frontendOriginExplicit,
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
    resumeIntentPollMs: Number(env.LUMON_PLUGIN_RESUME_INTENT_POLL_MS || DEFAULT_RESUME_INTENT_POLL_MS),
    autoResumeCooldownMs: Number(env.LUMON_PLUGIN_AUTO_RESUME_COOLDOWN_MS || DEFAULT_AUTO_RESUME_COOLDOWN_MS),
    autoResumeBusyRetryMs: Number(
      env.LUMON_PLUGIN_AUTO_RESUME_BUSY_RETRY_MS || DEFAULT_AUTO_RESUME_BUSY_RETRY_MS,
    ),
    autoResumeBusyMaxRetries: Number(
      env.LUMON_PLUGIN_AUTO_RESUME_BUSY_MAX_RETRIES || DEFAULT_AUTO_RESUME_BUSY_MAX_RETRIES,
    ),
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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, Math.max(0, Number(ms) || 0)));
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

export function extractProjectDirectory(event, pluginDirectory) {
  const directory =
    findNestedByKey(event, new Set(["project_directory", "projectDirectory", "cwd", "directory", "path"])) ||
    pluginDirectory;
  if (typeof directory !== "string" || !directory.trim()) {
    throw new Error("OpenCode did not provide a project directory.");
  }
  return directory;
}

function extractSessionState(event) {
  const candidates = [
    event?.state,
    event?.session?.state,
    event?.payload?.state,
    event?.payload?.session?.state,
    event?.message?.state,
    event?.message?.session?.state,
  ];
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim().length > 0) {
      return candidate.trim().toLowerCase();
    }
  }
  return null;
}

function extractTakeoverMetadata(event) {
  const modeCandidates = [
    event?.takeover_mode,
    event?.takeoverMode,
    event?.session?.takeover_mode,
    event?.session?.takeoverMode,
    event?.payload?.takeover_mode,
    event?.payload?.takeoverMode,
    event?.payload?.session?.takeover_mode,
    event?.payload?.session?.takeoverMode,
  ];
  let takeoverMode = null;
  for (const candidate of modeCandidates) {
    if (candidate === "remote" || candidate === "direct") {
      takeoverMode = candidate;
      break;
    }
  }

  const urlCandidates = [
    event?.takeover_url,
    event?.takeoverUrl,
    event?.session?.takeover_url,
    event?.session?.takeoverUrl,
    event?.payload?.takeover_url,
    event?.payload?.takeoverUrl,
    event?.payload?.session?.takeover_url,
    event?.payload?.session?.takeoverUrl,
  ];
  let takeoverUrl = null;
  for (const candidate of urlCandidates) {
    if (typeof candidate === "string" && candidate.trim().length > 0) {
      takeoverUrl = candidate.trim();
      break;
    }
  }

  return { takeoverMode, takeoverUrl };
}

function isDirectTakeoverContext(result, session) {
  const modeCandidate =
    result?.meta?.takeover_mode ||
    result?.takeover_mode ||
    session?.takeoverMode ||
    null;
  return modeCandidate === "direct";
}

function isDirectTakeoverActiveContext(result, session) {
  if (!isDirectTakeoverContext(result, session)) {
    return false;
  }
  if (session?.takeoverActive === true) {
    return true;
  }
  const stateCandidate =
    result?.meta?.session_state ||
    result?.session_state ||
    result?.state ||
    null;
  return typeof stateCandidate === "string" && stateCandidate.toLowerCase() === "takeover";
}

function shouldForegroundLumonOnResume(reason) {
  const normalized = String(reason || "").trim().toLowerCase();
  if (!normalized) {
    return true;
  }
  return normalized === "takeover_returned_control";
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
  throw new Error("Lumon could not resolve a project directory");
}

function makeGeneratedId(prefix) {
  const timestamp = Date.now().toString(36);
  const random = Math.random().toString(36).slice(2, 10);
  return `${prefix}_${timestamp}_${random}`;
}

function resolveSessionIdFromContext(context) {
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
  throw new Error("lumon_browser requires an OpenCode session id");
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
  return makeGeneratedId(`cmd_${commandToken}`);
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

function extractStructuredOpenSignal(event) {
  const candidates = [
    event?.lumon_open_signal,
    event?.open_signal,
    event?.open_signal_type,
    event?.payload?.lumon_open_signal,
    event?.payload?.open_signal,
    event?.payload?.open_signal_type,
    event?.payload?.open_signal?.kind,
    event?.payload?.open_signal?.type,
  ];
  for (const candidate of candidates) {
    if (typeof candidate !== "string") continue;
    const normalized = candidate.trim().toLowerCase();
    if (normalized === "browser" || normalized === "intervention") {
      return normalized;
    }
  }
  return null;
}

function hasStructuredIntervention(event) {
  if (!event || typeof event !== "object") return false;
  if (extractStructuredOpenSignal(event) === "intervention") {
    return true;
  }
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
  if (extractStructuredOpenSignal(event) === "browser") {
    return true;
  }
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
  const explicitSignal = extractStructuredOpenSignal(event);
  if (explicitSignal) {
    return explicitSignal;
  }
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

export {
  ACTIVE_BROWSER_TASK_WINDOW_MS,
  DEFAULT_AUTO_RESUME_BUSY_MAX_RETRIES,
  DEFAULT_AUTO_RESUME_BUSY_RETRY_MS,
  DEFAULT_RESUME_INTENT_POLL_MS,
  DIRECT_TAKEOVER_ENV,
  EXPECTED_FRONTEND_FEATURES,
  EXPECTED_RUNTIME_VERSION,
  OPEN_SIGNAL_DEDUPE_WINDOW_MS,
  REQUIRED_BACKEND_RUNTIME_FEATURES,
  assertNonEmptyStringField,
  buildOpenSignalKey,
  collectMessageText,
  debugTrace,
  extractSessionState,
  extractTakeoverMetadata,
  hasRequiredFeatures,
  isDirectTakeoverActiveContext,
  looksInteractiveBrowserPrompt,
  normalizeOrigin,
  readRuntimeOriginsFromEnvFile,
  resolveCommandId,
  resolveDirectory,
  resolveSessionIdForPromptSteering,
  resolveSessionIdFromContext,
  shouldForegroundLumonOnResume,
  shouldOpenForBrowserCommandResult,
  sleep,
};
