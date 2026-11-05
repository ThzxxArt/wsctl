<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  NAlert,
  NButton,
  NCard,
  NDivider,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSpace,
  NSwitch,
  NTabPane,
  NTabs,
  NTag,
} from "naive-ui";
import { config as configApi, layouts as layoutsApi, push as pushApi } from "@/api";
import type { ConfigField, ConfigSchema } from "@/api/types";
import { THEMES, usePrefsStore } from "@/stores/prefs";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";
import { parseTheme } from "@/utils/themes";
import { useI18n } from "vue-i18n";
import { promptDialog } from "@/dialogs";

const emit = defineEmits<{ close: [] }>();
const prefs = usePrefsStore();
const ui = useUiStore();
const workspace = useWorkspaceStore();
const { t } = useI18n();

const pushState = ref<"unknown" | "off" | "on" | "unsupported">("unknown");
const pushBusy = ref(false);
const themeInput = ref<HTMLInputElement | null>(null);

const schema = ref<ConfigSchema | null>(null);
const drafts = ref<Record<string, string>>({});
const loading = ref(false);
const saving = ref(false);
const errors = ref<string[]>([]);

const themeOptions = computed(() =>
  [...Object.keys(THEMES), ...Object.keys(prefs.prefs.customThemes)].map((name) => ({
    label: name,
    value: name,
  })),
);
const previewColors = computed(() => {
  const theme = prefs.terminalTheme;
  return [
    theme.background,
    theme.foreground,
    theme.red,
    theme.green,
    theme.yellow,
    theme.blue,
    theme.magenta,
    theme.cyan,
  ].filter((value): value is string => typeof value === "string");
});
const rendererOptions = [
  { label: "auto（WebGL → Canvas → DOM）", value: "auto" },
  { label: "WebGL", value: "webgl" },
  { label: "Canvas", value: "canvas" },
  { label: "DOM", value: "dom" },
];
const languageOptions = [
  { label: "简体中文", value: "zh-CN" },
  { label: "English", value: "en" },
];

function renderValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (value === null || value === undefined) return "";
  return JSON.stringify(value);
}

async function load(): Promise<void> {
  loading.value = true;
  errors.value = [];
  try {
    schema.value = await configApi.schema();
    const next: Record<string, string> = {};
    for (const fields of Object.values(schema.value.sections)) {
      for (const field of fields) next[field.key] = renderValue(field.value);
    }
    drafts.value = next;
  } catch (error) {
    errors.value = [(error as Error).message];
  } finally {
    loading.value = false;
  }
}

function parseField(field: ConfigField, raw: string): unknown {
  if (field.type === "bool") return raw === "true" || raw === "1";
  if (field.type === "int") return Number.parseInt(raw, 10);
  if (field.type === "float") return Number.parseFloat(raw);
  if (field.type === "list") {
    const text = raw.trim();
    if (!text) return [];
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed)) return parsed;
    } catch {
      /* fall through to comma split */
    }
    return text
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return raw;
}

async function save(): Promise<void> {
  if (!schema.value) return;
  const items: Array<{ key: string; value: unknown }> = [];
  const refused: string[] = [];
  for (const fields of Object.values(schema.value.sections)) {
    for (const field of fields) {
      const raw = drafts.value[field.key] ?? "";
      if (raw === renderValue(field.value)) continue;
      if (field.apply === "restart") {
        refused.push(field.key);
        continue;
      }
      items.push({ key: field.key, value: parseField(field, raw) });
    }
  }
  if (refused.length > 0) {
    errors.value = [`这些项需要修改配置文件并重启：${refused.join("、")}`];
  }
  if (items.length === 0) {
    if (refused.length === 0) ui.notify("没有修改", "info");
    return;
  }
  saving.value = true;
  try {
    const result = await configApi.set(items, true);
    errors.value = result.errors;
    if (result.errors.length === 0) {
      ui.notify(`已保存 ${result.written.length} 项配置`, "success");
      await load();
    }
  } catch (error) {
    errors.value = [(error as Error).message];
  } finally {
    saving.value = false;
  }
}

async function reload(): Promise<void> {
  try {
    const result = await configApi.reload();
    errors.value = result.errors;
    ui.notify(
      result.changed.length ? `已重载：${result.changed.join("、")}` : "配置无变化",
      result.errors.length ? "warning" : "success",
    );
    await load();
  } catch (error) {
    errors.value = [(error as Error).message];
  }
}

