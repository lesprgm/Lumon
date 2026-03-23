import { describe, expect, it, vi } from "vitest";

import { WebRTCClient } from "./webrtcClient";

describe("WebRTCClient", () => {
  it("reuses the hidden demo_local request payload for subsequent offer requests", () => {
    const sendMessage = vi.fn();
    const client = new WebRTCClient(sendMessage, () => {}, () => {});

    client.setOfferRequestPayload({ stream_profile: "demo_local" });
    client.requestOffer();

    expect(sendMessage).toHaveBeenCalledWith({
      type: "webrtc_request",
      payload: { stream_profile: "demo_local" },
    });
  });
});
