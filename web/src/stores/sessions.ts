import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { sessions as api } from "@/api";
import type { HistoryRow, SessionInfo } from "@/api/types";
import { usePresenceStore } from "@/stores/presence";
import { clearScreen } from "@/protocol/cache";

export interface SessionEvent {
  v: number;
  event: string;
  seq: number;
  ts: number;
  [key: string]: unknown;
}

export const useSessionsStore = defineStore("sessions", () => {
  const live = ref<SessionInfo[]>([]);
  const history = ref<HistoryRow[]>([]);
  const loading = ref(false);
  //: True after the first successful list load for this login. The workspace
  //: may auto-bind a blank pane only on that first load; binding on every
  //: ``live.length`` change raced ``create()`` and put one newly created
  //: session into two tabs at once.
  const initialized = ref(false);
  const error = ref("");
  const sseConnected = ref(false);
  let source: EventSource | null = null;
  let refreshTimer: number | null = null;
  //: Bumped on logout. A refresh that was already in flight must not write the
  //: previous account's rows back into the (just-cleared) store.
  let generation = 0;

  const byId = computed<Record<string, SessionInfo>>(() =>
    Object.fromEntries(live.value.map((session) => [session.id, session])),
  );
  const count = computed(() => live.value.length);

  async function refresh(): Promise<void> {
    const epoch = generation;
    loading.value = true;
    error.value = "";
    try {
      const [current, past] = await Promise.all([api.list(), api.history(50)]);
      if (epoch !== generation) return;
      live.value = current;
      history.value = past;
      initialized.value = true;
    } catch (err) {
      if (epoch !== generation) return;
      error.value = (err as Error).message;
    } finally {
      if (epoch === generation) loading.value = false;
    }
  }

  function scheduleRefresh(): void {
    if (refreshTimer !== null) return;
    refreshTimer = window.setTimeout(() => {
      refreshTimer = null;
      void refresh();
    }, 150);
  }

  function startEvents(): void {
    if (source) return;
    source = new EventSource("/api/v2/events");
    source.onopen = () => {
      const reconnected = !sseConnected.value;
      sseConnected.value = true;
      // Events missed while the stream was down are gone; reconcile the list
      // instead of leaving the sidebar stale until an unrelated action.
      if (reconnected) scheduleRefresh();
    };
    source.onerror = () => {
      sseConnected.value = false;
    };
    const handle = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data) as SessionEvent;
        if (data.event === "heartbeat") return;
        if (data.event === "presence") {
          const session = String(data.session ?? "");
          const clients = Number(data.clients ?? 0);
          if (session) {
            const names = Array.isArray(data.viewers)
              ? (data.viewers as unknown[]).map((value) => String(value))
              : undefined;
            usePresenceStore().apply(session, clients, names);
          }
          return;
        }
        if (data.event === "session_ended" && typeof data.session === "string") {
          const sid = data.session;
          live.value = live.value.filter((session) => session.id !== sid);
          usePresenceStore().forget(sid);
        }
        if (data.event === "session_created" || data.event === "session_ended") {
          scheduleRefresh();
        }
      } catch {
        /* ignore malformed */
      }
    };
    source.addEventListener("session_created", handle as EventListener);
    source.addEventListener("session_ended", handle as EventListener);
    source.addEventListener("presence", handle as EventListener);
    source.addEventListener("overflow", (() => {
      // The broker told us events were dropped and closed the stream. Force a
      // full list refresh; EventSource will reconnect on its own.
      sseConnected.value = false;
      scheduleRefresh();
    }) as EventListener);
  }

  function stopEvents(): void {
    source?.close();
    source = null;
    sseConnected.value = false;
  }

  function reset(): void {
    generation += 1;
    stopEvents();
    if (refreshTimer !== null) {
      window.clearTimeout(refreshTimer);
      refreshTimer = null;
    }
    live.value = [];
    history.value = [];
    error.value = "";
    loading.value = false;
    initialized.value = false;
  }

  async function create(body: Record<string, unknown>): Promise<SessionInfo> {
    const session = await api.create(body);
    await refresh();
    return session;
  }

  async function remove(sid: string): Promise<void> {
    await api.remove(sid);
    live.value = live.value.filter((session) => session.id !== sid);
    await clearScreen(sid);
    await refresh();
  }

  async function rename(sid: string, name: string): Promise<void> {
    await api.rename(sid, name);
    applyRename(sid, name);
  }

  function applyRename(sid: string, name: string): void {
    const current = live.value.find((item) => item.id === sid);
    if (current) current.name = name;
    const past = history.value.find((item) => item.id === sid);
    if (past) past.name = name;
  }

  function nameOf(sid: string | null): string {
    if (!sid) return "终端";
    return (
      byId.value[sid]?.name ?? history.value.find((item) => item.id === sid)?.name ?? "已结束会话"
    );
  }

  return {
    live,
    history,
    loading,
    initialized,
    error,
    sseConnected,
    byId,
    count,
    refresh,
    reset,
    startEvents,
    stopEvents,
    create,
    remove,
    rename,
    applyRename,
    nameOf,
  };
});
