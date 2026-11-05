import {
  FLAG_DISPLAY,
  FLAG_LAST,
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

const FATAL_CODES = new Set([4400, 4401, 4403, 4404, 4409, 4426, 4429]);
const INITIAL_DELAY = 500;
const MAX_DELAY = 30_000;
const PING_INTERVAL = 25_000;
const ACK_INTERVAL = 100;
//: A TCP connection can be half-open for minutes: the browser still reports
//: OPEN while nothing arrives or leaves. The server pings are request/response,
//: so a healthy socket answers well inside this window.
const LIVENESS_CHECK = 10_000;
//: Background tabs throttle timers to roughly once a minute; 90s leaves room
//: for one throttled ping round-trip before declaring the socket dead.
const LIVENESS_TIMEOUT = 90_000;
//: Snapshot repair is retried with backoff; after this many failed rounds the
//: socket is recycled instead of leaving the pane stuck on "resyncing".
const MAX_SNAPSHOT_RETRIES = 4;

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
  private livenessTimer: number | null = null;
  private snapshotTimer: number | null = null;
  private snapshotAttempts = 0;
  private snapshotLastProgressAt = 0;
  private lastMessageAt = 0;
  private closed = false;
  private snapshotPending = false;
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

  private readonly onOnline = (): void => {
    if (this.closed) return;
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
      this.delay = INITIAL_DELAY;
      this.connect();
    }
  };

  constructor(options: TerminalOptions) {
    window.addEventListener("online", this.onOnline);
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
    // A reconnect may race the previous socket's close event. Detach it first;
    // its handlers check ``this.ws === ws`` so a late close cannot clear the
    // new socket or schedule a second reconnect.
    const previous = this.ws;
    if (previous) {
      this.ws = null;
      try {
        previous.close();
      } catch {
        /* already closing */
      }
    }
    this.setState("connecting");
    this.lastMessageAt = Date.now();
    const ws = new WebSocket(this.url(), [SUBPROTOCOL]);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      if (this.ws !== ws) return;
      // Do not reset the backoff here: the server accepts the socket before
      // it rejects an overloaded/unauthorized session, so a reset on open made
      // every such rejection retry at 500ms forever. ``attached`` is the first
      // proof that a session actually exists.
      this.lastMessageAt = Date.now();
      // Stay "connecting" until the server's ``attached`` (or snapshot) proves
      // the session exists. A TCP open is not a working terminal.
      this.sendAttach();
      this.startHeartbeat();
    };
    ws.onmessage = (event) => {
      if (this.ws !== ws) return;
      this.lastMessageAt = Date.now();
      if (typeof event.data === "string") {
        this.handleControl(event.data);
      } else if (event.data instanceof ArrayBuffer) {
        this.handleData(event.data);
      } else if (event.data instanceof Blob) {
        void event.data.arrayBuffer().then((buffer) => {
          if (this.ws === ws) this.handleData(buffer);
        });
      }
    };
    ws.onerror = () => {
      /* close follows */
    };
    ws.onclose = (event) => {
      if (this.ws !== ws) return;
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

  /** Drop a half-open socket and let the normal backoff path replace it. */
  private restart(reason: string): void {
    if (this.closed) return;
    this.setState("disconnected", reason);
    this.stopHeartbeat();
    const ws = this.ws;
    this.ws = null;
    try {
      ws?.close();
    } catch {
      /* already gone */
    }
    this.scheduleReconnect();
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
    const isSnapshot = (frame.flags & FLAG_SNAPSHOT) !== 0;
    const isDisplay = (frame.flags & FLAG_DISPLAY) !== 0;
    if (isSnapshot && !isDisplay) {
      // A snapshot may be split into several frames with the same anchor.
      // Keep the resume cursor still until the frame carrying FLAG_LAST has
      // actually been painted; the first chunk must not acknowledge the whole
      // screen.
      this.snapshotPending = true;
      this.snapshotLastProgressAt = Date.now();
      this.setState("resyncing");
    }
    const ack = () => {
      const end = frame.seq + frame.payload.byteLength;
      this.ackSeq = Math.max(this.ackSeq, isSnapshot ? frame.seq : end);
      this.queueAck(this.ackSeq);
      if (isDisplay) {
        // Repaint output and the clear-screen prefix are display-only: they
        // never move the resume cursor and never clear a gap.
        return;
      }
      if (isSnapshot) {
        if ((frame.flags & FLAG_LAST) === 0) return;
        // A complete snapshot is a rendered screen anchored at ``seq``. It
        // restores contiguity -- and, when the generation changed, it replaces
        // the old stream offset instead of being max()'d against it.
        this.lastSeq = frame.seq;
        this.ackSeq = frame.seq;
        this.awaitingSnapshot = false;
        this.snapshotPending = false;
        this.resetSnapshotRepair();
        this.setState("connected");
        return;
      }
      if (!this.awaitingSnapshot) {
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
          this.snapshotPending = false;
          this.setState("resyncing");
          this.handlers.onNotice("会话回放不完整，正在重新同步", "warn");
          this.requestSnapshot();
          this.scheduleSnapshotRetry();
          break;
        }
        // Do not adopt ``message.seq`` here. Frames are written to xterm
        // asynchronously; the resume cursor advances in their write callbacks.
        // Adopting the server's end offset first would let a tab that closes
        // early persist a "caught up" marker over a screen that never got the
        // bytes.
        if (this.snapshotPending) {
          // Snapshot chunks queued before ``attached`` are still being
          // painted. The final chunk's ACK owns the cursor.
          break;
        }
        this.awaitingSnapshot = false;
        this.snapshotPending = false;
        this.resetSnapshotRepair();
        this.delay = INITIAL_DELAY; // an attached session is genuinely stable
        this.setState("connected");
        break;
      }
      case "gap":
        // A gap while a snapshot is in flight means the server may have
        // discarded the rest of that snapshot group. Never wait for a LAST
        // that will not come: ask for a fresh repair immediately and keep a
        // watchdog so a lost request cannot leave the pane stuck.
        this.awaitingSnapshot = true;
        this.snapshotPending = false;
        this.setState("resyncing");
        this.handlers.onNotice(
          `输出丢失 ${String(message.from)}..${String(message.to)}，正在请求快照`,
          "warn",
        );
        this.requestSnapshot();
        this.scheduleSnapshotRetry();
        break;
      case "snapshot":
        this.setState("resyncing");
        break;
      case "resync-begin":
        // The server sends this before the snapshot data, so any chunk that
        // follows belongs to the new repair, not to a discarded prefix.
        this.awaitingSnapshot = true;
        this.snapshotPending = false;
        this.setState("resyncing");
        break;
      case "resynced":
        if (message.incomplete) {
          // The repair itself lost bytes. Saying "connected" would hide a
          // screen that is still gapped; retry automatically with backoff.
          this.awaitingSnapshot = true;
          this.snapshotPending = false;
          this.setState("resyncing");
          this.handlers.onNotice("重新同步未完整，正在自动重试", "warn");
          this.requestSnapshot();
          this.scheduleSnapshotRetry();
          break;
        }
        if (this.snapshotPending) {
          // The final snapshot chunk's write callback advances the cursor.
          break;
        }
        // Do not adopt ``message.seq`` here: raw replay frames queued before
        // this control may still be unpainted, and ACKing an offset the user
        // never saw is exactly how a reconnect skips bytes.
        this.awaitingSnapshot = false;
        this.resetSnapshotRepair();
        this.setState("connected");
        break;
      case "resync-rejected":
        this.handlers.onNotice("重新同步过于频繁，请稍候", "warn");
        this.setState(this.awaitingSnapshot ? "resyncing" : "connected");
        if (this.awaitingSnapshot) this.scheduleSnapshotRetry();
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
    // Full jitter prevents a fleet of panes from reconnecting in lockstep
    // after a server restart; the lower half keeps recovery prompt.
    const wait = Math.round(this.delay * (0.5 + Math.random() * 0.5));
    this.delay = Math.min(this.delay * 2, MAX_DELAY);
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, wait);
  }

  private resetSnapshotRepair(): void {
    if (this.snapshotTimer !== null) window.clearTimeout(this.snapshotTimer);
    this.snapshotTimer = null;
    this.snapshotAttempts = 0;
    this.snapshotLastProgressAt = 0;
  }

  private scheduleSnapshotRetry(): void {
    if (this.closed || this.snapshotTimer !== null || !this.awaitingSnapshot) return;
    if (this.snapshotAttempts >= MAX_SNAPSHOT_RETRIES) {
      // A screen that cannot be repaired on this socket is usually a half-open
      // connection or a server-side repair failure. Reconnect with a fresh
      // attach rather than leaving the pane stuck in "resyncing" forever.
      this.restart("快照修复超时，正在重连");
      return;
    }
    const wait = Math.min(2_000 * 2 ** this.snapshotAttempts, 15_000);
    this.snapshotAttempts += 1;
    this.snapshotTimer = window.setTimeout(() => {
      this.snapshotTimer = null;
      if (this.closed || !this.awaitingSnapshot) return;
      this.requestSnapshot();
      this.scheduleSnapshotRetry();
    }, wait);
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.pingTimer = window.setInterval(() => {
      this.sendText(encodeControl({ type: "ping", nonce: Date.now() }));
    }, PING_INTERVAL);
    this.ackTimer = window.setInterval(() => this.flushAck(), ACK_INTERVAL);
    this.livenessTimer = window.setInterval(() => {
      if (this.closed || this.ws === null) return;
      const now = Date.now();
      if (now - this.lastMessageAt > LIVENESS_TIMEOUT) {
        this.restart("连接无响应，正在重连");
        return;
      }
      // A snapshot can stall without a close or a gap (the final chunk was
      // lost in transit). If no chunk has moved for 10s, ask again; the repair
      // retry cap eventually recycles the socket rather than hanging forever.
      if (
        (this.snapshotPending || this.awaitingSnapshot) &&
        now - this.snapshotLastProgressAt > 10_000
      ) {
        this.requestSnapshot();
      }
    }, LIVENESS_CHECK);
  }

  private stopHeartbeat(): void {
    if (this.pingTimer !== null) window.clearInterval(this.pingTimer);
    if (this.ackTimer !== null) window.clearInterval(this.ackTimer);
    if (this.livenessTimer !== null) window.clearInterval(this.livenessTimer);
    this.pingTimer = null;
    this.ackTimer = null;
    this.livenessTimer = null;
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
    if (this.closed) return;
    // A manual repair must also stop the screen cache from persisting a
    // half-painted frame at the old cursor. The flag is cleared only when the
    // snapshot's final chunk is written (or a complete resync arrives).
    this.awaitingSnapshot = true;
    this.snapshotLastProgressAt = Date.now();
    this.setState("resyncing");
    const payload: Record<string, unknown> = { type: "snapshot-request" };
    if (this.lastSeq !== null) payload.last_seq = this.lastSeq;
    this.sendText(encodeControl(payload));
    this.scheduleSnapshotRetry();
  }

  close(): void {
    this.closed = true;
    window.removeEventListener("online", this.onOnline);
    this.stopHeartbeat();
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    if (this.snapshotTimer !== null) window.clearTimeout(this.snapshotTimer);
    this.reconnectTimer = null;
    this.snapshotTimer = null;
    this.ws?.close(1000, "client closed");
    this.ws = null;
    this.setState("idle");
  }
}
