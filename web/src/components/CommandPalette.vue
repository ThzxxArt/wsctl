<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { useWorkspaceStore } from "@/stores/workspace";
import { useSessionsStore } from "@/stores/sessions";
import { useUiStore } from "@/stores/ui";
import { usePrefsStore } from "@/stores/prefs";
import { useAuthStore } from "@/stores/auth";
import type { SessionInfo } from "@/api/types";

interface Action {
  id: string;
  label: string;
  hint?: string;
  run: () => void | Promise<void>;
}

const emit = defineEmits<{ close: [] }>();
const workspace = useWorkspaceStore();
const sessions = useSessionsStore();
const ui = useUiStore();
const prefs = usePrefsStore();
const auth = useAuthStore();

const query = ref("");
const index = ref(0);
const input = ref<HTMLInputElement | null>(null);

function close(): void {
  emit("close");
}

function openSession(session: SessionInfo): void {
  const existing = workspace.allLeaves.find((leaf) => leaf.sessionId === session.id);
  if (existing) {
    workspace.focusPane(existing.id);
  } else {
    workspace.createTab(session.id, session.name);
  }
  close();
}

async function createSession(): Promise<void> {
  const created = await sessions.create({});
  try {
    workspace.createTab(created.id, created.name);
  } catch (error) {
    await sessions.remove(created.id).catch(() => undefined);
    throw error;
  }
  close();
}

async function split(direction: "row" | "col"): Promise<void> {
  const tab = workspace.activeTab;
  if (!tab) return;
  const created = await sessions.create({});
  try {
    const pane = workspace.splitPane(tab.focus, direction, created.id, created.name);
    if (!pane) throw new Error("无法在该分屏创建终端");
  } catch (error) {
    await sessions.remove(created.id).catch(() => undefined);
    throw error;
  }
  close();
}

const actions = computed<Action[]>(() => {
  const list: Action[] = [
    { id: "new-session", label: "新建会话", hint: "Ctrl+Shift+T", run: createSession },
    {
      id: "new-tab",
      label: "新建标签",
      run: () => {
        workspace.createTab(null, "终端");
        close();
      },
    },
    { id: "split-right", label: "向右分屏", run: () => split("row") },
    { id: "split-down", label: "向下分屏", run: () => split("col") },
    {
      id: "files",
      label: "打开文件面板",
      run: () => {
        ui.filesOpen = true;
        close();
      },
    },
    {
      id: "settings",
      label: "打开设置",
      run: () => {
        ui.settingsOpen = true;
        close();
      },
    },
    {
      id: "theme",
      label: "切换界面主题",
      run: () => {
        prefs.prefs.theme = prefs.effectivePageTheme === "dark" ? "light" : "dark";
        prefs.apply();
        close();
      },
    },
    {
      id: "font-up",
      label: "放大字号",
      run: () => {
        prefs.fontSizeUp();
        close();
      },
    },
    {
      id: "font-down",
      label: "缩小字号",
      run: () => {
        prefs.fontSizeDown();
        close();
      },
    },
    {
      id: "layout-columns",
      label: "布局：三栏",
      run: () => {
        workspace.applyPreset("columns");
        close();
      },
    },
    {
      id: "layout-rows",
      label: "布局：三行",
      run: () => {
        workspace.applyPreset("rows");
        close();
      },
    },
    {
      id: "layout-2x2",
      label: "布局：2×2",
      run: () => {
        workspace.applyPreset("2x2");
        close();
      },
    },
  ];
  if (auth.isAdmin) {
    list.push({
      id: "admin",
      label: "打开管理面板",
      run: () => {
        ui.adminOpen = true;
        close();
      },
    });
  }
  const active = workspace.activeTab;
  if (active) {
    const leaf = workspace.allLeaves.find((item) => item.id === active.focus);
    const sid = leaf?.sessionId;
    if (sid) {
      list.push(
        {
          id: "share",
          label: "分享当前会话",
          run: () => {
            ui.openShare(sid);
            close();
          },
        },
        {
          id: "replay",
          label: "回放录制",
          run: () => {
            ui.openReplay(sid);
            close();
          },
        },
      );
    }
  }
  return list;
});

const results = computed(() => {
  const needle = query.value.trim().toLowerCase();
  const actionsFiltered = actions.value.filter(
    (action) =>
      !needle || action.label.toLowerCase().includes(needle) || action.id.includes(needle),
  );
  const sessionItems = sessions.live
    .filter(
      (session) =>
        !needle ||
        session.name.toLowerCase().includes(needle) ||
        session.id.toLowerCase().includes(needle),
    )
    .map((session) => ({
      id: `session:${session.id}`,
      label: session.name,
      hint: `${session.backend} · ${session.id.slice(0, 8)}`,
      run: () => openSession(session),
    }));
  return [...actionsFiltered, ...sessionItems];
});

async function runAction(item: Action): Promise<void> {
  try {
    await item.run();
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") {
    event.preventDefault();
    close();
  } else if (event.key === "ArrowDown") {
    event.preventDefault();
    index.value = Math.min(results.value.length - 1, index.value + 1);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    index.value = Math.max(0, index.value - 1);
  } else if (event.key === "Enter") {
    event.preventDefault();
    const item = results.value[index.value];
    if (item) void runAction(item);
  }
}

onMounted(async () => {
  await nextTick();
  input.value?.focus();
});
</script>

<template>
  <div class="palette-backdrop" @mousedown.self="close">
    <section class="palette" role="dialog" aria-modal="true" :aria-label="$t('app.commandPalette')">
      <input
        ref="input"
        v-model="query"
        class="palette-input"
        :placeholder="ui.quickSwitchOpen ? $t('app.quickSwitch') : $t('app.runCommand')"
        @keydown="onKeydown"
      />
      <ul class="palette-list ws-scroll" role="listbox">
        <li
          v-for="(item, itemIndex) in results"
          :key="item.id"
          :class="{ active: itemIndex === index }"
          role="option"
          :aria-selected="itemIndex === index"
          @mouseenter="index = itemIndex"
          @mousedown.prevent="runAction(item)"
        >
          <span>{{ item.label }}</span>
          <span v-if="item.hint" class="hint">{{ item.hint }}</span>
        </li>
        <li v-if="results.length === 0" class="empty ws-muted">没有匹配项</li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.palette-backdrop {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  justify-content: center;
  padding-top: 14vh;
  background: rgb(0 0 0 / 42%);
  backdrop-filter: blur(2px);
}
.palette {
  width: min(560px, 92vw);
  max-height: 62vh;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-elevated);
  box-shadow: 0 30px 90px rgb(0 0 0 / 45%);
}
.palette-input {
  width: 100%;
  height: 46px;
  padding: 0 16px;
  border: 0;
  border-bottom: 1px solid var(--border);
  background: transparent;
  color: var(--text);
  font-size: 14px;
  outline: none;
}
.palette-list {
  max-height: calc(62vh - 46px);
  margin: 0;
  padding: 6px;
  list-style: none;
}
.palette-list li {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 12px;
  border-radius: 7px;
  font-size: 13px;
  cursor: pointer;
}
.palette-list li.active {
  background: color-mix(in srgb, var(--accent) 18%, transparent);
}
.hint {
  color: var(--muted);
  font-size: 11px;
}
.empty {
  justify-content: center;
  padding: 24px;
}
</style>
