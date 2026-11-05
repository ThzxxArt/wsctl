export const PROTOCOL_VERSION = 2;
export const SUBPROTOCOL = "wsctl.terminal.v2";

export const KIND_DATA = 1;
export const KIND_INPUT = 2;

export const FLAG_SNAPSHOT = 0b0000_0001;
export const FLAG_REPLAY = 0b0000_0010;
export const FLAG_LAST = 0b0000_0100;

const HEADER_SIZE = 10;
const INPUT_HEADER_SIZE = 2;
export const MAX_DATA_FRAME = 1024 * 1024;
export const MAX_INPUT_FRAME = 256 * 1024;

export interface DataFrame {
  seq: number;
  flags: number;
  payload: Uint8Array;
}

export interface InputFrame {
  flags: number;
  payload: Uint8Array;
}

export class ProtocolError extends Error {}

/**
 * The stream offset after a received frame.
 *
 * Live/replay frames carry real stream bytes, so they advance by their length.
 * A snapshot is a rendered screen anchored at `seq`; adding its byte length
 * would claim bytes that were never sent and make the next resume skip data.
 */
export function advanceSeq(lastSeq: number, seq: number, length: number, flags: number): number {
  if (flags & FLAG_SNAPSHOT) return seq;
  return Math.max(lastSeq, seq + length);
}

export function encodeData(seq: number, payload: Uint8Array, flags = 0): Uint8Array {
  if (!Number.isSafeInteger(seq) || seq < 0) throw new ProtocolError("seq 必须是非负整数");
  if (payload.byteLength > MAX_DATA_FRAME) throw new ProtocolError("数据帧超过上限");
  const frame = new Uint8Array(HEADER_SIZE + payload.byteLength);
  const view = new DataView(frame.buffer);
  frame[0] = KIND_DATA;
  view.setBigUint64(1, BigInt(seq), false);
  frame[9] = flags & 0xff;
  frame.set(payload, HEADER_SIZE);
  return frame;
}

export function decodeData(buffer: ArrayBuffer | Uint8Array): DataFrame {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  if (bytes.byteLength < HEADER_SIZE) throw new ProtocolError("数据帧头部不完整");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const kind = view.getUint8(0);
  if (kind !== KIND_DATA) throw new ProtocolError(`不是数据帧（kind=${kind}）`);
  const seq = Number(view.getBigUint64(1, false));
  if (!Number.isSafeInteger(seq)) throw new ProtocolError("seq 超出安全整数范围");
  const flags = view.getUint8(9);
  return { seq, flags, payload: bytes.slice(HEADER_SIZE) };
}

export function encodeInput(payload: Uint8Array, flags = 0): Uint8Array {
  if (payload.byteLength > MAX_INPUT_FRAME) throw new ProtocolError("输入帧超过上限");
  const frame = new Uint8Array(INPUT_HEADER_SIZE + payload.byteLength);
  frame[0] = KIND_INPUT;
  frame[1] = flags & 0xff;
  frame.set(payload, INPUT_HEADER_SIZE);
  return frame;
}

export function decodeInput(buffer: ArrayBuffer | Uint8Array): InputFrame {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  if (bytes.byteLength < INPUT_HEADER_SIZE) throw new ProtocolError("输入帧头部不完整");
  const kind = bytes[0];
  if (kind !== KIND_INPUT) throw new ProtocolError(`不是输入帧（kind=${kind}）`);
  return { flags: bytes[1], payload: bytes.slice(INPUT_HEADER_SIZE) };
}

export interface ControlMessage {
  v: number;
  type: string;
  [key: string]: unknown;
}

export function encodeControl(message: Record<string, unknown>): string {
  return JSON.stringify({ v: PROTOCOL_VERSION, ...message });
}

export function decodeControl(raw: string): ControlMessage {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch (error) {
    throw new ProtocolError(`控制消息不是合法 JSON：${(error as Error).message}`);
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ProtocolError("控制消息必须是 JSON 对象");
  }
  const message = value as Record<string, unknown>;
  if (message.v !== PROTOCOL_VERSION) {
    throw new ProtocolError(`不支持的协议版本：${String(message.v)}`);
  }
  if (typeof message.type !== "string") throw new ProtocolError("控制消息缺少 type");
  return message as ControlMessage;
}
