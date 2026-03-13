import { describe, expect, it, vi } from "vitest";

import { bootstrapSession, buildSessionWebSocketUrl, getBackendOrigin, sessionBootstrapFromUrl } from "./sessionBootstrap";

describe("sessionBootstrap", () => {
  it("builds the backend origin from the active host", () => {
    expect(getBackendOrigin({ protocol: "http:", hostname: "127.0.0.1" })).toBe("http://127.0.0.1:8000");
  });

  it("prefers an explicit backend origin from config", () => {
    expect(
      getBackendOrigin(
        { protocol: "http:", hostname: "127.0.0.1" },
        "http://127.0.0.1:8011/",
      ),
    ).toBe("http://127.0.0.1:8011");
  });

  it("builds a tokenized websocket url", () => {
    const url = buildSessionWebSocketUrl(
      {
        session_id: "sess_123",
        ws_token: "ws_secret",
        ws_path: "/ws/session",
        protocol_version: "1.3.1",
      },
      { protocol: "http:", hostname: "localhost" },
      "http://localhost:8012",
    );

    expect(url).toBe("ws://localhost:8012/ws/session?session_id=sess_123&token=ws_secret");
  });

  it("fetches a session bootstrap payload", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        session_id: "sess_123",
        ws_token: "ws_secret",
        ws_path: "/ws/session",
        protocol_version: "1.3.1",
      }),
    });

    const payload = await bootstrapSession(fetchMock as unknown as typeof fetch, "http://127.0.0.1:8000");

    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:8000/api/bootstrap", {
      headers: { Accept: "application/json" },
    });
    expect(payload.session_id).toBe("sess_123");
  });

  it("reads an existing bootstrap payload from the url", () => {
    expect(sessionBootstrapFromUrl("?session_id=sess_123&ws_token=ws_secret")).toEqual({
      session_id: "sess_123",
      ws_token: "ws_secret",
      ws_path: "/ws/session",
      protocol_version: "1.3.1",
    });
  });
});
