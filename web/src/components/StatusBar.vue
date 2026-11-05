<script setup lang="ts">
import { computed } from "vue";
import { useWorkspaceStore, findLeaf } from "@/stores/workspace";
import { useSessionsStore } from "@/stores/sessions";
import { useTerminalStore } from "@/stores/terminal";
import { usePrefsStore } from "@/stores/prefs";
import pkg from "../../package.json";

const version = pkg.version;

const workspace = useWorkspaceStore();
const sessions = useSessionsStore();
const terminal = useTerminalStore();
const prefs = usePrefsStore();

const focused = computed(() => {
  const tab = workspace.activeTab;
  if (!tab) return null;
  return findLeaf(tab.root, tab.focus);
});
const meta = computed(() =>
  focused.value?.sessionId ? terminal.meta[focused.value.sessionId] : null,
);

const stateLabel = computed(() => {
  const state = meta.value?.state ?? "idle";
  return {
    idle: "空闲",
    connecting: "连接中",
    connected: "已连接",
    resyncing: "同步中",
    disconnected: "已断开",
    failed: "连接失败",
    exited: "已结束",
  }[state];
});
</script>

<template>
  <footer class="statusbar">
    <div class="left">
      <span class="ws-status-dot" :class="meta?.state ?? 'idle'" />
      <span>{{ stateLabel }}</span>
      <span v-if="meta" class="sep">{{ meta.cols }}×{{ meta.rows }}</span>
      <span v-if="meta?.cwd" class="cwd" :title="meta.cwd">{{ meta.cwd }}</span>
      <span v-if="meta?.command" class="cmd" :title="meta.command">{{ meta.command }}</span>
      <span v-if="meta?.durationMs !== undefined && meta?.durationMs !== null" class="sep">
        {{ meta.durationMs }} ms
      </span>
      <span
        v-if="meta?.lastExitCode !== undefined && meta?.lastExitCode !== null"
        class="exit"
        :class="{ bad: meta.lastExitCode !== 0 }"
      >
        命令 exit {{ meta.lastExitCode }}
      </span>
    </div>
    <div class="right">
      <span>{{ sessions.live.length }} 会话</span>
      <span class="sep">{{ prefs.prefs.fontSize }}px</span>
      <span class="sep">WsCTL {{ version }}</span>
    </div>
  </footer>
</template>

<style scoped>
.statusbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  height: 24px;
  padding: 0 10px;
  border-top: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--muted);
  font-size: 11px;
  user-select: none;
}
.left,
.right {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.cwd,
.cmd {
  overflow: hidden;
  max-width: 260px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cwd {
  color: var(--text);
  opacity: 0.8;
}
.cmd {
  font-family: var(--term-font-family);
}
.sep::before {
  content: "·";
  margin-right: 8px;
  opacity: 0.5;
}
.exit {
  padding: 0 5px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--success) 16%, transparent);
  color: var(--success);
}
.exit.bad {
  background: color-mix(in srgb, var(--danger) 16%, transparent);
  color: var(--danger);
}
</style>
