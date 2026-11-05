<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { NButton, NEmpty, NInput, NModal, NProgress, NSpace } from "naive-ui";
import { files } from "@/api";
import type { FileEntry } from "@/api/types";
import { useUiStore } from "@/stores/ui";
import { useI18n } from "vue-i18n";
import { formatDateTime } from "@/utils/format";
import EditorDialog from "./EditorDialog.vue";

const emit = defineEmits<{ close: [] }>();
const ui = useUiStore();
const { t, locale } = useI18n();

const path = ref("");
const root = ref("");
const entries = ref<FileEntry[]>([]);
const loading = ref(false);
const uploadRatio = ref<number | null>(null);
const editor = ref<{ path: string; content: string; mtime: number | null } | null>(null);
const image = ref<string | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const listing = ref<HTMLElement | null>(null);
const filter = ref("");
const pageInfo = ref({ total: 0, offset: 0, limit: 0, truncated: false });

interface UploadJob {
  name: string;
  ratio: number;
  status: "pending" | "uploading" | "done" | "error";
  error?: string;
  controller?: AbortController;
}

const queue = ref<UploadJob[]>([]);
const ROW_HEIGHT = 44;
const scrollTop = ref(0);
const viewportHeight = ref(480);

const breadcrumbs = computed(() => {
  const parts = path.value.split("/").filter(Boolean);
  const rows = [{ label: "/", path: "" }];
  let current = "";
  for (const part of parts) {
    current = current ? `${current}/${part}` : part;
    rows.push({ label: part, path: current });
  }
  return rows;
});

const filtered = computed(() => {
  const needle = filter.value.trim().toLowerCase();
  if (!needle) return entries.value;
  return entries.value.filter((entry) => entry.name.toLowerCase().includes(needle));
});

const startIndex = computed(() => Math.max(0, Math.floor(scrollTop.value / ROW_HEIGHT) - 4));
const visibleCount = computed(() => Math.ceil(viewportHeight.value / ROW_HEIGHT) + 8);
const endIndex = computed(() =>
  Math.min(filtered.value.length, startIndex.value + visibleCount.value),
);
const visible = computed(() => filtered.value.slice(startIndex.value, endIndex.value));
const padTop = computed(() => startIndex.value * ROW_HEIGHT);
const padBottom = computed(() => Math.max(0, filtered.value.length - endIndex.value) * ROW_HEIGHT);

function onScroll(event: Event): void {
  const target = event.target as HTMLElement;
  scrollTop.value = target.scrollTop;
  viewportHeight.value = target.clientHeight || viewportHeight.value;
}

function join(base: string, name: string): string {
  return base ? `${base}/${name}` : name;
}

async function load(target = path.value, append = false): Promise<void> {
  loading.value = true;
  try {
    const page = await files.list(target, 2000, append ? entries.value.length : 0);
    path.value = page.path;
    root.value = page.root;
    entries.value = append ? [...entries.value, ...page.entries] : page.entries;
    pageInfo.value = {
      total: page.total,
      offset: page.offset,
      limit: page.limit,
      truncated: page.truncated,
    };
    if (!append) {
      scrollTop.value = 0;
      if (listing.value) listing.value.scrollTop = 0;
    }
  } catch (error) {
    ui.notify((error as Error).message, "error");
  } finally {
    loading.value = false;
  }
}

function loadMore(): void {
  void load(path.value, true);
}

function enter(entry: FileEntry): void {
  if (entry.type === "dir") {
    void load(join(path.value, entry.name));
  }
}

