<script setup lang="ts">
import { nextTick, ref } from "vue";
import { useWorkspaceStore, leavesOf } from "@/stores/workspace";
import { useUiStore } from "@/stores/ui";

const workspace = useWorkspaceStore();
const ui = useUiStore();
const editing = ref<string | null>(null);
const draft = ref("");
const inputRef = ref<HTMLInputElement | null>(null);
const dragIndex = ref<number | null>(null);

function paneCount(tabId: string): number {
  const tab = workspace.tabs.find((item) => item.id === tabId);
  return tab ? leavesOf(tab.root).length : 0;
}

function beginRename(tabId: string, name: string): void {
  editing.value = tabId;
  draft.value = name;
  void nextTick(() => inputRef.value?.select());
}

function commitRename(): void {
  if (editing.value) workspace.renameTab(editing.value, draft.value);
  editing.value = null;
}

function onDragStart(index: number): void {
  dragIndex.value = index;
}

function onDrop(index: number): void {
  if (dragIndex.value !== null) workspace.moveTab(dragIndex.value, index);
  dragIndex.value = null;
}

function newTab(): void {
  try {
    workspace.createTab(null, undefined);
  } catch (error) {
    ui.notify((error as Error).message, "warning");
  }
}
</script>

<template>
  <div class="tabbar">
    <div class="tab-scroll" role="tablist" :aria-label="$t('app.sessions')">
      <div
        v-for="(tab, index) in workspace.tabs"
        :key="tab.id"
        class="tab"
        :class="{ active: tab.id === workspace.activeTabId }"
        draggable="true"
        role="tab"
        :aria-selected="tab.id === workspace.activeTabId"
        tabindex="0"
        @dragstart="onDragStart(index)"
        @dragover.prevent
        @drop.prevent="onDrop(index)"
        @click="workspace.activateTab(tab.id)"
        @dblclick="beginRename(tab.id, tab.name)"
        @keydown.enter="workspace.activateTab(tab.id)"
      >
        <input
          v-if="editing === tab.id"
          ref="inputRef"
          v-model="draft"
          class="rename"
          @click.stop
          @blur="commitRename"
          @keydown.enter.prevent="commitRename"
          @keydown.esc.prevent="editing = null"
        />
        <template v-else>
          <span class="name">{{ tab.name }}</span>
          <span class="badge">{{ paneCount(tab.id) }}</span>
        </template>
        <button
          class="dup"
          :title="$t('app.duplicateTab')"
          :aria-label="$t('app.duplicateTab')"
          @click.stop="workspace.duplicateTab(tab.id)"
        >
          ⧉
        </button>
        <button
          class="close"
          :title="$t('app.closeTab')"
          :aria-label="$t('app.closeTab')"
          @click.stop="workspace.closeTab(tab.id)"
        >
          ×
        </button>
      </div>
    </div>
    <button
      class="new-tab"
      :title="$t('app.newTab')"
      :aria-label="$t('app.newTab')"
      @click="newTab"
    >
      +
    </button>
  </div>
</template>

<style scoped>
.tabbar {
  display: flex;
  align-items: stretch;
  min-width: 0;
  overflow: hidden;
}
.tab-scroll {
  display: flex;
  align-items: stretch;
  gap: 2px;
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
}
.tab-scroll::-webkit-scrollbar {
  display: none;
}
.tab {
  display: flex;
  align-items: center;
  gap: 6px;
  max-width: 220px;
  min-width: 110px;
  padding: 0 8px 0 12px;
  border: 1px solid transparent;
  border-bottom: 0;
  border-radius: 8px 8px 0 0;
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
  user-select: none;
}
.tab:hover {
  background: color-mix(in srgb, var(--accent) 8%, transparent);
}
.tab.active {
  border-color: var(--border);
  background: var(--bg-panel);
  color: var(--text);
}
.name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.badge {
  min-width: 17px;
  padding: 0 5px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--accent) 18%, transparent);
  font-size: 10px;
  text-align: center;
}
.close {
  width: 20px;
  height: 20px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--muted);
  line-height: 1;
}
.dup {
  width: 20px;
  height: 20px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--muted);
  font-size: 11px;
  line-height: 1;
}
.dup:hover,
.close:hover {
  background: color-mix(in srgb, var(--accent) 16%, transparent);
  color: var(--text);
}
.rename {
  width: 130px;
  height: 22px;
  padding: 0 6px;
  border: 1px solid var(--accent);
  border-radius: 5px;
  background: var(--bg);
  font-size: 12px;
}
.new-tab {
  align-self: center;
  width: 28px;
  height: 28px;
  border: 1px dashed var(--border);
  border-radius: 7px;
  background: transparent;
  color: var(--muted);
  font-size: 18px;
  line-height: 1;
}
.new-tab:hover {
  border-color: var(--accent);
  color: var(--accent);
}
</style>
