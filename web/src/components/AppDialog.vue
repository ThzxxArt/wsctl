<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import type { ComponentPublicInstance } from "vue";
import { NButton, NInput, NModal, NSpace } from "naive-ui";
import type { InputInst } from "naive-ui";
import { useI18n } from "vue-i18n";
import { dialogState, resolveDialog } from "@/dialogs";
import type { PromptRequest } from "@/dialogs";

const { t } = useI18n();
const state = dialogState();

const value = ref("");
const confirmation = ref("");
const error = ref("");
const inputRef = ref<InputInst | null>(null);
const confirmRef = ref<ComponentPublicInstance | null>(null);

const current = computed(() => state.current);
const prompt = computed<PromptRequest | null>(() =>
  current.value?.kind === "prompt" ? current.value : null,
);
const title = computed(() => {
  if (current.value?.title) return current.value.title;
  return prompt.value ? t("dialogs.promptTitle") : t("dialogs.confirmTitle");
});
const message = computed(() => current.value?.message ?? "");
const confirmText = computed(() => current.value?.confirmText ?? t("app.confirm"));
const cancelText = computed(() => current.value?.cancelText ?? t("app.cancel"));
const danger = computed(() => current.value?.kind === "confirm" && current.value.danger === true);

watch(
  () => current.value?.id,
  async () => {
    const request = current.value;
    if (!request) return;
    value.value = request.kind === "prompt" ? (request.defaultValue ?? "") : "";
    confirmation.value = "";
    error.value = "";
    await nextTick();
    focusPrimary();
  },
  { immediate: true },
);

function focusPrimary(): void {
  if (prompt.value) {
    inputRef.value?.focus();
    // Renaming a session/file starts with the old name selected, like the
    // native prompt did.
    if (prompt.value.defaultValue) inputRef.value?.select();
    return;
  }
  const element = confirmRef.value?.$el;
  if (element instanceof HTMLElement) element.focus();
}

function cancel(): void {
  const request = current.value;
  if (!request) return;
  resolveDialog(request.kind === "prompt" ? null : false);
}

function submit(): void {
  const request = current.value;
  if (!request) return;
  if (request.kind === "confirm") {
    resolveDialog(true);
    return;
  }

  // Never trim passwords: leading/trailing spaces may be intentional.
  const result = request.type === "password" ? value.value : value.value.trim();
  if (request.required !== false && result.length === 0) {
    error.value = t("dialogs.required");
    return;
  }
  if (request.minLength && result.length < request.minLength) {
    error.value = t("dialogs.minLength", { n: request.minLength });
    return;
  }
  if (request.confirmValue && result !== confirmation.value) {
    error.value = t("dialogs.passwordMismatch");
    return;
  }
  const invalid = request.validate?.(result) ?? null;
  if (invalid) {
    error.value = invalid;
    return;
  }
  resolveDialog(result);
}
</script>

<template>
  <NModal
    :show="Boolean(current)"
    preset="card"
    :title="title"
    :mask-closable="false"
    :auto-focus="false"
    style="width: min(460px, 92vw)"
    @close="cancel"
    @after-enter="focusPrimary"
  >
    <div class="app-dialog">
      <p v-if="message" class="message">{{ message }}</p>
      <template v-if="prompt">
        <NInput
          ref="inputRef"
          v-model:value="value"
          :type="prompt.type ?? 'text'"
          :placeholder="prompt.placeholder ?? ''"
          :show-password-on="prompt.type === 'password' ? 'click' : undefined"
          @keyup.enter="submit"
        />
        <NInput
          v-if="prompt.confirmValue"
          v-model:value="confirmation"
          type="password"
          :placeholder="prompt.confirmPlaceholder ?? t('dialogs.passwordConfirmPlaceholder')"
          show-password-on="click"
          @keyup.enter="submit"
        />
        <p v-if="error" class="error" role="alert">{{ error }}</p>
      </template>
    </div>
    <template #footer>
      <NSpace justify="end">
        <NButton @click="cancel">{{ cancelText }}</NButton>
        <NButton ref="confirmRef" :type="danger ? 'error' : 'primary'" @click="submit">
          {{ confirmText }}
        </NButton>
      </NSpace>
    </template>
  </NModal>
</template>

<style scoped>
.app-dialog {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.message {
  margin: 0;
  line-height: 1.65;
  white-space: pre-wrap;
  color: var(--text);
}

.error {
  margin: 0;
  color: var(--danger);
  font-size: 13px;
}
</style>