async function openText(entry: FileEntry): Promise<void> {
  try {
    const result = await files.read(join(path.value, entry.name));
    editor.value = {
      path: join(path.value, entry.name),
      content: result.content,
      mtime: result.mtime,
    };
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

async function save(content: string): Promise<void> {
  if (!editor.value) return;
  try {
    await files.write(editor.value.path, content, editor.value.mtime);
    ui.notify("已保存", "success");
    editor.value = null;
    await load();
  } catch (error) {
    // 409 means another tab/process changed the file; keep the editor open and
    // let the author decide instead of silently overwriting.
    ui.notify((error as Error).message, "error");
  }
}

async function makeDir(): Promise<void> {
  const name = window.prompt("新文件夹名称");
  if (!name) return;
  try {
    await files.mkdir(path.value, name);
    await load();
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

async function rename(entry: FileEntry): Promise<void> {
  const name = window.prompt("新名称", entry.name);
  if (!name || name === entry.name) return;
  try {
    await files.rename(join(path.value, entry.name), name);
    await load();
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

async function remove(entry: FileEntry): Promise<void> {
  if (!window.confirm(`删除 ${entry.name}？目录必须为空。`)) return;
  try {
    await files.remove(path.value, entry.name);
    await load();
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

function download(entry: FileEntry): void {
  // A direct same-origin link streams the file and lets the browser show its
  // own progress; fetch+blob buffered a multi-GB file in the tab first.
  const anchor = document.createElement("a");
  anchor.href = files.url(join(path.value, entry.name));
  anchor.download = entry.name;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

function previewImage(entry: FileEntry): void {
  image.value = files.imageUrl(join(path.value, entry.name));
}

function pickFile(): void {
  fileInput.value?.click();
}

async function uploadFiles(list: File[], target = path.value): Promise<void> {
  if (list.length === 0) return;
  const jobs: UploadJob[] = list.map((file) => ({
    name: file.name,
    ratio: 0,
    status: "pending",
  }));
  queue.value = [...queue.value, ...jobs];
  let failed = 0;
  for (let index = 0; index < list.length; index += 1) {
    const file = list[index];
    const job = jobs[index];
    job.status = "uploading";
    job.controller = new AbortController();
    uploadRatio.value = 0;
    const send = (overwrite: boolean) =>
      files.upload(
        target,
        file,
        overwrite,
        (ratio) => {
          job.ratio = ratio;
          uploadRatio.value = ratio;
        },
        job.controller?.signal,
      );
    try {
      try {
        await send(false);
      } catch (error) {
        if (
          (error as { status?: number }).status === 409 &&
          window.confirm(`${file.name} 已存在，覆盖它？`)
        ) {
          await send(true);
        } else {
          throw error;
        }
      }
      job.status = "done";
      job.ratio = 1;
    } catch (error) {
      job.status = "error";
      job.error = (error as Error).message;
      failed += 1;
    }
  }
  uploadRatio.value = null;
  if (failed === 0) {
    ui.notify(`已上传 ${list.length} 个文件`, "success");
  } else {
    ui.notify(`${list.length - failed} 个成功，${failed} 个失败`, "warning");
  }
  if (target === path.value) await load();
}

async function onUpload(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files ?? []);
  input.value = "";
  await uploadFiles(files);
}

async function onDropFiles(event: DragEvent, target = path.value): Promise<void> {
  const dropped = Array.from(event.dataTransfer?.files ?? []);
  if (dropped.length > 0) await uploadFiles(dropped, target);
}

async function onDropTo(entry: FileEntry, event: DragEvent): Promise<void> {
  if (entry.type !== "dir") return;
  await onDropFiles(event, join(path.value, entry.name));
}

function formatSize(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 ** 3) return `${(value / 1024 / 1024).toFixed(1)} MiB`;
  return `${(value / 1024 ** 3).toFixed(2)} GiB`;
}

function formatTime(value: number): string {
  return formatDateTime(value, locale.value);
}

function isImage(name: string): boolean {
  return /\.(png|jpe?g|gif|webp|bmp)$/i.test(name);
}

onMounted(() => load(""));

onBeforeUnmount(() => {
  // Closing the panel (or logging out) must not leave uploads writing files
  // behind it.
  for (const job of queue.value) job.controller?.abort();
});

watch(filter, () => {
  // Filtering shortens the virtual list; keeping the old scroll offset made
  // the window start past the last match and rendered an empty panel.
  scrollTop.value = 0;
  if (listing.value) listing.value.scrollTop = 0;
});
</script>

<template>
  <NModal
    :show="true"
    preset="card"
    style="width: min(1000px, 96vw); height: 84vh; display: flex; flex-direction: column"
    :title="t('app.files')"
    @close="emit('close')"
  >
    <div class="files-body">
      <div class="toolbar">
        <nav class="crumbs" aria-label="breadcrumb">
          <button v-for="crumb in breadcrumbs" :key="crumb.path" @click="load(crumb.path)">
            {{ crumb.label || "/" }}
          </button>
        </nav>
        <NSpace>
          <NInput
            v-model:value="filter"
            size="small"
            placeholder="过滤当前目录"
            style="width: 170px"
          />
          <NButton size="small" @click="load()">{{ t("app.refresh") }}</NButton>
          <NButton size="small" @click="makeDir">{{ t("files.newFolder") }}</NButton>
          <NButton size="small" type="primary" @click="pickFile">{{ t("files.upload") }}</NButton>
          <input ref="fileInput" type="file" multiple hidden @change="onUpload" />
        </NSpace>
      </div>
      <NProgress
        v-if="uploadRatio !== null"
        :percentage="Math.round(uploadRatio * 100)"
        style="margin-bottom: 8px"
      />
      <div v-if="queue.length" class="queue">
        <div
          v-for="(job, index) in queue"
          :key="`${job.name}-${index}`"
          class="queue-row"
          :class="job.status"
        >
          <span class="queue-name">{{ job.name }}</span>
          <span class="queue-status">
            {{
              job.status === "done"
                ? "完成"
                : job.status === "error"
                  ? job.error
                  : `${Math.round(job.ratio * 100)}%`
            }}
          </span>
          <button
            v-if="job.status === 'uploading'"
            class="queue-cancel"
            title="取消上传"
            @click="job.controller?.abort()"
          >
            ×
          </button>
        </div>
        <button class="queue-clear" @click="queue = []">清空</button>
      </div>
      <p class="root ws-muted">{{ root }}</p>
      <div
        ref="listing"
        class="listing ws-scroll"
        @scroll="onScroll"
        @dragover.prevent
        @drop.prevent="onDropFiles($event)"
      >
        <div v-if="pageInfo.truncated" class="page-more">
          <span class="ws-muted">已显示 {{ entries.length }} / {{ pageInfo.total }}</span>
          <button :disabled="loading" @click="loadMore">加载更多</button>
        </div>
        <NEmpty v-if="filtered.length === 0 && !loading" :description="t('files.empty')" />
        <template v-else>
          <div class="thead">
            <span>{{ t("files.name") }}</span
            ><span>{{ t("files.size") }}</span
            ><span>{{ t("files.modified") }}</span
            ><span>操作</span>
          </div>
          <div :style="{ paddingTop: `${padTop}px`, paddingBottom: `${padBottom}px` }">
            <div
              v-for="entry in visible"
              :key="entry.name"
              class="frow"
              :class="{ 'drop-target': entry.type === 'dir' }"
              @dragover.prevent
              @drop.prevent.stop="onDropTo(entry, $event)"
            >
              <button class="name" @click="enter(entry)">
                <span class="icon">{{ entry.type === "dir" ? "📁" : "📄" }}</span>
                {{ entry.name }}
              </button>
              <span>{{ entry.type === "dir" ? "-" : formatSize(entry.size) }}</span>
              <span>{{ formatTime(entry.mtime) }}</span>
              <div class="actions">
                <button v-if="entry.type === 'file'" @click="download(entry)">
                  {{ t("app.download") }}
                </button>
                <button
                  v-if="entry.type === 'file' && isImage(entry.name)"
                  @click="previewImage(entry)"
                >
                  预览
                </button>
                <button v-if="entry.type === 'file'" @click="openText(entry)">编辑</button>
                <button @click="rename(entry)">{{ t("app.rename") }}</button>
                <button class="danger" @click="remove(entry)">{{ t("app.delete") }}</button>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
    <EditorDialog
      v-if="editor"
      :path="editor.path"
      :content="editor.content"
      @close="editor = null"
      @save="save"
    />
    <NModal :show="!!image" preset="card" style="width: min(90vw, 1000px)" @close="image = null">
      <img v-if="image" :src="image" class="preview" alt="图片预览" />
    </NModal>
    <template #footer>
      <NButton @click="emit('close')">关闭</NButton>
    </template>
  </NModal>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}
.crumbs {
  display: flex;
  gap: 2px;
  overflow-x: auto;
  min-width: 0;
}
.crumbs button {
  padding: 3px 7px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
}
.crumbs button:hover {
  background: var(--bg-panel);
  color: var(--text);
}
.root {
  margin: 0 0 8px;
  font-size: 11px;
}
.page-more {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px;
  font-size: 12px;
}
.listing {
  flex: 1;
  min-height: 0;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.files-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  height: calc(84vh - 170px);
  overflow: hidden;
}
.thead,
.frow {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 88px 164px 280px;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  font-size: 12px;
}
.thead {
  position: sticky;
  top: 0;
  z-index: 1;
  height: 34px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--muted);
  font-weight: 500;
}
.frow {
  height: 44px;
  border-bottom: 1px solid var(--border);
}
.frow.drop-target:hover {
  background: color-mix(in srgb, var(--accent) 8%, transparent);
  outline: 1px dashed var(--accent);
}
.queue {
  position: relative;
  max-height: 92px;
  margin-bottom: 8px;
  padding: 6px 8px;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-panel);
  font-size: 11px;
}
.queue-row {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 2px 0;
  color: var(--muted);
}
.queue-row.done {
  color: var(--success);
}
.queue-row.error {
  color: var(--danger);
}
.queue-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.queue-clear {
  position: absolute;
  top: 4px;
  right: 6px;
  border: 0;
  background: transparent;
  color: var(--muted);
  font-size: 10px;
}
.queue-cancel {
  border: 0;
  background: transparent;
  color: var(--danger);
  font-size: 11px;
}
.name {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 7px;
  overflow: hidden;
  border: 0;
  background: transparent;
  color: var(--text);
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.actions button {
  padding: 2px 7px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: transparent;
  color: var(--muted);
  font-size: 11px;
}
.actions button:hover {
  border-color: var(--accent);
  color: var(--text);
}
.actions .danger:hover {
  border-color: var(--danger);
  color: var(--danger);
}
.preview {
  display: block;
  max-width: 100%;
  max-height: 76vh;
  margin: auto;
}
@media (max-width: 760px) {
  .thead,
  .frow {
    grid-template-columns: minmax(0, 1fr) 52px 180px;
    gap: 4px;
    padding: 0 6px;
    font-size: 10px;
  }
  .thead span:nth-child(3),
  .frow > span:nth-child(3) {
    display: none;
  }
  .frow {
    height: 44px;
  }
  .actions button {
    padding: 1px 4px;
    font-size: 9px;
  }
  .toolbar {
    flex-wrap: wrap;
  }
}
</style>
