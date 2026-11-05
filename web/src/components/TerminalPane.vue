<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { SearchAddon } from "@xterm/addon-search";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { WebglAddon } from "@xterm/addon-webgl";
import { CanvasAddon } from "@xterm/addon-canvas";
import { ImageAddon } from "@xterm/addon-image";
import { ClipboardAddon } from "@xterm/addon-clipboard";
import { SerializeAddon } from "@xterm/addon-serialize";
import type { IDisposable, ITerminalAddon } from "@xterm/xterm";
import { useWorkspaceStore, leavesOf, type LeafNode } from "@/stores/workspace";
import { useSessionsStore } from "@/stores/sessions";
import { useTerminalStore } from "@/stores/terminal";
import { terminalFontFamily, usePrefsStore } from "@/stores/prefs";
import { useUiStore } from "@/stores/ui";
import { usePresenceStore } from "@/stores/presence";
import PaneContextMenu from "./PaneContextMenu.vue";
import { TerminalSocket, type ConnectionState } from "@/protocol/client";
import { readScreen, writeScreen } from "@/protocol/cache";
import { sessions as sessionsApi } from "@/api";
import type { CommandBlock } from "@/api/types";
import { useI18n } from "vue-i18n";

const props = defineProps<{
  pane: LeafNode;
  tabId: string;
  focused: boolean;
  shareOnly?: boolean;
}>();
const writable = ref(true);
const shareMode = computed(() => Boolean(props.shareOnly || props.pane.share));
let connectEpoch = 0;
const workspace = useWorkspaceStore();
const sessions = useSessionsStore();
const terminalStore = useTerminalStore();
const prefs = usePrefsStore();
const ui = useUiStore();
const presence = usePresenceStore();
const { t } = useI18n();

const termHost = ref<HTMLDivElement | null>(null);
const searchOpen = ref(false);
const searchText = ref("");
const searchInput = ref<HTMLInputElement | null>(null);
const commandsOpen = ref(false);
const pickerOpen = ref(false);
const showMobileKeys = ref(false);
const zmodemOn = ref(false);
const zmodemActive = ref(false);
const desyncFrom = ref<number | null>(null);
const desyncTo = ref<number | null>(null);
const followOutput = ref(true);
const connectionState = ref<ConnectionState>("idle");
const connectionDetail = ref("");
const errorText = ref("");

let term: Terminal | null = null;
let fit: FitAddon | null = null;
let searchAddon: SearchAddon | null = null;
let serializeAddon: SerializeAddon | null = null;
let renderer: ITerminalAddon | null = null;
let socket: TerminalSocket | null = null;
let resizeObserver: ResizeObserver | null = null;
let visibilityObserver: IntersectionObserver | null = null;
let cacheTimer: number | null = null;
let zmodemSentry: any = null;
let zmodemTimer: number | null = null;
let pendingMarker: { marker: any; command: string } | null = null;
const markerByBlock = new Map<string, any>();
let writeQueue: Array<{ payload: Uint8Array; ack: () => void }> = [];
let writeBytes = 0;
let writeFrame: number | null = null;
let writeTimer: number | null = null;
let screenHydrated = false;
const WRITE_BATCH = 256 * 1024;
let disposables: IDisposable[] = [];
let audioContext: AudioContext | null = null;

const sessionId = computed(() => props.pane.sessionId);
const session = computed(() => (sessionId.value ? sessions.byId[sessionId.value] : undefined));
const meta = computed(() => (sessionId.value ? terminalStore.meta[sessionId.value] : undefined));
const commands = computed<CommandBlock[]>(() =>
  sessionId.value ? (terminalStore.commands[sessionId.value] ?? []) : [],
);
const otherTabs = computed(() => workspace.tabs.filter((tab) => tab.id !== props.tabId));
const statusText = computed(() => {
  if (!sessionId.value) return "未绑定会话";
  return {
    idle: t("app.connected"),
    connecting: t("app.connecting"),
    connected: t("app.connected"),
    resyncing: t("app.resyncing"),
    disconnected: t("app.disconnected"),
    failed: t("app.failed"),
    exited: t("app.exited"),
  }[connectionState.value];
});

function focus(): void {
  workspace.focusPane(props.pane.id);
  term?.focus();
}

function beep(): void {
  if (!prefs.prefs.notifySound) return;
  try {
    audioContext ??= new AudioContext();
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    oscillator.frequency.value = 760;
    gain.gain.value = 0.04;
    oscillator.connect(gain).connect(audioContext.destination);
    oscillator.start();
    oscillator.stop(audioContext.currentTime + 0.07);
  } catch {
    /* audio blocked */
  }
}

function notifySystem(message: string): void {
  if (!prefs.prefs.notifySystem || !document.hidden) return;
  if (!("Notification" in window)) return;
  if (Notification.permission === "granted") {
    new Notification("wsctl", { body: message });
  }
}

function titleFor(sessionName: string): void {
  if (prefs.prefs.notifyTitle && document.hidden) {
    document.title = `● ${sessionName} — wsctl`;
  }
}

function createTerminal(): Terminal {
  const instance = new Terminal({
    cursorBlink: prefs.prefs.cursorBlink,
    fontSize: prefs.prefs.fontSize,
    lineHeight: 1.2,
    fontFamily: terminalFontFamily(prefs.prefs.fontFamily),
    scrollback: prefs.prefs.scrollback,
    allowProposedApi: true,
    theme: prefs.terminalTheme,
    screenReaderMode: prefs.prefs.screenReader,
    rightClickSelectsWord: false,
    macOptionIsMeta: true,
    convertEol: false,
  });
  fit = new FitAddon();
  instance.loadAddon(fit);
  searchAddon = new SearchAddon();
  instance.loadAddon(searchAddon);
  serializeAddon = new SerializeAddon();
  instance.loadAddon(serializeAddon);
  try {
    const unicode = new Unicode11Addon();
    instance.loadAddon(unicode);
    instance.unicode.activeVersion = "11";
  } catch {
    /* older xterm */
  }
  instance.loadAddon(new WebLinksAddon());
  try {
    instance.loadAddon(new ClipboardAddon());
  } catch {
    /* the browser may deny clipboard access; OSC 52 is then a no-op */
  }
  try {
    instance.loadAddon(new ImageAddon());
  } catch {
    /* optional */
  }
  return instance;
}

