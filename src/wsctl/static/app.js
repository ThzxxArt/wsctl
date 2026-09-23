"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const els = {
    tabs: $("tabs"), newTab: $("new-tab"), wrap: $("terminal-wrap"),
    connection: $("connection"), whoami: $("whoami"), logout: $("logout"),
    overlay: $("login-overlay"), loginForm: $("login-form"), loginError: $("login-error"),
    filesToggle: $("files-toggle"), filePanel: $("file-panel"), fileCrumbs: $("file-crumbs"),
    fileClose: $("file-close"), fileList: $("file-list"), fileUploadBtn: $("file-upload-btn"),
    fileRefresh: $("file-refresh"), fileInput: $("file-input"), fileStatus: $("file-status"),
    fileFilter: $("file-filter"), fileKind: $("file-kind"), filePage: $("file-page"), filePrev: $("file-prev"),
    fileNext: $("file-next"), fileMkdir: $("file-mkdir-btn"),
    upProgress: $("file-upload-progress"), upFill: $("up-fill"), upLabel: $("up-label"),
    upCancel: $("up-cancel"),
    fileMenu: $("file-menu"), previewOverlay: $("preview-overlay"), previewTitle: $("preview-title"),
    previewNote: $("preview-note"), previewBody: $("preview-body"),
    previewClose: $("preview-close"), previewCancel: $("preview-cancel"),
    previewSave: $("preview-save"), previewDownload: $("preview-download"),
    shareBtn: $("share-btn"), shareOverlay: $("share-overlay"), shareQr: $("share-qr"),
    shareWrite: $("share-write"), shareTtl: $("share-ttl"), shareUrl: $("share-url"),
    shareCopy: $("share-copy"), shareRegenerate: $("share-regenerate"),
    shareRevoke: $("share-revoke"), shareClose: $("share-close"), shareNote: $("share-note"),
    sessionsBtn: $("sessions-btn"), sessionsOverlay: $("sessions-overlay"),
    sessionsList: $("sessions-list"), sessionsNew: $("sessions-new"), sessionsClose: $("sessions-close"),
    sessionFilter: $("session-filter"), sessionsDetachAll: $("sessions-detach-all"),
    adminBtn: $("admin-btn"), adminOverlay: $("admin-overlay"), adminBody: $("admin-body"),
    adminClose: $("admin-close"), adminNote: $("admin-note"),
    tabMenu: $("tab-menu"), moreBtn: $("more-btn"), moreMenu: $("more-menu"),
    settingsBtn: $("settings-btn"), settingsOverlay: $("settings-overlay"),
    setTheme: $("set-theme"), setFontsize: $("set-fontsize"), setFont: $("set-font"),
    settingsClose: $("settings-close"), settingsDone: $("settings-done"),
    recordBtn: $("record-btn"), replayBtn: $("replay-btn"), replayOverlay: $("replay-overlay"),
    replayHost: $("replay-host"), replayNote: $("replay-note"), replayClose: $("replay-close"),
    zmodemBtn: $("zmodem-btn"), hotkeyList: $("hotkey-list"), hotkeysReset: $("hotkeys-reset"),
    themeGallery: $("theme-gallery"), customThemeName: $("custom-theme-name"),
    customThemeJson: $("custom-theme-json"), customThemeAdd: $("custom-theme-add"),
    searchBtn: $("search-btn"), searchBar: $("search-bar"), searchInput: $("search-input"),
    searchCount: $("search-count"), searchNext: $("search-next"), searchClose: $("search-close"),
    searchCase: $("search-case"), searchWord: $("search-word"), searchRegex: $("search-regex"),
    hotkeyOverlay: $("hotkey-overlay"), hotkeyClose: $("hotkey-close"), hotkeyHelp: $("hotkey-help"),
    renameOverlay: $("rename-overlay"), renameForm: $("rename-form"), renameInput: $("rename-input"),
    renameTitle: $("rename-title"), renameCancel: $("rename-cancel"),
    confirmOverlay: $("confirm-overlay"), confirmTitle: $("confirm-title"),
    confirmMessage: $("confirm-message"), confirmOk: $("confirm-ok"), confirmCancel: $("confirm-cancel"),
    qrOverlay: $("qr-overlay"), qrTitle: $("qr-title"), qrSecret: $("qr-secret"),
    qrImage: $("qr-image"), qrDone: $("qr-done"), qrClose: $("qr-close"),
    toasts: $("toasts"),
    newSessionOverlay: $("new-session-overlay"), newSessionForm: $("new-session-form"),
    newSessionClose: $("new-session-close"), newSessionCancel: $("new-session-cancel"),
    newSessionQuick: $("new-session-quick"), newSessionError: $("new-session-error"),
    nsName: $("ns-name"), nsCommand: $("ns-command"), nsCwd: $("ns-cwd"),
    nsBackend: $("ns-backend"), nsSshBlock: $("ns-ssh-block"),
    nsSshHost: $("ns-ssh-host"), nsSshUser: $("ns-ssh-user"), nsSshPort: $("ns-ssh-port"),
    nsSshIdentity: $("ns-ssh-identity"), nsSshOptions: $("ns-ssh-options"),
  };

  const DEFAULT_KEYS = {
    new_session: "alt+n", close_session: "alt+w", kill_session: "alt+shift+w",
    next_tab: "alt+ArrowRight", prev_tab: "alt+ArrowLeft", toggle_files: "alt+f",
    toggle_sessions: "alt+l", toggle_settings: "alt+s", toggle_share: "alt+h",
    search: "ctrl+shift+f",
  };

  const params = new URLSearchParams(location.search);
  const sharedSession = params.get("session");
  const shareToken = params.get("share");
  const sharedMode = Boolean(sharedSession && shareToken);

  // Application close codes that mean "do not reconnect": the failure is
  // permanent and the reason was already delivered as a control message.
  //
  // This is a *mirror* of `wsctl.core.closecodes.FATAL_CLOSE_CODES`. The
  // browser cannot import Python, so the two are kept equal by
  // `tests/test_closecodes.py`, which parses this literal. Before that check
  // existed the CLI listed 4401 as fatal and this set did not, and nothing
  // complained. 4408 (half-open link) is deliberately absent: reconnecting
  // and replaying the screen is exactly the right response there.
  const FATAL_CLOSE_CODES = new Set([4400, 4401, 4403, 4404, 4409, 4429, 4500]);

  // How many sessions keep a live socket automatically. Opening every session
  // at once (the old behaviour) means N WebSockets and N xterm instances before
  // the user has looked at anything; the rest attach on demand.
  const AUTO_CONNECT_MAX = 8;

  const encoder = new TextEncoder();
  const sessions = new Map();
  // Session ids ordered least-recently-used first, tracking live sockets.
  const liveOrder = [];
  let activeId = null;
  let me = null;
  let menuSid = null;
  let menuOpenedAt = 0;
  let shareSid = null;
  let adminTab = "users";

  const DEFAULT_MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, "DejaVu Sans Mono", monospace';

  const TERM_OPTIONS = {
    cursorBlink: true,
    fontFamily: DEFAULT_MONO,
    fontSize: 14,
    theme: { background: "#0e1117", foreground: "#e6e9ef" },
    scrollback: 10000,
    allowProposedApi: true,
  };

  const THEMES = {
    dark: { background: "#0e1117", foreground: "#e6e9ef", cursor: "#4f9cf9", selectionBackground: "#2a3446" },
    light: { background: "#f6f7f9", foreground: "#1b2029", cursor: "#2f6fd0", selectionBackground: "#cfd8e6" },
    dracula: {
      background: "#282a36", foreground: "#f8f8f2", cursor: "#f8f8f2", selectionBackground: "#44475a",
      black: "#21222c", red: "#ff5555", green: "#50fa7b", yellow: "#f1fa8c",
      blue: "#bd93f9", magenta: "#ff79c6", cyan: "#8be9fd", white: "#f8f8f2",
      brightBlack: "#6272a4", brightRed: "#ff6e6e", brightGreen: "#69ff94", brightYellow: "#ffffa5",
      brightBlue: "#d6acff", brightMagenta: "#ff92df", brightCyan: "#a4ffff", brightWhite: "#ffffff",
    },
    solarized_dark: {
      background: "#002b36", foreground: "#839496", cursor: "#93a1a1", selectionBackground: "#073642",
      black: "#073642", red: "#dc322f", green: "#859900", yellow: "#b58900",
      blue: "#268bd2", magenta: "#d33682", cyan: "#2aa198", white: "#eee8d5",
      brightBlack: "#586e75", brightRed: "#cb4b16", brightGreen: "#586e75", brightYellow: "#657b83",
      brightBlue: "#839496", brightMagenta: "#6c71c4", brightCyan: "#93a1a1", brightWhite: "#fdf6e3",
    },
    solarized_light: {
      background: "#fdf6e3", foreground: "#657b83", cursor: "#586e75", selectionBackground: "#eee8d5",
      black: "#073642", red: "#dc322f", green: "#859900", yellow: "#b58900",
      blue: "#268bd2", magenta: "#d33682", cyan: "#2aa198", white: "#eee8d5",
      brightBlack: "#002b36", brightRed: "#cb4b16", brightGreen: "#586e75", brightYellow: "#657b83",
      brightBlue: "#839496", brightMagenta: "#6c71c4", brightCyan: "#93a1a1", brightWhite: "#fdf6e3",
    },
    nord: {
      background: "#2e3440", foreground: "#d8dee9", cursor: "#d8dee9", selectionBackground: "#434c5e",
      black: "#3b4252", red: "#bf616a", green: "#a3be8c", yellow: "#ebcb8b",
      blue: "#81a1c1", magenta: "#b48ead", cyan: "#88c0d0", white: "#e5e9f0",
      brightBlack: "#4c566a", brightRed: "#bf616a", brightGreen: "#a3be8c", brightYellow: "#ebcb8b",
      brightBlue: "#81a1c1", brightMagenta: "#b48ead", brightCyan: "#8fbcbb", brightWhite: "#eceff4",
    },
    gruvbox: {
      background: "#282828", foreground: "#ebdbb2", cursor: "#ebdbb2", selectionBackground: "#504945",
      black: "#282828", red: "#cc241d", green: "#98971a", yellow: "#d79921",
      blue: "#458588", magenta: "#b16286", cyan: "#689d6a", white: "#a89984",
      brightBlack: "#928374", brightRed: "#fb4934", brightGreen: "#b8bb26", brightYellow: "#fabd2f",
      brightBlue: "#83a598", brightMagenta: "#d3869b", brightCyan: "#8ec07c", brightWhite: "#ebdbb2",
    },
    monokai: {
      background: "#272822", foreground: "#f8f8f2", cursor: "#f8f8f0", selectionBackground: "#49483e",
      black: "#272822", red: "#f92672", green: "#a6e22e", yellow: "#f4bf75",
      blue: "#66d9ef", magenta: "#ae81ff", cyan: "#a1efe4", white: "#f8f8f2",
      brightBlack: "#75715e", brightRed: "#f92672", brightGreen: "#a6e22e", brightYellow: "#f4bf75",
      brightBlue: "#66d9ef", brightMagenta: "#ae81ff", brightCyan: "#a1efe4", brightWhite: "#f9f8f5",
    },
    one_dark: {
      background: "#282c34", foreground: "#abb2bf", cursor: "#528bff", selectionBackground: "#3e4451",
      black: "#282c34", red: "#e06c75", green: "#98c379", yellow: "#e5c07b",
      blue: "#61afef", magenta: "#c678dd", cyan: "#56b6c2", white: "#abb2bf",
      brightBlack: "#5c6370", brightRed: "#e06c75", brightGreen: "#98c379", brightYellow: "#e5c07b",
      brightBlue: "#61afef", brightMagenta: "#c678dd", brightCyan: "#56b6c2", brightWhite: "#ffffff",
    },
    tokyo_night: {
      background: "#1a1b26", foreground: "#c0caf5", cursor: "#c0caf5", selectionBackground: "#33467c",
      black: "#15161e", red: "#f7768e", green: "#9ece6a", yellow: "#e0af68",
      blue: "#7aa2f7", magenta: "#bb9af7", cyan: "#7dcfff", white: "#a9b1d6",
      brightBlack: "#414868", brightRed: "#f7768e", brightGreen: "#9ece6a", brightYellow: "#e0af68",
      brightBlue: "#7aa2f7", brightMagenta: "#bb9af7", brightCyan: "#7dcfff", brightWhite: "#c0caf5",
    },
  };

  const prefs = Object.assign(
    { theme: "auto", termTheme: "dark", fontSize: 14, fontFamily: "", keybindings: {}, customThemes: {} },
    readPrefs(),
  );
  prefs.keybindings = Object.assign({}, DEFAULT_KEYS, prefs.keybindings);
  prefs.customThemes = prefs.customThemes || {};

  function readPrefs() {
    try {
      const parsed = JSON.parse(localStorage.getItem("wsctl-prefs") || "{}");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch { return {}; }
  }

  function resolveTheme() {
    if (prefs.customThemes[prefs.termTheme]) return prefs.customThemes[prefs.termTheme];
    return THEMES[prefs.termTheme] || THEMES.dark;
  }

  function effectivePageTheme() {
    if (prefs.theme === "auto") {
      return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    }
    return prefs.theme;
  }

  function applyPrefs() {
    document.documentElement.dataset.theme = effectivePageTheme();
    for (const s of sessions.values()) {
      s.term.options.fontSize = prefs.fontSize;
      s.term.options.fontFamily = prefs.fontFamily || DEFAULT_MONO;
      s.term.options.theme = resolveTheme();
      try { s.fit.fit(); } catch { /* 隐藏时忽略 */ }
    }
    try { localStorage.setItem("wsctl-prefs", JSON.stringify(prefs)); } catch { /* 忽略 */ }
  }

  window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
    if (prefs.theme === "auto") applyPrefs();
  });

  // -- Toast / 弹窗 -----------------------------------------------------

  function toast(message, kind) {
    const el = document.createElement("div");
    el.className = "toast" + (kind ? " " + kind : "");
    el.textContent = message;
    // Every toast can be dismissed by clicking it; errors stay until then, so
    // a failure the user was not looking at is not swallowed by a timer.
    el.title = "点击关闭";
    el.addEventListener("click", () => el.remove());
    els.toasts.appendChild(el);
    if (kind !== "error") {
      setTimeout(() => {
        if (!el.isConnected) return;
        el.style.transition = "opacity .3s";
        el.style.opacity = "0";
        setTimeout(() => el.remove(), 300);
      }, 3200);
    }
  }

  // Dialogs are singletons: a second call while one is open would overwrite
  // the input the user is typing into (this actually shipped as a rename that
  // silently sent the *old* name back to the server).
  let renamePromptOpen = false;
  let confirmDialogOpen = false;

  function confirmDialog(message, title) {
    if (confirmDialogOpen) return Promise.resolve(false);
    confirmDialogOpen = true;
    return new Promise((resolve) => {
      els.confirmTitle.textContent = title || "确认";
      els.confirmMessage.textContent = message;
      els.confirmOverlay.classList.remove("hidden");
      const done = (value) => {
        confirmDialogOpen = false;
        els.confirmOverlay.classList.add("hidden");
        els.confirmOk.removeEventListener("click", onOk);
        els.confirmCancel.removeEventListener("click", onCancel);
        resolve(value);
      };
      const onOk = () => done(true);
      const onCancel = () => done(false);
      els.confirmOk.addEventListener("click", onOk);
      els.confirmCancel.addEventListener("click", onCancel);
    });
  }

  // Dangerous actions ask for the *name* typed back, the way a hosting panel
  // makes you type the repository name before deleting it. "确定 / 取消" alone
  // is one stray click away from an irreversible delete.
  function confirmByName(message, expected, title) {
    if (renamePromptOpen) return Promise.resolve(false);
    renamePromptOpen = true;
    return new Promise((resolve) => {
      els.renameTitle.textContent = title || "确认";
      els.renameInput.type = "text";
      els.renameInput.value = "";
      els.renameInput.placeholder = `请输入「${expected}」以确认`;
      els.renameOverlay.classList.remove("hidden");
      els.renameInput.focus();
      const submit = els.renameForm.querySelector("button[type=submit]");
      const originalLabel = submit.textContent;
      submit.textContent = "确认删除";
      submit.disabled = true;
      const note = document.createElement("p");
      note.className = "hint";
      note.textContent = message;
      els.renameForm.insertBefore(note, els.renameInput);
      const onInput = () => { submit.disabled = els.renameInput.value.trim() !== expected; };
      els.renameInput.addEventListener("input", onInput);
      const done = (value) => {
        renamePromptOpen = false;
        els.renameOverlay.classList.add("hidden");
        submit.disabled = false;
        submit.textContent = originalLabel;
        els.renameInput.placeholder = "";
        note.remove();
        els.renameInput.removeEventListener("input", onInput);
        els.renameForm.removeEventListener("submit", onSubmit);
        els.renameCancel.removeEventListener("click", onCancel);
        resolve(value);
      };
      const onSubmit = (e) => {
        e.preventDefault();
        if (els.renameInput.value.trim() !== expected) return;
        done(true);
      };
      const onCancel = () => done(false);
      els.renameForm.addEventListener("submit", onSubmit);
      els.renameCancel.addEventListener("click", onCancel);
      onInput();
    });
  }

  function promptDialog(title, value, opts) {
    if (renamePromptOpen) return Promise.resolve(null);
    renamePromptOpen = true;
    const masked = Boolean(opts && opts.masked);
    return new Promise((resolve) => {
      els.renameTitle.textContent = title || "重命名";
      els.renameInput.type = masked ? "password" : "text";
      els.renameInput.value = value || "";
      els.renameOverlay.classList.remove("hidden");
      els.renameInput.focus();
      els.renameInput.select();
      const done = (result) => {
        renamePromptOpen = false;
        els.renameOverlay.classList.add("hidden");
        els.renameForm.removeEventListener("submit", onSubmit);
        els.renameCancel.removeEventListener("click", onCancel);
        resolve(result);
      };
      const onSubmit = (e) => { e.preventDefault(); done(els.renameInput.value.trim() || null); };
      const onCancel = () => done(null);
      els.renameForm.addEventListener("submit", onSubmit);
      els.renameCancel.addEventListener("click", onCancel);
    });
  }

  // 统一弹窗：Esc 关闭最上层，点击遮罩关闭（登录框除外）
  const OVERLAY_CLOSERS = {
    "login-overlay": null,
    "share-overlay": () => els.shareOverlay.classList.add("hidden"),
    "sessions-overlay": () => els.sessionsOverlay.classList.add("hidden"),
    "admin-overlay": () => els.adminOverlay.classList.add("hidden"),
    "settings-overlay": () => els.settingsOverlay.classList.add("hidden"),
    "replay-overlay": () => els.replayClose.click(),
    "rename-overlay": () => els.renameCancel.click(),
    "confirm-overlay": () => els.confirmCancel.click(),
    "qr-overlay": () => closeQrDialog(true),
    "new-session-overlay": () => closeNewSession(),
    "preview-overlay": () => closePreview(),
    "hotkey-overlay": () => toggleHotkeyHelp(false),
  };
  // Esc closes the *topmost* visible modal (they can be nested, e.g. the QR
  // dialog opened from the admin panel).
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const open = [...document.querySelectorAll(".overlay:not(.hidden)")];
    if (!open.length) return;
    const close = OVERLAY_CLOSERS[open[open.length - 1].id];
    if (close) close();
  });
  for (const [id, close] of Object.entries(OVERLAY_CLOSERS)) {
    if (!close) continue;
    const el = $(id);
    if (el) el.addEventListener("mousedown", (e) => { if (e.target === el) close(); });
  }
  // 焦点陷阱：Tab 在弹窗内循环
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const overlays = [...document.querySelectorAll(".overlay:not(.hidden)")];
    const ov = overlays[overlays.length - 1];
    if (!ov) return;
    const list = [...ov.querySelectorAll(
      'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])',
    )].filter((el) => el.offsetParent !== null);
    if (!list.length) return;
    const first = list[0];
    const last = list[list.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }, true);

  // -- 基础 -------------------------------------------------------------

  function wsUrl(s) {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const query = s && s.share ? `?share=${encodeURIComponent(s.share)}` : "";
    return `${proto}//${location.host}/ws${query}`;
  }

  function setConnection(text, cls) {
    els.connection.textContent = text;
    els.connection.className = "status" + (cls ? " " + cls : "");
  }

  // The indicator describes the *visible* terminal. A background tab
  // reconnecting or being suspended must not rewrite it to "空闲"/"连接中".
  function setConnectionFor(s, text, cls) {
    if (s && activeId && s.id !== activeId) return;
    setConnection(text, cls);
  }

  // Derive the indicator from a session's actual state, so switching tabs
  // repaints it correctly regardless of which callbacks happened to fire.
  function describeConnection(s) {
    if (!s) return ["空闲", ""];
    if (s.exited) return ["已结束", "bad"];
    if (s.standby || !s.ws) return ["未连接", ""];
    if (s.ws.readyState === WebSocket.OPEN) return ["已连接", "ok"];
    if (s.ws.readyState === WebSocket.CONNECTING) return ["连接中", ""];
    return ["已断开", s.intentional ? "" : "bad"];
  }

  // How long a request may hang before the UI gives up and says so. Without
  // this a single wedged request left the admin panel on its skeleton forever.
  const API_TIMEOUT_MS = 15000;

  function apiError(message, kind) {
    const err = new Error(message);
    err.kind = kind || "server";
    return err;
  }

  async function api(method, path, body) {
    let res;
    try {
      res = await fetch(path, {
        method,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
        signal: AbortSignal.timeout(API_TIMEOUT_MS),
      });
    } catch (err) {
      if (err && (err.name === "TimeoutError" || err.name === "AbortError")) {
        throw apiError("请求超时，请检查网络或服务状态", "timeout");
      }
      throw apiError("网络错误，无法连接到服务", "network");
    }
    if (res.status === 401) {
      if (!sharedMode) showLogin("请先登录");
      throw new Error("未授权");
    }
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch { /* 忽略 */ }
      throw apiError(detail, res.status === 401 ? "auth" : "server");
    }
    if (res.status === 204) return null;
    return res.json();
  }

  function sendControl(s, obj) {
    if (s.ws && s.ws.readyState === WebSocket.OPEN) s.ws.send(JSON.stringify(obj));
  }
  function sendInput(s, data) {
    if (s.ws && s.ws.readyState === WebSocket.OPEN) s.ws.send(encoder.encode(data));
  }
  function markTab(s, state) {
    s.tabEl.classList.toggle("connected", state === "connected");
    s.tabEl.classList.toggle("exited", state === "exited");
    s.tabEl.classList.toggle("standby", state === "standby");
    s.tabEl.title = state === "standby" ? "未连接（点击打开）" : "";
  }

  // -- 连接池：最多 AUTO_CONNECT_MAX 个会话保持在线 ----------------------

  function noteLive(s) {
    const index = liveOrder.indexOf(s.id);
    if (index >= 0) liveOrder.splice(index, 1);
    liveOrder.push(s.id);
  }

  function forgetLive(id) {
    const index = liveOrder.indexOf(id);
    if (index >= 0) liveOrder.splice(index, 1);
  }

  function suspendTab(s) {
    // Keep the tab (and the server-side session) but drop the socket, so the
    // browser is not holding dozens of terminals open nobody is looking at.
    if (!s || s.exited) return;
    s.intentional = true;
    s.standby = true;
    if (s.heartbeat) { clearInterval(s.heartbeat); s.heartbeat = null; }
    if (s.reconnectTimer) { clearTimeout(s.reconnectTimer); s.reconnectTimer = null; }
    if (s.ws) { try { s.ws.close(); } catch { /* 忽略 */ } s.ws = null; }
    forgetLive(s.id);
    markTab(s, "standby");
    setConnectionFor(s, "空闲", "");
  }

  function ensureConnected(s) {
    if (!s || s.exited) return;
    if (s.ws && s.ws.readyState <= WebSocket.OPEN) { noteLive(s); return; }
    while (liveOrder.length >= AUTO_CONNECT_MAX) {
      const victim = sessions.get(liveOrder[0]);
      if (!victim || victim === s) { forgetLive(liveOrder[0]); continue; }
      suspendTab(victim);
    }
    s.standby = false;
    noteLive(s);
    connect(s);
  }

  function scheduleReconnect(s) {
    if (s.exited || s.reconnectTimer) return;
    s.reconnectTimer = setTimeout(() => { s.reconnectTimer = null; connect(s); }, s.delay);
    s.delay = Math.min(s.delay * 2, 8000);
  }

  function connect(s) {
    s.intentional = false;
    setConnectionFor(s, "连接中", "");
    const ws = new WebSocket(wsUrl(s));
    ws.binaryType = "arraybuffer";
    s.ws = ws;
    s.attached = false;

    ws.onopen = () => {
      s.delay = 500;
      // Do NOT clear the screen here. ``onopen`` only means the socket is up;
      // the replay that justifies clearing arrives afterwards. Clearing
      // eagerly turned every reconnect that then failed -- a slow consumer
      // being dropped again mid-replay is exactly that case -- into a terminal
      // that stayed black with no way back. The screen is wiped on the first
      // replayed byte instead (see handleBinary).
      s.pendingReplayClear = s.everConnected;
      s.everConnected = true;
      s.standby = false;
      // Remember which rename generation this attach belongs to.
      s.attachNameVersion = s.nameVersion || 0;
      noteLive(s);
      markTab(s, "connected");
      setConnectionFor(s, "已连接", "ok");
      sendControl(s, { type: "attach", session: s.id, cols: s.term.cols, rows: s.term.rows, share: s.share || undefined });
      if (s.heartbeat) clearInterval(s.heartbeat);
      s.heartbeat = setInterval(() => sendControl(s, { type: "ping" }), 25000);
    };

    ws.onmessage = (event) => {
      if (typeof event.data === "string") {
        let msg;
        try { msg = JSON.parse(event.data); } catch { return; }
        handleControl(s, msg);
      } else {
        const bytes = new Uint8Array(event.data);
        // The replay is arriving: *now* the old screen can go. Between the
        // socket opening and here the previous screen is left intact, so a
        // reconnect that never completes still shows what was there.
        if (s.pendingReplayClear) {
          s.pendingReplayClear = false;
          try { s.term.reset(); } catch { /* 忽略 */ }
        }
        if (s.sentry) {
          try { s.sentry.consume(bytes); } catch { s.term.write(bytes); }
          // Any traffic counts as transfer progress; re-arm the watchdog that
          // releases the input lock if a transfer dies without `session_end`.
          if (s.zmodemActive) armZmodemWatchdog(s);
        } else s.term.write(bytes);
      }
    };

    ws.onclose = (event) => {
      if (s.heartbeat) { clearInterval(s.heartbeat); s.heartbeat = null; }
      forgetLive(s.id);
      if (event.code === 4401) {
        setConnectionFor(s, "未授权", "bad");
        if (!sharedMode) showLogin("请先登录");
        return;
      }
      if (FATAL_CLOSE_CODES.has(event.code)) {
        // Permanent failure (session gone, forbidden, limit): the server already
        // explained it over the control channel — do not reconnect in a loop.
        s.exited = true;
        markTab(s, "exited");
        setConnectionFor(s, "已结束", "bad");
        if (event.code === 4404) {
          toast("会话不存在或已结束", "error");
          if (sessions.has(s.id)) detachTab(s.id);
        } else if (event.code === 4403) {
          toast("无权访问该会话", "error");
        }
        return;
      }
      setConnectionFor(s, s.intentional ? "空闲" : "已断开", s.intentional ? "" : "bad");
      if (!s.intentional && !s.exited) scheduleReconnect(s);
    };
    ws.onerror = () => setConnectionFor(s, "连接错误", "bad");
  }

  function handleControl(s, msg) {
    switch (msg.type) {
      case "attached":
        s.attached = true;
        // ``attached`` is queued *after* the scrollback replay. If the flag is
        // still set here the replay was empty (an idle session), and the old
        // screen is stale rather than current -- clear it now instead of
        // letting the next real output wipe a still-accurate display.
        if (s.pendingReplayClear) {
          s.pendingReplayClear = false;
          try { s.term.reset(); } catch { /* 忽略 */ }
        }
        s.writable = msg.writable !== false;
        // `attached` echoes the name the session had when this socket attached.
        // A rename issued after that must win, otherwise the tab silently
        // reverts to the old label (and looks like the rename was lost).
        if ((s.nameVersion || 0) === (s.attachNameVersion || 0)) {
          s.name = msg.name;
          s.tabEl.querySelector(".label").textContent = msg.name;
        }
        if (s.writable === false && !s.tabEl.querySelector(".ro")) {
          const badge = document.createElement("span");
          badge.className = "readonly-badge ro";
          badge.textContent = "只读";
          s.tabEl.appendChild(badge);
        }
        break;
      case "exit":
        // The only control message that belongs on screen: the session itself
        // is over, so this line is part of its story and is replayed with the
        // scrollback on reconnect.
        s.exited = true; markTab(s, "exited");
        s.term.write(`\r\n\x1b[33m[wsctl] 会话已退出（退出码 ${msg.code}）\x1b[0m\r\n`);
        break;
      case "renamed":
        // A rename issued over HTTP (another tab, or the CLI) must reach the
        // sockets already attached. Without this two clients on one session
        // kept showing different names until each happened to reconnect.
        s.name = msg.name;
        s.nameVersion = (s.nameVersion || 0) + 1;
        s.tabEl.querySelector(".label").textContent = msg.name;
        break;
      case "notice":
        toast(msg.msg || "提示", msg.level === "error" ? "error" : "");
        break;
      case "evicted":
        // A notice, not terminal output: writing it into the buffer would make
        // it show up again on every reconnect replay. ``backpressure`` used to
        // arrive as nothing at all -- the socket simply closed "normally".
        toast(
          msg.msg || (msg.reason === "backpressure"
            ? "输出过快，连接已释放（会话仍在运行，重连即可恢复）"
            : "连接已被释放（会话仍在运行）"),
          "error",
        );
        s.standby = true;
        markTab(s, "standby");
        setConnectionFor(s, "未连接", "");
        break;
      case "error":
        // Terminal output is for what the *shell* printed. An operational
        // notice written here used to be replayed to every reconnect as if the
        // program had said it -- and it scrolled the prompt away mid-command.
        toast(msg.msg || "会话错误", "error");
        if (!s.attached) setConnectionFor(s, msg.msg || "连接失败", "bad");
        break;
      default: break;
    }
  }

  function createTab(id, name, opts) {
    const options = opts || {};
    const pane = document.createElement("div");
    pane.className = "term-pane";
    els.wrap.appendChild(pane);

    const term = new Terminal(Object.assign({}, TERM_OPTIONS, {
      fontSize: prefs.fontSize,
      fontFamily: prefs.fontFamily || DEFAULT_MONO,
      theme: resolveTheme(),
    }));
    const fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    if (window.WebLinksAddon) term.loadAddon(new WebLinksAddon.WebLinksAddon());
    if (window.ImageAddon) term.loadAddon(new ImageAddon.ImageAddon());
    term.open(pane);

    const tabEl = document.createElement("div");
    tabEl.className = "tab";
    tabEl.innerHTML = '<span class="dot"></span><span class="label"></span><span class="close">\u00d7</span>';
    tabEl.querySelector(".label").textContent = name;
    els.tabs.appendChild(tabEl);

    const s = {
      id, name, term, fit, pane, tabEl,
      ws: null, heartbeat: null, reconnectTimer: null, delay: 500,
      intentional: false, exited: false, standby: false, attached: false,
      pendingReplayClear: false,
      nameVersion: 0, attachNameVersion: 0,
      share: options.share || null,
      writable: options.writable !== false,
      recording: Boolean(options.recording),
      sentry: null, zmodemActive: false, zmodemWatchdog: null, everConnected: false,
      searchAddon: null, searchHits: [], searchPos: -1,
      autoConnect: options.autoConnect !== false,
    };
    sessions.set(id, s);

    tabEl.addEventListener("click", (e) => {
      if (e.target.classList.contains("close")) return;
      activateTab(id);
    });
    tabEl.addEventListener("dblclick", async (e) => {
      if (e.target.classList.contains("close")) return;
      e.stopPropagation();
      const name = await promptDialog("重命名会话", s.name);
      if (!name || name === s.name) return;
      // Mark the rename generation *before* the round trip so a concurrent
      // `attached` from a reconnect cannot clobber the result.
      s.nameVersion = (s.nameVersion || 0) + 1;
      const version = s.nameVersion;
      try {
        const info = await api("PATCH", `/api/sessions/${id}`, { name });
        if (version !== s.nameVersion) return;  // a newer rename won
        s.name = info.name;
        tabEl.querySelector(".label").textContent = info.name;
      } catch (err) { toast(String(err.message || err), "error"); }
    });
    tabEl.querySelector(".close").addEventListener("click", (e) => { e.stopPropagation(); detachTab(id); });
    tabEl.addEventListener("contextmenu", (e) => { e.preventDefault(); openTabMenu(e.clientX, e.clientY, id); });
    let pressTimer = null;
    const cancelPress = () => { if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; } };
    tabEl.addEventListener("touchstart", (e) => {
      const touch = e.touches[0];
      cancelPress();
      pressTimer = setTimeout(() => { pressTimer = null; openTabMenu(touch.clientX, touch.clientY, id); }, 500);
    }, { passive: true });
    tabEl.addEventListener("touchend", cancelPress);
    tabEl.addEventListener("touchmove", cancelPress);
    tabEl.addEventListener("touchcancel", cancelPress);

    term.onData((data) => {
      if (s.writable === false) return;
      if (s.zmodemActive) return;
      sendInput(s, data);
    });
    term.onResize(({ cols, rows }) => sendControl(s, { type: "resize", cols, rows }));

    // 右键：有选区则复制，否则粘贴剪贴板（终端常见习惯）
    pane.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      if (s.writable === false) return;
      const sel = s.term.getSelection && s.term.getSelection();
      if (sel) { navigator.clipboard.writeText(sel).then(() => toast("已复制", "ok")).catch(() => {}); return; }
      navigator.clipboard.readText().then((text) => { if (text) sendInput(s, text); }).catch(() => {});
    });

    if (s.autoConnect) ensureConnected(s);
    else markTab(s, "standby");
    // `activate: false` keeps bulk creation (session restore) from cycling the
    // LRU: each created tab would otherwise come online and evict the last.
    if (options.activate !== false) activateTab(id);
    return s;
  }

  function activateTab(id) {
    activeId = id;
    for (const s of sessions.values()) {
      const on = s.id === id;
      s.pane.classList.toggle("active", on);
      s.tabEl.classList.toggle("active", on);
    }
    const s = sessions.get(id);
    if (!s) return;
    els.recordBtn.classList.toggle("rec-on", Boolean(s.recording));
    // Opening a tab is the user saying "show me this one": bring it online.
    ensureConnected(s);
    // `connect()` may have skipped its own status write (it ran before
    // `activeId` pointed here), so repaint from the resulting state.
    setConnection(...describeConnection(s));
    requestAnimationFrame(() => {
      try { s.fit.fit(); } catch { /* 隐藏时忽略 */ }
      s.term.focus();
      sendControl(s, { type: "resize", cols: s.term.cols, rows: s.term.rows });
    });
  }

  function detachTab(id) {
    const s = sessions.get(id);
    if (!s) return;
    s.intentional = true;
    if (s.heartbeat) clearInterval(s.heartbeat);
    if (s.reconnectTimer) clearTimeout(s.reconnectTimer);
    if (s.ws) s.ws.close();
    forgetLive(id);
    s.term.dispose();
    s.pane.remove();
    s.tabEl.remove();
    sessions.delete(id);
    if (activeId === id) {
      const next = sessions.keys().next();
      if (!next.done) activateTab(next.value);
      else activeId = null;
    }
  }

  async function killTab(id) {
    const s = sessions.get(id);
    const name = s ? s.name : id;
    const ok = await confirmDialog(`确定终止会话「${name}」？该 shell 进程会被结束。`, "终止会话");
    if (!ok) return;
    detachTab(id);
    try { await api("DELETE", `/api/sessions/${id}`); toast("会话已终止", "ok"); }
    catch { /* 已不存在 */ }
    if (!els.sessionsOverlay.classList.contains("hidden")) refreshSessions();
  }

  function openTabMenu(x, y, id) {
    menuSid = id; menuOpenedAt = Date.now();
    els.tabMenu.style.left = `${x}px`;
    els.tabMenu.style.top = `${y}px`;
    els.tabMenu.classList.remove("hidden");
  }
  function closeTabMenu() { els.tabMenu.classList.add("hidden"); menuSid = null; }
  els.tabMenu.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const id = menuSid;
      closeTabMenu();
      if (!id) return;
      if (btn.dataset.action === "kill") killTab(id);
      else detachTab(id);
    });
  });
  document.addEventListener("click", () => {
    if (Date.now() - menuOpenedAt < 350) return;
    closeTabMenu();
    els.moreMenu.classList.add("hidden");
  });

  // -- 新建会话（可指定名称/命令/后端/SSH） -----------------------------

  function openNewSession() {
    els.newSessionError.classList.add("hidden");
    els.nsName.value = "";
    els.nsCommand.value = "";
    els.nsCwd.value = "";
    els.nsBackend.value = "local";
    els.nsSshHost.value = "";
    els.nsSshUser.value = "";
    els.nsSshPort.value = "";
    els.nsSshIdentity.value = "";
    els.nsSshOptions.value = "";
    els.nsSshBlock.classList.add("hidden");
    els.newSessionOverlay.classList.remove("hidden");
    els.nsName.focus();
  }

  function closeNewSession() {
    els.newSessionOverlay.classList.add("hidden");
    els.newSessionError.classList.add("hidden");
  }

  function buildSessionBody() {
    const backend = els.nsBackend.value;
    const body = {};
    const name = els.nsName.value.trim();
    const command = els.nsCommand.value.trim();
    const cwd = els.nsCwd.value.trim();
    if (name) body.name = name;
    if (cwd) body.cwd = cwd;
    if (backend !== "local") body.backend = backend;
    if (backend === "ssh") {
      const host = els.nsSshHost.value.trim();
      if (!host) throw new Error("SSH 后端需要填写主机名");
      const ssh = { host };
      const user = els.nsSshUser.value.trim();
      const port = els.nsSshPort.value.trim();
      const identity = els.nsSshIdentity.value.trim();
      const options = els.nsSshOptions.value.split("\n").map((l) => l.trim()).filter(Boolean);
      if (user) ssh.user = user;
      if (port) ssh.port = Number(port);
      if (identity) ssh.identity = identity;
      if (options.length) ssh.options = options;
      if (command) ssh.command = command;
      body.ssh = ssh;
      body.backend = "ssh";
    } else if (command) {
      body.command = command;
    }
    return body;
  }

  async function newSession(quick) {
    // `quick` (Alt+N, programmatic) skips the dialog and opens a default shell.
    if (!quick && !sharedMode) {
      openNewSession();
      return null;
    }
    const info = await api("POST", "/api/sessions", {});
    createTab(info.id, info.name || "终端", { recording: info.recording });
    return info;
  }

  els.nsBackend.addEventListener("change", () => {
    els.nsSshBlock.classList.toggle("hidden", els.nsBackend.value !== "ssh");
  });
  els.newSessionClose.addEventListener("click", closeNewSession);
  els.newSessionCancel.addEventListener("click", closeNewSession);
  els.newSessionQuick.addEventListener("click", () => {
    closeNewSession();
    newSession(true).catch((e) => toast(String(e.message || e), "error"));
  });
  els.newSessionForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    let body;
    try { body = buildSessionBody(); }
    catch (err) {
      els.newSessionError.textContent = String(err.message || err);
      els.newSessionError.classList.remove("hidden");
      return;
    }
    try {
      const info = await api("POST", "/api/sessions", body);
      closeNewSession();
      createTab(info.id, info.name || "终端", { recording: info.recording });
    } catch (err) {
      els.newSessionError.textContent = String(err.message || err);
      els.newSessionError.classList.remove("hidden");
    }
  });

  window.addEventListener("resize", () => {
    const s = activeId && sessions.get(activeId);
    if (s) { try { s.fit.fit(); } catch { /* 忽略 */ } }
  });
  window.addEventListener("beforeunload", () => {
    for (const s of sessions.values()) { s.intentional = true; if (s.ws) s.ws.close(); }
  });

  els.newTab.addEventListener("click", () => { if (!sharedMode) openNewSession(); });
  els.logout.addEventListener("click", async () => {
    try { await api("POST", "/api/logout"); } catch { /* 忽略 */ }
    location.reload();
  });

  // -- 移动端更多菜单 ---------------------------------------------------

  const MORE_ITEMS = [
    ["search-btn", "搜索"], ["record-btn", "录制"], ["replay-btn", "回放"],
    ["zmodem-btn", "传输"], ["share-btn", "分享"], ["sessions-btn", "会话"],
    ["admin-btn", "管理"], ["settings-btn", "设置"], ["files-toggle", "文件"], ["logout", "退出"],
  ];
  els.moreBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    els.moreMenu.innerHTML = "";
    for (const [id, label] of MORE_ITEMS) {
      const target = $(id);
      if (!target || target.classList.contains("hidden")) continue;
      const b = document.createElement("button");
      b.textContent = label;
      b.addEventListener("click", () => { els.moreMenu.classList.add("hidden"); target.click(); });
      els.moreMenu.appendChild(b);
    }
    els.moreMenu.style.right = "10px";
    els.moreMenu.style.top = "48px";
    els.moreMenu.classList.toggle("hidden");
  });

  // -- 登录 -------------------------------------------------------------

  function showLogin(message) {
    if (message) { els.loginError.textContent = message; els.loginError.classList.remove("hidden"); }
    els.overlay.classList.remove("hidden");
    $("username").focus();
  }
  function hideLogin() { els.overlay.classList.add("hidden"); els.loginError.classList.add("hidden"); }

  els.loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: $("username").value,
          password: $("password").value,
          totp: $("totp").value || null,
        }),
      });
      if (!res.ok) { showLogin("用户名或密码错误"); return; }
      hideLogin();
      bootstrap();
    } catch { showLogin("网络错误"); }
  });

  async function bootstrap() {
    if (sharedMode) {
      document.body.classList.add("shared");
      hideLogin();
      els.whoami.textContent = "共享（只读）";
      createTab(sharedSession, "共享会话", { share: shareToken, writable: false });
      return;
    }
    try { me = await api("GET", "/api/me"); } catch { return; }
    const roleText = me.role === "admin" ? "管理员" : "用户";
    els.whoami.textContent = `${me.username}（${roleText}）`;
    if (me.role === "admin") els.adminBtn.classList.remove("hidden");
    hideLogin();
    try {
      const list = await api("GET", "/api/sessions");
      if (!list.length) { await newSession(true); return; }
      // Most recently used first: those are the ones that get a live socket.
      // The rest stay in the tab bar as "未连接" and attach when opened, so a
      // user with dozens of sessions does not open dozens of sockets.
      const ordered = list.slice().sort((a, b) => (b.last_active || 0) - (a.last_active || 0));
      ordered.forEach((info, index) => {
        createTab(info.id, info.name, {
          recording: info.recording,
          autoConnect: index < AUTO_CONNECT_MAX,
          activate: false,
        });
      });
      // Show the newest one first (and make sure it is online).
      const newest = ordered[0] && sessions.get(ordered[0].id);
      if (newest) activateTab(newest.id);
    } catch (err) { setConnection(String(err.message || err), "bad"); }
  }

  // -- 分享 -------------------------------------------------------------

  function showShareNote(text) { els.shareNote.textContent = text; els.shareNote.classList.remove("hidden"); }
  function renderShare(sid, token) {
    els.shareUrl.value = `${location.origin}/?session=${sid}&share=${encodeURIComponent(token)}`;
    els.shareQr.src = `/api/sessions/${sid}/qr.svg?origin=${encodeURIComponent(location.origin)}&t=${Date.now()}`;
  }

  async function openShare() {
    const s = activeId && sessions.get(activeId);
    if (!s) { toast("没有活动会话", "error"); return; }
    shareSid = s.id;
    els.shareNote.classList.add("hidden");
    try {
      const existing = await api("GET", `/api/sessions/${s.id}/share`);
      if (existing.shared) {
        els.shareWrite.checked = Boolean(existing.writable);
        renderShare(s.id, existing.token);
        showShareNote("已复用现有分享链接；修改选项后请点“生成新链接”");
      } else {
        const info = await api("POST", `/api/sessions/${s.id}/share`, {
          writable: els.shareWrite.checked,
          ttl: els.shareTtl.value ? Number(els.shareTtl.value) : null,
        });
        renderShare(s.id, info.token);
      }
      els.shareOverlay.classList.remove("hidden");
    } catch (err) { toast(String(err.message || err), "error"); }
  }

  els.shareBtn.addEventListener("click", openShare);
  els.shareClose.addEventListener("click", () => els.shareOverlay.classList.add("hidden"));
  els.shareCopy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(els.shareUrl.value); showShareNote("已复制到剪贴板"); }
    catch { els.shareUrl.select(); document.execCommand("copy"); }
  });
  els.shareRegenerate.addEventListener("click", async () => {
    if (!shareSid) return;
    if (!(await confirmDialog("生成新链接会立即使旧链接失效，是否继续？", "生成新链接"))) return;
    try {
      const info = await api("POST", `/api/sessions/${shareSid}/share`, {
        writable: els.shareWrite.checked,
        ttl: els.shareTtl.value ? Number(els.shareTtl.value) : null,
      });
      renderShare(shareSid, info.token);
      showShareNote("已生成新链接，旧链接已失效");
    } catch (err) { showShareNote(String(err.message || err)); }
  });
  els.shareRevoke.addEventListener("click", async () => {
    if (!shareSid) return;
    try {
      await api("DELETE", `/api/sessions/${shareSid}/share`);
      els.shareOverlay.classList.add("hidden");
      toast("已撤销分享", "ok");
    } catch (err) { showShareNote(String(err.message || err)); }
  });

  // -- 会话列表 ---------------------------------------------------------

  function formatDuration(seconds) {
    seconds = Math.max(0, Math.floor(seconds));
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d) return `${d}天${h}时`;
    if (h) return `${h}时${m}分`;
    if (m) return `${m}分`;
    return `${seconds}秒`;
  }

  // Which half of the session dialog is showing. The finished half used to be
  // unreachable: the rows were written to `term_sessions` and pruned by the
  // retention policy, but never displayed anywhere.
  let sessionTab = "live";

  async function refreshSessions() {
    els.sessionsList.innerHTML = '<li class="sk"></li><li class="sk"></li><li class="sk"></li>';
    if (sessionTab === "history") return refreshSessionHistory();
    let list;
    try { list = await api("GET", "/api/sessions"); }
    catch (err) { toast(String(err.message || err), "error"); return; }
    const filter = (els.sessionFilter.value || "").trim().toLowerCase();
    const now = Date.now() / 1000;
    els.sessionsList.innerHTML = "";
    const shown = list.filter((i) =>
      !filter || i.name.toLowerCase().includes(filter) || i.id.toLowerCase().includes(filter));
    if (!shown.length) { els.sessionsList.innerHTML = '<li class="empty">暂无会话</li>'; return; }
    for (const info of shown) {
      const li = document.createElement("li");
      li.className = "session-row";
      const label = document.createElement("span");
      label.className = "session-name";
      label.textContent = info.name;
      const tags = [
        `${info.clients} 连接`,
        `运行 ${formatDuration(now - info.created_at)}`,
        `空闲 ${formatDuration(now - info.last_active)}`,
      ];
      if (info.backend && info.backend !== "local") tags.push(info.backend);
      if (info.bytes) tags.push(formatSize(info.bytes));
      if (info.shared) tags.push("已分享");
      if (info.recording) tags.push("录制中");
      label.title = tags.join(" · ");
      const meta = document.createElement("span");
      meta.className = "session-meta";
      meta.textContent = tags.join(" · ");
      const openBtn = document.createElement("button");
      openBtn.className = "text-btn";
      openBtn.textContent = sessions.has(info.id) ? "切换" : "打开";
      openBtn.addEventListener("click", () => {
        if (sessions.has(info.id)) activateTab(info.id);
        else createTab(info.id, info.name, { recording: info.recording });
        els.sessionsOverlay.classList.add("hidden");
      });
      const killBtn = document.createElement("button");
      killBtn.className = "text-btn danger";
      killBtn.textContent = "终止";
      killBtn.addEventListener("click", async () => {
        if (sessions.has(info.id)) { await killTab(info.id); }
        else {
          if (!(await confirmDialog(`确定终止会话「${info.name}」？`, "终止会话"))) return;
          try { await api("DELETE", `/api/sessions/${info.id}`); toast("会话已终止", "ok"); } catch { /* 忽略 */ }
          refreshSessions();
        }
      });
      li.append(label, meta, openBtn, killBtn);
      els.sessionsList.appendChild(li);
    }
  }

  async function refreshSessionHistory() {
    let rows;
    try { rows = await api("GET", "/api/sessions/history"); }
    catch (err) { toast(String(err.message || err), "error"); return; }
    const filter = (els.sessionFilter.value || "").trim().toLowerCase();
    els.sessionsList.innerHTML = "";
    const shown = rows.filter((r) =>
      !filter || String(r.name || "").toLowerCase().includes(filter)
        || String(r.id || "").toLowerCase().includes(filter));
    if (!shown.length) { els.sessionsList.innerHTML = '<li class="empty">暂无已结束的会话</li>'; return; }
    const STATUS_LABELS = { killed: "已终止", expired: "已过期", stopped: "已停止", interrupted: "已中断" };
    for (const r of shown) {
      const li = document.createElement("li");
      li.className = "session-row";
      const label = document.createElement("span");
      label.className = "session-name";
      label.textContent = r.name || r.id;
      const meta = document.createElement("span");
      meta.className = "session-meta";
      meta.textContent = [
        STATUS_LABELS[r.status] || r.status,
        `运行 ${formatDuration(r.duration)}`,
        r.backend && r.backend !== "local" ? r.backend : "",
      ].filter(Boolean).join(" · ");
      li.title = `命令：${r.command || (r.argv ? JSON.parse(r.argv).join(" ") : "默认 shell")}`;
      const detail = document.createElement("button");
      detail.className = "text-btn";
      detail.textContent = "详情";
      detail.addEventListener("click", async () => {
        try {
          const info = await api("GET", `/api/sessions/${encodeURIComponent(r.id)}/detail`);
          toast(`${info.name}：${info.status}，运行 ${formatDuration(info.duration)}`, "");
        } catch (err) { toast(String(err.message || err), "error"); }
      });
      const reopen = document.createElement("button");
      reopen.className = "text-btn";
      reopen.textContent = "重新打开";
      // An SSH session needs its structured target (host/user/port/identity)
      // which history does not keep. Reopening it as a local shell would run
      // the remote command on this machine -- so the button says why instead.
      if (r.backend === "ssh") {
        reopen.disabled = true;
        reopen.title = "SSH 会话需在「新建会话」中重新填写目标后打开";
      }
      reopen.addEventListener("click", async () => {
        try {
          // Replay the recorded argv, not "command + backend name". For an SSH
          // session the old shape sent backend=ssh with no ssh block (400),
          // and the tempting fix -- dropping the backend -- would have run the
          // *remote* command on the *local* shell.
          let argv = [];
          try { argv = typeof r.argv === "string" ? JSON.parse(r.argv) : (r.argv || []); }
          catch { argv = []; }
          if (!argv.length) {
            toast("该会话未记录启动命令，无法原样重开", "error");
            return;
          }
          const backend = r.backend === "tmux" ? "tmux" : "local";
          const info = await api("POST", `/api/sessions/${encodeURIComponent(r.id)}/reopen`, {
            argv, name: r.name, cwd: r.cwd || undefined, backend,
          });
          els.sessionsOverlay.classList.add("hidden");
          sessionTab = "live";
          createTab(info.id, info.name || "终端", { recording: info.recording });
        } catch (err) { toast(String(err.message || err), "error"); }
      });
      li.append(label, meta, detail, reopen);
      els.sessionsList.appendChild(li);
    }
  }

  document.querySelectorAll("[data-stab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll("[data-stab]").forEach((t) => t.classList.toggle("active", t === tab));
      sessionTab = tab.dataset.stab;
      els.sessionsDetachAll.classList.toggle("hidden", sessionTab !== "live");
      refreshSessions();
    });
  });

  els.sessionFilter.addEventListener("input", () => refreshSessions());
  els.sessionsDetachAll.addEventListener("click", () => {
    for (const id of Array.from(sessions.keys())) detachTab(id);
    els.sessionsOverlay.classList.add("hidden");
    toast("已断开全部标签（会话仍在运行）", "ok");
  });

  function toggleSessions() {
    const hidden = els.sessionsOverlay.classList.toggle("hidden");
    if (!hidden) refreshSessions();
  }
  els.sessionsBtn.addEventListener("click", toggleSessions);
  els.sessionsClose.addEventListener("click", () => els.sessionsOverlay.classList.add("hidden"));
  els.sessionsNew.addEventListener("click", () => {
    els.sessionsOverlay.classList.add("hidden");
    openNewSession();
  });


  // -- 表格排序 + 分页 --------------------------------------------------
  // Every admin table gets the same affordances: click a header to sort,
  // page through long lists. Users and recordings had neither (only audit had
  // a "load more"), so a hundred-row table was one unsorted wall of text.
  const TABLE_PAGE = 25;
  const tableState = {};

  function sortRows(rows, key, spec) {
    const { field, numeric } = spec[key] || {};
    if (!field) return rows.slice();
    const dir = (spec._dir && spec._dir.key === key && spec._dir.dir === -1) ? -1 : 1;
    return rows.slice().sort((a, b) => {
      const x = a[field]; const y = b[field];
      if (numeric) return ((Number(x) || 0) - (Number(y) || 0)) * dir;
      return String(x ?? "").localeCompare(String(y ?? ""), "zh-Hans-CN") * dir;
    });
  }

  function buildTable(host, id, columns, rows, spec, renderRow) {
    const state = tableState[id] || (tableState[id] = { offset: 0, sort: null });
    const sorted = state.sort ? sortRows(rows, state.sort, spec) : rows.slice();
    const page = sorted.slice(state.offset, state.offset + TABLE_PAGE);

    // Re-render *replaces* the previous table. Appending left a second (third,
    // fourth...) copy stacked underneath on every header click or page turn.
    // Re-render callbacks must be given the *outer* host. Handing them
    // ``container`` instead made ``querySelector(":scope > .table-host")``
    // miss (it looks among the container's children), so each click nested a
    // fresh .table-host inside the old one and left every previous table in
    // place -- exactly the duplication this is meant to prevent.
    const container = host.querySelector(":scope > .table-host") || (() => {
      const div = document.createElement("div");
      div.className = "table-host";
      host.appendChild(div);
      return div;
    })();
    container.replaceChildren();

    const table = document.createElement("table");
    table.className = "admin-table sortable";
    const thead = document.createElement("thead");
    const htr = document.createElement("tr");
    for (const [key, label] of columns) {
      const th = document.createElement("th");
      th.textContent = label + (state.sort === key ? (state.dir === -1 ? " ↓" : " ↑") : " ↕");
      th.title = "点击排序";
      th.addEventListener("click", () => {
        if (state.sort === key) state.dir = state.dir === -1 ? 1 : -1;
        else { state.sort = key; state.dir = 1; }
        buildTable(host, id, columns, rows, spec, renderRow);
      });
      htr.appendChild(th);
    }
    thead.appendChild(htr);
    table.appendChild(thead);
    const tbody = document.createElement("tbody");
    for (const row of page) tbody.appendChild(renderRow(row));
    table.appendChild(tbody);
    container.appendChild(table);

    const bar = document.createElement("div");
    bar.className = "admin-toolbar";
    const info = document.createElement("span");
    info.className = "file-status";
    info.textContent = sorted.length
      ? `${state.offset + 1}-${Math.min(state.offset + TABLE_PAGE, sorted.length)} / ${sorted.length}`
      : "0 项";
    const prev = document.createElement("button");
    prev.className = "text-btn"; prev.textContent = "上一页"; prev.disabled = state.offset <= 0;
    prev.addEventListener("click", () => {
      state.offset = Math.max(0, state.offset - TABLE_PAGE);
      buildTable(host, id, columns, rows, spec, renderRow);
    });
    const next = document.createElement("button");
    next.className = "text-btn"; next.textContent = "下一页";
    next.disabled = state.offset + TABLE_PAGE >= sorted.length;
    next.addEventListener("click", () => {
      state.offset += TABLE_PAGE;
      buildTable(host, id, columns, rows, spec, renderRow);
    });
    bar.append(info, prev, next);
    container.appendChild(bar);
  }

  // -- 管理面板 ---------------------------------------------------------

  function adminNote(text, isError) {
    if (!text) { els.adminNote.classList.add("hidden"); return; }
    els.adminNote.textContent = text;
    els.adminNote.classList.toggle("hidden", false);
    els.adminNote.style.color = isError ? "var(--danger)" : "var(--ok)";
  }

  async function openAdmin() {
    els.adminOverlay.classList.remove("hidden");
    adminNote("");
    await renderAdmin();
  }
  els.adminBtn.addEventListener("click", openAdmin);
  els.adminClose.addEventListener("click", () => els.adminOverlay.classList.add("hidden"));
  document.querySelectorAll(".tab2").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab2").forEach((t) => t.classList.toggle("active", t === tab));
      adminTab = tab.dataset.tab;
      renderAdmin();
    });
  });

  async function renderAdmin() {
    // Rebuilding the whole body used to throw away scroll position and focus,
    // so a filter typed into the audit box vanished the moment anything
    // refreshed. Both are restored across the rebuild.
    const scrollTop = els.adminBody.scrollTop;
    const active = document.activeElement;
    const focusKey = active && active.id ? active.id : null;
    const caret = active && typeof active.selectionStart === "number" ? active.selectionStart : null;
    els.adminBody.innerHTML = "";
    try {
      if (adminTab === "overview") await renderOverview();
      else if (adminTab === "users") await renderUsers();
      else if (adminTab === "audit") await renderAudit();
      else if (adminTab === "config") await renderConfig();
      else await renderRecordings();
    } catch (err) { adminNote(String(err.message || err), true); }
    els.adminBody.scrollTop = scrollTop;
    if (focusKey) {
      const restored = document.getElementById(focusKey);
      if (restored) {
        restored.focus();
        if (caret !== null && typeof restored.setSelectionRange === "function") {
          try { restored.setSelectionRange(caret, caret); } catch { /* 忽略 */ }
        }
      }
    }
  }

  async function renderOverview() {
    // "Is this box healthy?" used to need four separate lookups
    // (/healthz, /metrics, /api/audit, the instances table) and still left
    // the finished sessions invisible.
    const data = await api("GET", "/api/overview");
    const host = document.createElement("div");
    host.className = "overview";

    const stat = document.createElement("div");
    stat.className = "overview-stats";
    const cards = [
      ["运行中会话", `${data.sessions} / ${data.max_sessions}`],
      ["已连接客户端", String(data.clients)],
      ["会话缓冲", formatSize(data.bytes || 0)],
      ["因内存释放的连接", String(data.evicted_clients || 0)],
    ];
    for (const [label, value] of cards) {
      const card = document.createElement("div");
      card.className = "overview-card";
      card.innerHTML = `<div class="oc-value">${esc(value)}</div><div class="oc-label">${esc(label)}</div>`;
      stat.appendChild(card);
    }
    host.appendChild(stat);

    const instTitle = document.createElement("div");
    instTitle.className = "section-title";
    instTitle.textContent = "实例";
    host.appendChild(instTitle);
    const instTable = document.createElement("table");
    instTable.className = "admin-table";
    instTable.innerHTML = "<thead><tr><th>PID</th><th>主机</th><th>心跳</th><th>ID</th></tr></thead>";
    const ib = document.createElement("tbody");
    for (const inst of data.instances || []) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${esc(inst.pid ?? "")}</td><td>${esc(inst.host || "")}</td>` +
        `<td>${formatDuration(inst.age)}前</td><td>${esc(String(inst.id || "").slice(0, 8))}</td>`;
      ib.appendChild(tr);
    }
    instTable.appendChild(ib);
    host.appendChild(instTable);

    const finTitle = document.createElement("div");
    finTitle.className = "section-title";
    finTitle.textContent = "最近结束的会话";
    host.appendChild(finTitle);
    if (!(data.recently_finished || []).length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "暂无";
      host.appendChild(empty);
    } else {
      const finTable = document.createElement("table");
      finTable.className = "admin-table";
      finTable.innerHTML = "<thead><tr><th>名称</th><th>状态</th><th>时长</th></tr></thead>";
      const fb = document.createElement("tbody");
      for (const r of data.recently_finished) {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${esc(r.name || r.id)}</td><td>${esc(r.status || "")}</td>` +
          `<td>${formatDuration(r.duration)}</td>`;
        fb.appendChild(tr);
      }
      finTable.appendChild(fb);
      host.appendChild(finTable);
    }

    const auditTitle = document.createElement("div");
    auditTitle.className = "section-title";
    auditTitle.textContent = "最近事件";
    host.appendChild(auditTitle);
    const auditTable = document.createElement("table");
    auditTable.className = "admin-table";
    auditTable.innerHTML = "<thead><tr><th>时间</th><th>事件</th><th>用户</th><th>IP</th><th>详情</th></tr></thead>";
    const ab = document.createElement("tbody");
    for (const r of data.recent_audit || []) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${new Date(r.ts * 1000).toLocaleString()}</td><td>${esc(r.event)}</td>` +
        `<td>${r.user_id ?? ""}</td><td>${esc(r.ip || "")}</td><td>${esc(r.payload || "")}</td>`;
      ab.appendChild(tr);
    }
    auditTable.appendChild(ab);
    host.appendChild(auditTable);

    els.adminBody.appendChild(host);
  }

  function esc(text) {
    return String(text).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);
  }

  async function renderUsers() {
    const users = await api("GET", "/api/users");
    const bar = document.createElement("div");
    bar.className = "admin-toolbar";
    bar.innerHTML = '<input id="nu-name" placeholder="用户名" /><input id="nu-pass" type="password" placeholder="密码" />' +
      '<select id="nu-role"><option value="user">用户</option><option value="admin">管理员</option></select>' +
      '<button id="nu-add" class="text-btn">新增用户</button>';
    els.adminBody.appendChild(bar);
    bar.querySelector("#nu-add").addEventListener("click", async () => {
      const username = bar.querySelector("#nu-name").value.trim();
      const password = bar.querySelector("#nu-pass").value;
      const role = bar.querySelector("#nu-role").value;
      if (!username || !password) { adminNote("用户名和密码不能为空", true); return; }
      try {
        await api("POST", "/api/users", { username, password, role });
        toast("已创建用户", "ok"); adminNote("已创建 " + username, false); renderAdmin();
      } catch (err) { adminNote(String(err.message || err), true); }
    });

    const rows = users;
    buildTable(
      els.adminBody,
      "users",
      [["username", "用户名"], ["role", "角色"], ["disabled", "状态"], ["totp", "两步验证"], ["", "操作"]],
      rows,
      {
        username: { field: "username" },
        role: { field: "role" },
        disabled: { field: "disabled", numeric: true },
        totp: { field: "totp", numeric: true },
      },
      (u) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${esc(u.username)}</td><td>${u.role === "admin" ? "管理员" : "用户"}</td>` +
          `<td>${u.disabled ? "已禁用" : "正常"}</td><td>${u.totp ? "已启用" : "未启用"}</td>`;
        const td = document.createElement("td");
        td.className = "actions";
        const mk = (label, fn, cls) => {
          const b = document.createElement("button");
          b.className = "text-btn" + (cls ? " " + cls : "");
          b.textContent = label;
          b.addEventListener("click", fn);
          td.appendChild(b);
        };
        mk(u.role === "admin" ? "降为用户" : "升为管理员", async () => {
          try { await api("PATCH", `/api/users/${encodeURIComponent(u.username)}`, { role: u.role === "admin" ? "user" : "admin" }); renderAdmin(); }
          catch (err) { adminNote(String(err.message || err), true); }
        });
        mk(u.disabled ? "启用" : "禁用", async () => {
          try { await api("PATCH", `/api/users/${encodeURIComponent(u.username)}`, { disabled: !u.disabled }); renderAdmin(); }
          catch (err) { adminNote(String(err.message || err), true); }
        });
        mk("重置密码", async () => {
          const pw = await promptDialog(`为 ${u.username} 设置新密码`, "", { masked: true });
          if (!pw) return;
          try { await api("PATCH", `/api/users/${encodeURIComponent(u.username)}`, { password: pw }); adminNote("密码已更新（该用户登录态已失效）", false); }
          catch (err) { adminNote(String(err.message || err), true); }
        });
        mk(u.totp ? "关闭 2FA" : "启用 2FA", async () => {
          try {
            if (u.totp) { await api("DELETE", `/api/users/${encodeURIComponent(u.username)}/totp`); renderAdmin(); }
            else {
              const info = await api("POST", `/api/users/${encodeURIComponent(u.username)}/totp`);
              showQrDialog(u.username, info);
            }
          } catch (err) { adminNote(String(err.message || err), true); }
        });
        mk("删除", async () => {
          // Irreversible: type the name back.
          if (!(await confirmByName(`删除用户「${u.username}」？该用户的会话与登录态会一并清理。`, u.username, "删除用户"))) return;
          try { await api("DELETE", `/api/users/${encodeURIComponent(u.username)}`); renderAdmin(); }
          catch (err) { adminNote(String(err.message || err), true); }
        }, "danger");
        tr.appendChild(td);
        return tr;
      },
    );
  }

  function closeQrDialog(refresh) {
    els.qrOverlay.classList.add("hidden");
    els.qrImage.innerHTML = "";
    if (refresh) renderAdmin();
  }

  function showQrDialog(username, info) {
    els.qrTitle.textContent = `为 ${username} 启用 2FA`;
    els.qrSecret.textContent = info.secret;
    els.qrImage.innerHTML = info.qr_svg;
    els.qrOverlay.classList.remove("hidden");
    els.qrDone.focus();
  }

  els.qrDone.addEventListener("click", () => closeQrDialog(true));
  els.qrClose.addEventListener("click", () => closeQrDialog(true));

  async function renderAudit() {
    const bar = document.createElement("div");
    bar.className = "admin-toolbar";
    bar.innerHTML = '<input id="af-event" placeholder="事件类型" /><input id="af-user" placeholder="用户 ID" />' +
      '<input id="af-ip" placeholder="IP" /><button id="af-go" class="text-btn">查询</button>';
    els.adminBody.appendChild(bar);
    const host = document.createElement("div");
    els.adminBody.appendChild(host);

    const PAGE = 100;
    let offset = 0;
    const query = () => {
      const q = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
      const ev = bar.querySelector("#af-event").value.trim();
      const uid = bar.querySelector("#af-user").value.trim();
      const ip = bar.querySelector("#af-ip").value.trim();
      if (ev) q.set("event", ev);
      if (uid) q.set("user_id", uid);
      if (ip) q.set("ip", ip);
      return q.toString();
    };

    const table = document.createElement("table");
    table.className = "admin-table";
    table.innerHTML = "<thead><tr><th>时间</th><th>事件</th><th>用户</th><th>会话</th><th>IP</th><th>详情</th></tr></thead>";
    const tb = document.createElement("tbody");
    table.appendChild(tb);
    const more = document.createElement("button");
    more.className = "text-btn";
    more.textContent = "加载更多";

    const loadPage = async () => {
      try {
        const rows = await api("GET", `/api/audit?${query()}`);
        if (offset === 0) tb.innerHTML = "";
        if (!rows.length && offset === 0) {
          tb.innerHTML = '<tr><td colspan="6" class="empty">暂无记录</td></tr>';
        }
        for (const r of rows) {
          const tr = document.createElement("tr");
          tr.innerHTML = `<td>${new Date(r.ts * 1000).toLocaleString()}</td><td>${esc(r.event)}</td>` +
            `<td>${r.user_id ?? ""}</td><td>${esc(r.term_session_id || "")}</td><td>${esc(r.ip || "")}</td>` +
            `<td>${esc(r.payload || "")}</td>`;
          tb.appendChild(tr);
        }
        offset += rows.length;
        more.style.display = rows.length === PAGE ? "" : "none";
        host.innerHTML = "";
        host.append(table, more);
      } catch (err) { adminNote(String(err.message || err), true); }
    };
    more.addEventListener("click", loadPage);
    bar.querySelector("#af-go").addEventListener("click", () => { offset = 0; loadPage(); });
    await loadPage();
  }

  async function renderRecordings() {
    const rows = await api("GET", "/api/recordings");
    const host = document.createElement("div");
    els.adminBody.appendChild(host);
    if (!rows.length) { host.innerHTML = '<div class="empty">暂无录制</div>'; return; }
    buildTable(
      host,
      "recordings",
      [["name", "文件"], ["size", "大小"], ["mtime", "时间"], ["", "操作"]],
      rows,
      {
        name: { field: "name" },
        size: { field: "size", numeric: true },
        mtime: { field: "mtime", numeric: true },
      },
      (r) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${esc(r.name)}</td><td>${formatSize(r.size)}</td><td>${new Date(r.mtime * 1000).toLocaleString()}</td>`;
        const td = document.createElement("td");
        td.className = "actions";
        const play = document.createElement("button");
        play.className = "text-btn"; play.textContent = "回放";
        play.addEventListener("click", () => startReplay(`/api/recordings/${encodeURIComponent(r.name)}`));
        const dl = document.createElement("button");
        dl.className = "text-btn"; dl.textContent = "下载";
        dl.addEventListener("click", () => { window.location.href = `/api/recordings/${encodeURIComponent(r.name)}`; });
        const del = document.createElement("button");
        del.className = "text-btn danger"; del.textContent = "删除";
        del.addEventListener("click", async () => {
          // Irreversible: type the name back.
          if (!(await confirmByName(`删除录制「${r.name}」？该操作不可撤销。`, r.name, "删除录制"))) return;
          try { await api("DELETE", `/api/recordings/${encodeURIComponent(r.name)}`); renderAdmin(); }
          catch (err) { adminNote(String(err.message || err), true); }
        });
        td.append(play, dl, del);
        tr.appendChild(td);
        return tr;
      },
    );
  }

  async function renderConfig() {
    const data = await api("GET", "/api/config");
    const host = document.createElement("div");
    els.adminBody.appendChild(host);

    const meta = document.createElement("p");
    meta.className = "hint";
    meta.textContent = `配置文件：${data.config_path}　·　数据目录：${data.data_dir}`;
    host.appendChild(meta);

    const reloadBar = document.createElement("div");
    reloadBar.className = "admin-toolbar";
    const reloadBtn = document.createElement("button");
    reloadBtn.className = "text-btn";
    reloadBtn.textContent = "重载配置";
    const reloadNote = document.createElement("span");
    reloadNote.className = "file-status";
    reloadBtn.addEventListener("click", async () => {
      try {
        const info = await api("POST", "/api/config/reload");
        const changed = info.changed || [];
        const errors = info.errors || [];
        if (errors.length) {
          reloadNote.textContent = "重载有问题：" + errors.join("；");
          reloadNote.style.color = "var(--danger)";
          toast("配置重载存在问题", "error");
        } else {
          reloadNote.textContent = "已重载；变更：" + (changed.join("、") || "无");
          reloadNote.style.color = "var(--ok)";
          toast("配置已重载", "ok");
        }
      } catch (err) {
        reloadNote.textContent = String(err.message || err);
        reloadNote.style.color = "var(--danger)";
      }
    });
    reloadBar.append(reloadBtn, reloadNote);
    host.appendChild(reloadBar);

    const table = document.createElement("table");
    table.className = "config-table";
    table.innerHTML = "<thead><tr><th>配置项</th><th>生效值</th><th>生效方式</th></tr></thead>";
    const tbody = document.createElement("tbody");
    // Three tiers, not two. `new` used to be shown as 热更新, which promised
    // an immediate effect the server never delivered (those settings only
    // reach sessions/recordings/sockets created after the edit).
    const KIND_LABELS = { hot: "立即生效", new: "新建时生效", restart: "需重启" };
    const KIND_TIPS = {
      hot: "保存配置后，下一个请求 / 连接 / 维护周期就用新值，无需任何额外操作。",
      // The tooltip is the whole point of the middle tier: without it the
      // badge reads like a weaker form of "hot" and the user assumes it landed.
      new: "只影响此后新建的会话、录制或连接。已存在的不受影响，想让旧会话用上新值需重建。",
      restart: "需要重启服务才生效（网络、TLS、数据目录、日志、Webhook 等）。",
    };
    for (const field of data.fields || []) {
      const tr = document.createElement("tr");
      const kindLabel = KIND_LABELS[field.kind] || "需重启";
      tr.innerHTML =
        `<td>${esc(field.key)}</td>` +
        `<td class="val">${esc(field.value === "" ? "（空）" : field.value)}</td>` +
        `<td><span class="kind-badge ${field.kind}" title="${esc(KIND_TIPS[field.kind] || KIND_TIPS.restart)}">${kindLabel}</span></td>`;
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    host.appendChild(table);
    if (me && me.role !== "admin") adminNote("配置为只读视图", false);
  }

  // -- 录制 -------------------------------------------------------------

  let replayBlobUrl = null;
  let replayPlayer = null;

  els.recordBtn.addEventListener("click", async () => {
    const s = activeId && sessions.get(activeId);
    if (!s) { toast("没有活动会话", "error"); return; }
    try {
      if (s.recording) {
        await api("POST", `/api/sessions/${s.id}/recording/stop`);
        s.recording = false; els.recordBtn.classList.remove("rec-on");
      } else {
        await api("POST", `/api/sessions/${s.id}/recording/start`, {});
        s.recording = true; els.recordBtn.classList.add("rec-on");
      }
    } catch (err) { toast(String(err.message || err), "error"); }
  });

  async function startReplay(fetchUrl) {
    els.replayHost.innerHTML = "";
    els.replayNote.classList.add("hidden");
    els.replayOverlay.classList.remove("hidden");
    try {
      const res = await fetch(fetchUrl);
      if (!res.ok) {
        els.replayNote.textContent = "没有可回放的录制";
        els.replayNote.classList.remove("hidden");
        return;
      }
      const text = await res.text();
      if (replayBlobUrl) URL.revokeObjectURL(replayBlobUrl);
      replayBlobUrl = URL.createObjectURL(new Blob([text], { type: "application/x-asciicast" }));
      if (window.AsciinemaPlayer) {
        replayPlayer = AsciinemaPlayer.create(replayBlobUrl, els.replayHost, { autoPlay: true, fit: "width", controls: true });
      } else {
        els.replayNote.textContent = "播放器不可用";
        els.replayNote.classList.remove("hidden");
      }
    } catch (err) {
      els.replayNote.textContent = String(err.message || err);
      els.replayNote.classList.remove("hidden");
    }
  }

  els.replayBtn.addEventListener("click", () => {
    const s = activeId && sessions.get(activeId);
    if (!s) return;
    startReplay(`/api/sessions/${s.id}/recording`);
  });

  els.replayClose.addEventListener("click", () => {
    els.replayOverlay.classList.add("hidden");
    els.replayHost.innerHTML = "";
    if (replayPlayer && replayPlayer.dispose) { try { replayPlayer.dispose(); } catch { /* 忽略 */ } }
    replayPlayer = null;
    if (replayBlobUrl) { URL.revokeObjectURL(replayBlobUrl); replayBlobUrl = null; }
  });

  // -- 终端搜索 ---------------------------------------------------------

  const searchOpts = { caseSensitive: false, wholeWord: false, regex: false };

  // All-match highlighting needs real decorations (a single selection can only
  // ever show one hit). That is what `@xterm/addon-search` provides, so search
  // runs through it rather than hand-rolled buffer scanning -- which could
  // count matches but never light them up.
  function hex(color, fallback) {
    // The addon requires `#RRGGBB`; CSS variables here may be rgba().
    return /^#[0-9a-fA-F]{6}$/.test(color || "") ? color : fallback;
  }

  function searchDecorations(theme) {
    return {
      matchBackground: hex(theme.selectionBackground, "#2a3446"),
      matchBorder: hex(theme.cursor, "#4f9cf9"),
      matchOverviewRuler: hex(theme.cursor, "#4f9cf9"),
      activeMatchBackground: hex(theme.cursor, "#4f9cf9"),
      activeMatchBorder: hex(theme.background, "#0e1117"),
      activeMatchColorOverviewRuler: hex(theme.cursor, "#4f9cf9"),
    };
  }

  function ensureSearchAddon(s) {
    if (s.searchAddon) return s.searchAddon;
    if (!window.SearchAddon || !window.SearchAddon.SearchAddon) return null;
    const addon = new window.SearchAddon.SearchAddon();
    try { s.term.loadAddon(addon); } catch { return null; }
    // ``onDidChangeResults`` is an xterm ``IEvent`` -- a *subscribe function*,
    // not a settable callback. Assigning to it overwrites the subscription
    // entry point and the counter never updates again.
    addon.onDidChangeResults((e) => {
      if (activeId !== s.id) return;
      if (!e || e.resultCount <= 0) { els.searchCount.textContent = "0/0"; return; }
      els.searchCount.textContent =
        (e.resultIndex >= 0 ? `${e.resultIndex + 1}/` : "") + `${e.resultCount}`;
    });
    s.searchAddon = addon;
    return addon;
  }

  function runSearch(direction) {
    const s = activeId && sessions.get(activeId);
    if (!s) return;
    const query = els.searchInput.value;
    if (!query) {
      els.searchCount.textContent = "";
      if (s.searchAddon) s.searchAddon.clearDecorations();
      return;
    }
    const addon = ensureSearchAddon(s);
    if (!addon) {
      els.searchCount.textContent = "搜索组件不可用";
      return;
    }
    const options = {
      regex: searchOpts.regex,
      wholeWord: searchOpts.wholeWord,
      caseSensitive: searchOpts.caseSensitive,
      // This is the "highlight every match" the manual scanner could never do.
      decorations: searchDecorations(resolveTheme()),
    };
    let found;
    try {
      found = direction > 0 ? addon.findNext(query, options) : addon.findPrevious(query, options);
    } catch {
      els.searchCount.textContent = "无效模式";
      return;
    }
    if (!found && els.searchCount.textContent === "") els.searchCount.textContent = "0/0";
  }

  function bindSearchToggle(button, key, label) {
    if (!button) return;
    button.setAttribute("aria-pressed", String(searchOpts[key]));
    button.addEventListener("click", () => {
      searchOpts[key] = !searchOpts[key];
      button.setAttribute("aria-pressed", String(searchOpts[key]));
      button.title = `${searchOpts[key] ? "关闭" : "开启"}${label}`;
      const s = activeId && sessions.get(activeId);
      if (s && s.searchAddon) s.searchAddon.clearDecorations();
      if (els.searchInput.value) runSearch(1);
    });
  }
  bindSearchToggle(els.searchCase, "caseSensitive", "区分大小写");
  bindSearchToggle(els.searchWord, "wholeWord", "全词匹配");
  bindSearchToggle(els.searchRegex, "regex", "正则表达式");

  function toggleSearch(show) {
    const visible = show === undefined ? els.searchBar.classList.contains("hidden") : show;
    els.searchBar.classList.toggle("hidden", !visible);
    if (visible) { els.searchInput.focus(); els.searchInput.select(); }
    else {
      const s = activeId && sessions.get(activeId);
      if (s) {
        s.searchHits = []; s.searchPos = -1;
        if (s.searchAddon) s.searchAddon.clearDecorations();
        try { s.term.clearSelection(); } catch { /* 忽略 */ }
      }
    }
  }
  els.searchBtn.addEventListener("click", () => toggleSearch());
  els.searchClose.addEventListener("click", () => toggleSearch(false));
  els.searchNext.addEventListener("click", () => runSearch(1));
  els.searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); runSearch(e.shiftKey ? -1 : 1); }
    else if (e.key === "Escape") { e.preventDefault(); toggleSearch(false); }
  });
  els.searchInput.addEventListener("input", () => {
    const s = activeId && sessions.get(activeId);
    if (s) { s.searchPos = -1; }
    if (els.searchInput.value) runSearch(1);
    else els.searchCount.textContent = "";
  });

  // -- ZMODEM -----------------------------------------------------------

  // While a transfer runs, keystrokes are withheld from the remote (they would
  // corrupt the protocol). If the transfer dies without its end event the
  // terminal would look dead, so an inactivity watchdog force-releases it.
  const ZMODEM_WATCHDOG_MS = 20000;

  function armZmodemWatchdog(s) {
    if (s.zmodemWatchdog) clearTimeout(s.zmodemWatchdog);
    s.zmodemWatchdog = setTimeout(() => {
      s.zmodemWatchdog = null;
      if (s.zmodemActive) {
        s.zmodemActive = false;
        toast("文件传输已中断，终端输入已恢复", "error");
      }
    }, ZMODEM_WATCHDOG_MS);
  }

  function releaseZmodem(s) {
    if (s.zmodemWatchdog) { clearTimeout(s.zmodemWatchdog); s.zmodemWatchdog = null; }
    s.zmodemActive = false;
  }

  function makeSentry(s) {
    if (!window.Zmodem) return null;
    return new Zmodem.Sentry({
      to_terminal: (octets) => s.term.write(new Uint8Array(octets)),
      sender: (octets) => { if (s.ws && s.ws.readyState === WebSocket.OPEN) s.ws.send(new Uint8Array(octets)); },
      on_detect: (detection) => handleZmodem(s, detection),
      on_retract: () => releaseZmodem(s),
    });
  }
  function handleZmodem(s, detection) {
    if (!window.Zmodem) return;
    let zsession;
    try { zsession = detection.confirm(); } catch { return; }
    s.zmodemActive = true;
    armZmodemWatchdog(s);
    const done = () => releaseZmodem(s);
    if (zsession.type === "send") {
      const picker = document.createElement("input");
      picker.type = "file"; picker.multiple = true;
      picker.addEventListener("change", () => {
        if (!picker.files || !picker.files.length) { done(); return; }
        Zmodem.Browser.send_files(zsession, picker.files, {}).then(() => zsession.close()).then(done, done);
      });
      picker.click();
    } else {
      zsession.on("offer", (xfer) => {
        const name = xfer.get_details().name;
        const payload = [];
        xfer.on("input", (chunk) => { payload.push(new Uint8Array(chunk)); armZmodemWatchdog(s); });
        xfer.accept().then(() => Zmodem.Browser.save_to_disk(payload, name));
      });
      zsession.on("session_end", done);
      zsession.start();
    }
  }
  els.zmodemBtn.addEventListener("click", () => {
    const s = activeId && sessions.get(activeId);
    if (!s) return;
    if (s.sentry) { s.sentry = null; releaseZmodem(s); els.zmodemBtn.classList.remove("rec-on"); }
    else { s.sentry = makeSentry(s); if (s.sentry) els.zmodemBtn.classList.add("rec-on"); }
  });

  // -- 快捷键 -----------------------------------------------------------

  function runAction(action) {
    switch (action) {
      case "new_session": newSession(true).catch(() => {}); break;
      case "close_session": if (activeId) detachTab(activeId); break;
      case "kill_session": if (activeId) killTab(activeId); break;
      case "next_tab": case "prev_tab": {
        const ids = Array.from(sessions.keys());
        if (!ids.length) break;
        const idx = Math.max(0, ids.indexOf(activeId));
        const step = action === "next_tab" ? 1 : -1;
        activateTab(ids[(idx + step + ids.length) % ids.length]);
        break;
      }
      case "toggle_files": {
        const hidden = els.filePanel.classList.toggle("hidden");
        if (!hidden) loadFiles(filePath);
        break;
      }
      case "toggle_sessions": toggleSessions(); break;
      case "toggle_settings": els.settingsOverlay.classList.toggle("hidden"); break;
      case "toggle_share": openShare(); break;
      case "search": toggleSearch(); break;
      default: break;
    }
  }

  function comboOf(event) {
    const keys = [];
    if (event.ctrlKey) keys.push("ctrl");
    if (event.altKey) keys.push("alt");
    if (event.shiftKey) keys.push("shift");
    if (event.metaKey) keys.push("meta");
    if (["Control", "Alt", "Shift", "Meta"].includes(event.key)) return null;
    let key = event.key;
    if (key.length === 1) key = key.toLowerCase();
    keys.push(key);
    return keys.join("+");
  }

  document.addEventListener("keydown", (event) => {
    if (sharedMode) return;
    const s = activeId && sessions.get(activeId);
    // 复制/粘贴：Ctrl+Shift+C / Ctrl+Shift+V，以及有选区时的 Ctrl+C
    if (s) {
      const sel = s.term.getSelection && s.term.getSelection();
      if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "c") {
        if (sel) { navigator.clipboard.writeText(sel).then(() => toast("已复制", "ok")).catch(() => {}); }
        event.preventDefault(); return;
      }
      if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "v") {
        navigator.clipboard.readText().then((text) => { if (text) sendInput(s, text); }).catch(() => {});
        event.preventDefault(); return;
      }
      if (event.ctrlKey && !event.shiftKey && !event.altKey && event.key.toLowerCase() === "c" && sel) {
        navigator.clipboard.writeText(sel).then(() => toast("已复制", "ok")).catch(() => {});
        event.preventDefault(); return;
      }
    }
    // `?` opens the shortcut cheatsheet. Deliberately not rebindable: it is
    // the one chord whose whole purpose is telling you what the others are.
    if (event.key === "?" && !event.ctrlKey && !event.altKey && !event.metaKey) {
      event.preventDefault(); event.stopPropagation();
      toggleHotkeyHelp();
      return;
    }
    const combo = comboOf(event);
    if (!combo) return;
    for (const [action, binding] of Object.entries(prefs.keybindings)) {
      if (binding && binding.toLowerCase() === combo.toLowerCase()) {
        event.preventDefault(); event.stopPropagation(); runAction(action); return;
      }
    }
  }, true);

  const HOTKEY_LABELS = {
    new_session: "新建会话", close_session: "关闭标签（保持会话）", kill_session: "终止会话",
    next_tab: "下一个标签", prev_tab: "上一个标签", toggle_files: "文件面板",
    toggle_sessions: "会话列表", toggle_settings: "设置", toggle_share: "分享会话", search: "搜索终端",
  };

  // Discoverability: the bindings existed and were editable, but there was no
  // way to find out what they were without opening Settings and reading a
  // table of inputs.
  function toggleHotkeyHelp(show) {
    // No argument means "flip it". The first version derived `hidden` from
    // `classList.contains("hidden")` and then forced that same value back, so
    // the overlay could never open -- and the browser test caught it.
    const currentlyHidden = els.hotkeyOverlay.classList.contains("hidden");
    const hidden = show === undefined ? !currentlyHidden : !show;
    els.hotkeyOverlay.classList.toggle("hidden", hidden);
    if (hidden) return;
    els.hotkeyHelp.innerHTML = "";
    const fixed = [
      ["?", "打开 / 关闭本帮助"],
      ["Esc", "关闭最上层弹窗或搜索"],
      ["Ctrl+Shift+C", "复制选区"],
      ["Ctrl+Shift+V", "粘贴剪贴板"],
      ["Ctrl+C（有选区时）", "复制选区"],
    ];
    const rows = Object.entries(prefs.keybindings)
      .map(([action, binding]) => [binding || "未绑定", HOTKEY_LABELS[action] || action])
      .concat(fixed);
    for (const [combo, label] of rows) {
      const row = document.createElement("div");
      row.className = "hotkey-row";
      const name = document.createElement("span");
      name.textContent = label;
      const kbd = document.createElement("kbd");
      kbd.textContent = combo;
      // The help sheet is for reading *and* for taking away: click a chord to
      // copy it (someone pastes these into their own notes or a wiki).
      kbd.title = "点击复制";
      kbd.style.cursor = "pointer";
      kbd.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(`${combo}\t${label}`);
          toast(`已复制：${combo}`, "ok");
        } catch { /* 剪贴板不可用时忽略 */ }
      });
      row.append(name, kbd);
      els.hotkeyHelp.appendChild(row);
    }
    const copyAll = document.createElement("button");
    copyAll.className = "text-btn";
    copyAll.textContent = "复制全部";
    copyAll.addEventListener("click", async () => {
      const text = rows.map(([combo, label]) => `${combo}\t${label}`).join("\n");
      try { await navigator.clipboard.writeText(text); toast("已复制全部快捷键", "ok"); }
      catch { /* 剪贴板不可用时忽略 */ }
    });
    els.hotkeyHelp.appendChild(copyAll);
    els.hotkeyClose.focus();
  }
  els.hotkeyClose.addEventListener("click", () => toggleHotkeyHelp(false));

  function renderHotkeys() {
    els.hotkeyList.innerHTML = "";
    for (const action of Object.keys(DEFAULT_KEYS)) {
      const row = document.createElement("div");
      row.className = "hotkey-row";
      const label = document.createElement("span");
      label.textContent = HOTKEY_LABELS[action] || action;
      const input = document.createElement("input");
      input.value = prefs.keybindings[action] || "";
      input.addEventListener("change", () => {
        const value = input.value.trim();
        const clash = Object.entries(prefs.keybindings).find(
          ([other, binding]) => other !== action && binding && value
            && binding.toLowerCase() === value.toLowerCase());
        if (clash) {
          toast(`快捷键与「${HOTKEY_LABELS[clash[0]] || clash[0]}」冲突`, "error");
          input.value = prefs.keybindings[action] || "";
          return;
        }
        prefs.keybindings[action] = value;
        applyPrefs();
      });
      row.append(label, input);
      els.hotkeyList.appendChild(row);
    }
  }
  els.hotkeysReset.addEventListener("click", () => {
    prefs.keybindings = Object.assign({}, DEFAULT_KEYS);
    renderHotkeys(); applyPrefs();
  });
  renderHotkeys();

  function renderThemeGallery() {
    els.themeGallery.innerHTML = "";
    const names = Object.keys(THEMES).concat(Object.keys(prefs.customThemes));
    const order = ["background", "foreground", "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"];
    for (const name of names) {
      const theme = prefs.customThemes[name] || THEMES[name];
      const button = document.createElement("button");
      button.type = "button";
      button.className = "theme-swatch" + (prefs.termTheme === name ? " active" : "");
      const swName = document.createElement("span");
      swName.className = "sw-name";
      swName.textContent = name;
      const colors = document.createElement("span");
      colors.className = "sw-colors";
      for (const key of order.slice(2)) {
        const i = document.createElement("i");
        i.style.background = theme[key] || theme.background || "#000";
        colors.appendChild(i);
      }
      button.style.background = theme.background || "#000";
      button.style.color = theme.foreground || "#fff";
      button.append(swName, colors);
      button.addEventListener("click", () => { prefs.termTheme = name; applyPrefs(); renderThemeGallery(); });
      els.themeGallery.appendChild(button);
    }
  }

  els.customThemeAdd.addEventListener("click", () => {
    const name = els.customThemeName.value.trim();
    if (!name) return;
    try {
      const theme = JSON.parse(els.customThemeJson.value);
      if (!theme || typeof theme !== "object") throw new Error("invalid");
      prefs.customThemes[name] = theme;
      prefs.termTheme = name;
      els.customThemeName.value = ""; els.customThemeJson.value = "";
      applyPrefs(); renderThemeGallery();
    } catch { toast("主题 JSON 无效", "error"); }
  });
  renderThemeGallery();

  els.setTheme.value = prefs.theme;
  els.setFontsize.value = String(prefs.fontSize);
  els.setFont.value = prefs.fontFamily || "";
  els.settingsBtn.addEventListener("click", () => els.settingsOverlay.classList.remove("hidden"));
  els.settingsClose.addEventListener("click", () => els.settingsOverlay.classList.add("hidden"));
  els.settingsDone.addEventListener("click", () => els.settingsOverlay.classList.add("hidden"));
  els.setTheme.addEventListener("change", () => { prefs.theme = els.setTheme.value; applyPrefs(); });
  els.setFontsize.addEventListener("change", () => { prefs.fontSize = Number(els.setFontsize.value); applyPrefs(); });
  els.setFont.addEventListener("change", () => { prefs.fontFamily = els.setFont.value; applyPrefs(); });
  applyPrefs();

  // -- 文件面板 ---------------------------------------------------------
  //
  // The panel used to offer exactly three verbs (list / download / upload) and
  // upload had no progress and no cancel. Everything below completes the
  // file-management surface: organise (mkdir / rename / delete), read in place
  // (preview / edit), find (filter + paging) and see what a long upload is
  // doing.

  const FILE_PAGE_SIZE = 200;
  let filePath = "";
  let fileOffset = 0;
  let menuEntry = null;
  let previewPath = null;
  let uploadXhr = null;

  function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let value = bytes / 1024; let i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
    return `${value.toFixed(1)} ${units[i]}`;
  }

  function renderCrumbs(path) {
    // Clickable crumbs *and* an editable path: clicking is faster for the
    // common case, typing wins when the tree is deep.
    const parts = path ? path.split("/") : [];
    let acc = "";
    let html = '<button data-path="">根目录</button>';
    for (const part of parts) {
      acc = acc ? `${acc}/${part}` : part;
      html += ` / <button data-path="${esc(acc)}">${esc(part)}</button>`;
    }
    els.fileCrumbs.innerHTML = html;
    els.fileCrumbs.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => { fileOffset = 0; loadFiles(btn.dataset.path); });
    });
    els.fileCrumbs.title = path ? `${path}（回车跳转到任意子路径）` : "根目录";
    els.fileCrumbs.onclick = (e) => {
      if (e.target.tagName !== "SPAN" || !e.target.isContentEditable) return;
    };
    els.fileCrumbs.ondblclick = () => {
      els.fileCrumbs.contentEditable = "true";
      els.fileCrumbs.focus();
      document.getSelection().selectAllChildren(els.fileCrumbs);
    };
    els.fileCrumbs.onkeydown = (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        els.fileCrumbs.contentEditable = "false";
        const target = els.fileCrumbs.textContent.trim().replace(/^\/+|\/+$/g, "");
        fileOffset = 0;
        loadFiles(target);
      } else if (e.key === "Escape") {
        els.fileCrumbs.contentEditable = "false";
        renderCrumbs(filePath);
      }
    };
  }

  async function loadFiles(path) {
    filePath = path || "";
    els.fileStatus.textContent = "加载中…";
    els.fileList.innerHTML = '<li class="sk"></li><li class="sk"></li><li class="sk"></li>';
    const filter = (els.fileFilter.value || "").trim();
    const params = new URLSearchParams({
      path: filePath,
      offset: String(fileOffset),
      limit: String(FILE_PAGE_SIZE),
    });
    if (filter) params.set("contains", filter);
    if (els.fileKind && els.fileKind.value) params.set("kind", els.fileKind.value);
    try {
      const data = await api("GET", `/api/files?${params}`);
      renderCrumbs(filePath);
      els.fileList.innerHTML = "";
      const total = data.total ?? data.entries.length;
      const shown = data.entries.length;
      els.filePage.textContent = total > FILE_PAGE_SIZE || fileOffset > 0
        ? `${fileOffset + 1}-${fileOffset + shown} / ${total}`
        : `${total} 项`;
      els.filePrev.disabled = fileOffset <= 0;
      els.fileNext.disabled = fileOffset + shown >= total;
      if (!shown) {
        els.fileList.innerHTML = `<li class="empty">${filter ? "没有匹配的条目" : "空目录"}</li>`;
      }
      for (const entry of data.entries) {
        const li = document.createElement("li");
        li.dataset.name = entry.name;
        li.dataset.type = entry.type;
        const icon = entry.type === "dir" ? "📁" : "📄";
        const meta = entry.type === "dir" ? "" : formatSize(entry.size);
        li.innerHTML = `<span class="ficon">${icon}</span><span class="fname">${esc(entry.name)}</span><span class="fmeta">${meta}</span>`;
        li.addEventListener("click", () => {
          const child = filePath ? `${filePath}/${entry.name}` : entry.name;
          if (entry.type === "dir") { fileOffset = 0; loadFiles(child); }
          else window.location.href = `/api/files/download?path=${encodeURIComponent(child)}`;
        });
        // A directory is a drop target: dragging a file onto a folder puts it
        // *in* that folder, which is what people expect from a file manager.
        if (entry.type === "dir") {
          li.addEventListener("dragover", (e) => { e.preventDefault(); e.stopPropagation(); li.classList.add("dir-over"); });
          li.addEventListener("dragleave", () => li.classList.remove("dir-over"));
          li.addEventListener("drop", (e) => {
            e.preventDefault(); e.stopPropagation();
            li.classList.remove("dir-over");
            const child = filePath ? `${filePath}/${entry.name}` : entry.name;
            uploadFiles(e.dataTransfer.files, child);
          });
        }
        li.addEventListener("contextmenu", (e) => {
          e.preventDefault();
          openFileMenu(e.clientX, e.clientY, entry);
        });
        els.fileList.appendChild(li);
      }
      if (data.truncated) els.fileStatus.textContent = `${total} 项（已过滤）`;
      else if (!filter) els.fileStatus.textContent = `${total} 项`;
      else els.fileStatus.textContent = `${total} 项匹配`;
    } catch (err) { els.fileStatus.textContent = String(err.message || err); }
  }

  // -- 上下文菜单 -------------------------------------------------------
  function openFileMenu(x, y, entry) {
    menuEntry = entry;
    els.fileMenu.style.left = `${x}px`;
    els.fileMenu.style.top = `${y}px`;
    els.fileMenu.classList.remove("hidden");
  }
  function closeFileMenu() { els.fileMenu.classList.add("hidden"); menuEntry = null; }

  function entryPath(entry) {
    return filePath ? `${filePath}/${entry.name}` : entry.name;
  }

  els.fileMenu.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const entry = menuEntry;
      const action = btn.dataset.faction;
      closeFileMenu();
      if (!entry) return;
      const rel = entryPath(entry);
      try {
        if (action === "open") {
          if (entry.type === "dir") { fileOffset = 0; loadFiles(rel); }
          else window.location.href = `/api/files/download?path=${encodeURIComponent(rel)}`;
        } else if (action === "preview") {
          await openPreview(rel);
        } else if (action === "rename") {
          const name = await promptDialog(`重命名「${entry.name}」`, entry.name);
          if (!name || name === entry.name) return;
          await api("POST", "/api/files/rename", { path: rel, name });
          toast("已重命名", "ok");
          loadFiles(filePath);
        } else if (action === "copy") {
          await navigator.clipboard.writeText(rel);
          toast("已复制路径", "ok");
        } else if (action === "delete") {
          // Irreversible and un-recursive: type the name back before it happens.
          const ok = await confirmByName(
            entry.type === "dir"
              ? `删除空目录「${entry.name}」？非空目录无法删除。`
              : `删除文件「${entry.name}」？该操作不可撤销。`,
            entry.name,
            "删除条目",
          );
          if (!ok) return;
          await api("DELETE", `/api/files?path=${encodeURIComponent(filePath)}&name=${encodeURIComponent(entry.name)}`);
          toast("已删除", "ok");
          loadFiles(filePath);
        }
      } catch (err) { toast(String(err.message || err), "error"); }
    });
  });
  document.addEventListener("click", () => closeFileMenu());
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeFileMenu(); });

  // -- 预览 / 编辑 ------------------------------------------------------
  async function openPreview(rel) {
    previewPath = rel;
    els.previewTitle.textContent = rel.split("/").pop();
    els.previewNote.textContent = "读取中…";
    els.previewBody.value = "";
    els.previewOverlay.classList.remove("hidden");
    try {
      const res = await fetch(`/api/files/preview?path=${encodeURIComponent(rel)}`);
      if (!res.ok) {
        let detail = res.statusText;
        try { detail = (await res.json()).detail || detail; } catch { /* 忽略 */ }
        els.previewNote.textContent = `无法预览：${detail}`;
        els.previewSave.disabled = true;
        return;
      }
      const text = await res.text();
      els.previewBody.value = text;
      const encoding = res.headers.get("X-Wsctl-Encoding") || "utf-8";
      els.previewNote.textContent = `编码 ${encoding} · ${formatSize(new Blob([text]).size)} · 修改后点「保存」`;
      els.previewSave.disabled = false;
      els.previewBody.focus();
    } catch (err) {
      els.previewNote.textContent = String(err.message || err);
      els.previewSave.disabled = true;
    }
  }
  function closePreview() {
    els.previewOverlay.classList.add("hidden");
    previewPath = null;
    els.previewBody.value = "";
    els.previewSave.disabled = false;
  }
  els.previewClose.addEventListener("click", closePreview);
  els.previewCancel.addEventListener("click", closePreview);
  els.previewDownload.addEventListener("click", () => {
    if (!previewPath) return;
    window.location.href = `/api/files/download?path=${encodeURIComponent(previewPath)}`;
  });
  els.previewSave.addEventListener("click", async () => {
    if (!previewPath) return;
    try {
      await api("PUT", "/api/files/content", { path: previewPath, content: els.previewBody.value });
      toast("已保存", "ok");
      closePreview();
      loadFiles(filePath);
    } catch (err) { els.previewNote.textContent = String(err.message || err); }
  });

  // -- 上传（进度 + 可取消） -------------------------------------------
  // ``fetch`` has no upload progress. XMLHttpRequest does, and a 100 MB
  // upload with no feedback is indistinguishable from a hang -- so this path
  // deliberately uses the older API.
  function uploadFiles(files, targetPath) {
    const dest = targetPath === undefined ? filePath : targetPath;
    if (!files || !files.length) return;
    const queue = Array.from(files);
    uploadNext(queue, dest, false);
  }

  async function uploadNext(queue, dest, overwrite) {
    if (!queue.length) {
      els.upProgress.classList.add("hidden");
      uploadXhr = null;
      // Reload first, *then* report: `loadFiles` writes its own status line
      // and would immediately overwrite "上传完成". The pre-refactor code had
      // this ordering in a comment and it still got lost in the rewrite.
      await loadFiles(filePath);
      els.fileStatus.textContent = "上传完成";
      toast("上传完成", "ok");
      return;
    }
    const file = queue[0];
    const form = new FormData();
    form.append("path", dest);
    form.append("file", file);
    if (overwrite) form.append("overwrite", "1");

    const xhr = new XMLHttpRequest();
    uploadXhr = xhr;
    els.upProgress.classList.remove("hidden");
    els.upLabel.textContent = `正在上传 ${file.name}…`;
    els.upFill.style.width = "0%";
    xhr.open("POST", "/api/files/upload");
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const pct = Math.round((e.loaded / e.total) * 100);
      els.upFill.style.width = `${pct}%`;
      els.upLabel.textContent = `正在上传 ${file.name}… ${pct}%（${formatSize(e.loaded)} / ${formatSize(e.total)}）`;
    };
    xhr.onerror = () => {
      els.upProgress.classList.add("hidden");
      els.fileStatus.textContent = "上传失败：网络错误";
    };
    xhr.onabort = () => {
      els.upProgress.classList.add("hidden");
      els.fileStatus.textContent = "已取消上传";
      uploadXhr = null;
    };
    xhr.onload = async () => {
      uploadXhr = null;
      if (xhr.status === 409 && !overwrite) {
        // Never silently replace: ask first, then retry with overwrite=1.
        const ok = await confirmDialog(`「${file.name}」已存在，是否覆盖？`, "覆盖文件");
        if (!ok) { queue.shift(); uploadNext(queue, dest, false); return; }
        uploadNext(queue, dest, true);
        return;
      }
      if (xhr.status >= 400) {
        let detail = xhr.statusText;
        try { detail = JSON.parse(xhr.responseText).detail || detail; } catch { /* 忽略 */ }
        els.upProgress.classList.add("hidden");
        els.fileStatus.textContent = `失败：${detail}`;
        return;
      }
      els.upFill.style.width = "100%";
      queue.shift();
      uploadNext(queue, dest, false);
    };
    xhr.send(form);
  }

  els.upCancel.addEventListener("click", () => {
    // Cancelling must also drop the staged sidecar the server may hold open;
    // aborting the request lets its error path unlink it.
    if (uploadXhr) uploadXhr.abort();
  });

  els.fileMkdir.addEventListener("click", async () => {
    const name = await promptDialog("新建目录", "");
    if (!name) return;
    try {
      await api("POST", `/api/files/mkdir?path=${encodeURIComponent(filePath)}`, { name });
      toast("已创建目录", "ok");
      loadFiles(filePath);
    } catch (err) { toast(String(err.message || err), "error"); }
  });

  els.fileFilter.addEventListener("input", () => { fileOffset = 0; loadFiles(filePath); });
  els.fileKind.addEventListener("change", () => { fileOffset = 0; loadFiles(filePath); });
  els.filePrev.addEventListener("click", () => {
    fileOffset = Math.max(0, fileOffset - FILE_PAGE_SIZE);
    loadFiles(filePath);
  });
  els.fileNext.addEventListener("click", () => {
    fileOffset += FILE_PAGE_SIZE;
    loadFiles(filePath);
  });

  els.filesToggle.addEventListener("click", () => {
    const hidden = els.filePanel.classList.toggle("hidden");
    if (!hidden) loadFiles(filePath);
  });
  els.fileClose.addEventListener("click", () => els.filePanel.classList.add("hidden"));
  els.fileRefresh.addEventListener("click", () => loadFiles(filePath));
  els.fileUploadBtn.addEventListener("click", () => els.fileInput.click());
  els.fileInput.addEventListener("change", () => { uploadFiles(els.fileInput.files); els.fileInput.value = ""; });
  els.fileList.addEventListener("dragover", (e) => { e.preventDefault(); els.fileList.classList.add("dragover"); });
  els.fileList.addEventListener("dragleave", () => els.fileList.classList.remove("dragover"));
  els.fileList.addEventListener("drop", (e) => {
    e.preventDefault(); els.fileList.classList.remove("dragover"); uploadFiles(e.dataTransfer.files);
  });

  bootstrap();
})();
