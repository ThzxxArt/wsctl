<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { NButton, NModal, NSpace, NTag } from "naive-ui";
import { recordings } from "@/api";
import type { RecordingInfo } from "@/api/types";
import { useUiStore } from "@/stores/ui";
import { useI18n } from "vue-i18n";
import { confirmDialog } from "@/dialogs";

const props = defineProps<{ session: string }>();
const emit = defineEmits<{ close: [] }>();
const ui = useUiStore();
const { t } = useI18n();

const rows = ref<RecordingInfo[]>([]);
const selected = ref<string | null>(null);
const host = ref<HTMLDivElement | null>(null);
let player: { dispose?: () => void } | null = null;

const ordered = computed(() => {
  const match = rows.value.filter((row) => row.name.startsWith(props.session));
  const rest = rows.value.filter((row) => !row.name.startsWith(props.session));
  return [...match, ...rest];
});

async function load(): Promise<void> {
  try {
    rows.value = await recordings.list();
    const preferred = ordered.value[0];
    if (preferred) await play(preferred.name);
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

async function play(name: string): Promise<void> {
  selected.value = name;
  await nextTick();
  player?.dispose?.();
  player = null;
  if (!host.value) return;
  host.value.replaceChildren();
  try {
    const module = await import("asciinema-player");
    await import("asciinema-player/dist/bundle/asciinema-player.css");
    player = module.create(recordings.url(name), host.value, {
      fit: "width",
      autoPlay: false,
      controls: true,
      speed: 1,
    });
  } catch (error) {
    ui.notify(`回放器加载失败：${(error as Error).message}`, "error");
  }
}

async function remove(name: string): Promise<void> {
  const confirmed = await confirmDialog({
    title: "删除录制",
    message: `删除录制 ${name}？`,
    confirmText: "删除",
    danger: true,
  });
  if (!confirmed) return;
  try {
    await recordings.remove(name);
    if (selected.value === name) {
      selected.value = null;
      player?.dispose?.();
      player = null;
    }
    await load();
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

function formatSize(value: number): string {
  return value < 1024 * 1024
    ? `${(value / 1024).toFixed(1)} KiB`
    : `${(value / 1024 / 1024).toFixed(1)} MiB`;
}

onMounted(load);
onBeforeUnmount(() => player?.dispose?.());
</script>

<template>
  <NModal
    :show="true"
    preset="card"
    style="width: min(1080px, 96vw); max-height: 92vh"
    :title="t('app.replay')"
    @close="emit('close')"
  >
    <div class="layout">
      <aside class="list ws-scroll">
        <p v-if="ordered.length === 0" class="ws-muted">
          还没有录制。可在会话分享/菜单中开始录制。
        </p>
        <button
          v-for="row in ordered"
          :key="row.name"
          class="item"
          :class="{ active: selected === row.name }"
          @click="play(row.name)"
        >
          <span class="name">{{ row.name }}</span>
          <span class="meta">{{ formatSize(row.size) }}</span>
          <NTag v-if="row.name.startsWith(session)" size="tiny" type="success">本会话</NTag>
        </button>
      </aside>
      <section class="player">
        <div ref="host" class="player-host" />
        <NSpace v-if="selected" style="margin-top: 8px" justify="end">
          <NButton size="small" tag="a" :href="recordings.url(selected)" download
            >下载 cast</NButton
          >
          <NButton size="small" type="error" @click="remove(selected)">删除</NButton>
        </NSpace>
      </section>
    </div>
    <template #footer>
      <NButton @click="emit('close')">关闭</NButton>
    </template>
  </NModal>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: 14px;
}
.list {
  max-height: 66vh;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  padding: 8px 10px;
  border: 0;
  border-bottom: 1px solid var(--border);
  background: transparent;
  color: var(--text);
  text-align: left;
  font-size: 12px;
}
.item.active {
  background: color-mix(in srgb, var(--accent) 14%, transparent);
}
.item .name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--term-font-family);
}
.item .meta {
  color: var(--muted);
  font-size: 10px;
}
.player-host {
  min-height: 320px;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  background: #000;
}
@media (max-width: 760px) {
  .layout {
    grid-template-columns: 1fr;
  }
  .list {
    max-height: 180px;
  }
}
</style>