function base64urlBytes(value: string): Uint8Array {
  const padded = value
    .replace(/-/g, "+")
    .replace(/_/g, "/")
    .padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(padded);
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

async function activeRegistration(): Promise<ServiceWorkerRegistration | null> {
  // ``navigator.serviceWorker.ready`` never settles if registration failed,
  // and WebKit 27 closes the page outright when a settings modal awaits it
  // while a worker is activating. ``getRegistration`` is synchronous-ish and
  // safe; the production entry point registers the worker on load.
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return null;
  try {
    const registration = await navigator.serviceWorker.getRegistration();
    return registration?.active ? registration : null;
  } catch {
    return null;
  }
}

async function loadPushState(): Promise<void> {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    pushState.value = "unsupported";
    return;
  }
  const registration = await activeRegistration();
  if (!registration) {
    pushState.value = "off";
    return;
  }
  try {
    const subscription = await registration.pushManager.getSubscription();
    pushState.value = subscription ? "on" : "off";
  } catch {
    pushState.value = "off";
  }
}

async function enablePush(): Promise<void> {
  pushBusy.value = true;
  try {
    const vapid = await pushApi.vapid();
    if (!vapid.enabled) {
      ui.notify("服务端未启用 Web Push：先运行 wsctl push keys 并写入配置", "warning");
      return;
    }
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      ui.notify("浏览器拒绝了通知权限", "warning");
      return;
    }
    const registration = await activeRegistration();
    if (!registration) {
      ui.notify("Service Worker 尚未就绪，请刷新页面后重试", "warning");
      return;
    }
    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: base64urlBytes(vapid.public_key).buffer as ArrayBuffer,
    });
    await pushApi.subscribe(subscription.toJSON() as PushSubscriptionJSON);
    pushState.value = "on";
    ui.notify("已启用 Web Push", "success");
  } catch (error) {
    ui.notify((error as Error).message, "error");
  } finally {
    pushBusy.value = false;
  }
}

async function disablePush(): Promise<void> {
  pushBusy.value = true;
  try {
    const registration = await activeRegistration();
    if (!registration) {
      ui.notify("Service Worker 尚未就绪，请刷新页面后重试", "warning");
      return;
    }
    const subscription = await registration.pushManager.getSubscription();
    if (subscription) {
      await pushApi.unsubscribe(subscription.endpoint);
      await subscription.unsubscribe();
    }
    pushState.value = "off";
    ui.notify("已关闭 Web Push", "success");
  } catch (error) {
    ui.notify((error as Error).message, "error");
  } finally {
    pushBusy.value = false;
  }
}

