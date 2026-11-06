<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { useAuthStore } from "@/stores/auth";
import { useI18n } from "vue-i18n";

const auth = useAuthStore();
const { t } = useI18n();
const username = ref("");
const password = ref("");
const totp = ref("");
const showTotp = ref(false);
const totpInput = ref<HTMLInputElement | null>(null);
const formError = ref("");
const totpVisible = computed(() => auth.totpRequired || showTotp.value);

watch(
  () => auth.totpRequired,
  async (required) => {
    if (!required) return;
    await nextTick();
    totpInput.value?.focus();
  },
);

// A new username is a new account: do not carry the previous account's
// "two-factor required" state or error into it.
watch(username, () => {
  auth.clearTotpPrompt();
  showTotp.value = false;
  formError.value = "";
});

watch([password, totp], () => {
  formError.value = "";
  auth.error = "";
});

async function toggleTotp(): Promise<void> {
  showTotp.value = !showTotp.value;
  if (showTotp.value) {
    await nextTick();
    totpInput.value?.focus();
  }
}

async function submit(): Promise<void> {
  formError.value = "";
  if (!username.value || !password.value) return;
  if (totpVisible.value && !totp.value.trim()) {
    formError.value = t("app.totpRequired");
    totpInput.value?.focus();
    return;
  }
  const ok = await auth.login(
    username.value,
    password.value,
    totpVisible.value ? totp.value.trim() : undefined,
  );
  if (ok) {
    password.value = "";
    totp.value = "";
    showTotp.value = false;
  }
}
</script>

<template>
  <main class="login-shell">
    <section class="login-card" aria-labelledby="login-title">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">$_</span>
        <div>
          <h1 id="login-title">WsCTL</h1>
          <p>{{ t("app.loginHint") }}</p>
        </div>
      </div>
      <form @submit.prevent="submit">
        <label>
          <span>{{ t("app.username") }}</span>
          <input v-model="username" autocomplete="username" autofocus required />
        </label>
        <label>
          <span>{{ t("app.password") }}</span>
          <input v-model="password" type="password" autocomplete="current-password" required />
        </label>
        <label v-if="totpVisible">
          <span>{{ t("app.totp") }}</span>
          <input
            ref="totpInput"
            v-model="totp"
            inputmode="numeric"
            maxlength="6"
            pattern="[0-9]*"
            autocomplete="one-time-code"
            :placeholder="t('app.totpPlaceholder')"
          />
        </label>
        <p v-if="auth.totpRequired && !auth.error && !formError" class="hint" role="status">
          {{ t("app.totpHint") }}
        </p>
        <p v-if="formError" class="error" role="alert">{{ formError }}</p>
        <p v-if="auth.error" class="error" role="alert">{{ auth.error }}</p>
        <button class="submit" type="submit" :disabled="auth.loading">
          {{ auth.loading ? t("app.loading") : totpVisible ? t("app.verify") : t("app.login") }}
        </button>
        <button v-if="!auth.totpRequired" type="button" class="link" @click="toggleTotp">
          {{ showTotp ? t("app.hideTotp") : t("app.useTotp") }}
        </button>
      </form>
    </section>
  </main>
</template>

<style scoped>
.login-shell {
  display: grid;
  place-items: center;
  min-height: 100dvh;
  padding: 24px;
  background:
    radial-gradient(circle at 15% 10%, rgb(79 156 249 / 18%), transparent 45%),
    radial-gradient(circle at 85% 85%, rgb(71 199 138 / 12%), transparent 40%), var(--bg);
}

.login-card {
  width: min(400px, 100%);
  padding: 32px;
  border: 1px solid var(--border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--bg-elevated) 92%, transparent);
  box-shadow: 0 30px 80px rgb(0 0 0 / 35%);
  backdrop-filter: blur(12px);
}

.brand {
  display: flex;
  gap: 14px;
  align-items: center;
  margin-bottom: 28px;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 46px;
  height: 46px;
  border-radius: 12px;
  background: linear-gradient(135deg, var(--accent), #7b5cff);
  color: white;
  font-family: var(--term-font-family);
  font-size: 17px;
  font-weight: 700;
}

h1 {
  margin: 0;
  font-size: 24px;
  letter-spacing: 0.02em;
}

.brand p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 13px;
}

form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--muted);
}

input {
  height: 40px;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
  color: var(--text);
  outline: none;
}

input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 20%, transparent);
}

.submit {
  height: 42px;
  border: 0;
  border-radius: 9px;
  background: linear-gradient(135deg, var(--accent), var(--accent-strong));
  color: white;
  font-weight: 600;
}

.submit:disabled {
  opacity: 0.6;
  cursor: wait;
}

.error {
  margin: 0;
  padding: 9px 12px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--danger) 14%, transparent);
  color: var(--danger);
  font-size: 13px;
}

.hint {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}

.link {
  align-self: center;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--accent);
  font-size: 12px;
}

.link:hover {
  text-decoration: underline;
}
</style>
