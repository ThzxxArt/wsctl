import {
  FLAG_SNAPSHOT,
  MAX_INPUT_FRAME,
  ProtocolError,
  SUBPROTOCOL,
  advanceSeq,
  decodeControl,
  decodeData,
  encodeControl,
  encodeInput,
  type ControlMessage,
} from "./frames";

export type ConnectionState =
  "idle" | "connecting" | "connected" | "resyncing" | "disconnected" | "failed" | "exited";

export interface TerminalHandlers {
  onData: (payload: Uint8Array, seq: number, flags: number, ack: () => void) => void;
  onControl: (message: ControlMessage) => void;
  onState: (state: ConnectionState, detail?: string) => void;
  onNotice: (message: string, level?: "info" | "warn" | "error") => void;
}

export interface TerminalOptions {
  session: string;
  share?: string | null;
  cols: number;
  rows: number;
  lastSeq?: number | null;
  generation?: string | null;
  handlers: TerminalHandlers;
  onAck?: (seq: number) => void;
}

const FATAL_CODES = new Set([4400, 4401, 4403, 4404, 4409, 4426, 4429, 4500]);
const INITIAL_DELAY = 500;
const MAX_DELAY = 30_000;
const PING_INTERVAL = 25_000;
const ACK_INTERVAL = 100;

export class TerminalSocket {
  private ws: WebSocket | null = null;
  private readonly handlers: TerminalHandlers;
  private readonly session: string;
  private readonly share: string | null;
  private readonly onAck?: (seq: number) => void;
  private cols: number;
  private rows: number;
  private delay = INITIAL_DELAY;
  private reconnectTimer: number | null = null;
  private pingTimer: number | null = null;
  private ackTimer: number | null = null;
  private closed = false;
  private pendingAck: number | null = null;
  private lastAckSent = 0;
  //: Highest stream offset *received*, used only for flow-control ACKs. It may
  //: run ahead of ``lastSeq`` after a shed, because acknowledging delivery is
  //: not the same as being able to resume from there.
  private ackSeq = 0;
  //: True between a loss signal and a successful snapshot. While set, incoming
  //: live frames are rendered but must not advance the resume cursor: doing so
  //: would tell the next reconnect to skip exactly the bytes that are missing.
  awaitingSnapshot = false;

  lastSeq: number | null;
  generation: string | null;

  constructor(options: TerminalOptions) {
    this.session = options.session;
    this.share = options.share ?? null;
    this.cols = options.cols;
    this.rows = options.rows;
    this.lastSeq = options.lastSeq ?? null;
    this.generation = options.generation ?? null;
    this.handlers = options.handlers;
    this.onAck = options.onAck;
  }

  private setState(state: ConnectionState, detail?: string): void {
    this.handlers.onState(state, detail);
  }

  private url(): string {
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    const query = this.share ? `?share=${encodeURIComponent(this.share)}` : "";
    return `${scheme}//${location.host}/ws/v2${query}`;
  }

