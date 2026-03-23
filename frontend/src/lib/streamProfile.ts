import type { ClientPayloadMap } from "../protocol/types";

import type { StreamProfile } from "../protocol/types";

export function normalizeStreamProfile(raw: string | null | undefined): StreamProfile | null {
  return raw === "demo_local" ? raw : null;
}

export function buildWebRTCRequestPayload(
  streamProfile: StreamProfile | null,
): ClientPayloadMap["webrtc_request"] {
  return streamProfile ? { stream_profile: streamProfile } : {};
}