function attachCanvasRenderer(): void {
  if (!term) return;
  try {
    const canvas = new CanvasAddon();
    term.loadAddon(canvas);
    renderer = canvas;
  } catch {
    // xterm's built-in renderer remains; it is slower but never blank.
    renderer = null;
  }
}

function attachRenderer(): void {
  if (!term) return;
  renderer?.dispose();
  renderer = null;
  const mode = prefs.prefs.termRenderer;
  if (mode === "dom") return;
  if (mode === "auto" || mode === "webgl") {
    try {
      const webgl = new WebglAddon();
      webgl.onContextLoss(() => {
        // A lost GPU context leaves the canvas blank or stale. Dispose it,
        // fall back immediately, and force a snapshot so the new renderer
        // has a complete screen rather than repainting whatever was last
        // uploaded.
        if (renderer === webgl) renderer = null;
        try {
          webgl.dispose();
        } catch {
          /* already disposed */
        }
        if (!term) return;
        attachCanvasRenderer();
        term.refresh(0, term.rows - 1);
        socket?.requestSnapshot();
      });
      term.loadAddon(webgl);
      renderer = webgl;
      return;
    } catch {
      if (mode === "webgl") {
        ui.notify("WebGL 渲染器不可用，已回退到 Canvas", "warning");
      }
    }
  }
  attachCanvasRenderer();
}

function scheduleFlush(): void {
  if (writeFrame !== null || writeTimer !== null || writeQueue.length === 0) return;
  // A background tab gets no animation frames. Holding the ACK until the tab
  // is visible again makes the server believe the viewer is slow: its queue
  // grows, frames are shed, and the tab returns to a gapped terminal. A short
  // timer keeps parsing and acknowledging while hidden, without pretending
  // the tab is painting at 60 Hz.
  if (document.visibilityState === "hidden") {
    writeTimer = window.setTimeout(() => {
      writeTimer = null;
      flushWrites();
    }, 50);
    return;
  }
  writeFrame = requestAnimationFrame(flushWrites);
}

function flushWrites(): void {
  writeFrame = null;
  if (writeTimer !== null) {
    window.clearTimeout(writeTimer);
    writeTimer = null;
  }
  if (!term || writeQueue.length === 0) return;

  // Parse at most one batch per frame. The old version joined the entire
  // backlog (a burst could be several MiB) into one xterm.write, freezing the
  // UI and delaying every ACK behind it. Bounding the input keeps scrolling
  // responsive while the rest waits for the next frame.
  const queued: Array<{ payload: Uint8Array; ack: () => void }> = [];
  let total = 0;
  while (writeQueue.length > 0) {
    const next = writeQueue[0];
    if (queued.length > 0 && total + next.payload.byteLength > WRITE_BATCH) break;
    writeQueue.shift();
    queued.push(next);
    total += next.payload.byteLength;
    if (total >= WRITE_BATCH) break;
  }
  if (total === 0) return;
  writeBytes = Math.max(0, writeBytes - total);

  const combined = new Uint8Array(total);
  let offset = 0;
  for (const item of queued) {
    combined.set(item.payload, offset);
    offset += item.payload.byteLength;
  }
  term.write(combined, () => {
    for (const item of queued) item.ack();
    if (followOutput.value) term?.scrollToBottom();
    scheduleFlush();
  });
}

function enqueueWrite(payload: Uint8Array, ack: () => void): void {
  writeQueue.push({ payload, ack });
  writeBytes += payload.byteLength;
  scheduleFlush();
}

function scheduleCache(): void {
  if (!serializeAddon || !sessionId.value || !socket) return;
  // A screen with a known gap must never be persisted with the *high*
  // sequence number: a later reload would restore a gapped screen and resume
  // from an offset that skips exactly the missing bytes. Wait for the snapshot
  // repair; until then the previous cache entry stays valid.
  if (desyncFrom.value !== null || socket.awaitingSnapshot) return;
  if (cacheTimer !== null) window.clearTimeout(cacheTimer);
  cacheTimer = window.setTimeout(() => {
    if (!serializeAddon || !sessionId.value || !socket?.generation) return;
    if (desyncFrom.value !== null || socket.awaitingSnapshot) return;
    // Keep the user's configured scrollback in the persisted screen. A hard
    // 2000-line cap silently discarded history every time a tab was switched
    // (inactive tabs unmount).
    const serialized = serializeAddon.serialize({
      scrollback: Math.max(2000, prefs.prefs.scrollback),
    });
    void writeScreen({
      session: sessionId.value,
      generation: socket.generation,
      lastSeq: socket.lastSeq ?? 0,
      serialized,
      updatedAt: Date.now(),
    });
    // Serializing 10k scrollback lines is synchronous main-thread work. Doing
    // it 1.2s after every command produced a visible hitch; wait for a longer
    // quiet period so interactive typing and TUIs are never competing with it.
  }, 5_000);
}

function refreshSize(): void {
  if (!fit || !term || !termHost.value) return;
  const { clientWidth, clientHeight } = termHost.value;
  if (clientWidth < 4 || clientHeight < 4) return;
  try {
    fit.fit();
  } catch {
    return;
  }
  const cols = term.cols;
  const rows = term.rows;
  if (sessionId.value) {
    terminalStore.setSize(sessionId.value, cols, rows);
    socket?.resize(cols, rows);
  }
}

