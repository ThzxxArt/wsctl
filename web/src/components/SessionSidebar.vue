<script setup lang="ts">
import { computed, ref } from "vue";
import { useSessionsStore } from "@/stores/sessions";
import { useWorkspaceStore } from "@/stores/workspace";
import { useUiStore } from "@/stores/ui";
import { usePresenceStore } from "@/stores/presence";
import { useAuthStore } from "@/stores/auth";
import { recordings, sessions as api } from "@/api";
import type { SessionInfo } from "@/api/types";

const sessions = useSessionsStore();
const workspace = useWorkspaceStore();
const ui = useUiStore();
const presence = usePresenceStore();
const auth = useAuthStore();
const filter = ref("");
const tab = ref<"live" | "history">("live");
const backendFilter = ref("");
const statusFilter = ref("");
const ownerFilter = ref("");

const backends = computed(() => {
  const values = new Set<string>();
  for (const row of [...sessions.live, ...sessions.history]) {
    if (row.backend) values.add(row.backend);
  }
  return [...values].sort();
});

const owners = computed(() => {
  const values = new Set<string>();
  for (const row of [...sessions.live, ...sessions.history]) {
    if (row.owner) values.add(row.owner);
  }
  return [...values].sort();
});

const filtered = computed(() => {
  const needle = filter.value.trim().toLowerCase();
  const rows = tab.value === "live" ? sessions.live : sessions.history;
  return rows.filter((row) => {
    if (backendFilter.value && row.backend !== backendFilter.value) return false;
    if (statusFilter.value && row.status !== statusFilter.value) return false;
    if (ownerFilter.value && row.owner !== ownerFilter.value) return false;
    if (!needle) return true;
    return (
      row.name.toLowerCase().includes(needle) ||
      row.id.toLowerCase().includes(needle) ||
      (row.owner ?? "").toLowerCase().includes(needle) ||
      (row.cwd ?? "").toLowerCase().includes(needle) ||
      (row.command ?? "").toLowerCase().includes(needle)
    );
  });
});

function closeSidebarOnMobile(): void {
  if (window.innerWidth <= 760) ui.sidebarOpen = false;
}

function open(session: SessionInfo): void {
  const active = workspace.activeTab;
  if (active) {
    const focused = active.focus;
    const rows = workspace.allLeaves.find((leaf) => leaf.id === focused);
    if (rows && rows.sessionId === null) {
      workspace.setPaneSession(rows.id, session.id, session.name);
      closeSidebarOnMobile();
      return;
    }
    if (workspace.allLeaves.some((leaf) => leaf.sessionId === session.id)) {
      const existing = workspace.allLeaves.find((leaf) => leaf.sessionId === session.id);
      if (existing) workspace.focusPane(existing.id);
      closeSidebarOnMobile();
      return;
    }
  }
  try {
    workspace.createTab(session.id, session.name);
    closeSidebarOnMobile();
  } catch (error) {
    ui.notify((error as Error).message, "warning");
  }
}

