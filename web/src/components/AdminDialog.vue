<script setup lang="ts">
import { computed, ref } from "vue";
import { useQuery } from "@tanstack/vue-query";
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NCheckboxGroup,
  NInput,
  NModal,
  NSelect,
  NSpace,
  NSwitch,
  NTabPane,
  NTabs,
  NTag,
} from "naive-ui";
import { audit, keys, overview, recordings, users } from "@/api";
import type { ApiKeyRecord, AuditRow, Overview, RecordingInfo, User } from "@/api/types";
import { useUiStore } from "@/stores/ui";
import { useAuthStore } from "@/stores/auth";
import { useI18n } from "vue-i18n";
import { formatDateTime } from "@/utils/format";

const emit = defineEmits<{ close: [] }>();
const ui = useUiStore();
const auth = useAuthStore();
const { t, locale } = useI18n();

const tab = ref("overview");
const verifyResult = ref<string>("");
const newToken = ref("");
const totpSetup = ref<{ username: string; secret: string; qr: string } | null>(null);
const totpCode = ref("");
const newUsername = ref("");
const newPassword = ref("");
const newRole = ref("user");
const keyName = ref("");
const keyScopes = ref<string[]>([]);
const auditFilter = ref("");

const overviewQuery = useQuery({
  queryKey: ["admin", "overview"],
  queryFn: () => overview.get(),
});
const usersQuery = useQuery({
  queryKey: ["admin", "users"],
  queryFn: () => users.list(),
});
const auditQuery = useQuery({
  queryKey: ["admin", "audit", auditFilter],
  queryFn: () => audit.list(200, 0, auditFilter.value || undefined),
});
const keysQuery = useQuery({
  queryKey: ["admin", "keys"],
  queryFn: () => keys.list(),
});
const recordingsQuery = useQuery({
  queryKey: ["admin", "recordings"],
  queryFn: () => recordings.list(),
});

const info = computed<Overview | null>(() => overviewQuery.data.value ?? null);
const userRows = computed<User[]>(() => usersQuery.data.value ?? []);
const auditRows = computed<AuditRow[]>(() => auditQuery.data.value ?? []);
const keyRows = computed<ApiKeyRecord[]>(() => keysQuery.data.value?.keys ?? []);
const recordingRows = computed<RecordingInfo[]>(() => recordingsQuery.data.value ?? []);
const error = computed(() => {
  for (const query of [overviewQuery, usersQuery, auditQuery, keysQuery, recordingsQuery]) {
    if (query.error.value) {
      return (query.error.value as Error).message ?? String(query.error.value);
    }
  }
  return "";
});

const roleOptions = [
  { label: "普通用户", value: "user" },
  { label: "管理员", value: "admin" },
];

const loadOverview = () => overviewQuery.refetch();
const loadUsers = () => usersQuery.refetch();
const loadAudit = () => auditQuery.refetch();
const loadKeys = () => keysQuery.refetch();
const loadRecordings = () => recordingsQuery.refetch();

async function createUser(): Promise<void> {
  if (!newUsername.value || !newPassword.value) return;
  try {
    await users.create(newUsername.value, newPassword.value, newRole.value);
    newUsername.value = "";
    newPassword.value = "";
    await loadUsers();
    ui.notify("用户已创建", "success");
  } catch (err) {
    ui.notify((err as Error).message, "error");
  }
}

async function resetPassword(user: User): Promise<void> {
  const password = window.prompt(`为 ${user.username} 设置新密码（至少 8 位）`);
  if (!password) return;
  try {
    await users.update(user.username, { password });
    ui.notify("密码已更新；旧登录与旧会话将被终止", "success");
  } catch (err) {
    ui.notify((err as Error).message, "error");
  }
}

async function toggleRole(user: User): Promise<void> {
  const role = user.role === "admin" ? "user" : "admin";
  try {
    await users.update(user.username, { role });
    await loadUsers();
  } catch (err) {
    ui.notify((err as Error).message, "error");
  }
}

async function toggleDisabled(user: User): Promise<void> {
  try {
    await users.update(user.username, { disabled: !user.disabled });
    await loadUsers();
  } catch (err) {
    ui.notify((err as Error).message, "error");
  }
}

async function removeUser(user: User): Promise<void> {
  if (!window.confirm(`删除用户 ${user.username}？该用户的所有会话会结束。`)) return;
  try {
    await users.remove(user.username);
    await loadUsers();
  } catch (err) {
    ui.notify((err as Error).message, "error");
  }
}

async function setupTotp(user: User): Promise<void> {
  try {
    if (user.totp) {
      if (!window.confirm(`关闭 ${user.username} 的两步验证？`)) return;
      await users.disableTotp(user.username);
      await loadUsers();
      return;
    }
    const result = await users.enableTotp(user.username);
    totpSetup.value = { username: user.username, secret: result.secret, qr: result.qr_svg };
    totpCode.value = "";
  } catch (err) {
    ui.notify((err as Error).message, "error");
  }
}

