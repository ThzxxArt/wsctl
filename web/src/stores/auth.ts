import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { auth } from "@/api";
import { ApiError, onUnauthorized } from "@/api/client";
import type { User } from "@/api/types";

export const useAuthStore = defineStore("auth", () => {
  const user = ref<User | null>(null);
  const loading = ref(false);
  const error = ref("");
  const initialized = ref(false);

  const isAuthenticated = computed(() => Boolean(user.value?.username));
  const isAdmin = computed(() => user.value?.role === "admin");

  async function restore(): Promise<void> {
    loading.value = true;
    try {
      user.value = await auth.me();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) user.value = null;
      else error.value = (err as Error).message;
    } finally {
      loading.value = false;
      initialized.value = true;
    }
  }

  async function login(username: string, password: string, totp?: string): Promise<boolean> {
    loading.value = true;
    error.value = "";
    try {
      const result = await auth.login(username, password, totp);
      user.value = result;
      return true;
    } catch (err) {
      error.value = (err as Error).message;
      return false;
    } finally {
      loading.value = false;
    }
  }

  async function logout(): Promise<void> {
    try {
      await auth.logout();
    } finally {
      user.value = null;
    }
  }

  onUnauthorized(() => {
    user.value = null;
  });

  return { user, loading, error, initialized, isAuthenticated, isAdmin, restore, login, logout };
});