async function saveServerLayout(): Promise<void> {
  const name = await promptDialog({
    title: "保存布局",
    message: "布局名称",
    defaultValue: "default",
    placeholder: "布局名称",
  });
  if (!name) return;
  try {
    await layoutsApi.put(name, workspace.snapshot());
    ui.notify(`布局「${name}」已保存到服务器`, "success");
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

async function loadServerLayout(): Promise<void> {
  const name = await promptDialog({
    title: "加载布局",
    message: "要加载的布局名称",
    defaultValue: "default",
    placeholder: "布局名称",
  });
  if (!name) return;
  try {
    const record = await layoutsApi.get(name);
    if (!workspace.replaceLayout(record.layout as { tabs: any[]; activeTabId: string })) {
      ui.notify("布局内容无效", "error");
      return;
    }
    ui.notify(`布局「${name}」已加载`, "success");
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

async function importTheme(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) {
    ui.notify("配色文件过大（上限 2 MB）", "error");
    return;
  }
  try {
    const { name, theme } = parseTheme(await file.text(), file.name);
    prefs.prefs.customThemes = { ...prefs.prefs.customThemes, [name]: theme };
    prefs.prefs.termTheme = name;
    prefs.apply();
    ui.notify(`已导入配色「${name}」`, "success");
  } catch (error) {
    ui.notify((error as Error).message, "error");
  }
}

function onTabChange(name: string): void {
  // Touch service-worker APIs only when the user opens the notification tab.
  // Playwright's WebKit build closes the page when a modal probes a worker
  // during activation; real Safari is fine, but there is no reason to ask
  // before the user shows interest in push.
  if (name === "behavior") void loadPushState();
}

onMounted(() => {
  void load();
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    pushState.value = "unsupported";
  }
});
</script>

<template>
  <NModal
    :show="true"
    preset="card"
    style="width: min(880px, 94vw); max-height: 88vh"
    :title="t('app.settings')"
    @close="emit('close')"
  >
    <div class="settings-body">
      <NTabs type="line" animated @update:value="onTabChange">
        <NTabPane name="appearance" :tab="t('settings.appearance')">
          <div class="grid">
            <label
              >{{ t("settings.theme") }}
              <NSelect
                v-model:value="prefs.prefs.theme"
                :options="[
                  { label: '跟随系统', value: 'auto' },
                  { label: '深色', value: 'dark' },
                  { label: '浅色', value: 'light' },
                ]"
              />
            </label>
            <label
              >{{ t("settings.termTheme") }}
              <NSelect v-model:value="prefs.prefs.termTheme" :options="themeOptions" />
            </label>
            <div class="swatches" aria-hidden="true">
              <span v-for="color in previewColors" :key="color" :style="{ background: color }" />
            </div>
            <label
              >{{ t("settings.language") }}
              <NSelect v-model:value="prefs.prefs.language" :options="languageOptions" />
            </label>
          </div>
          <NSpace style="margin-top: 12px">
            <NButton size="small" @click="themeInput?.click()"
              >导入配色（JSON / iTerm2 / GNOME）</NButton
            >
            <input
              ref="themeInput"
              type="file"
              accept=".json,.itermcolors,.dconf,.theme,application/json"
              hidden
              @change="importTheme"
            />
          </NSpace>
          <NDivider />
          <NSpace>
            <NButton size="small" @click="saveServerLayout">保存布局到服务器</NButton>
            <NButton size="small" @click="loadServerLayout">从服务器加载布局</NButton>
          </NSpace>
        </NTabPane>

        <NTabPane name="terminal" :tab="t('settings.terminal')">
          <div class="grid">
            <label
              >{{ t("settings.fontSize") }}
              <NInputNumber v-model:value="prefs.prefs.fontSize" :min="8" :max="32" />
            </label>
            <label
              >{{ t("settings.fontFamily") }}
              <NInput v-model:value="prefs.prefs.fontFamily" placeholder="留空使用等宽字体" />
            </label>
            <label
              >{{ t("settings.renderer") }}
              <NSelect v-model:value="prefs.prefs.termRenderer" :options="rendererOptions" />
            </label>
            <label
              >{{ t("settings.scrollback") }}
              <NInputNumber
                v-model:value="prefs.prefs.scrollback"
                :min="1000"
                :max="200000"
                :step="1000"
              />
            </label>
          </div>
        </NTabPane>

        <NTabPane name="behavior" :tab="t('settings.behavior')">
          <div class="switches">
            <label
              >{{ t("settings.sound") }} <NSwitch v-model:value="prefs.prefs.notifySound"
            /></label>
            <label
              >{{ t("settings.title") }} <NSwitch v-model:value="prefs.prefs.notifyTitle"
            /></label>
            <label
              >{{ t("settings.systemNotify") }} <NSwitch v-model:value="prefs.prefs.notifySystem"
            /></label>
            <label
              >{{ t("settings.copyOnSelect") }} <NSwitch v-model:value="prefs.prefs.copyOnSelect"
            /></label>
            <label
              >{{ t("settings.rightClickPaste") }}
              <NSwitch v-model:value="prefs.prefs.rightClickPaste"
            /></label>
            <label
              >{{ t("settings.cursorBlink") }} <NSwitch v-model:value="prefs.prefs.cursorBlink"
            /></label>
            <label>高对比主题 <NSwitch v-model:value="prefs.prefs.highContrast" /></label>
            <label>屏幕阅读器模式 <NSwitch v-model:value="prefs.prefs.screenReader" /></label>
            <label>移动端手势 <NSwitch v-model:value="prefs.prefs.mobileGestures" /></label>
          </div>
          <NDivider />
          <div class="push-row">
            <div>
              <strong>Web Push</strong>
              <p class="ws-muted">
                服务端开关 + 浏览器订阅。需要先在配置中运行 <code>wsctl push keys</code>。
              </p>
            </div>
            <NSpace>
              <NTag v-if="pushState === 'on'" type="success" size="small">已订阅</NTag>
              <NTag v-else-if="pushState === 'unsupported'" type="warning" size="small"
                >浏览器不支持</NTag
              >
              <NTag v-else size="small">未订阅</NTag>
              <NButton
                v-if="pushState !== 'on' && pushState !== 'unsupported'"
                size="small"
                :loading="pushBusy"
                @click="enablePush"
              >
                启用通知
              </NButton>
              <NButton
                v-else-if="pushState === 'on'"
                size="small"
                :loading="pushBusy"
                @click="disablePush"
              >
                关闭通知
              </NButton>
            </NSpace>
          </div>
        </NTabPane>

        <NTabPane name="server" :tab="t('settings.config')">
          <NSpace vertical size="large">
            <NAlert v-if="errors.length" type="error" :show-icon="true">
              <div v-for="message in errors" :key="message">{{ message }}</div>
            </NAlert>
            <div class="config-head">
              <span class="ws-muted">{{ schema?.config_path }}</span>
              <NSpace>
                <NButton size="small" :loading="loading" @click="load">{{
                  t("app.refresh")
                }}</NButton>
                <NButton size="small" @click="reload">热重载</NButton>
                <NButton size="small" type="primary" :loading="saving" @click="save">{{
                  t("app.save")
                }}</NButton>
              </NSpace>
            </div>
            <NCard
              v-for="(fields, section) in schema?.sections ?? {}"
              :key="section"
              size="small"
              :title="schema?.section_titles?.[section] ?? section"
            >
              <div class="config-grid">
                <div v-for="field in fields" :key="field.key" class="config-field">
                  <div class="config-main">
                    <div class="config-copy">
                      <div class="config-title">{{ field.title || field.key }}</div>
                      <code class="config-key">{{ field.key }}</code>
                      <p v-if="field.description" class="config-desc">
                        {{ field.description }}
                      </p>
                    </div>
                    <div class="config-side">
                      <NTag
                        size="tiny"
                        :type="
                          field.apply === 'restart'
                            ? 'warning'
                            : field.apply === 'new'
                              ? 'info'
                              : 'success'
                        "
                      >
                        {{
                          field.apply === "hot"
                            ? t("settings.apply")
                            : field.apply === "new"
                              ? t("settings.newSessions")
                              : t("settings.restart")
                        }}
                      </NTag>
                      <NSwitch
                        v-if="field.type === 'bool'"
                        :value="drafts[field.key] === 'true'"
                        :disabled="field.apply === 'restart'"
                        @update:value="(value: boolean) => (drafts[field.key] = String(value))"
                      />
                    </div>
                  </div>
                  <NInput
                    v-if="field.type !== 'bool'"
                    :value="drafts[field.key]"
                    :disabled="field.apply === 'restart'"
                    :placeholder="field.secret ? '已隐藏；输入新值以覆盖' : field.default"
                    @update:value="(value: string) => (drafts[field.key] = value)"
                  />
                </div>
              </div>
            </NCard>
            <NDivider />
            <p class="ws-muted tips">
              热更新项立即生效；「仅新会话」影响之后创建的会话；「需重启」必须修改配置文件并重启服务。
              浏览器偏好保存在本机，不会上传。
            </p>
          </NSpace>
        </NTabPane>
      </NTabs>
    </div>
    <template #footer>
      <NButton @click="emit('close')">关闭</NButton>
    </template>
  </NModal>
</template>

<style scoped>
.settings-body {
  max-height: 70vh;
  overflow: auto;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
}
.grid label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--muted);
}
.swatches {
  display: flex;
  gap: 4px;
  align-items: center;
  padding-top: 22px;
}
.swatches span {
  width: 22px;
  height: 22px;
  border: 1px solid var(--border);
  border-radius: 5px;
}
.switches {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.switches label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 9px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.config-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.config-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 12px;
}
.config-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-panel);
}
.config-main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.config-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}
.config-title {
  color: var(--text);
  font-size: 13px;
  font-weight: 600;
  /* Match the switch's 22px box so the two centres share one line. */
  line-height: 22px;
}
.config-key {
  color: var(--muted);
  font-family: var(--term-font-family);
  font-size: 11px;
  line-height: 1.4;
}
.config-desc {
  margin: 2px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}
.config-side {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 8px;
  min-height: 24px;
}
.tips {
  font-size: 12px;
  line-height: 1.7;
}
.push-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.push-row p {
  margin: 3px 0 0;
  font-size: 12px;
}
</style>