async function confirmTotp(): Promise<void> {
  if (!totpSetup.value) return;
  const code = totpCode.value.trim();
  if (!/^\d{6}$/.test(code)) {
    ui.notify("请输入 6 位验证码", "warning");
    return;
  }
  try {
    await users.confirmTotp(totpSetup.value.username, code);
    totpSetup.value = null;
    totpCode.value = "";
    await loadUsers();
    ui.notify("两步验证已启用", "success");
  } catch (err) {
    // Keep the same secret/QR visible so a typo can be retried; regenerating
    // it would silently invalidate what the user just scanned.
    ui.notify((err as Error).message, "error");
  }
}

async function verify(): Promise<void> {
  try {
    const result = await audit.verify();
    verifyResult.value = result.ok
      ? `审计链完整：${result.checked} 条${result.truncated ? "（已按保留策略截断头部）" : ""}`
      : `审计链在 #${result.broken_at} 处损坏`;
  } catch (err) {
    verifyResult.value = (err as Error).message;
  }
}

async function createKey(): Promise<void> {
  if (!keyName.value) return;
  try {
    const result = await keys.create(keyName.value, [...keyScopes.value]);
    newToken.value = result.token;
    keyName.value = "";
    keyScopes.value = [];
    await loadKeys();
  } catch (err) {
    ui.notify((err as Error).message, "error");
  }
}

async function removeKey(key: ApiKeyRecord): Promise<void> {
  if (!window.confirm(`删除 API Key「${key.name || key.id}」？`)) return;
  try {
    await keys.remove(key.id);
    await loadKeys();
  } catch (err) {
    ui.notify((err as Error).message, "error");
  }
}

async function removeRecording(name: string): Promise<void> {
  if (!window.confirm(`删除录制 ${name}？`)) return;
  try {
    await recordings.remove(name);
    await loadRecordings();
  } catch (err) {
    ui.notify((err as Error).message, "error");
  }
}

function formatTime(value: number | null | undefined): string {
  if (!value) return "-";
  return formatDateTime(value, locale.value);
}
</script>

