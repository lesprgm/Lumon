import type { AnyServerEnvelope, WebRTCIcePayload, WebRTCOfferPayload } from "../protocol/types";

export type WebRTCStatus = "idle" | "connecting" | "connected" | "disconnected" | "failed" | "closed";

type SendMessage = (message: { type: string; payload: Record<string, unknown> }) => void;

type StatusHandler = (status: WebRTCStatus) => void;

type StreamHandler = (stream: MediaStream | null) => void;

const MAX_RECONNECT_ATTEMPTS = 3;
const RECONNECT_DELAY_MS = 2000;
const ICE_DISCONNECTED_TIMEOUT_MS = 5000;

export class WebRTCClient {
  private peer: RTCPeerConnection | null = null;
  private status: WebRTCStatus = "idle";
  private readonly sendMessage: SendMessage;
  private readonly onStatus: StatusHandler;
  private readonly onStream: StreamHandler;
  private reconnectAttempts = 0;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private disconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private lastOfferPayload: WebRTCOfferPayload | null = null;
  private isDisposed = false;

  constructor(sendMessage: SendMessage, onStatus: StatusHandler, onStream: StreamHandler) {
    this.sendMessage = sendMessage;
    this.onStatus = onStatus;
    this.onStream = onStream;
  }

  requestOffer(): void {
    this.sendMessage({ type: "webrtc_request", payload: {} });
  }

  handleServerMessage(message: AnyServerEnvelope): void {
    if (message.type === "webrtc_offer") {
      void this.acceptOffer(message.payload);
      return;
    }
    if (message.type === "webrtc_ice") {
      void this.addIceCandidate(message.payload);
    }
  }

  close(): void {
    this.isDisposed = true;
    this.clearReconnectTimeout();
    this.clearDisconnectTimeout();
    this.disposePeer("closed");
  }

  private clearReconnectTimeout(): void {
    if (this.reconnectTimeout !== null) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
  }

  private clearDisconnectTimeout(): void {
    if (this.disconnectTimeout !== null) {
      clearTimeout(this.disconnectTimeout);
      this.disconnectTimeout = null;
    }
  }

  private scheduleReconnect(): void {
    if (this.isDisposed || this.reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      this.setStatus("failed");
      return;
    }

    this.clearReconnectTimeout();
    this.reconnectAttempts++;

    this.setStatus("disconnected");

    this.reconnectTimeout = setTimeout(() => {
      if (this.lastOfferPayload && !this.isDisposed) {
        void this.acceptOffer(this.lastOfferPayload);
      }
    }, RECONNECT_DELAY_MS);
  }

  private async acceptOffer(payload: WebRTCOfferPayload): Promise<void> {
    if (this.isDisposed) {
      return;
    }

    this.setStatus("connecting");
    this.lastOfferPayload = payload;

    if (this.peer) {
      this.disposePeer("closed");
    }

    this.peer = this.buildPeer(payload.ice_servers ?? []);

    try {
      await this.peer.setRemoteDescription({ type: payload.type, sdp: payload.sdp });
      const answer = await this.peer.createAnswer();
      await this.peer.setLocalDescription(answer);

      if (this.peer.localDescription) {
        this.sendMessage({
          type: "webrtc_answer",
          payload: {
            sdp: this.peer.localDescription.sdp,
            type: this.peer.localDescription.type,
          },
        });
      }
    } catch {
      this.scheduleReconnect();
    }
  }

  private async addIceCandidate(payload: WebRTCIcePayload): Promise<void> {
    if (!this.peer || !payload.candidate) {
      return;
    }
    try {
      await this.peer.addIceCandidate({
        candidate: payload.candidate,
        sdpMid: payload.sdp_mid ?? undefined,
        sdpMLineIndex: payload.sdp_mline_index ?? undefined,
      });
    } catch {
      // ICE candidate add failed - connection may be unstable but don't fail immediately
    }
  }

  private buildPeer(iceServers: Array<Record<string, unknown>>): RTCPeerConnection {
    const peer = new RTCPeerConnection({
      iceServers: normalizeIceServers(iceServers),
      iceTransportPolicy: "all",
    });

    peer.onicecandidate = (event) => {
      if (!event.candidate) {
        return;
      }
      this.sendMessage({
        type: "webrtc_ice",
        payload: {
          candidate: event.candidate.candidate,
          sdp_mid: event.candidate.sdpMid ?? null,
          sdp_mline_index: event.candidate.sdpMLineIndex ?? null,
        },
      });
    };

    peer.ontrack = (event) => {
      const [stream] = event.streams;
      if (stream) {
        this.onStream(stream);
      }
      event.track.onended = () => {
        if (!this.isDisposed) {
          this.scheduleReconnect();
        }
      };
    };

    peer.onconnectionstatechange = () => {
      if (this.isDisposed) {
        return;
      }

      switch (peer.connectionState) {
        case "connected":
          this.clearDisconnectTimeout();
          this.reconnectAttempts = 0;
          this.setStatus("connected");
          break;
        case "disconnected":
          this.startDisconnectTimer();
          break;
        case "failed":
          this.clearDisconnectTimeout();
          this.scheduleReconnect();
          break;
        case "closed":
          this.clearDisconnectTimeout();
          this.setStatus("closed");
          break;
      }
    };

    peer.oniceconnectionstatechange = () => {
      if (this.isDisposed) {
        return;
      }

      switch (peer.iceConnectionState) {
        case "disconnected":
          this.startDisconnectTimer();
          break;
        case "failed":
          this.clearDisconnectTimeout();
          this.scheduleReconnect();
          break;
        case "closed":
          this.clearDisconnectTimeout();
          this.setStatus("closed");
          break;
      }
    };

    return peer;
  }

  private startDisconnectTimer(): void {
    this.clearDisconnectTimeout();

    this.disconnectTimeout = setTimeout(() => {
      if (this.peer && !this.isDisposed) {
        const state = this.peer.iceConnectionState;
        if (state === "disconnected" || state === "failed") {
          this.scheduleReconnect();
        }
      }
    }, ICE_DISCONNECTED_TIMEOUT_MS);
  }

  private disposePeer(nextStatus: WebRTCStatus): void {
    const peer = this.peer;
    this.peer = null;
    if (peer) {
      peer.onicecandidate = null;
      peer.ontrack = null;
      peer.onconnectionstatechange = null;
      peer.oniceconnectionstatechange = null;
      try {
        peer.close();
      } catch {
        // ignore close failures during teardown
      }
    }
    this.onStream(null);
    this.setStatus(nextStatus);
  }

  private setStatus(next: WebRTCStatus): void {
    if (this.status === next) {
      return;
    }
    this.status = next;
    this.onStatus(next);
  }
}

function normalizeIceServers(servers: Array<Record<string, unknown>>): RTCIceServer[] {
  return servers
    .map((server) => {
      const urls = server.urls;
      if (typeof urls === "string" || Array.isArray(urls)) {
        return { urls } as RTCIceServer;
      }
      return null;
    })
    .filter((server): server is RTCIceServer => server !== null);
}
