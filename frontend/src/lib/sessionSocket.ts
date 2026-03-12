import type { AnyServerEnvelope, ClientEnvelope, ClientMessageType } from "../protocol/types";

export class SessionSocket {
  private socket: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof globalThis.setTimeout> | null = null;
  private manualClose = false;
  private readonly url: string;
  private readonly onMessage: (message: AnyServerEnvelope) => void;
  private readonly onStatus: (status: "disconnected" | "connecting" | "connected" | "error") => void;

  constructor(
    url: string,
    onMessage: (message: AnyServerEnvelope) => void,
    onStatus: (status: "disconnected" | "connecting" | "connected" | "error") => void,
  ) {
    this.url = url;
    this.onMessage = onMessage;
    this.onStatus = onStatus;
  }

  connect(): void {
    this.manualClose = false;
    this.onStatus("connecting");
    this.socket = new WebSocket(this.url);
    this.socket.onopen = () => this.onStatus("connected");
    this.socket.onclose = (event) => {
      this.onStatus("disconnected");
      if (!this.manualClose && this.shouldReconnect(event)) {
        this.scheduleReconnect();
      }
    };
    this.socket.onerror = () => this.onStatus("error");
    this.socket.onmessage = (event) => {
      const message = JSON.parse(event.data) as AnyServerEnvelope;
      this.onMessage(message);
    };
  }

  disconnect(): void {
    this.manualClose = true;
    if (this.reconnectTimer !== null) {
      globalThis.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.socket?.close();
    this.socket = null;
    this.onStatus("disconnected");
  }

  send<K extends ClientMessageType>(message: ClientEnvelope<K>): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return;
    }
    this.socket.send(JSON.stringify(message));
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) {
      return;
    }
    this.reconnectTimer = globalThis.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 1500);
  }

  private shouldReconnect(event: CloseEvent): boolean {
    // 1008 is what the backend returns for stale or invalid session credentials.
    // Reconnecting forever on that path just hammers the backend and keeps a dead tab noisy.
    if (event.code === 1008) {
      return false;
    }
    return true;
  }
}
