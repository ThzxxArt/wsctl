import { defineStore } from "pinia";
import { ref } from "vue";

export type ToastLevel = "info" | "success" | "warning" | "error";

export interface Toast {
  id: number;
  level: ToastLevel;
  message: string;
}

let nextId = 1;

export const useUiStore = defineStore("ui", () => {
  const toasts = ref<Toast[]>([]);
  const commandPaletteOpen = ref(false);
  const settingsOpen = ref(false);
  const adminOpen = ref(false);
  const filesOpen = ref(false);
  const shareSession = ref<string | null>(null);
  const replaySession = ref<string | null>(null);
  const quickSwitchOpen = ref(false);
  // On a phone the sidebar is a full-height overlay; opening it by default
  // hides the terminal the user just logged in to see.
  const sidebarOpen = ref(typeof window === "undefined" ? true : window.innerWidth > 760);

  function notify(message: string, level: ToastLevel = "info", timeout = 4200): void {
    const toast: Toast = { id: nextId++, level, message };
    toasts.value.push(toast);
    if (timeout > 0) {
      window.setTimeout(() => dismiss(toast.id), timeout);
    }
  }

  function dismiss(id: number): void {
    toasts.value = toasts.value.filter((toast) => toast.id !== id);
  }

  function openShare(session: string): void {
    shareSession.value = session;
  }

  function openReplay(session: string): void {
    replaySession.value = session;
  }

  return {
    toasts,
    commandPaletteOpen,
    settingsOpen,
    adminOpen,
    filesOpen,
    shareSession,
    replaySession,
    quickSwitchOpen,
    sidebarOpen,
    notify,
    dismiss,
    openShare,
    openReplay,
  };
});
