import { describe, expect, it } from "vitest";
import {
  FLAG_REPLAY,
  FLAG_SNAPSHOT,
  ProtocolError,
  advanceSeq,
  decodeControl,
  decodeData,
  decodeInput,
  encodeControl,
  encodeData,
  encodeInput,
} from "@/protocol/frames";

describe("protocol frames", () => {
  it("round-trips a data frame with a sequence beyond 32 bits", () => {
    const payload = new TextEncoder().encode("hello 世界");
    const frame = encodeData(2 ** 40 + 7, payload, FLAG_REPLAY | FLAG_SNAPSHOT);
    const decoded = decodeData(frame);
    expect(decoded.seq).toBe(2 ** 40 + 7);
    expect(decoded.flags).toBe(FLAG_REPLAY | FLAG_SNAPSHOT);
    expect(new TextDecoder().decode(decoded.payload)).toBe("hello 世界");
  });

  it("rejects a data frame with the wrong kind", () => {
    const frame = encodeData(0, new Uint8Array([1]));
    frame[0] = 9;
    expect(() => decodeData(frame)).toThrow(ProtocolError);
  });

  it("rejects a truncated header", () => {
    expect(() => decodeData(new Uint8Array([1, 2, 3]))).toThrow(ProtocolError);
  });

  it("round-trips an input frame", () => {
    const frame = encodeInput(new TextEncoder().encode("ls\r"));
    const decoded = decodeInput(frame);
    expect(new TextDecoder().decode(decoded.payload)).toBe("ls\r");
  });

  it("rejects an input frame over the limit", () => {
    expect(() => encodeInput(new Uint8Array(256 * 1024 + 1))).toThrow(ProtocolError);
  });

  it("stamps and validates the control version", () => {
    const text = encodeControl({ type: "ping", nonce: 1 });
    const message = decodeControl(text);
    expect(message.v).toBe(2);
    expect(message.type).toBe("ping");
  });

  it("rejects a control message from another version", () => {
    expect(() => decodeControl(JSON.stringify({ v: 1, type: "ping" }))).toThrow(ProtocolError);
  });

  it("rejects a control message with no type", () => {
    expect(() => decodeControl(JSON.stringify({ v: 2 }))).toThrow(ProtocolError);
  });

  it("advances by stream length except for a rendered snapshot", () => {
    expect(advanceSeq(100, 110, 5, 0)).toBe(115);
    expect(advanceSeq(100, 90, 5, FLAG_REPLAY)).toBe(100);
    expect(advanceSeq(100, 140, 8192, FLAG_SNAPSHOT)).toBe(140);
  });

  it("rejects malformed JSON", () => {
    expect(() => decodeControl("{oops")).toThrow(ProtocolError);
  });
});
