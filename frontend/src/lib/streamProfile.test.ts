import { describe, expect, it } from "vitest";

import { buildWebRTCRequestPayload, normalizeStreamProfile } from "./streamProfile";

describe("streamProfile", () => {
  it("normalizes only the hidden demo_local profile", () => {
    expect(normalizeStreamProfile("demo_local")).toBe("demo_local");
    expect(normalizeStreamProfile("interactive")).toBeNull();
    expect(normalizeStreamProfile(null)).toBeNull();
  });

  it("builds a webrtc payload when demo_local is enabled", () => {
    expect(buildWebRTCRequestPayload("demo_local")).toEqual({ stream_profile: "demo_local" });
    expect(buildWebRTCRequestPayload(null)).toEqual({});
  });
});
