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

  constructor(
    public url: string,
    public protocols?: string[],
  ) {
    FakeWebSocket.instances.push(this);
  }

  send(): void {}

  close(): void {}
}

function makeSocket(states: string[]): TerminalSocket {
  return new TerminalSocket({
    session: "s1",
    cols: 80,
    rows: 24,
    handlers: {
      onData: () => {},
      onControl: () => {},
      onState: (state) => states.push(state),
      onNotice: () => {},
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
});
