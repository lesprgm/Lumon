import { describe, expect, it, vi, afterEach } from "vitest";

import { SessionSocket } from "./sessionSocket";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSED = 3;

  onopen: (() => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  readyState = FakeWebSocket.CONNECTING;
  sent: string[] = [];

  constructor(public readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
  }

  send(payload: string): void {
    this.sent.push(payload);
  }
}

describe("SessionSocket", () => {
  const originalWebSocket = globalThis.WebSocket;

  afterEach(() => {
    vi.restoreAllMocks();
    FakeWebSocket.instances.length = 0;
    globalThis.WebSocket = originalWebSocket;
  });

  it("does not reconnect forever after a 1008 stale-session close", () => {
    vi.useFakeTimers();
    globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket;
    const onMessage = vi.fn();
    const onStatus = vi.fn();
    const socket = new SessionSocket("ws://127.0.0.1/ws", onMessage, onStatus);

    socket.connect();
    expect(FakeWebSocket.instances).toHaveLength(1);

    FakeWebSocket.instances[0].onclose?.({ code: 1008 } as CloseEvent);
    vi.advanceTimersByTime(2000);

    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("still reconnects after transient closes", () => {
    vi.useFakeTimers();
    globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket;
    const onMessage = vi.fn();
    const onStatus = vi.fn();
    const socket = new SessionSocket("ws://127.0.0.1/ws", onMessage, onStatus);

    socket.connect();
    expect(FakeWebSocket.instances).toHaveLength(1);

    FakeWebSocket.instances[0].onclose?.({ code: 1006 } as CloseEvent);
    vi.advanceTimersByTime(1600);

    expect(FakeWebSocket.instances).toHaveLength(2);
  });
});
