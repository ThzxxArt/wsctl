import { defineStore } from "pinia";
import { ref } from "vue";

export interface SessionPresence {
  session: string;
  clients: number;
  action: string;
}

export const usePresenceStore = defineStore("presence", () => {
  const online = ref<Record<string, number>>({});
  const viewers = ref<Record<string, string[]>>({});

  function apply(session: string, clients: number, names?: string[]): void {
    online.value = { ...online.value, [session]: clients };
    if (names) viewers.value = { ...viewers.value, [session]: names };
  }

  function forget(session: string): void {
    if (!(session in online.value) && !(session in viewers.value)) return;
    const nextOnline = { ...online.value };
    const nextViewers = { ...viewers.value };
    delete nextOnline[session];
    delete nextViewers[session];
    online.value = nextOnline;
    viewers.value = nextViewers;
  }

  return { online, viewers, apply, forget };
});