  connect(): void {
    if (this.closed) return;
    this.setState("connecting");
    const ws = new WebSocket(this.url(), [SUBPROTOCOL]);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      this.delay = INITIAL_DELAY;
      this.setState("connected");
      this.sendAttach();
      this.startHeartbeat();
    };
    ws.onmessage = (event) => {
      if (typeof event.data === "string") {
        this.handleControl(event.data);
      } else if (event.data instanceof ArrayBuffer) {
        this.handleData(event.data);
      } else if (event.data instanceof Blob) {
        void event.data.arrayBuffer().then((buffer) => this.handleData(buffer));
      }
    };
    ws.onerror = () => {
      /* close follows */
    };
    ws.onclose = (event) => {
      this.stopHeartbeat();
      this.ws = null;
      if (this.closed) return;
      if (event.code === 4404 || event.code === 4403) {
        this.setState("failed", `会话不可用（${event.code}）`);
      }
      if (FATAL_CODES.has(event.code)) {
        this.setState("failed", `连接已关闭（${event.code}）`);
        return;
      }
      this.setState("disconnected", `连接断开（${event.code || 1006}）`);
      this.scheduleReconnect();
    };
  }

  private sendAttach(): void {
    this.ackSeq = this.lastSeq ?? 0;
    this.pendingAck = null;
    const payload: Record<string, unknown> = {
      type: "attach",
      session: this.session,
      cols: this.cols,
      rows: this.rows,
      capabilities: ["snapshot", "osc133", "resume"],
    };
    if (!this.awaitingSnapshot && this.lastSeq !== null) {
      payload.last_seq = this.lastSeq;
    } else if (this.awaitingSnapshot) {
      // A gapped screen must not claim a contiguous offset: let the server
      // send a snapshot instead of replaying bytes on top of the gap.
      payload.snapshot_required = true;
    }
    if (this.generation) payload.generation = this.generation;
    this.sendText(encodeControl(payload));
  }

  private handleData(buffer: ArrayBuffer): void {
    let frame;
    try {
      frame = decodeData(buffer);
    } catch (error) {
      this.handlers.onNotice((error as Error).message, "error");
      return;
    }
    if (frame.flags & FLAG_SNAPSHOT) {
      // Snapshot bytes replace the screen; a resync state ends once written.
      this.setState("resyncing");
    }
    const isSnapshot = (frame.flags & FLAG_SNAPSHOT) !== 0;
    const ack = () => {
      const end = frame.seq + frame.payload.byteLength;
      this.ackSeq = Math.max(this.ackSeq, isSnapshot ? frame.seq : end);
      this.queueAck(this.ackSeq);
      if (isSnapshot) {
        // A snapshot is a rendered screen anchored at ``seq``. It restores
        // contiguity -- and, when the generation changed, it replaces the old
        // stream offset instead of being max()'d against it.
        this.lastSeq = frame.seq;
        this.ackSeq = frame.seq;
        this.awaitingSnapshot = false;
        this.setState("connected");
      } else if (!this.awaitingSnapshot) {
        this.lastSeq = advanceSeq(
          this.lastSeq ?? 0,
          frame.seq,
          frame.payload.byteLength,
          frame.flags,
        );
      }
    };
    this.handlers.onData(frame.payload, frame.seq, frame.flags, ack);
  }

  private handleControl(raw: string): void {
    let message: ControlMessage;
    try {
      message = decodeControl(raw);
    } catch (error) {
      this.handlers.onNotice((error as Error).message, "error");
      return;
    }
    switch (message.type) {
      case "attached": {
        if (typeof message.generation === "string") this.generation = message.generation;
        const incomplete = message.incomplete === true;
        if (incomplete) {
          // The server's replay itself was shed. Adopting ``seq`` here would
          // claim bytes the terminal never received and make every later
          // resume skip them; ask for a screen snapshot instead.
          this.awaitingSnapshot = true;
          this.setState("resyncing");
          this.handlers.onNotice("会话回放不完整，正在重新同步", "warn");
          this.requestSnapshot();
          break;
        }
        // Do not adopt ``message.seq`` here. Frames are written to xterm
        // asynchronously; the resume cursor advances in their write callbacks.
        // Adopting the server's end offset first would let a tab that closes
        // early persist a "caught up" marker over a screen that never got the
        // bytes.
        this.awaitingSnapshot = false;
        this.setState("connected");
        break;
      }
      case "gap":
        this.awaitingSnapshot = true;
        this.handlers.onNotice(
          `输出丢失 ${String(message.from)}..${String(message.to)}，正在请求快照`,
          "warn",
        );
        this.setState("resyncing");
        this.requestSnapshot();
        break;
      case "snapshot":
        this.setState("resyncing");
        break;
      case "resync-begin":
        this.setState("resyncing");
        break;
      case "resynced":
        if (message.incomplete) {
          // The repair itself lost bytes. Saying "connected" would hide a
          // screen that is still gapped; stay honest and let the user retry.
          this.awaitingSnapshot = true;
          this.setState("resyncing");
          this.handlers.onNotice("重新同步未完整，请稍后重试", "warn");
          break;
        }
        if (typeof message.seq === "number") {
          this.lastSeq = Math.max(this.lastSeq ?? 0, message.seq);
          this.ackSeq = Math.max(this.ackSeq, message.seq);
        }
        this.awaitingSnapshot = false;
        this.setState("connected");
        break;
      case "resync-rejected":
        this.handlers.onNotice("重新同步过于频繁，请稍候", "warn");
        this.setState(this.awaitingSnapshot ? "resyncing" : "connected");
        break;
      case "notice":
        this.handlers.onNotice(String(message.msg ?? "提示"), "warn");
        break;
      case "error":
        this.handlers.onNotice(String(message.msg ?? "服务器错误"), "error");
        break;
      case "exit":
        this.setState("exited", `会话已退出（退出码 ${String(message.code)}）`);
        this.closed = true;
        // The server closes the socket right after; stop the timers now so a
        // tab that stays open on an exited pane does not keep a 100ms ACK
        // interval (and a 25s ping) running forever.
        this.stopHeartbeat();
        this.pendingAck = null;
        break;
      case "evicted":
        this.handlers.onNotice(String(message.msg ?? "连接已被释放"), "warn");
        break;
      case "pong":
        break;
      default:
        break;
    }
    this.handlers.onControl(message);
  }

  private scheduleReconnect(): void {
    if (this.closed || this.reconnectTimer !== null) return;
    const wait = this.delay;
    this.delay = Math.min(this.delay * 2, MAX_DELAY);
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, wait);
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.pingTimer = window.setInterval(() => {
      this.sendText(encodeControl({ type: "ping", nonce: Date.now() }));
    }, PING_INTERVAL);
    this.ackTimer = window.setInterval(() => this.flushAck(), ACK_INTERVAL);
  }

  private stopHeartbeat(): void {
    if (this.pingTimer !== null) window.clearInterval(this.pingTimer);
    if (this.ackTimer !== null) window.clearInterval(this.ackTimer);
    this.pingTimer = null;
    this.ackTimer = null;
  }

  private queueAck(seq: number): void {
    this.pendingAck = Math.max(this.pendingAck ?? 0, seq);
    this.onAck?.(seq);
    const now = performance.now();
    if (now - this.lastAckSent >= ACK_INTERVAL) this.flushAck();
  }

  private flushAck(): void {
    if (this.pendingAck === null || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.sendText(encodeControl({ type: "ack", seq: this.pendingAck }));
    this.lastAckSent = performance.now();
    this.pendingAck = null;
  }

  private sendText(text: string): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(text);
  }

  private sendBinary(bytes: Uint8Array): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(bytes);
  }

  sendInput(data: string | Uint8Array): void {
    const bytes = typeof data === "string" ? new TextEncoder().encode(data) : data;
    if (bytes.byteLength > MAX_INPUT_FRAME) {
      this.handlers.onNotice("单条输入过大，已丢弃（可分批粘贴）", "warn");
      return;
    }
    try {
      this.sendBinary(encodeInput(bytes));
    } catch (error) {
      this.handlers.onNotice((error as ProtocolError).message, "error");
    }
  }

  resize(cols: number, rows: number): void {
    this.cols = cols;
    this.rows = rows;
    this.sendText(encodeControl({ type: "resize", cols, rows }));
  }

  requestSnapshot(): void {
    this.sendText(encodeControl({ type: "snapshot-request" }));
  }

  close(): void {
    this.closed = true;
    this.stopHeartbeat();
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.ws?.close(1000, "client closed");
    this.ws = null;
    this.setState("idle");
  }
}