async function createSession(): Promise<void> {
  try {
    const created = await sessions.create({});
    try {
      workspace.createTab(created.id, created.name);
      if (window.innerWidth <= 760) ui.sidebarOpen = false;
    } catch (error) {
      await sessions.remove(created.id).catch(() => undefined);
      throw error;
    }
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

async function stop(session: SessionInfo): Promise<void> {
  if (!window.confirm(`终止会话「${session.name}」？正在运行的任务会结束。`)) return;
  try {
    await sessions.remove(session.id);
    ui.notify(`已终止 ${session.name}`, "success");
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

async function rename(session: SessionInfo): Promise<void> {
  const name = window.prompt("新的会话名称", session.name);
  if (!name || name === session.name) return;
  try {
    await sessions.rename(session.id, name);
    workspace.renamePane(
      workspace.allLeaves.find((leaf) => leaf.sessionId === session.id)?.id ?? "",
      name,
    );
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

async function reopen(session: SessionInfo): Promise<void> {
  try {
    const reopened = await api.reopen(session.id);
    await sessions.refresh();
    try {
      workspace.createTab(reopened.id, reopened.name);
    } catch (error) {
      // Reopen already spawned a process; a local tab-limit failure must not
      // leave it running with no surface.
      await sessions.remove(reopened.id).catch(() => undefined);
      throw error;
    }
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

async function toggleRecording(session: SessionInfo): Promise<void> {
  try {
    if (session.recording) {
      await recordings.stop(session.id);
      ui.notify("录制已停止", "success");
    } else {
      await recordings.start(session.id, false);
      ui.notify("录制已开始", "success");
    }
    await sessions.refresh();
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 / 1024).toFixed(1)} MiB`;
}
</script>

<template>
  <aside class="sidebar" :aria-label="$t('app.sessions')">
    <div class="sidebar-head">
      <div class="segmented" role="tablist">
        <button :class="{ active: tab === 'live' }" role="tab" @click="tab = 'live'">
          {{ $t("app.sessions") }} <span>{{ sessions.live.length }}</span>
        </button>
        <button :class="{ active: tab === 'history' }" role="tab" @click="tab = 'history'">
          {{ $t("app.history") }}
        </button>
      </div>
      <button class="new" :title="$t('app.newSession')" @click="createSession">＋</button>
    </div>
    <input v-model="filter" class="filter" :placeholder="$t('app.search')" />
    <div class="filters">
      <select v-model="backendFilter" :aria-label="$t('app.backend')">
        <option value="">全部后端</option>
        <option v-for="backend in backends" :key="backend" :value="backend">{{ backend }}</option>
      </select>
      <select v-model="statusFilter" :aria-label="$t('app.status')">
        <option value="">全部状态</option>
        <option value="running">运行中</option>
        <option value="stopped">已停止</option>
        <option value="expired">已过期</option>
        <option value="killed">已终止</option>
        <option value="interrupted">已中断</option>
      </select>
      <select v-model="ownerFilter" :aria-label="$t('app.owner')">
        <option value="">全部用户</option>
        <option v-for="owner in owners" :key="owner" :value="owner">{{ owner }}</option>
      </select>
    </div>
    <div class="list ws-scroll">
      <p v-if="sessions.loading && sessions.live.length === 0" class="empty ws-muted">
        正在加载会话…
      </p>
      <div v-else-if="sessions.error" class="empty ws-muted">
        <p>{{ sessions.error }}</p>
        <button @click="sessions.refresh()">重试</button>
      </div>
      <p v-else-if="filtered.length === 0" class="empty ws-muted">{{ $t("app.noSessions") }}</p>
      <article
        v-for="session in filtered"
        :key="session.id"
        class="row"
        tabindex="0"
        @click="tab === 'live' ? open(session) : undefined"
        @keydown.enter="tab === 'live' ? open(session) : undefined"
      >
        <div class="row-top">
          <span class="dot" :class="{ alive: session.alive }" />
          <strong>{{ session.name }}</strong>
          <span
            v-if="presence.online[session.id]"
            class="clients"
            :title="`${presence.online[session.id]} 位观众`"
          >
            {{ presence.online[session.id] }}👁
          </span>
        </div>
        <div class="meta">
          <span>{{ session.backend }}</span>
          <span v-if="session.owner">{{ session.owner }}</span>
          <span>{{ formatBytes(session.bytes) }}</span>
          <span v-if="session.recording" class="rec">REC</span>
        </div>
        <div v-if="presence.viewers[session.id]?.length" class="viewers">
          观众：{{ presence.viewers[session.id].join("、") }}
        </div>
        <div class="actions" @keydown.enter.stop>
          <button v-if="tab === 'live'" @click.stop="rename(session)">
            {{ $t("app.rename") }}
          </button>
          <button v-if="tab === 'live'" @click.stop="ui.openShare(session.id)">
            {{ $t("app.share") }}
          </button>
          <button v-if="tab === 'live'" @click.stop="toggleRecording(session)">
            {{ session.recording ? "停止录制" : "录制" }}
          </button>
          <button
            v-if="tab === 'live' && session.recording && auth.isAdmin"
            @click.stop="ui.openReplay(session.id)"
          >
            {{ $t("app.replay") }}
          </button>
          <button v-if="tab === 'live'" class="danger" @click.stop="stop(session)">
            {{ $t("app.delete") }}
          </button>
          <button v-if="tab === 'history'" @click.stop="reopen(session)">重新打开</button>
        </div>
      </article>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  width: 264px;
  min-width: 264px;
  border-right: 1px solid var(--border);
  background: var(--bg-elevated);
}
.sidebar-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  border-bottom: 1px solid var(--border);
}
.segmented {
  display: flex;
  flex: 1;
  padding: 2px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
}
.segmented button {
  flex: 1;
  padding: 4px 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
}
.segmented button.active {
  background: var(--bg-panel);
  color: var(--text);
  box-shadow: 0 1px 4px rgb(0 0 0 / 18%);
}
.segmented span {
  margin-left: 3px;
  opacity: 0.7;
}
.new {
  width: 28px;
  height: 28px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: transparent;
  color: var(--accent);
  font-size: 16px;
}
.filter {
  margin: 8px;
  height: 30px;
  padding: 0 9px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--bg);
  color: var(--text);
  font-size: 12px;
  outline: none;
}
.filters {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 5px;
  padding: 0 8px 8px;
}
.filters select {
  min-width: 0;
  height: 26px;
  padding: 0 4px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--muted);
  font-size: 10px;
}
.filter:focus {
  border-color: var(--accent);
}
.list {
  flex: 1;
  min-height: 0;
  padding: 0 8px 8px;
}
.empty {
  padding: 24px 8px;
  font-size: 13px;
  text-align: center;
}
.row {
  padding: 9px;
  border: 1px solid transparent;
  border-radius: 8px;
  cursor: pointer;
}
.row:hover,
.row:focus-visible {
  border-color: var(--border);
  background: var(--bg-panel);
  outline: none;
}
.row-top {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}
.row-top strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--muted);
  flex: none;
}
.dot.alive {
  background: var(--success);
}
.clients {
  margin-left: auto;
  font-size: 10px;
  color: var(--muted);
}
.meta {
  display: flex;
  gap: 8px;
  margin-top: 4px;
  color: var(--muted);
  font-size: 10px;
}
.rec {
  color: var(--danger);
  font-weight: 700;
}
.viewers {
  margin-top: 3px;
  overflow: hidden;
  color: var(--accent);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 7px;
}
.actions button {
  padding: 2px 6px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: transparent;
  color: var(--muted);
  font-size: 10px;
}
.actions button:hover {
  color: var(--text);
  border-color: var(--accent);
}
.actions .danger:hover {
  color: var(--danger);
  border-color: var(--danger);
}
@media (max-width: 760px) {
  .sidebar {
    position: absolute;
    z-index: 30;
    height: 100%;
    box-shadow: 12px 0 32px rgb(0 0 0 / 28%);
  }
}
</style>
