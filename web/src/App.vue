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
import { useQueryClient } from "@tanstack/vue-query";
import { useAuthStore } from "@/stores/auth";
import { usePrefsStore } from "@/stores/prefs";
import { useUiStore } from "@/stores/ui";
import { useSessionsStore } from "@/stores/sessions";
import { useWorkspaceStore } from "@/stores/workspace";
import { useTerminalStore } from "@/stores/terminal";
import { usePresenceStore } from "@/stores/presence";
import { clearAllScreens } from "@/protocol/cache";
import LoginView from "@/components/LoginView.vue";
import WorkspaceView from "@/components/WorkspaceView.vue";
import ToastHost from "@/components/ToastHost.vue";
import CommandPalette from "@/components/CommandPalette.vue";
import AppDialog from "@/components/AppDialog.vue";
import { confirmDialog, resetDialogs } from "@/dialogs";

function asyncDialog(loader: () => Promise<unknown>) {
  return defineAsyncComponent({
    loader: loader as never,
    onError: (error, retry, fail, attempts) => {
      // A deploy removes the old hashed chunk; a tab left open across it then
      // imports a 404. Retry once, then ask for a reload instead of silently
      // rendering nothing when the user clicks the dialog.
      if (attempts < 2) retry();
      else {
        console.warn("dialog chunk failed to load", error);
        fail();
        void confirmDialog({
          title: "资源已更新",
          message: "页面资源已更新，需要刷新后继续。现在刷新？",
          confirmText: "刷新",
        }).then((reload) => {
          if (reload) location.reload();
        });
      }
    },
  });
}

const SettingsDialog = asyncDialog(() => import("@/components/SettingsDialog.vue"));
const AdminDialog = asyncDialog(() => import("@/components/AdminDialog.vue"));
const FilePanel = asyncDialog(() => import("@/components/FilePanel.vue"));
const ShareDialog = asyncDialog(() => import("@/components/ShareDialog.vue"));
const ReplayDialog = asyncDialog(() => import("@/components/ReplayDialog.vue"));

const auth = useAuthStore();
const prefs = usePrefsStore();
const ui = useUiStore();
const sessions = useSessionsStore();
const workspace = useWorkspaceStore();
const terminalStore = useTerminalStore();
const presence = usePresenceStore();
const queryClient = useQueryClient();
const { locale: i18nLocale } = useI18n();

const shareToken = computed(() => {
  const match = location.pathname.match(/^\/s\/([^/]+)/);
  return match?.[1] ?? new URLSearchParams(location.search).get("share");
});
// An anonymous visitor with a share link must not be sent to LoginView: the
// whole point of a share is that its bearer token is the credential.
const shareOnly = computed(() => !auth.isAuthenticated && Boolean(shareToken.value));

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
  if (typeof window !== "undefined" && !window.isSecureContext && !prefs.prefs.seenInsecureNotice) {
    prefs.prefs.seenInsecureNotice = true;
    ui.notify(
      "当前不是 HTTPS：浏览器可能拒绝剪贴板读写，请使用 HTTPS 或 localhost",
      "warning",
      8000,
    );
  }
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
      // Session ids, pane titles, command history and the per-user layout are
      // not safe to keep across a logout: the next account would see the
      // previous one's workspace. Clear *all* cached screens, not only the
      // currently open leaves -- closed tabs and ended sessions retained full
      // terminal transcripts.
      void clearAllScreens();
      workspace.reset();
      terminalStore.meta = {};
      terminalStore.commands = {};
      presence.online = {};
      presence.viewers = {};
      ui.toasts = [];
      ui.filesOpen = false;
      resetDialogs();
      queryClient.clear();
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
  { immediate: true },
);

function closeTransient(): void {
  ui.settingsOpen = false;
  ui.adminOpen = false;
  // Do not unmount the file panel here: it owns the editor's dirty-state
  // guard. Closing Settings used to discard an unsaved draft behind the modal.
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
        <LoginView v-if="auth.initialized && !auth.isAuthenticated && !shareOnly" />
        <div v-else-if="!auth.initialized" class="ws-login-shell" aria-busy="true">
          <span class="ws-muted">{{ $t("app.loading") }}</span>
        </div>
        <WorkspaceView v-else :share-only="shareOnly" />
        <ToastHost />
        <SettingsDialog v-if="!shareOnly && ui.settingsOpen" @close="closeTransient" />
        <AdminDialog v-if="!shareOnly && ui.adminOpen" @close="closeTransient" />
        <FilePanel v-if="!shareOnly && ui.filesOpen" @close="ui.filesOpen = false" />
        <ShareDialog
          v-if="!shareOnly && ui.shareSession"
          :session="ui.shareSession"
          @close="ui.shareSession = null"
        />
        <ReplayDialog
          v-if="!shareOnly && ui.replaySession"
          :session="ui.replaySession"
          @close="ui.replaySession = null"
        />
        <CommandPalette
          v-if="!shareOnly && (ui.commandPaletteOpen || ui.quickSwitchOpen)"
          @close="
            ui.commandPaletteOpen = false;
            ui.quickSwitchOpen = false;
          "
        />
        <AppDialog />
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