async function connect(): Promise<void> {
  // A second connect (double retry, quick pane rebind) must invalidate the
  // first one's pending cache read; otherwise the slower one can repaint its
  // old screen and install a socket pointing at the wrong session.
  const epoch = ++connectEpoch;
  disconnect();
  // A new socket must not inherit the previous session's gap/repair state.
  desyncFrom.value = null;
  desyncTo.value = null;
  if (writeFrame !== null) cancelAnimationFrame(writeFrame);
  if (writeTimer !== null) window.clearTimeout(writeTimer);
  writeFrame = null;
  writeTimer = null;
  writeQueue = [];
  writeBytes = 0;
  const sid = sessionId.value;
  if (!sid || !term) {
    connectionState.value = "idle";
    return;
  }
  const cached = await readScreen(sid);
  if (epoch !== connectEpoch || sid !== sessionId.value || !term) return;
  let lastSeq: number | null = null;
  let generation: string | null = null;
  if (cached) {
    lastSeq = cached.lastSeq;
    generation = cached.generation;
    // Only the first connect may paint the persisted screen. A manual
    // reconnect happens on a terminal that already holds live content; writing
    // the older serialization on top of it duplicates the screen.
    if (cached.serialized && !screenHydrated) {
      term.write(cached.serialized);
    }
  }
  screenHydrated = true;
  const cols = term.cols || 80;
  const rows = term.rows || 24;
  const ws = new TerminalSocket({
    session: sid,
    share: props.pane.share ?? null,
    cols,
    rows,
    lastSeq,
    generation,
    onAck: (seq) => {
      if (socket === ws && sessionId.value === sid) scheduleCache();
      void seq;
    },
    handlers: {
      onData: (payload, _seq, _flags, ack) => {
        if (socket !== ws) return;
        if (zmodemSentry) {
          try {
            zmodemSentry.consume(payload);
          } catch {
            /* sentry rejected a frame; terminal data still must not be lost */
            term?.write(payload, ack);
          }
          ack();
        } else {
          enqueueWrite(payload, ack);
        }
        scheduleCache();
      },
      onState: (state, detail) => {
        if (socket !== ws) return;
        connectionState.value = state;
        connectionDetail.value = detail ?? "";
        terminalStore.setState(sid, state, detail);
        if (state === "connected") errorText.value = "";
        if (state === "failed") errorText.value = detail ?? "";
        if (state === "exited") {
          terminalStore.setExit(sid, null);
          titleFor(sessions.nameOf(sid));
        }
      },
      onControl: (message) => {
        if (socket !== ws) return;
        if (message.type === "attached") {
          writable.value = message.writable !== false;
        }
        if (message.type === "gap") {
          desyncFrom.value = typeof message.from === "number" ? message.from : 0;
          desyncTo.value = typeof message.to === "number" ? message.to : 0;
        } else if (message.type === "resynced") {
          // An incomplete resync means the repair itself lost bytes; keep the
          // bar until a complete snapshot actually arrives. The bare
          // ``snapshot`` event is only metadata; its data frame may still be
          // in the outbound queue.
          if (!message.incomplete) {
            desyncFrom.value = null;
            desyncTo.value = null;
          }
        }
        if (message.type === "command") {
          const phase = String(message.phase ?? "");
          if (phase === "running") {
            terminalStore.setCommand(
              sid,
              String(message.command ?? ""),
              message.cwd as string | null,
            );
            pendingMarker = {
              marker: term?.registerMarker(0) ?? null,
              command: String(message.command ?? ""),
            };
          } else if (phase === "finished") {
            const block: CommandBlock = {
              id: String(message.id ?? ""),
              session: sid,
              command: String(message.command ?? ""),
              cwd: (message.cwd as string | null) ?? null,
              started_at: (message.started_at as number | null) ?? null,
              ended_at: (message.ended_at as number | null) ?? null,
              duration_ms: (message.duration_ms as number | null) ?? null,
              exit_code: (message.exit_code as number | null) ?? null,
              status: (message.status as CommandBlock["status"]) ?? "ok",
            };
            if (pendingMarker?.marker) {
              markerByBlock.set(block.id, pendingMarker.marker);
              if (markerByBlock.size > 1000) {
                const oldest = markerByBlock.keys().next().value;
                if (oldest !== undefined) markerByBlock.delete(oldest);
              }
            }
            pendingMarker = null;
            terminalStore.addCommand(sid, block);
            if (block.status === "failed") {
              beep();
              notifySystem(`命令失败（exit ${block.exit_code}）：${block.command}`);
            }
          }
        } else if (message.type === "presence") {
          if (typeof message.clients === "number") {
            const names = Array.isArray(message.viewers)
              ? message.viewers.map((value) => String(value))
              : undefined;
            presence.apply(sid, message.clients, names);
          }
        } else if (message.type === "renamed") {
          const name = typeof message.name === "string" ? message.name : "";
          if (name) {
            workspace.renamePane(props.pane.id, name);
            sessions.applyRename(sid, name);
          }
        } else if (message.type === "exit") {
          terminalStore.setExit(sid, typeof message.code === "number" ? message.code : null);
        }
      },
      onNotice: (message, level) => {
        if (socket !== ws) return;
        ui.notify(message, level === "error" ? "error" : level === "warn" ? "warning" : "info");
      },
    },
  });
  socket = ws;
  ws.connect();
  refreshSize();
}

function disconnect(): void {
  socket?.close();
  socket = null;
}

