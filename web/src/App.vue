<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NConfigProvider,
  NDialogProvider,
  NGlobalStyle,
  NMessageProvider,
  darkTheme,
  dateEnUS,
  dateZhCN,
  enUS,
  zhCN,
} from "naive-ui";
import { useAuthStore } from "@/stores/auth";
import { usePrefsStore } from "@/stores/prefs";
import { useUiStore } from "@/stores/ui";
import { useSessionsStore } from "@/stores/sessions";
import { useWorkspaceStore } from "@/stores/workspace";
import { clearScreen } from "@/protocol/cache";
import LoginView from "@/components/LoginView.vue";
import WorkspaceView from "@/components/WorkspaceView.vue";
import ToastHost from "@/components/ToastHost.vue";
import CommandPalette from "@/components/CommandPalette.vue";

const SettingsDialog = defineAsyncComponent(() => import("@/components/SettingsDialog.vue"));
const AdminDialog = defineAsyncComponent(() => import("@/components/AdminDialog.vue"));
const FilePanel = defineAsyncComponent(() => import("@/components/FilePanel.vue"));
const ShareDialog = defineAsyncComponent(() => import("@/components/ShareDialog.vue"));
const ReplayDialog = defineAsyncComponent(() => import("@/components/ReplayDialog.vue"));

const auth = useAuthStore();
const prefs = usePrefsStore();
const ui = useUiStore();
const sessions = useSessionsStore();
const workspace = useWorkspaceStore();
const { locale: i18nLocale } = useI18n();

const naiveTheme = computed(() => (prefs.effectivePageTheme === "dark" ? darkTheme : null));
const themeOverrides = computed(() => ({
  common: {
    primaryColor: prefs.effectivePageTheme === "dark" ? "#4f9cf9" : "#2f6fd0",
    primaryColorHover: prefs.effectivePageTheme === "dark" ? "#6cb0ff" : "#4a86e0",
    primaryColorPressed: prefs.effectivePageTheme === "dark" ? "#2f7fe0" : "#1d56ad",
    borderRadius: "8px",
  },
}));
const locale = computed(() => (prefs.prefs.language === "en" ? enUS : zhCN));
const dateLocale = computed(() => (prefs.prefs.language === "en" ? dateEnUS : dateZhCN));

onMounted(async () => {
  await auth.restore();
  if (auth.isAuthenticated) {
    workspace.hydrate();
    sessions.startEvents();
    void sessions.refresh();
  }
});

watch(
  () => auth.isAuthenticated,
  (authenticated) => {
    if (authenticated) {
      workspace.hydrate();
      sessions.startEvents();
      void sessions.refresh();
    } else {
      sessions.reset();
      // Session ids, pane titles and the per-user layout are not safe to keep
      // across a logout: the next account would see the previous one's
      // workspace (and send attach attempts for its sessions). The persisted
      // screen cache is part of the same data and is dropped with it.
      for (const leaf of workspace.allLeaves) {
        if (leaf.sessionId) void clearScreen(leaf.sessionId);
      }
      workspace.reset();
      closeTransient();
      ui.shareSession = null;
      ui.replaySession = null;
    }
  },
);

watch(
  () => prefs.prefs.language,
  (language) => {
    localStorage.setItem("wsctl-language", language);
    document.documentElement.lang = language;
    i18nLocale.value = language;
  },
);

function closeTransient(): void {
  ui.settingsOpen = false;
  ui.adminOpen = false;
  ui.filesOpen = false;
  ui.commandPaletteOpen = false;
  ui.quickSwitchOpen = false;
}
</script>

<template>
  <NConfigProvider
    :theme="naiveTheme"
    :theme-overrides="themeOverrides"
    :locale="locale"
    :date-locale="dateLocale"
  >
    <NGlobalStyle />
    <NDialogProvider>
      <NMessageProvider>
        <LoginView v-if="auth.initialized && !auth.isAuthenticated" />
        <div v-else-if="!auth.initialized" class="ws-login-shell" aria-busy="true">
          <span class="ws-muted">{{ $t("app.loading") }}</span>
        </div>
        <WorkspaceView v-else />
        <ToastHost />
        <SettingsDialog v-if="ui.settingsOpen" @close="closeTransient" />
        <AdminDialog v-if="ui.adminOpen" @close="closeTransient" />
        <FilePanel v-if="ui.filesOpen" @close="closeTransient" />
        <ShareDialog
          v-if="ui.shareSession"
          :session="ui.shareSession"
          @close="ui.shareSession = null"
        />
        <ReplayDialog
          v-if="ui.replaySession"
          :session="ui.replaySession"
          @close="ui.replaySession = null"
        />
        <CommandPalette
          v-if="ui.commandPaletteOpen || ui.quickSwitchOpen"
          @close="
            ui.commandPaletteOpen = false;
            ui.quickSwitchOpen = false;
          "
        />
      </NMessageProvider>
    </NDialogProvider>
  </NConfigProvider>
</template>

<style scoped>
.ws-login-shell {
  display: grid;
  place-items: center;
  height: 100dvh;
}
</style>
