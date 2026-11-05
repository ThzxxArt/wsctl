<script setup lang="ts">
import { ref } from "vue";
import { useAuthStore } from "@/stores/auth";
import { useI18n } from "vue-i18n";

const auth = useAuthStore();
const { t } = useI18n();
const username = ref("");
const password = ref("");
const totp = ref("");
const showTotp = ref(false);

async function submit(): Promise<void> {
  if (!username.value || !password.value) return;
  const ok = await auth.login(
    username.value,
    password.value,
    showTotp.value ? totp.value : undefined,
  );
  if (!ok && auth.error.includes("一次性验证码")) showTotp.value = true;
  if (ok) {
    password.value = "";
    totp.value = "";
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
        <label v-if="showTotp">
          <span>{{ t("app.totp") }}</span>
          <input v-model="totp" inputmode="numeric" autocomplete="one-time-code" />
        </label>
        <p v-if="auth.error" class="error" role="alert">{{ auth.error }}</p>
        <button class="submit" type="submit" :disabled="auth.loading">
          {{ auth.loading ? t("app.loading") : t("app.login") }}
        </button>
      </form>
      <form class="sr-only" @submit.prevent="showTotp = !showTotp">
        <button type="submit">{{ t("app.totp") }}</button>
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
</style>