async function setup(): Promise<void> {
  if (!termHost.value) return;
  term = createTerminal();
  term.open(termHost.value);
  if (localStorage.getItem("wsctl-debug") === "1") {
    const registry = ((
      window as unknown as { __wsctlTerminals?: Record<string, Terminal> }
    ).__wsctlTerminals ??= {});
    registry[props.pane.id] = term;
  }
  attachRenderer();
  disposables = [
    term.onData((data) => {
      if (!writable.value) return; // a read-only share must not echo locally
      if (zmodemActive.value) return; // protocol bytes own the input stream
      socket?.sendInput(data);
    }),
    term.onBinary((data) => {
      if (!writable.value || zmodemActive.value) return;
      socket?.sendInput(Uint8Array.from(data, (char) => char.charCodeAt(0)));
    }),
    term.onBell(() => {
      beep();
      titleFor(sessions.nameOf(sessionId.value));
      if (sessionId.value) terminalStore.ring(sessionId.value);
      notifySystem(`终端响铃：${paneTitle.value}`);
    }),
    term.onTitleChange((title) => {
      if (title.trim() && sessionId.value) workspace.renamePane(props.pane.id, title.trim());
    }),
    term.onResize(({ cols, rows }) => {
      if (sessionId.value) terminalStore.setSize(sessionId.value, cols, rows);
      socket?.resize(cols, rows);
    }),
    term.onSelectionChange(() => {
      if (prefs.prefs.copyOnSelect && term?.hasSelection()) {
        void writeClipboard(term.getSelection());
      }
    }),
  ];
  resizeObserver = new ResizeObserver(() => refreshSize());
  resizeObserver.observe(termHost.value);
  visibilityObserver = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        nextTick(refreshSize);
      }
    },
    { threshold: 0.05 },
  );
  visibilityObserver.observe(termHost.value);
  await connect();
}

const paneTitle = computed(() => session.value?.name ?? props.pane.title ?? "终端");

function reconnect(): void {
  errorText.value = "";
  void connect();
}

function clearTerminal(): void {
  term?.clear();
  term?.focus();
}

function toggleSearch(): void {
  searchOpen.value = !searchOpen.value;
  if (searchOpen.value) {
    nextTick(() => {
      searchInput.value?.focus();
      searchInput.value?.select();
    });
  }
}

function findNext(): void {
  if (!searchText.value) return;
  const found = searchAddon?.findNext(searchText.value, { incremental: true });
  if (!found) ui.notify("没有匹配内容", "info", 1500);
}

function findPrevious(): void {
  if (!searchText.value) return;
  searchAddon?.findPrevious(searchText.value);
}

async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  } catch {
    return false;
  }
}

async function copySelection(): Promise<void> {
  const text = term?.getSelection() ?? "";
  if (!text) return;
  if (await writeClipboard(text)) {
    ui.notify(t("app.copied"), "success", 1200);
  } else {
    ui.notify("复制失败：浏览器不允许访问剪贴板", "warning");
  }
}

async function paste(): Promise<void> {
  if (!writable.value) {
    ui.notify("该分享为只读", "warning");
    return;
  }
  try {
    const text = await navigator.clipboard.readText();
    // ``term.paste`` wraps the text in bracketed-paste markers when the shell
    // enabled them; sending raw bytes made a multi-line paste execute every
    // line immediately.
    term?.paste(text);
  } catch {
    ui.notify("浏览器拒绝了剪贴板读取", "warning");
  }
}

