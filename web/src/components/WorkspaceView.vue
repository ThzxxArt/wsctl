<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useAuthStore } from "@/stores/auth";
import { useSessionsStore } from "@/stores/sessions";
import { useWorkspaceStore, findLeaf, leavesOf } from "@/stores/workspace";
import { useUiStore } from "@/stores/ui";
import { usePrefsStore } from "@/stores/prefs";
import { matchesBinding } from "@/utils/keybindings";
import { shares } from "@/api";
import TabBar from "./TabBar.vue";
import SessionSidebar from "./SessionSidebar.vue";
import SplitPane from "./SplitPane.vue";
import TerminalPane from "./TerminalPane.vue";
import StatusBar from "./StatusBar.vue";

const auth = useAuthStore();
const sessions = useSessionsStore();
const props = defineProps<{ shareOnly?: boolean }>();
const workspace = useWorkspaceStore();
const ui = useUiStore();
const prefs = usePrefsStore();

const activeLeaf = computed(() => {
  const tab = workspace.activeTab;
  return tab ? findLeaf(tab.root, tab.focus) : null;
});
const maximizedLeaf = computed(() => {
  if (!workspace.maximizedPaneId) return null;
  return workspace.allLeaves.find((leaf) => leaf.id === workspace.maximizedPaneId) ?? null;
});

