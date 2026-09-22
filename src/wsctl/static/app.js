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
    renameOverlay: $("rename-overlay"), renameForm: $("rename-form"), renameInput: $("rename-input"),
    renameTitle: $("rename-title"), renameCancel: $("rename-cancel"),
    confirmOverlay: $("confirm-overlay"), confirmTitle: $("confirm-title"),
    confirmMessage: $("confirm-message"), confirmOk: $("confirm-ok"), confirmCancel: $("confirm-cancel"),
    qrOverlay: $("qr-overlay"), qrTitle: $("qr-title"), qrSecret: $("qr-secret"),
    qrImage: $("qr-image"), qrDone: $("qr-done"), qrClose: $("qr-close"),
    toasts: $("toasts"),
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
  const FATAL_CLOSE_CODES = new Set([4400, 4403, 4404, 4409, 4500]);

  const encoder = new TextEncoder();
  const sessions = new Map();
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
    els.toasts.appendChild(el);
    setTimeout(() => {
      el.style.transition = "opacity .3s";
      el.style.opacity = "0";
      setTimeout(() => el.remove(), 300);
    }, 3200);
  }

  function confirmDialog(message, title) {
    return new Promise((resolve) => {
      els.confirmTitle.textContent = title || "确认";
      els.confirmMessage.textContent = message;
      els.confirmOverlay.classList.remove("hidden");
      const done = (value) => {
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

  function promptDialog(title, value, opts) {
    const masked = Boolean(opts && opts.masked);
    return new Promise((resolve) => {
      els.renameTitle.textContent = title || "重命名";
      els.renameInput.type = masked ? "password" : "text";
      els.renameInput.value = value || "";
      els.renameOverlay.classList.remove("hidden");
      els.renameInput.focus();
      els.renameInput.select();
      const done = (result) => {
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

  async function api(method, path, body) {
    const res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401) {
      if (!sharedMode) showLogin("请先登录");
      throw new Error("未授权");
    }
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch { /* 忽略 */ }
      throw new Error(detail);
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
  }

  function scheduleReconnect(s) {
    if (s.exited || s.reconnectTimer) return;
    s.reconnectTimer = setTimeout(() => { s.reconnectTimer = null; connect(s); }, s.delay);
    s.delay = Math.min(s.delay * 2, 8000);
  }

  function connect(s) {
    s.intentional = false;
    setConnection("连接中", "");
    const ws = new WebSocket(wsUrl(s));
    ws.binaryType = "arraybuffer";
    s.ws = ws;

    ws.onopen = () => {
      s.delay = 500;
      if (s.everConnected) { try { s.term.reset(); } catch { /* 忽略 */ } }
      s.everConnected = true;
      markTab(s, "connected");
      setConnection("已连接", "ok");
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
        if (s.sentry) { try { s.sentry.consume(bytes); } catch { s.term.write(bytes); } }
        else s.term.write(bytes);
      }
    };

    ws.onclose = (event) => {
      if (s.heartbeat) { clearInterval(s.heartbeat); s.heartbeat = null; }
      if (event.code === 4401) {
        setConnection("未授权", "bad");
        if (!sharedMode) showLogin("请先登录");
        return;
      }
      if (FATAL_CLOSE_CODES.has(event.code)) {
        // Permanent failure (session gone, forbidden, limit): the server already
        // explained it over the control channel — do not reconnect in a loop.
        s.exited = true;
        markTab(s, "exited");
        setConnection("已结束", "bad");
        if (event.code === 4404) {
          toast("会话不存在或已结束", "error");
          if (sessions.has(s.id)) detachTab(s.id);
        } else if (event.code === 4403) {
          toast("无权访问该会话", "error");
        }
        return;
      }
      setConnection(s.intentional ? "空闲" : "已断开", s.intentional ? "" : "bad");
      if (!s.intentional && !s.exited) scheduleReconnect(s);
    };
    ws.onerror = () => setConnection("连接错误", "bad");
  }

  function handleControl(s, msg) {
    switch (msg.type) {
      case "attached":
        s.name = msg.name;
        s.writable = msg.writable !== false;
        s.tabEl.querySelector(".label").textContent = msg.name;
        if (s.writable === false && !s.tabEl.querySelector(".ro")) {
          const badge = document.createElement("span");
          badge.className = "readonly-badge ro";
          badge.textContent = "只读";
          s.tabEl.appendChild(badge);
        }
        break;
      case "exit":
        s.exited = true; markTab(s, "exited");
        s.term.write(`\r\n\x1b[33m[wsctl] 会话已退出（退出码 ${msg.code}）\x1b[0m\r\n`);
        break;
      case "error":
        s.term.write(`\r\n\x1b[31m[wsctl] ${msg.msg}\x1b[0m\r\n`);
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
      intentional: false, exited: false,
      share: options.share || null,
      writable: options.writable !== false,
      recording: Boolean(options.recording),
      sentry: null, zmodemActive: false, everConnected: false,
      searchHits: [], searchPos: -1,
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
      if (!name) return;
      try {
        const info = await api("PATCH", `/api/sessions/${id}`, { name });
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

    connect(s);
    activateTab(id);
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

  async function newSession() {
    const info = await api("POST", "/api/sessions", {});
    createTab(info.id, info.name || "终端", { recording: info.recording });
    return info;
  }

  window.addEventListener("resize", () => {
    const s = activeId && sessions.get(activeId);
    if (s) { try { s.fit.fit(); } catch { /* 忽略 */ } }
  });
  window.addEventListener("beforeunload", () => {
    for (const s of sessions.values()) { s.intentional = true; if (s.ws) s.ws.close(); }
  });

  els.newTab.addEventListener("click", () => newSession().catch((e) => toast(String(e.message || e), "error")));
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
      if (list.length) list.forEach((s) => createTab(s.id, s.name, { recording: s.recording }));
      else await newSession();
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

  async function refreshSessions() {
    els.sessionsList.innerHTML = '<li class="sk"></li><li class="sk"></li><li class="sk"></li>';
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
    newSession().then(() => els.sessionsOverlay.classList.add("hidden"))
      .catch((err) => toast(String(err.message || err), "error"));
  });

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
    els.adminBody.innerHTML = "";
    try {
      if (adminTab === "users") await renderUsers();
      else if (adminTab === "audit") await renderAudit();
      else await renderRecordings();
    } catch (err) { adminNote(String(err.message || err), true); }
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

    const table = document.createElement("table");
    table.className = "admin-table";
    table.innerHTML = "<thead><tr><th>用户名</th><th>角色</th><th>状态</th><th>两步验证</th><th>操作</th></tr></thead>";
    const tbody = document.createElement("tbody");
    for (const u of users) {
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
        if (!(await confirmDialog(`确定删除用户「${u.username}」？`, "删除用户"))) return;
        try { await api("DELETE", `/api/users/${encodeURIComponent(u.username)}`); renderAdmin(); }
        catch (err) { adminNote(String(err.message || err), true); }
      }, "danger");
      tr.appendChild(td);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    els.adminBody.appendChild(table);
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
    const table = document.createElement("table");
    table.className = "admin-table";
    table.innerHTML = "<thead><tr><th>文件</th><th>大小</th><th>时间</th><th>操作</th></tr></thead>";
    const tb = document.createElement("tbody");
    for (const r of rows) {
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
        if (!(await confirmDialog(`确定删除录制「${r.name}」？`, "删除录制"))) return;
        try { await api("DELETE", `/api/recordings/${encodeURIComponent(r.name)}`); renderAdmin(); }
        catch (err) { adminNote(String(err.message || err), true); }
      });
      td.append(play, dl, del);
      tr.appendChild(td);
      tb.appendChild(tr);
    }
    table.appendChild(tb);
    host.appendChild(table);
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

  function runSearch(direction) {
    const s = activeId && sessions.get(activeId);
    if (!s) return;
    const query = els.searchInput.value;
    if (!query) { els.searchCount.textContent = ""; return; }
    const buf = s.term.buffer.active;
    const lower = query.toLowerCase();
    const hits = [];
    for (let i = 0; i < buf.length; i++) {
      const line = buf.getLine(i);
      if (line && line.translateToString(true).toLowerCase().includes(lower)) hits.push(i);
    }
    s.searchHits = hits;
    if (!hits.length) { els.searchCount.textContent = "0/0"; return; }
    if (s.searchPos < 0 || s.searchPos >= hits.length) s.searchPos = direction > 0 ? 0 : hits.length - 1;
    else s.searchPos = (s.searchPos + (direction > 0 ? 1 : -1) + hits.length) % hits.length;
    const line = hits[s.searchPos];
    try { s.term.scrollToLine(line); } catch { /* 忽略 */ }
    const text = buf.getLine(line).translateToString(true);
    const col = text.toLowerCase().indexOf(lower);
    try { s.term.select(col < 0 ? 0 : col, line, query.length); } catch { /* 忽略 */ }
    els.searchCount.textContent = `${s.searchPos + 1}/${hits.length}`;
  }

  function toggleSearch(show) {
    const visible = show === undefined ? els.searchBar.classList.contains("hidden") : show;
    els.searchBar.classList.toggle("hidden", !visible);
    if (visible) { els.searchInput.focus(); els.searchInput.select(); }
    else {
      const s = activeId && sessions.get(activeId);
      if (s) { s.searchHits = []; s.searchPos = -1; try { s.term.clearSelection(); } catch { /* 忽略 */ } }
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

  function makeSentry(s) {
    if (!window.Zmodem) return null;
    return new Zmodem.Sentry({
      to_terminal: (octets) => s.term.write(new Uint8Array(octets)),
      sender: (octets) => { if (s.ws && s.ws.readyState === WebSocket.OPEN) s.ws.send(new Uint8Array(octets)); },
      on_detect: (detection) => handleZmodem(s, detection),
      on_retract: () => {},
    });
  }
  function handleZmodem(s, detection) {
    if (!window.Zmodem) return;
    let zsession;
    try { zsession = detection.confirm(); } catch { return; }
    s.zmodemActive = true;
    const done = () => { s.zmodemActive = false; };
    if (zsession.type === "send") {
      const picker = document.createElement("input");
      picker.type = "file"; picker.multiple = true;
      picker.addEventListener("change", () => {
        Zmodem.Browser.send_files(zsession, picker.files, {}).then(() => zsession.close()).then(done, done);
      });
      picker.click();
    } else {
      zsession.on("offer", (xfer) => {
        const name = xfer.get_details().name;
        const payload = [];
        xfer.on("input", (chunk) => payload.push(new Uint8Array(chunk)));
        xfer.accept().then(() => Zmodem.Browser.save_to_disk(payload, name));
      });
      zsession.on("session_end", done);
      zsession.start();
    }
  }
  els.zmodemBtn.addEventListener("click", () => {
    const s = activeId && sessions.get(activeId);
    if (!s) return;
    if (s.sentry) { s.sentry = null; s.zmodemActive = false; els.zmodemBtn.classList.remove("rec-on"); }
    else { s.sentry = makeSentry(s); if (s.sentry) els.zmodemBtn.classList.add("rec-on"); }
  });

  // -- 快捷键 -----------------------------------------------------------

  function runAction(action) {
    switch (action) {
      case "new_session": newSession().catch(() => {}); break;
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

  let filePath = "";

  function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let value = bytes / 1024; let i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
    return `${value.toFixed(1)} ${units[i]}`;
  }

  function renderCrumbs(path) {
    const parts = path ? path.split("/") : [];
    let acc = "";
    let html = '<button data-path="">根目录</button>';
    for (const part of parts) {
      acc = acc ? `${acc}/${part}` : part;
      html += ` / <button data-path="${esc(acc)}">${esc(part)}</button>`;
    }
    els.fileCrumbs.innerHTML = html;
    els.fileCrumbs.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => loadFiles(btn.dataset.path));
    });
  }

  async function loadFiles(path) {
    filePath = path || "";
    els.fileStatus.textContent = "加载中…";
    els.fileList.innerHTML = '<li class="sk"></li><li class="sk"></li><li class="sk"></li>';
    try {
      const data = await api("GET", `/api/files?path=${encodeURIComponent(filePath)}`);
      renderCrumbs(filePath);
      els.fileList.innerHTML = "";
      if (!data.entries.length) els.fileList.innerHTML = '<li class="empty">空目录</li>';
      for (const entry of data.entries) {
        const li = document.createElement("li");
        const icon = entry.type === "dir" ? "📁" : "📄";
        const meta = entry.type === "dir" ? "" : formatSize(entry.size);
        li.innerHTML = `<span class="ficon">${icon}</span><span class="fname">${esc(entry.name)}</span><span class="fmeta">${meta}</span>`;
        li.addEventListener("click", () => {
          const child = filePath ? `${filePath}/${entry.name}` : entry.name;
          if (entry.type === "dir") loadFiles(child);
          else window.location.href = `/api/files/download?path=${encodeURIComponent(child)}`;
        });
        els.fileList.appendChild(li);
      }
      els.fileStatus.textContent = `${data.entries.length} 项`;
    } catch (err) { els.fileStatus.textContent = String(err.message || err); }
  }

  async function uploadFiles(files) {
    if (!files || !files.length) return;
    try {
      for (const file of files) {
        const form = new FormData();
        form.append("path", filePath);
        form.append("file", file);
        els.fileStatus.textContent = `正在上传 ${file.name}…`;
        const res = await fetch("/api/files/upload", { method: "POST", body: form });
        if (!res.ok) {
          let detail = res.statusText;
          try { detail = (await res.json()).detail || detail; } catch { /* 忽略 */ }
          els.fileStatus.textContent = `失败：${detail}`;
          return;
        }
      }
      els.fileStatus.textContent = "上传完成";
      toast("上传完成", "ok");
      await loadFiles(filePath);
    } catch (err) { els.fileStatus.textContent = `失败：${err.message || err}`; }
  }

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