async function split(direction: "row" | "col"): Promise<void> {
  try {
    const created = await sessions.create({});
    workspace.splitPane(props.pane.id, direction, created.id, created.name);
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

async function createHere(): Promise<void> {
  try {
    const created = await sessions.create({});
    workspace.setPaneSession(props.pane.id, created.id, created.name);
    pickerOpen.value = false;
    // The session watcher resets the screen and reconnects; calling connect()
    // here as well opened (and immediately closed) a second socket.
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

function choose(sid: string): void {
  workspace.setPaneSession(props.pane.id, sid, sessions.nameOf(sid));
  pickerOpen.value = false;
}

function swapWithNext(): void {
  const tab = workspace.activeTab;
  if (!tab) return;
  const leaves = leavesOf(tab.root);
  if (leaves.length < 2) {
    ui.notify("当前标签只有一个分屏", "info", 1500);
    return;
  }
  const index = leaves.findIndex((leaf) => leaf.id === props.pane.id);
  const next = leaves[(index + 1) % leaves.length];
  if (workspace.swapPanes(props.pane.id, next.id)) {
    ui.notify("已交换分屏内容", "success", 1200);
  }
}

function moveToTab(event: Event): void {
  const target = event.target as HTMLSelectElement;
  const tabId = target.value;
  target.value = "";
  if (!tabId) return;
  try {
    if (workspace.movePaneToTab(props.pane.id, tabId)) {
      ui.notify("已移动分屏", "success", 1200);
    }
  } catch (error) {
    ui.notify((error as Error).message, "warning");
  }
}

function sendKey(sequence: string): void {
  socket?.sendInput(sequence);
  term?.focus();
}

function paneEventMatches(event: Event): boolean {
  const detail = (event as CustomEvent<{ paneId?: string }>).detail;
  return !detail?.paneId || detail.paneId === props.pane.id;
}

function onSearchEvent(event: Event): void {
  if (paneEventMatches(event)) {
    searchOpen.value = true;
    nextTick(() => searchInput.value?.focus());
  }
}

function onClearEvent(event: Event): void {
  if (paneEventMatches(event)) clearTerminal();
}

function onCopyEvent(event: Event): void {
  if (paneEventMatches(event)) void copySelection();
}

function onPasteEvent(event: Event): void {
  if (paneEventMatches(event)) void paste();
}

function runCommand(command: string): void {
  if (!command) return;
  socket?.sendInput(command + "\r");
  term?.focus();
}

async function copyCommand(command: string): Promise<void> {
  if (!(await writeClipboard(command))) {
    ui.notify("复制失败：浏览器不允许访问剪贴板", "warning");
    return;
  }
  ui.notify(t("app.copied"), "success", 1200);
}

function hasMarker(block: CommandBlock): boolean {
  return markerByBlock.has(block.id);
}

function blockRange(block: CommandBlock): { start: number; end: number } | null {
  if (!term) return null;
  const marker = markerByBlock.get(block.id);
  if (!marker) return null;
  const buffer = term.buffer.active;
  const start = marker.line;
  if (start < 0) return null;
  let end = buffer.baseY + buffer.cursorY;
  for (const other of markerByBlock.values()) {
    if (other.line > start && other.line < end) end = other.line;
  }
  return { start, end };
}

function jumpToBlock(block: CommandBlock): void {
  const range = blockRange(block);
  if (!range || !term) return;
  term.scrollToLine(Math.max(0, range.start - 2));
  term.focus();
}

async function copyOutput(block: CommandBlock): Promise<void> {
  const range = blockRange(block);
  if (!range || !term) {
    ui.notify("该命令块没有可复制的输出（历史块或已滚出缓冲）", "warning");
    return;
  }
  const buffer = term.buffer.active;
  const lines: string[] = [];
  for (let line = range.start; line < range.end; line += 1) {
    lines.push(buffer.getLine(line)?.translateToString(true) ?? "");
  }
  while (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
  const text = lines.join("\n");
  if (!text) {
    ui.notify("该命令块没有输出", "info", 1500);
    return;
  }
  if (!(await writeClipboard(text))) {
    ui.notify("复制失败：浏览器不允许访问剪贴板", "warning");
    return;
  }
  ui.notify(`已复制 ${lines.length} 行输出`, "success", 1500);
}

function selectOutput(block: CommandBlock): void {
  const range = blockRange(block);
  if (!range || !term) {
    ui.notify("该命令块没有可选择的输出（历史块或已滚出缓冲）", "warning");
    return;
  }
  term.selectLines(range.start, Math.max(range.start, range.end - 1));
  term.focus();
}

function jumpPrompt(direction: 1 | -1): void {
  if (!term) return;
  const lines = [...markerByBlock.values()]
    .map((marker) => Number(marker.line))
    .filter((line) => Number.isFinite(line) && line >= 0)
    .sort((a, b) => a - b);
  if (lines.length === 0) {
    ui.notify("还没有命令块可供跳转", "info", 1500);
    return;
  }
  const current = term.buffer.active.viewportY;
  const target =
    direction > 0
      ? lines.find((line) => line > current + 1)
      : [...lines].reverse().find((line) => line < current - 1);
  if (target === undefined) {
    ui.notify(direction > 0 ? "已经是最后一个提示符" : "已经是第一个提示符", "info", 1500);
    return;
  }
  term.scrollToLine(Math.max(0, target - 2));
  term.focus();
}

function releaseZmodem(): void {
  if (zmodemTimer !== null) window.clearTimeout(zmodemTimer);
  zmodemTimer = null;
  zmodemActive.value = false;
  zmodemOn.value = zmodemSentry !== null;
}

function resetZmodem(): void {
  zmodemSentry = null;
  if (zmodemTimer !== null) window.clearTimeout(zmodemTimer);
  zmodemTimer = null;
  zmodemActive.value = false;
  zmodemOn.value = false;
}

function armZmodemWatchdog(): void {
  if (zmodemTimer !== null) window.clearTimeout(zmodemTimer);
  zmodemTimer = window.setTimeout(() => {
    zmodemTimer = null;
    if (zmodemActive.value) {
      releaseZmodem();
      ui.notify("文件传输已中断，终端输入已恢复", "error");
    }
  }, 20_000);
}

async function toggleZmodem(): Promise<void> {
  if (zmodemActive.value) return;
  if (zmodemSentry) {
    zmodemSentry = null;
    releaseZmodem();
    return;
  }
  try {
    const module = await import("zmodem.js");
    const Zmodem = (module as unknown as { default?: any }).default ?? module;
    const owner = socket;
    zmodemSentry = new Zmodem.Sentry({
      // Tie the protocol bytes to the socket that was current when the
      // transfer armed; a pane rebind must never inject them into a new PTY.
      to_terminal: (octets: number[]) => term?.write(new Uint8Array(octets)),
      sender: (octets: number[]) => {
        if (socket === owner) owner?.sendInput(new Uint8Array(octets));
      },
      on_detect: (detection: any) => handleZmodem(detection),
      on_retract: () => {
        zmodemActive.value = false;
        zmodemOn.value = zmodemSentry !== null;
      },
    });
    zmodemOn.value = true;
    ui.notify("ZMODEM 传输已就绪：远端执行 sz/rz 即可", "info", 2500);
  } catch (error) {
    ui.notify(`ZMODEM 加载失败：${(error as Error).message}`, "error");
  }
}

function saveToDisk(payload: Uint8Array[], name: string): void {
  const blob = new Blob(payload as BlobPart[]);
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

async function sendFiles(session: any, files: File[]): Promise<void> {
  let remaining = files.reduce((sum, file) => sum + file.size, 0);
  for (let index = 0; index < files.length; index += 1) {
    const file = files[index];
    const transfer = await session.send_offer({
      name: file.name,
      size: file.size,
      mtime: new Date(file.lastModified),
      files_remaining: files.length - index,
      bytes_remaining: remaining,
    });
    if (!transfer) continue;
    const buffer = await file.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    transfer.send(bytes);
    await transfer.end();
    remaining -= file.size;
  }
  await session.close();
}

function handleZmodem(detection: any): void {
  let session: any;
  try {
    session = detection.confirm();
  } catch {
    return;
  }
  zmodemActive.value = true;
  zmodemOn.value = true;
  armZmodemWatchdog();
  const done = () => releaseZmodem();
  if (session.type === "send") {
    const picker = document.createElement("input");
    picker.type = "file";
    picker.multiple = true;
    picker.addEventListener("change", () => {
      if (!picker.files?.length) {
        done();
        return;
      }
      sendFiles(session, Array.from(picker.files)).then(done, done);
    });
    picker.click();
  } else {
    session.on("offer", (transfer: any) => {
      const name = transfer.get_details().name;
      const payload: Uint8Array[] = [];
      transfer.on("input", (chunk: number[]) => {
        payload.push(new Uint8Array(chunk));
        armZmodemWatchdog();
      });
      transfer
        .accept()
        .then(() => saveToDisk(payload, name))
        .catch(() => releaseZmodem());
    });
    session.on("session_end", done);
    session.start();
  }
}

const contextMenu = ref<{ x: number; y: number } | null>(null);

function onContextMenu(event: MouseEvent): void {
  event.preventDefault();
  if (prefs.prefs.rightClickPaste) {
    void paste();
    return;
  }
  contextMenu.value = { x: event.clientX, y: event.clientY };
}

function closeContextMenu(): void {
  contextMenu.value = null;
}

function contextAction(action: string): void {
  closeContextMenu();
  if (action === "copy") void copySelection();
  else if (action === "paste") void paste();
  else if (action === "clear") clearTerminal();
  else if (action === "repaint") socket?.requestSnapshot();
  else if (action === "split") void split("row");
  else if (action === "search") toggleSearch();
}

watch(
  () => props.pane.sessionId,
  async () => {
    searchOpen.value = false;
    pickerOpen.value = false;
    markerByBlock.clear();
    pendingMarker = null;
    resetZmodem();
    // A pane can be rebound to a different session. Keeping the old screen
    // would paint the new session's output on top of it; the cache marker must
    // also be reset so the new session's persisted screen can hydrate.
    term?.reset();
    screenHydrated = false;
    await nextTick();
    await connect();
  },
);

watch(
  () => [prefs.prefs.fontSize, prefs.prefs.fontFamily],
  () => {
    if (!term) return;
    term.options.fontSize = prefs.prefs.fontSize;
    term.options.fontFamily = terminalFontFamily(prefs.prefs.fontFamily);
    nextTick(refreshSize);
  },
);

watch(
  () => prefs.prefs.termTheme,
  () => {
    if (term) term.options.theme = prefs.terminalTheme;
  },
  { deep: true },
);

watch(
  () => prefs.prefs.cursorBlink,
  (value) => {
    if (term) term.options.cursorBlink = value;
  },
);

watch(
  () => prefs.prefs.screenReader,
  (value) => {
    if (term) term.options.screenReaderMode = value;
  },
);

watch(
  () => prefs.prefs.termRenderer,
  () => attachRenderer(),
);

watch(
  () => props.focused,
  (focused) => {
    if (focused) {
      nextTick(() => {
        refreshSize();
        term?.focus();
      });
    }
  },
);

onMounted(async () => {
  await nextTick();
  await setup();
  const initial = sessionId.value;
  if (initial) {
    try {
      const history = await sessionsApi.commands(initial, 100);
      if (sessionId.value === initial) {
        for (const block of history.commands) terminalStore.addCommand(initial, block);
      }
    } catch {
      /* the live stream still works if history is unavailable */
    }
  }
  if (!sessionId.value) pickerOpen.value = true;
  showMobileKeys.value = window.matchMedia("(max-width: 760px)").matches;
  window.addEventListener("wsctl-search", onSearchEvent);
  window.addEventListener("wsctl-clear", onClearEvent);
  window.addEventListener("wsctl-copy", onCopyEvent);
  window.addEventListener("wsctl-paste", onPasteEvent);
});

onBeforeUnmount(() => {
  window.removeEventListener("wsctl-search", onSearchEvent);
  window.removeEventListener("wsctl-clear", onClearEvent);
  window.removeEventListener("wsctl-copy", onCopyEvent);
  window.removeEventListener("wsctl-paste", onPasteEvent);
  if (cacheTimer !== null) window.clearTimeout(cacheTimer);
  if (writeFrame !== null) cancelAnimationFrame(writeFrame);
  if (writeTimer !== null) window.clearTimeout(writeTimer);
  writeFrame = null;
  writeTimer = null;
  writeQueue = [];
  writeBytes = 0;
  if (zmodemTimer !== null) window.clearTimeout(zmodemTimer);
  zmodemSentry = null;
  if (localStorage.getItem("wsctl-debug") === "1") {
    const registry = (window as unknown as { __wsctlTerminals?: Record<string, Terminal> })
      .__wsctlTerminals;
    if (registry) delete registry[props.pane.id];
  }
  disconnect();
  resizeObserver?.disconnect();
  visibilityObserver?.disconnect();
  for (const disposable of disposables) disposable.dispose();
  renderer?.dispose();
  term?.dispose();
  term = null;
});
</script>

<template>
  <section
    class="ws-pane"
    :class="{ 'is-focused': focused }"
    :data-state="connectionState"
    :aria-label="paneTitle"
    @mousedown="focus"
  >
    <header class="ws-pane-header">
      <span class="ws-status-dot" :class="connectionState" />
      <span class="pane-title" :title="paneTitle">{{ paneTitle }}</span>
      <span class="pane-state">{{ statusText }}</span>
      <span v-if="meta && meta.cols" class="pane-size">{{ meta.cols }}×{{ meta.rows }}</span>
      <span v-if="!writable" class="readonly-badge">只读</span>
      <div class="pane-actions">
        <select
          v-if="!shareMode && otherTabs.length"
          class="move-tab"
          title="移动到其他标签"
          aria-label="移动到其他标签"
          @change="moveToTab"
        >
          <option value="">移动到…</option>
          <option v-for="tab in otherTabs" :key="tab.id" :value="tab.id">{{ tab.name }}</option>
        </select>
        <button
          class="ws-icon-btn"
          title="搜索 (Ctrl+Shift+F)"
          aria-label="搜索终端内容"
          @click.stop="toggleSearch"
        >
          ⌕
        </button>
        <button
          class="ws-icon-btn"
          :class="{ 'zmodem-on': followOutput }"
          :title="followOutput ? '跟随输出：开' : '跟随输出：关'"
          :aria-label="followOutput ? '关闭跟随输出' : '开启跟随输出'"
          @click.stop="
            followOutput = !followOutput;
            followOutput && term?.scrollToBottom();
          "
        >
          ⤓
        </button>
        <button
          class="ws-icon-btn"
          :class="{ 'zmodem-on': zmodemOn }"
          :disabled="zmodemActive"
          :title="zmodemActive ? '传输进行中' : 'ZMODEM 文件传输（sz/rz）'"
          :aria-label="zmodemActive ? 'ZMODEM 传输进行中' : '开始 ZMODEM 文件传输'"
          @click.stop="toggleZmodem"
        >
          ⇅
        </button>
        <button
          class="ws-icon-btn"
          title="命令块"
          aria-label="打开命令块面板"
          @click.stop="commandsOpen = !commandsOpen"
        >
          ≡
        </button>
        <button class="ws-icon-btn" title="清屏" aria-label="清空终端" @click.stop="clearTerminal">
          ⌫
        </button>
        <button
          v-if="!shareMode"
          class="ws-icon-btn"
          title="向右分屏"
          aria-label="向右分屏"
          @click.stop="split('row')"
        >
          ◫
        </button>
        <button
          v-if="!shareMode"
          class="ws-icon-btn"
          title="向下分屏"
          aria-label="向下分屏"
          @click.stop="split('col')"
        >
          ⬓
        </button>
        <button
          v-if="!shareMode"
          class="ws-icon-btn"
          title="与下一个分屏交换"
          aria-label="与下一个分屏交换"
          @click.stop="swapWithNext"
        >
          ⇄
        </button>
        <button
          v-if="!shareMode"
          class="ws-icon-btn"
          :title="$t('app.maximize')"
          :aria-label="$t('app.maximize')"
          @click.stop="workspace.toggleMaximize(pane.id)"
        >
          ⤢
        </button>
        <button
          v-if="!shareMode"
          class="ws-icon-btn"
          :title="$t('app.closePane')"
          :aria-label="$t('app.closePane')"
          @click.stop="workspace.closePane(pane.id)"
        >
          ×
        </button>
      </div>
    </header>

    <div class="ws-pane-body" @contextmenu="onContextMenu">
      <div v-show="sessionId" ref="termHost" class="ws-term" />

      <div v-if="!sessionId && !shareMode" class="pane-picker">
        <p>这个分屏还没有绑定会话。</p>
        <button class="primary" @click="createHere">{{ $t("app.newSession") }}</button>
        <div class="picker-list ws-scroll">
          <button v-for="item in sessions.live" :key="item.id" @click="choose(item.id)">
            <span class="dot" :class="{ alive: item.alive }" />
            {{ item.name }}
            <span class="ws-muted">{{ item.backend }}</span>
          </button>
        </div>
      </div>

      <div v-if="sessionId && meta?.state === 'exited'" class="pane-ghost">
        <span>会话已结束（退出码 {{ meta.exitCode ?? "?" }}）</span>
        <div class="ghost-actions">
          <button @click="workspace.closePane(pane.id)">关闭分屏</button>
          <button @click="pickerOpen = true">连接其他会话</button>
        </div>
      </div>

      <div v-if="errorText" class="pane-error" role="alert">
        {{ errorText }}
        <button @click="reconnect">{{ $t("app.retry") }}</button>
      </div>

      <div v-if="desyncFrom !== null" class="desync-bar" role="status">
        <span>连接丢失了字节 {{ desyncFrom }}..{{ desyncTo }}，屏幕可能不完整</span>
        <button @click="socket?.requestSnapshot()">重新同步</button>
      </div>

      <div v-if="searchOpen" class="pane-search">
        <input
          ref="searchInput"
          v-model="searchText"
          class="pane-search-input"
          placeholder="搜索终端内容"
          aria-label="搜索终端内容"
          @keydown.enter.prevent="findNext"
          @keydown.shift.exact.enter.prevent="findPrevious"
          @keydown.esc.prevent="searchOpen = false"
        />
        <button aria-label="上一个匹配" @click="findPrevious">↑</button>
        <button aria-label="下一个匹配" @click="findNext">↓</button>
        <button aria-label="关闭搜索" @click="searchOpen = false">×</button>
      </div>

      <aside v-if="commandsOpen" class="command-gutter ws-scroll">
        <div class="gutter-head">
          <strong>命令块</strong>
          <div class="gutter-nav">
            <button title="上一个提示符" @click="jumpPrompt(-1)">↑</button>
            <button title="下一个提示符" @click="jumpPrompt(1)">↓</button>
            <button title="关闭" @click="commandsOpen = false">×</button>
          </div>
        </div>
        <p v-if="commands.length === 0" class="ws-muted">
          尚未检测到命令。安装 Shell Integration 后可用：<code
            >wsctl shell-integration install --write-rc</code
          >
        </p>
        <article v-for="block in commands" :key="block.id" class="command" :class="block.status">
          <div class="command-line">
            <span class="exit">{{ block.exit_code ?? "…" }}</span>
            <code>{{ block.command || "(未命名命令)" }}</code>
          </div>
          <div class="command-meta">
            <span v-if="block.duration_ms !== null">{{ block.duration_ms }} ms</span>
            <span v-if="block.cwd">{{ block.cwd }}</span>
          </div>
          <div class="command-actions">
            <button @click="runCommand(block.command)">重跑</button>
            <button @click="copyCommand(block.command)">复制命令</button>
            <button v-if="hasMarker(block)" @click="selectOutput(block)">选中输出</button>
            <button v-if="hasMarker(block)" @click="copyOutput(block)">复制输出</button>
            <button v-if="hasMarker(block)" @click="jumpToBlock(block)">跳转</button>
          </div>
        </article>
      </aside>

      <PaneContextMenu
        v-if="contextMenu"
        :x="contextMenu.x"
        :y="contextMenu.y"
        @action="contextAction"
        @close="closeContextMenu"
      />
    </div>

    <div v-if="showMobileKeys" class="mobile-keys">
      <button @click="sendKey('\x1b')">Esc</button>
      <button @click="sendKey('\x03')">Ctrl+C</button>
      <button @click="sendKey('\t')">Tab</button>
      <button @click="sendKey('\x1b[A')">↑</button>
      <button @click="sendKey('\x1b[B')">↓</button>
      <button @click="sendKey('\x1b[D')">←</button>
      <button @click="sendKey('\x1b[C')">→</button>
      <button @click="paste">粘贴</button>
    </div>

    <div v-if="pickerOpen && sessionId" class="picker-overlay" @click.self="pickerOpen = false">
      <div class="picker-card">
        <h3>切换会话</h3>
        <div class="picker-list ws-scroll">
          <button v-for="item in sessions.live" :key="item.id" @click="choose(item.id)">
            <span class="dot" :class="{ alive: item.alive }" />
            {{ item.name }}
            <span class="ws-muted">{{ item.backend }}</span>
          </button>
        </div>
        <div class="picker-actions">
          <button @click="createHere">{{ $t("app.newSession") }}</button>
          <button @click="pickerOpen = false">取消</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.pane-title {
  overflow: hidden;
  max-width: 180px;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}
.pane-state,
.pane-size {
  font-size: 10px;
}
.readonly-badge {
  padding: 1px 6px;
  border: 1px solid var(--warning);
  border-radius: 999px;
  color: var(--warning);
  font-size: 10px;
  white-space: nowrap;
}
.pane-actions {
  display: flex;
  gap: 1px;
  margin-left: auto;
}
@media (max-width: 760px) {
  .ws-pane-header {
    gap: 4px;
    padding: 0 4px;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .ws-pane-header::-webkit-scrollbar {
    display: none;
  }
  .pane-state,
  .pane-size,
  .move-tab {
    display: none;
  }
  .pane-title {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .pane-actions {
    flex: 0 0 auto;
  }
  .pane-actions .ws-icon-btn {
    min-width: 24px;
    padding: 0 3px;
  }
}
.move-tab {
  max-width: 92px;
  height: 22px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: transparent;
  color: var(--muted);
  font-size: 10px;
}
.ws-icon-btn.zmodem-on {
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 14%, transparent);
}
.ws-icon-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.pane-picker {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 24px;
  background: var(--bg-panel);
  color: var(--muted);
  font-size: 13px;
}
.pane-picker .primary {
  padding: 7px 14px;
  border: 0;
  border-radius: 7px;
  background: var(--accent);
  color: white;
}
.picker-list {
  width: min(420px, 100%);
  max-height: 240px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--bg);
}
.picker-list button {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 12px;
  border: 0;
  border-bottom: 1px solid var(--border);
  background: transparent;
  color: var(--text);
  text-align: left;
  font-size: 13px;
}
.picker-list button:hover {
  background: color-mix(in srgb, var(--accent) 10%, transparent);
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--muted);
}
.dot.alive {
  background: var(--success);
}
.pane-ghost {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  background: color-mix(in srgb, var(--bg-panel) 92%, transparent);
  color: var(--muted);
  font-size: 13px;
}
.ghost-actions {
  display: flex;
  gap: 8px;
}
.ghost-actions button,
.picker-actions button {
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--bg);
  color: var(--text);
  font-size: 12px;
}
.pane-error {
  position: absolute;
  top: 8px;
  left: 50%;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border: 1px solid color-mix(in srgb, var(--danger) 45%, var(--border));
  border-radius: 8px;
  background: var(--bg-elevated);
  color: var(--danger);
  font-size: 12px;
  transform: translateX(-50%);
}
.pane-error button {
  border: 0;
  background: transparent;
  color: var(--accent);
}
.desync-bar {
  position: absolute;
  top: 8px;
  left: 50%;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  border: 1px solid color-mix(in srgb, var(--warning) 55%, var(--border));
  border-radius: 8px;
  background: var(--bg-elevated);
  color: var(--warning);
  font-size: 11px;
  transform: translateX(-50%);
}
.desync-bar button {
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: transparent;
  color: var(--text);
  font-size: 11px;
}
.pane-search {
  position: absolute;
  top: 6px;
  right: 8px;
  z-index: 6;
  display: flex;
  gap: 4px;
  padding: 4px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-elevated);
  box-shadow: 0 10px 30px rgb(0 0 0 / 30%);
}
.pane-search input {
  width: 210px;
  height: 26px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--text);
  font-size: 12px;
}
.pane-search button {
  width: 26px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
}
.pane-search button:hover {
  background: var(--bg-panel);
  color: var(--text);
}
.command-gutter {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  z-index: 4;
  width: min(340px, 42%);
  padding: 10px;
  border-left: 1px solid var(--border);
  background: color-mix(in srgb, var(--bg-elevated) 96%, transparent);
  font-size: 11px;
}
.gutter-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.gutter-head button {
  border: 0;
  background: transparent;
  color: var(--muted);
}
.gutter-nav {
  display: flex;
  gap: 2px;
}
.command {
  padding: 7px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--success);
  border-radius: 6px;
  margin-bottom: 6px;
}
.command.failed {
  border-left-color: var(--danger);
}
.command.running {
  border-left-color: var(--warning);
}
.command-line {
  display: flex;
  gap: 6px;
  align-items: baseline;
}
.command-line code {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--term-font-family);
}
.exit {
  color: var(--muted);
}
.command-meta {
  display: flex;
  gap: 8px;
  margin-top: 3px;
  color: var(--muted);
  font-size: 10px;
}
.command-actions {
  display: flex;
  gap: 4px;
  margin-top: 5px;
}
.command-actions button {
  padding: 1px 6px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: transparent;
  color: var(--muted);
  font-size: 10px;
}
.mobile-keys {
  display: flex;
  gap: 4px;
  padding: 4px 8px;
  border-top: 1px solid var(--border);
  overflow-x: auto;
  background: var(--bg-elevated);
}
.mobile-keys button {
  min-width: 38px;
  height: 28px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--muted);
  font-size: 11px;
}
.picker-overlay {
  position: absolute;
  inset: 0;
  z-index: 20;
  display: grid;
  place-items: center;
  background: rgb(0 0 0 / 45%);
}
.picker-card {
  width: min(440px, 92%);
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-elevated);
  box-shadow: 0 24px 70px rgb(0 0 0 / 40%);
}
.picker-card h3 {
  margin: 0 0 12px;
  font-size: 14px;
}
.picker-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}
</style>