<template>
  <NModal
    :show="true"
    preset="card"
    style="width: min(1080px, 96vw); max-height: 90vh"
    :title="t('app.admin')"
    @close="emit('close')"
  >
    <NAlert v-if="error" type="error" :show-icon="true" style="margin-bottom: 12px">{{
      error
    }}</NAlert>
    <NTabs v-model:value="tab" type="line" animated>
      <NTabPane name="overview" :tab="t('admin.overview')">
        <div class="cards">
          <NCard size="small" title="会话">
            <strong class="big">{{ info?.sessions ?? "-" }}</strong>
          </NCard>
          <NCard size="small" title="用户">
            <strong class="big">{{ info?.users ?? "-" }}</strong>
          </NCard>
          <NCard size="small" title="在线连接">
            <strong class="big">{{ info?.clients ?? "-" }}</strong>
          </NCard>
          <NCard size="small" title="版本">
            <strong class="big">{{ info?.version ?? "-" }}</strong>
          </NCard>
        </div>
        <NButton size="small" style="margin-top: 12px" @click="loadOverview">刷新</NButton>
      </NTabPane>

      <NTabPane name="users" :tab="t('admin.users')">
        <NSpace align="end" style="margin-bottom: 12px">
          <NInput v-model:value="newUsername" placeholder="用户名" style="width: 160px" />
          <NInput
            v-model:value="newPassword"
            type="password"
            placeholder="密码（≥8 位）"
            style="width: 190px"
          />
          <NSelect v-model:value="newRole" :options="roleOptions" style="width: 130px" />
          <NButton type="primary" @click="createUser">创建用户</NButton>
        </NSpace>
        <NAlert v-if="totpSetup" type="info" style="margin-bottom: 12px">
          <p>为 {{ totpSetup.username }} 绑定两步验证：</p>
          <div class="qr" v-html="totpSetup.qr" />
          <code class="mono">{{ totpSetup.secret }}</code>
          <NSpace style="margin-top: 8px" align="center">
            <NInput
              v-model:value="totpCode"
              placeholder="6 位验证码"
              maxlength="6"
              style="width: 150px"
              @keyup.enter="confirmTotp"
            />
            <NButton type="primary" @click="confirmTotp">完成绑定</NButton>
            <NButton quaternary @click="totpSetup = null">取消</NButton>
          </NSpace>
        </NAlert>
        <table class="table">
          <thead>
            <tr>
              <th>ID</th>
              <th>用户名</th>
              <th>角色</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in userRows" :key="user.id">
              <td>{{ user.id }}</td>
              <td>{{ user.username }}</td>
              <td>
                <NTag size="small" :type="user.role === 'admin' ? 'warning' : 'default'">{{
                  user.role
                }}</NTag>
              </td>
              <td>
                <NSwitch :value="!user.disabled" @update:value="toggleDisabled(user)" />
              </td>
              <td class="actions">
                <button @click="resetPassword(user)">改密</button>
                <button @click="toggleRole(user)">切换角色</button>
                <button @click="setupTotp(user)">{{ user.totp ? "关闭 TOTP" : "TOTP" }}</button>
                <button
                  class="danger"
                  :disabled="user.username === auth.user?.username"
                  @click="removeUser(user)"
                >
                  删除
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </NTabPane>

      <NTabPane name="audit" :tab="t('admin.audit')">
        <NSpace style="margin-bottom: 10px">
          <NInput
            v-model:value="auditFilter"
            placeholder="按事件过滤，例如 login"
            style="width: 240px"
          />
          <NButton @click="loadAudit">查询</NButton>
          <NButton type="primary" @click="verify">{{ t("admin.verify") }}</NButton>
        </NSpace>
        <NAlert
          v-if="verifyResult"
          :type="verifyResult.includes('完整') ? 'success' : 'error'"
          style="margin-bottom: 10px"
        >
          {{ verifyResult }}
        </NAlert>
        <div class="scroll-table">
          <table class="table">
            <thead>
              <tr>
                <th>时间</th>
                <th>事件</th>
                <th>用户</th>
                <th>会话</th>
                <th>IP</th>
                <th>内容</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in auditRows" :key="row.id">
                <td>{{ formatTime(row.ts) }}</td>
                <td>{{ row.event }}</td>
                <td>{{ row.user_id ?? "-" }}</td>
                <td class="mono">{{ row.term_session_id?.slice(0, 8) ?? "-" }}</td>
                <td>{{ row.ip ?? "-" }}</td>
                <td class="payload">{{ row.payload ?? "" }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </NTabPane>

      <NTabPane name="keys" :tab="t('admin.keys')">
        <NSpace style="margin-bottom: 10px">
          <NInput v-model:value="keyName" placeholder="Key 名称" style="width: 220px" />
          <NCheckboxGroup v-model:value="keyScopes">
            <NSpace>
              <NCheckbox value="read" label="读取" />
              <NCheckbox value="write" label="写入" />
              <NCheckbox value="admin" label="管理" />
            </NSpace>
          </NCheckboxGroup>
          <NButton type="primary" @click="createKey">创建 API Key</NButton>
        </NSpace>
        <NAlert
          v-if="keyScopes.length === 0"
          type="info"
          :show-icon="true"
          style="margin-bottom: 10px"
        >
          未勾选 scope 的 Key 继承账号全部权限（与浏览器登录相同）。自动化场景建议至少选择「读取」。
        </NAlert>
        <NAlert v-if="newToken" type="warning" style="margin-bottom: 10px">
          只显示一次：<code class="mono">{{ newToken }}</code>
        </NAlert>
        <table class="table">
          <thead>
            <tr>
              <th>ID</th>
              <th>名称</th>
              <th>Scopes</th>
              <th>创建</th>
              <th>最后使用</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="key in keyRows" :key="key.id">
              <td>{{ key.id }}</td>
              <td>{{ key.name }}</td>
              <td>{{ key.scopes.join(", ") || "-" }}</td>
              <td>{{ formatTime(key.created_at) }}</td>
              <td>{{ formatTime(key.last_used) }}</td>
              <td><button class="danger" @click="removeKey(key)">删除</button></td>
            </tr>
          </tbody>
        </table>
      </NTabPane>

      <NTabPane name="recordings" :tab="t('admin.recordings')">
        <NSpace style="margin-bottom: 10px">
          <NButton @click="loadRecordings">刷新</NButton>
        </NSpace>
        <table class="table">
          <thead>
            <tr>
              <th>名称</th>
              <th>大小</th>
              <th>时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in recordingRows" :key="row.name">
              <td class="mono">{{ row.name }}</td>
              <td>{{ (row.size / 1024).toFixed(1) }} KiB</td>
              <td>{{ formatTime(row.mtime) }}</td>
              <td class="actions">
                <a :href="recordings.url(row.name)" download>下载</a>
                <button class="danger" @click="removeRecording(row.name)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </NTabPane>
    </NTabs>
    <template #footer>
      <NButton @click="emit('close')">关闭</NButton>
    </template>
  </NModal>
</template>

<style scoped>
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
}
.big {
  font-size: 26px;
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
  vertical-align: top;
}
.table th {
  color: var(--muted);
  font-weight: 500;
}
.scroll-table {
  max-height: 52vh;
  overflow: auto;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
.actions button,
.actions a {
  padding: 2px 7px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: transparent;
  color: var(--muted);
  font-size: 11px;
  text-decoration: none;
}
.actions button:hover,
.actions a:hover {
  border-color: var(--accent);
  color: var(--text);
}
.actions .danger:hover {
  border-color: var(--danger);
  color: var(--danger);
}
.mono {
  font-family: var(--term-font-family);
}
.qr {
  width: 160px;
  margin: 8px 0;
  padding: 6px;
  border-radius: 8px;
  background: white;
}
.qr :deep(svg) {
  width: 100%;
  height: auto;
}
.payload {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