async function newSession(): Promise<void> {
  try {
    const created = await sessions.create({});
    try {
      workspace.createTab(created.id, created.name);
      if (window.innerWidth <= 760) ui.sidebarOpen = false;
    } catch (error) {
      // The tab limit is local; leaving the shell running with no pane would
      // leak a process the user cannot see. Close what was just created.
      await sessions.remove(created.id).catch(() => undefined);
      throw error;
    }
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

function newTab(): void {
  try {
    workspace.createTab(null, "终端");
  } catch (error) {
    ui.notify((error as Error).message, "warning");
  }
}

async function splitFocused(direction: "row" | "col"): Promise<void> {
  const leaf = activeLeaf.value;
  if (!leaf) return;
  try {
    const created = await sessions.create({});
    try {
      const pane = workspace.splitPane(leaf.id, direction, created.id, created.name);
      if (!pane) throw new Error("无法在该分屏创建终端");
    } catch (error) {
      await sessions.remove(created.id).catch(() => undefined);
      throw error;
    }
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

function cyclePane(delta: number): void {
  const tab = workspace.activeTab;
  if (!tab) return;
  const leaves = leavesOf(tab.root);
  const index = leaves.findIndex((leaf) => leaf.id === tab.focus);
  const next = leaves[(index + delta + leaves.length) % leaves.length];
  if (next) workspace.focusPane(next.id);
}

function focusSessionAction(): void {
  const sid = activeLeaf.value?.sessionId;
  if (!sid) {
    ui.notify("请先选择或创建一个会话", "info");
    return;
  }
  ui.openShare(sid);
}

function replayAction(): void {
  const sid = activeLeaf.value?.sessionId;
  if (!sid) {
    ui.notify("请先选择会话", "info");
    return;
  }
  ui.openReplay(sid);
}

function dispatchPaneEvent(name: string): void {
  window.dispatchEvent(new CustomEvent(name, { detail: { paneId: activeLeaf.value?.id } }));
}

function onKeydown(event: KeyboardEvent): void {
  // Do not steal shortcuts from an open modal or the file editor. Ctrl+, over
  // a dirty editor used to open Settings, whose close path then discarded the
  // draft.
  if (document.querySelector(".n-modal")) return;
  const target = event.target as HTMLElement | null;
  const typing =
    target?.tagName === "INPUT" ||
    target?.tagName === "TEXTAREA" ||
    target?.tagName === "SELECT" ||
    target?.isContentEditable;

  const bindings = prefs.prefs.keybindings;
  const action = Object.keys(bindings).find((name) => matchesBinding(event, bindings[name]));
  if (!action) return;
  if (
    props.shareOnly &&
    ![
      "terminal.copy",
      "terminal.paste",
      "terminal.search",
      "terminal.clear",
      "font.increase",
      "font.decrease",
      "font.reset",
    ].includes(action)
  ) {
    // A share-only visitor has no session list, settings, admin, tabs or
    // split panes; letting those shortcuts through would only produce 401s.
    return;
  }
  // A focused xterm is a <textarea>; treating that as "the user is typing in a
  // form" disabled every global shortcut except the palette. Chorded bindings
  // (Ctrl/Alt/Meta) cannot be mistaken for terminal input -- xterm never
  // receives them, because this handler captures first -- so only unmodified
  // keys stay suppressed while a text field has focus.
  const chorded = /(^|\+)\s*(ctrl|alt|meta)\s*\+/i.test(bindings[action] ?? "");
  if (typing && !chorded && !["palette.open", "quickSwitch"].includes(action)) return;

  const handled = (): void => {
    event.preventDefault();
    event.stopPropagation();
  };

  switch (action) {
    case "tab.new":
      handled();
      newTab();
      break;
    case "tab.close":
      handled();
      if (workspace.activeTab) workspace.closeTab(workspace.activeTab.id);
      break;
    case "tab.duplicate":
      handled();
      if (workspace.activeTab) {
        try {
          workspace.duplicateTab(workspace.activeTab.id);
        } catch (error) {
          ui.notify((error as Error).message, "warning");
        }
      }
      break;
    case "pane.splitRight":
      handled();
      void splitFocused("row");
      break;
    case "pane.splitDown":
      handled();
      void splitFocused("col");
      break;
    case "pane.maximize":
      handled();
      if (activeLeaf.value) workspace.toggleMaximize(activeLeaf.value.id);
      break;
    case "pane.focusNext":
      handled();
      cyclePane(1);
      break;
    case "pane.focusPrev":
      handled();
      cyclePane(-1);
      break;
    case "pane.grow":
      handled();
      if (activeLeaf.value) workspace.nudgeRatio(activeLeaf.value.id, 5);
      break;
    case "pane.shrink":
      handled();
      if (activeLeaf.value) workspace.nudgeRatio(activeLeaf.value.id, -5);
      break;
    case "terminal.copy":
      handled();
      dispatchPaneEvent("wsctl-copy");
      break;
    case "terminal.paste":
      handled();
      dispatchPaneEvent("wsctl-paste");
      break;
    case "terminal.search":
      handled();
      dispatchPaneEvent("wsctl-search");
      break;
    case "terminal.clear":
      handled();
      dispatchPaneEvent("wsctl-clear");
      break;
    case "palette.open":
    case "palette.openAlt":
      handled();
      ui.commandPaletteOpen = true;
      break;
    case "quickSwitch":
      handled();
      ui.quickSwitchOpen = true;
      break;
    case "settings.open":
      handled();
      ui.settingsOpen = true;
      break;
    case "font.increase":
      handled();
      prefs.fontSizeUp();
      break;
    case "font.decrease":
      handled();
      prefs.fontSizeDown();
      break;
    case "font.reset":
      handled();
      prefs.fontSizeReset();
      break;
    default:
      break;
  }
}

function resetTitle(): void {
  document.title = "WsCTL · 终端工作台";
}

const installEvent = ref<{ prompt: () => Promise<void> } | null>(null);
const offline = ref(typeof navigator !== "undefined" ? !navigator.onLine : false);

function onBeforeInstall(event: Event): void {
  event.preventDefault();
  installEvent.value = event as unknown as { prompt: () => Promise<void> };
}

async function installApp(): Promise<void> {
  if (!installEvent.value) return;
  await installEvent.value.prompt();
  installEvent.value = null;
}

function onOffline(): void {
  offline.value = true;
  ui.notify("网络已断开；连接会在恢复后自动重建", "warning", 6000);
}

function onOnline(): void {
  offline.value = false;
  ui.notify("网络已恢复", "success", 3000);
}

let touchStart: { x: number; y: number } | null = null;
let pinchStart = 0;
let pinchFont = 14;

function touchDistance(touches: TouchList): number {
  const [a, b] = [touches[0], touches[1]];
  return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
}

function onTouchStart(event: TouchEvent): void {
  if (!prefs.prefs.mobileGestures) return;
  if (event.touches.length === 1) {
    touchStart = { x: event.touches[0].clientX, y: event.touches[0].clientY };
    pinchStart = 0;
  } else if (event.touches.length === 2) {
    touchStart = null;
    pinchStart = touchDistance(event.touches);
    pinchFont = prefs.prefs.fontSize;
  }
}

function onTouchMove(event: TouchEvent): void {
  if (!prefs.prefs.mobileGestures || event.touches.length !== 2 || pinchStart <= 0) return;
  const ratio = touchDistance(event.touches) / pinchStart;
  const size = Math.min(32, Math.max(8, Math.round(pinchFont * ratio)));
  if (size !== prefs.prefs.fontSize) prefs.prefs.fontSize = size;
  event.preventDefault();
}

function onTouchEnd(event: TouchEvent): void {
  if (!prefs.prefs.mobileGestures) return;
  if (pinchStart > 0) {
    pinchStart = 0;
    return;
  }
  if (!touchStart) return;
  const touch = event.changedTouches[0];
  const dx = touch.clientX - touchStart.x;
  const dy = touch.clientY - touchStart.y;
  touchStart = null;
  if (Math.abs(dx) < 70 || Math.abs(dy) > 50) return;
  const index = workspace.tabs.findIndex((tab) => tab.id === workspace.activeTabId);
  if (index < 0) return;
  const next = workspace.tabs[index + (dx < 0 ? 1 : -1)];
  if (next) workspace.activateTab(next.id);
}

async function openShareFromUrl(): Promise<void> {
  const match = location.pathname.match(/^\/s\/([^/]+)$/);
  const queryToken = new URLSearchParams(location.search).get("share");
  const token = match?.[1] ?? queryToken;
  if (!token) return;
  if (workspace.allLeaves.some((leaf) => leaf.share === token)) return;
  try {
    const info = await shares.resolve(token);
    workspace.createTab(info.session, "分享会话", true, token);
    ui.notify(info.scope === "read" ? "已通过只读分享连接" : "已通过分享连接", "success");
    // A 0.1.x link used ``?session=&share=``. Keep it working, but move the
    // address bar to the canonical ``/s/<token>`` so the next refresh and any
    // copy/paste no longer depends on the legacy form.
    if (!match && queryToken) {
      history.replaceState(null, "", `/s/${encodeURIComponent(queryToken)}`);
    }
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

let autoBoundInitialSession = false;
watch(
  () => sessions.initialized,
  (ready) => {
    if (!ready || autoBoundInitialSession) return;
    autoBoundInitialSession = true;
    if (sessions.live.length === 0) return;
    if (
      workspace.tabs.every((tab) => leavesOf(tab.root).every((leaf) => leaf.sessionId === null))
    ) {
      const first = sessions.live[0];
      const leaf = workspace.allLeaves[0];
      if (leaf) workspace.setPaneSession(leaf.id, first.id, first.name);
    }
  },
  { immediate: true },
);

onMounted(() => {
  window.addEventListener("keydown", onKeydown, true);
  window.addEventListener("focus", resetTitle);
  window.addEventListener("beforeinstallprompt", onBeforeInstall);
  window.addEventListener("offline", onOffline);
  window.addEventListener("online", onOnline);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) resetTitle();
  });
  resetTitle();
  void openShareFromUrl();
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown, true);
  window.removeEventListener("focus", resetTitle);
  window.removeEventListener("beforeinstallprompt", onBeforeInstall);
  window.removeEventListener("offline", onOffline);
  window.removeEventListener("online", onOnline);
});
</script>

<template>
  <div class="ws-shell">
    <div v-if="offline" class="offline-bar" role="status">网络已断开，恢复后会自动重连</div>
    <header class="topbar">
      <div class="topbar-left">
        <button
          v-if="!shareOnly"
          class="ws-icon-btn sidebar-toggle"
          :title="ui.sidebarOpen ? '收起会话侧栏' : '展开会话侧栏'"
          :aria-label="ui.sidebarOpen ? '收起会话侧栏' : '展开会话侧栏'"
          :aria-expanded="ui.sidebarOpen"
          @click="ui.sidebarOpen = !ui.sidebarOpen"
        >
          ☰
        </button>
        <span class="brand">WsCTL</span>
      </div>
      <span v-if="shareOnly" class="share-only-label">分享会话</span>
      <TabBar v-if="!shareOnly" />
      <div v-if="!shareOnly" class="top-actions">
        <button
          class="ws-icon-btn"
          :title="$t('app.newSession')"
          :aria-label="$t('app.newSession')"
          @click="newSession"
        >
          ＋
        </button>
        <select
          class="layout-select"
          :title="$t('app.layout')"
          :aria-label="$t('app.layout')"
          @change="workspace.applyPreset(($event.target as HTMLSelectElement).value as any)"
        >
          <option value="single">{{ $t("app.layout") }}: 1</option>
          <option value="columns">⋮⋮⋮</option>
          <option value="rows">≡≡≡</option>
          <option value="2x2">⊞</option>
        </select>
        <button
          class="ws-icon-btn"
          :title="$t('app.share')"
          :aria-label="$t('app.share')"
          @click="focusSessionAction"
        >
          ⇗
        </button>
        <button
          v-if="auth.isAdmin"
          class="ws-icon-btn"
          :title="$t('app.replay')"
          :aria-label="$t('app.replay')"
          @click="replayAction"
        >
          ▶
        </button>
        <button
          class="ws-icon-btn"
          :title="$t('app.files')"
          :aria-label="$t('app.files')"
          @click="ui.filesOpen = true"
        >
          ▤
        </button>
        <button
          v-if="installEvent"
          class="ws-icon-btn"
          title="安装 PWA"
          aria-label="安装 PWA"
          @click="installApp"
        >
          ⇩
        </button>
        <button
          class="ws-icon-btn"
          :title="$t('app.settings')"
          :aria-label="$t('app.settings')"
          @click="ui.settingsOpen = true"
        >
          ⚙
        </button>
        <button
          v-if="auth.isAdmin"
          class="ws-icon-btn"
          :title="$t('app.admin')"
          :aria-label="$t('app.admin')"
          @click="ui.adminOpen = true"
        >
          ♜
        </button>
        <button
          class="ws-icon-btn user"
          :title="`退出登录：${auth.user?.username ?? ''}`"
          :aria-label="`退出登录：${auth.user?.username ?? ''}`"
          @click="auth.logout()"
        >
          {{ auth.user?.username }} ⏻
        </button>
      </div>
    </header>

    <div class="ws-main">
      <SessionSidebar v-if="!shareOnly && ui.sidebarOpen" />
      <main
        class="ws-workspace"
        @touchstart.passive="onTouchStart"
        @touchmove="onTouchMove"
        @touchend="onTouchEnd"
      >
        <TerminalPane
          v-if="maximizedLeaf"
          :pane="maximizedLeaf"
          :tab-id="workspace.activeTab?.id ?? ''"
          :focused="true"
          :share-only="shareOnly"
        />
        <SplitPane
          v-else-if="workspace.activeTab"
          :node="workspace.activeTab.root"
          :tab-id="workspace.activeTab.id"
          :share-only="shareOnly"
        />
        <div v-else class="empty-workspace">
          <p>{{ shareOnly ? "正在打开分享会话…" : $t("app.noSessions") }}</p>
          <button v-if="!shareOnly" @click="newSession">{{ $t("app.newSession") }}</button>
        </div>
      </main>
    </div>
    <StatusBar />
  </div>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  height: 48px;
  padding: 8px 10px 0;
  border-bottom: 1px solid var(--border);
  background: var(--bg-elevated);
  user-select: none;
}
.topbar-left {
  display: flex;
  flex-shrink: 0;
  align-self: center;
  align-items: center;
  gap: 8px;
}
.sidebar-toggle {
  width: 30px;
  height: 30px;
  border-color: var(--border);
  background: color-mix(in srgb, var(--accent) 14%, transparent);
  color: var(--text);
  font-size: 15px;
}
.sidebar-toggle:hover {
  border-color: color-mix(in srgb, var(--accent) 55%, var(--border));
  background: color-mix(in srgb, var(--accent) 24%, transparent);
}
.offline-bar {
  padding: 4px 10px;
  border-bottom: 1px solid color-mix(in srgb, var(--warning) 50%, var(--border));
  background: color-mix(in srgb, var(--warning) 16%, var(--bg-elevated));
  color: var(--warning);
  font-size: 11px;
  text-align: center;
}
.brand {
  padding: 0 4px;
  color: var(--accent);
  font-family: var(--term-font-family);
  font-size: 17px;
  font-weight: 800;
  letter-spacing: 0.02em;
  line-height: 1;
}
.share-only-label {
  align-self: center;
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--muted);
  font-size: 12px;
}

.top-actions {
  display: flex;
  align-items: center;
  align-self: center;
  gap: 2px;
  margin-left: auto;
}
.layout-select {
  height: 26px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--muted);
  font-size: 11px;
}
.user {
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.empty-workspace {
  display: grid;
  place-items: center;
  gap: 12px;
  flex: 1;
  color: var(--muted);
}
.empty-workspace button {
  padding: 8px 16px;
  border: 0;
  border-radius: 8px;
  background: var(--accent);
  color: white;
}
@media (max-width: 760px) {
  .brand,
  .layout-select {
    display: none;
  }
  .top-actions {
    overflow-x: auto;
    max-width: 62vw;
    padding-bottom: 4px;
    scrollbar-width: none;
  }
  .top-actions::-webkit-scrollbar {
    display: none;
  }
}
</style>
