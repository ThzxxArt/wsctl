<script setup lang="ts">
import { onMounted, ref } from "vue";
import { NAlert, NButton, NInput, NInputNumber, NModal, NSelect, NSpace, NTag } from "naive-ui";
import { shares } from "@/api";
import type { ShareRecord } from "@/api/types";
import { useUiStore } from "@/stores/ui";
import { useI18n } from "vue-i18n";
import { formatDateTime } from "@/utils/format";

const props = defineProps<{ session: string }>();
const emit = defineEmits<{ close: [] }>();
const ui = useUiStore();
const { t, locale } = useI18n();

const rows = ref<ShareRecord[]>([]);
const scope = ref<"read" | "write" | "control">("read");
const label = ref("");
const ttl = ref<number | null>(24);
const created = ref<{ url: string; absolute_url: string; qr_svg: string } | null>(null);
const loading = ref(false);
const creating = ref(false);

const scopeOptions = [
  { label: t("share.read"), value: "read" },
  { label: t("share.write"), value: "write" },
  { label: t("share.control"), value: "control" },
];

async function load(): Promise<void> {
  loading.value = true;
  try {
    rows.value = (await shares.list(props.session)).shares;
  } catch (error) {
    ui.notify((error as Error).message, "error");
  } finally {
    loading.value = false;
  }
}

async function create(): Promise<void> {
  if (creating.value) return;
  creating.value = true;
  try {
    const result = await shares.create(
      props.session,
      scope.value,
      label.value,
      ttl.value ? ttl.value * 3600 : null,
    );
    created.value = {
      url: result.url,
      absolute_url: result.absolute_url || `${location.origin}${result.url}`,
      qr_svg: result.qr_svg,
    };
    label.value = "";
    await load();
  } catch (error) {
    ui.notify((error as Error).message, "error");
  } finally {
    creating.value = false;
  }
}

async function revoke(row: ShareRecord): Promise<void> {
  if (!window.confirm(`撤销分享链接 #${row.id}？已打开的观众会立即失去访问。`)) return;
  try {
    await shares.revoke(row.id);
    if (created.value) created.value = null;
    await load();
    ui.notify("分享已撤销", "success");
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

async function copy(): Promise<void> {
  if (!created.value) return;
  try {
    await navigator.clipboard.writeText(created.value.absolute_url);
    ui.notify(t("share.copied"), "success");
  } catch (error) {
    ui.notify(`复制失败：${(error as Error).message}`, "error");
  }
}

function formatTime(value: number | null): string {
  return value ? formatDateTime(value, locale.value) : "永久";
}

onMounted(load);
</script>

<template>
  <NModal
    :show="true"
    preset="card"
    style="width: min(720px, 94vw); max-height: 88vh"
    :title="t('share.title')"
    @close="emit('close')"
  >
    <div class="share-body">
      <NSpace align="end" style="margin-bottom: 12px">
        <NSelect v-model:value="scope" :options="scopeOptions" style="width: 130px" />
        <NInput v-model:value="label" :placeholder="t('share.label')" style="width: 180px" />
        <NInputNumber
          v-model:value="ttl"
          :min="1"
          :max="8760"
          :placeholder="t('share.ttl')"
          style="width: 150px"
        />
        <NButton type="primary" :loading="creating" @click="create">{{
          t("share.create")
        }}</NButton>
      </NSpace>

      <NAlert v-if="created" type="success" style="margin-bottom: 12px">
        <div class="created">
          <div>
            <p>链接只显示一次，请立即复制：</p>
            <code>{{ created.absolute_url }}</code>
            <NSpace style="margin-top: 8px">
              <NButton size="small" @click="copy">{{ t("app.copy") }}</NButton>
              <NButton size="small" tag="a" :href="created.url" target="_blank">打开</NButton>
            </NSpace>
          </div>
          <div v-if="created.qr_svg" class="qr" v-html="created.qr_svg" />
        </div>
      </NAlert>

      <h4>{{ t("share.links") }}</h4>
      <p v-if="rows.length === 0" class="ws-muted">{{ t("share.none") }}</p>
      <table v-else class="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>{{ t("share.scope") }}</th>
            <th>{{ t("share.label") }}</th>
            <th>创建</th>
            <th>过期</th>
            <th>最后使用</th>
            <th />
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.id">
            <td>{{ row.id }}</td>
            <td>
              <NTag
                size="small"
                :type="
                  row.scope === 'read' ? 'default' : row.scope === 'write' ? 'info' : 'warning'
                "
                >{{ row.scope }}</NTag
              >
            </td>
            <td>{{ row.label || "-" }}</td>
            <td>{{ formatTime(row.created_at) }}</td>
            <td>{{ formatTime(row.expires_at) }}</td>
            <td>{{ formatTime(row.last_used) }}</td>
            <td>
              <button class="danger" @click="revoke(row)">{{ t("share.revoke") }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <template #footer>
      <NButton @click="emit('close')">关闭</NButton>
    </template>
  </NModal>
</template>

<style scoped>
.share-body {
  max-height: calc(88vh - 140px);
  overflow: auto;
}
.created {
  display: flex;
  gap: 18px;
  align-items: center;
  justify-content: space-between;
}
.created p {
  margin: 0 0 6px;
  font-size: 12px;
}
.created code {
  display: block;
  word-break: break-all;
  font-family: var(--term-font-family);
  font-size: 12px;
}
.qr {
  width: 150px;
  flex: none;
  padding: 6px;
  border-radius: 8px;
  background: white;
}
.qr :deep(svg) {
  width: 100%;
  height: auto;
}
.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.table th,
.table td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--border);
  text-align: left;
}
.table th {
  color: var(--muted);
  font-weight: 500;
}
.danger {
  padding: 2px 7px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: transparent;
  color: var(--muted);
  font-size: 11px;
}
.danger:hover {
  border-color: var(--danger);
  color: var(--danger);
}
</style>
