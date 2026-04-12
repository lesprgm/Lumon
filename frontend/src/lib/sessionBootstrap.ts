import type { SessionBootstrapPayload } from "../protocol/types";

const DEFAULT_BOOTSTRAP_ATTEMPTS = 4;
const DEFAULT_BOOTSTRAP_RETRY_DELAY_MS = 1200;
const DEFAULT_BOOTSTRAP_TIMEOUT_MS = 2500;

function normalizeBackendOrigin(rawOrigin: string): string {
  return rawOrigin.replace(/\/+$/, "");
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, ms));
}

export function getBackendOrigin(
  locationLike: Pick<Location, "protocol" | "hostname"> = window.location,
  configuredOrigin: string | undefined = import.meta.env.VITE_LUMON_BACKEND_ORIGIN,
): string {
  if (configuredOrigin && configuredOrigin.trim().length > 0) {
    return normalizeBackendOrigin(configuredOrigin);
  }
  return `${locationLike.protocol}//${locationLike.hostname}:8000`;
}

export async function bootstrapSession(
  fetchImpl: typeof fetch = fetch,
  backendOrigin: string = getBackendOrigin(),
): Promise<SessionBootstrapPayload> {
  let lastError: Error | null = null;
  for (let attempt = 0; attempt < DEFAULT_BOOTSTRAP_ATTEMPTS; attempt += 1) {
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    const timeoutHandle = controller
      ? globalThis.setTimeout(() => controller.abort(), DEFAULT_BOOTSTRAP_TIMEOUT_MS)
      : null;
    try {
      const response = await fetchImpl(`${backendOrigin}/api/bootstrap`, {
        headers: {
          Accept: "application/json",
        },
        ...(controller ? { signal: controller.signal } : {}),
      });
      if (response.ok) {
        return (await response.json()) as SessionBootstrapPayload;
      }
      lastError = new Error(`Bootstrap failed with status ${response.status}`);
    } catch (error) {
      lastError =
        error instanceof Error ? error : new Error("Bootstrap request failed");
    } finally {
      if (timeoutHandle) {
        globalThis.clearTimeout(timeoutHandle);
      }
    }
    if (attempt < DEFAULT_BOOTSTRAP_ATTEMPTS - 1) {
      await sleep(DEFAULT_BOOTSTRAP_RETRY_DELAY_MS);
    }
  }
  throw lastError ?? new Error("Bootstrap request failed");
}

export function sessionBootstrapFromUrl(
  search: string,
  defaultWsPath = "/ws/session",
): SessionBootstrapPayload | null {
  const params = new URLSearchParams(search);
  const sessionId = params.get("session_id");
  const wsToken = params.get("ws_token");
  if (!sessionId || !wsToken) {
    return null;
  }
  return {
    session_id: sessionId,
    ws_token: wsToken,
    ws_path: params.get("ws_path") || defaultWsPath,
    protocol_version: params.get("protocol_version") || "1.3.1",
  };
}

export function buildSessionWebSocketUrl(
  bootstrap: SessionBootstrapPayload,
  locationLike: Pick<Location, "protocol" | "hostname"> = window.location,
  backendOrigin: string = getBackendOrigin(locationLike),
): string {
  const backend = new URL(backendOrigin);
  const protocol = backend.protocol === "https:" ? "wss" : "ws";
  const params = new URLSearchParams({
    session_id: bootstrap.session_id,
    token: bootstrap.ws_token,
  });
  return `${protocol}://${backend.host}${bootstrap.ws_path}?${params.toString()}`;
}
