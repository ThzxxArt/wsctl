import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TerminalSocket } from "@/protocol/client";

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];

  readyState = 0;
  binaryType = "";
  onopen: (() => void) | null = null;
  onmessage: ((event: unknown) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  sent: string[] = [];

  constructor(
    public url: string,
    public protocols?: string[],
  ) {
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {}
}

function makeSocket(states: string[], onAuthExpired?: () => void): TerminalSocket {
  return new TerminalSocket({
    session: "s1",
    cols: 80,
    rows: 24,
    handlers: {
      onData: () => {},
      onControl: () => {},
      onState: (state) => states.push(state),
      onNotice: () => {},
      onAuthExpired,
    },
  });
}

describe("TerminalSocket close codes", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("retries a 4500 server failure with backoff instead of dying", () => {
    const states: string[] = [];
    const socket = makeSocket(states);
    socket.connect();
    expect(FakeWebSocket.instances).toHaveLength(1);

    FakeWebSocket.instances[0].onclose?.({ code: 4500 });
    expect(states).toContain("disconnected");
    expect(states).not.toContain("failed");

    vi.advanceTimersByTime(1_000);
    expect(FakeWebSocket.instances.length).toBeGreaterThan(1);
    socket.close();
  });

  it("does not retry an expired login", () => {
    const states: string[] = [];
    const socket = makeSocket(states);
    socket.connect();
    FakeWebSocket.instances[0].onclose?.({ code: 4401 });
    expect(states).toContain("failed");

    vi.advanceTimersByTime(5_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    socket.close();
  });

  it("surfaces a 4401 as an expired login and never retries", () => {
    const states: string[] = [];
    let expired = 0;
    const socket = makeSocket(states, () => (expired += 1));
    socket.connect();
    FakeWebSocket.instances[0].onclose?.({ code: 4401 });
    expect(expired).toBe(1);
    expect(states).toContain("failed");
    vi.advanceTimersByTime(5_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    socket.close();
  });

  it("ignores a late close from a superseded socket", () => {
    const states: string[] = [];
    const socket = makeSocket(states);
    socket.connect();
    const first = FakeWebSocket.instances[0];
    socket.connect();
    expect(FakeWebSocket.instances).toHaveLength(2);

    // The old socket closes after the new one exists; clearing ``this.ws``
    // here would silently stop all input on the live connection.
    first.onclose?.({ code: 4401 });
    expect(states).not.toContain("failed");
    expect(FakeWebSocket.instances).toHaveLength(2);
    socket.close();
  });

  it("requests a fresh snapshot on gap and retries it with backoff", () => {
    const states: string[] = [];
    const socket = makeSocket(states);
    socket.connect();
    const ws = FakeWebSocket.instances[0];
    ws.readyState = 1;
    ws.onopen?.();

    ws.onmessage?.({
      data: JSON.stringify({ v: 2, type: "gap", from: 10, to: 20 }),
    });
    const requests = () =>
      ws.sent.filter((message) => message.includes('"snapshot-request"')).length;
    expect(requests()).toBe(1);
    expect(states).toContain("resyncing");

    vi.advanceTimersByTime(2_000);
    expect(requests()).toBeGreaterThan(1);
    socket.close();
  });

  it("reconnects when a socket stops answering liveness pings", () => {
    const states: string[] = [];
    const socket = makeSocket(states);
    socket.connect();
    FakeWebSocket.instances[0].readyState = 1;
    FakeWebSocket.instances[0].onopen?.();

    vi.advanceTimersByTime(100_000);
    vi.advanceTimersByTime(1_000);
    expect(states).toContain("disconnected");
    expect(FakeWebSocket.instances.length).toBeGreaterThan(1);
    socket.close();
  });
});
