"use strict";

(() => {
  const els = {
    tabs: document.getElementById("tabs"),
    newTab: document.getElementById("new-tab"),
    wrap: document.getElementById("terminal-wrap"),
    connection: document.getElementById("connection"),
    whoami: document.getElementById("whoami"),
    logout: document.getElementById("logout"),
    overlay: document.getElementById("login-overlay"),
    loginForm: document.getElementById("login-form"),
    loginError: document.getElementById("login-error"),
    filesToggle: document.getElementById("files-toggle"),
    filePanel: document.getElementById("file-panel"),
    fileCrumbs: document.getElementById("file-crumbs"),
    fileClose: document.getElementById("file-close"),
    fileList: document.getElementById("file-list"),
    fileUploadBtn: document.getElementById("file-upload-btn"),
    fileRefresh: document.getElementById("file-refresh"),
    fileInput: document.getElementById("file-input"),
    fileStatus: document.getElementById("file-status"),
    shareBtn: document.getElementById("share-btn"),
    shareOverlay: document.getElementById("share-overlay"),
    shareQr: document.getElementById("share-qr"),
    shareWrite: document.getElementById("share-write"),
    shareUrl: document.getElementById("share-url"),
    shareCopy: document.getElementById("share-copy"),
    shareRevoke: document.getElementById("share-revoke"),
    shareClose: document.getElementById("share-close"),
    shareNote: document.getElementById("share-note"),
    settingsBtn: document.getElementById("settings-btn"),
    settingsOverlay: document.getElementById("settings-overlay"),
    setTheme: document.getElementById("set-theme"),
    setFontsize: document.getElementById("set-fontsize"),
    settingsClose: document.getElementById("settings-close"),
    recordBtn: document.getElementById("record-btn"),
    replayBtn: document.getElementById("replay-btn"),
    replayOverlay: document.getElementById("replay-overlay"),
    replayHost: document.getElementById("replay-host"),
    replayNote: document.getElementById("replay-note"),
    replayClose: document.getElementById("replay-close"),
    zmodemBtn: document.getElementById("zmodem-btn"),
    hotkeyList: document.getElementById("hotkey-list"),
    hotkeysReset: document.getElementById("hotkeys-reset"),
    themeGallery: document.getElementById("theme-gallery"),
    customThemeName: document.getElementById("custom-theme-name"),
    customThemeJson: document.getElementById("custom-theme-json"),
    customThemeAdd: document.getElementById("custom-theme-add"),
  };

  const DEFAULT_KEYS = {
    new_session: "alt+n",
    close_session: "alt+w",
    next_tab: "alt+ArrowRight",
    prev_tab: "alt+ArrowLeft",
    toggle_files: "alt+f",
    toggle_settings: "alt+s",
    toggle_share: "alt+h",
  };

  const params = new URLSearchParams(location.search);
  const sharedSession = params.get("session");
  const shareToken = params.get("share");
  const sharedMode = Boolean(sharedSession && shareToken);

  const encoder = new TextEncoder();
  const sessions = new Map();
  let activeId = null;
  let me = null;

  const TERM_OPTIONS = {
    cursorBlink: true,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "DejaVu Sans Mono", monospace',
    fontSize: 14,
    theme: { background: "#10131a", foreground: "#d7dae0" },
    scrollback: 10000,
  };

  const THEMES = {
    dark: { background: "#10131a", foreground: "#d7dae0", cursor: "#4f9cf9", selectionBackground: "#2a3446" },
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
    { theme: "dark", termTheme: "dark", fontSize: 14, keybindings: {}, customThemes: {} },
    readPrefs(),
  );
  prefs.keybindings = Object.assign({}, DEFAULT_KEYS, prefs.keybindings);
  prefs.customThemes = prefs.customThemes || {};

  function readPrefs() {
    try {
      const parsed = JSON.parse(localStorage.getItem("wsctl-prefs") || "{}");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
      return {};
    }
  }

  function resolveTheme() {
    if (prefs.customThemes[prefs.termTheme]) return prefs.customThemes[prefs.termTheme];
    return THEMES[prefs.termTheme] || THEMES.dark;
  }

  function applyPrefs() {
    document.documentElement.dataset.theme = prefs.theme;
    for (const s of sessions.values()) {
      s.term.options.fontSize = prefs.fontSize;
      s.term.options.theme = resolveTheme();
      try { s.fit.fit(); } catch { /* hidden */ }
    }
    try {
      localStorage.setItem("wsctl-prefs", JSON.stringify(prefs));
    } catch {
      /* storage unavailable/full: preferences just won't persist */
    }
  }

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
      throw new Error("unauthorized");
    }
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
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
    s.reconnectTimer = setTimeout(() => {
      s.reconnectTimer = null;
      connect(s);
    }, s.delay);
    s.delay = Math.min(s.delay * 2, 8000);
  }

  function connect(s) {
    s.intentional = false;
    setConnection("connecting", "");
    const ws = new WebSocket(wsUrl(s));
    ws.binaryType = "arraybuffer";
    s.ws = ws;

    ws.onopen = () => {
      s.delay = 500;
      if (s.everConnected) {
        // Reconnect: clear the local screen so the server's replay rebuilds it
        // instead of appending to stale content.
        try { s.term.reset(); } catch { /* ignore */ }
      }
      s.everConnected = true;
      markTab(s, "connected");
      setConnection("connected", "ok");
      sendControl(s, {
        type: "attach",
        session: s.id,
        cols: s.term.cols,
        rows: s.term.rows,
        share: s.share || undefined,
      });
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
        if (s.sentry) {
          try {
            s.sentry.consume(bytes);
          } catch {
            s.term.write(bytes);
          }
        } else {
          s.term.write(bytes);
        }
      }
    };

    ws.onclose = (event) => {
      if (s.heartbeat) { clearInterval(s.heartbeat); s.heartbeat = null; }
      if (event.code === 4401 || event.code === 4403) {
        setConnection("unauthorized", "bad");
        if (!sharedMode) showLogin("请先登录");
        return;
      }
      setConnection(s.intentional ? "idle" : "disconnected", s.intentional ? "" : "bad");
      if (!s.intentional && !s.exited) scheduleReconnect(s);
    };

    ws.onerror = () => setConnection("error", "bad");
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
          badge.textContent = "read-only";
          s.tabEl.appendChild(badge);
        }
        break;
      case "exit":
        s.exited = true;
        markTab(s, "exited");
        s.term.write(`\r\n\x1b[33m[wsctl] session exited (code ${msg.code})\x1b[0m\r\n`);
        break;
      case "error":
        s.term.write(`\r\n\x1b[31m[wsctl] ${msg.msg}\x1b[0m\r\n`);
        break;
      default:
        break;
    }
  }

  function createTab(id, name, opts) {
    const options = opts || {};
    const pane = document.createElement("div");
    pane.className = "term-pane";
    els.wrap.appendChild(pane);

    const term = new Terminal(Object.assign({}, TERM_OPTIONS, {
      fontSize: prefs.fontSize,
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
      sentry: null,
      zmodemActive: false,
      everConnected: false,
    };
    sessions.set(id, s);

    tabEl.addEventListener("click", (e) => {
      if (e.target.classList.contains("close")) return;
      activateTab(id);
    });
    tabEl.addEventListener("dblclick", async (e) => {
      if (e.target.classList.contains("close")) return;
      e.stopPropagation();
      const name = window.prompt("Rename session", s.name);
      if (!name) return;
      try {
        const info = await api("PATCH", `/api/sessions/${id}`, { name });
        s.name = info.name;
        tabEl.querySelector(".label").textContent = info.name;
      } catch (err) {
        setConnection(String(err.message || err), "bad");
      }
    });
    tabEl.querySelector(".close").addEventListener("click", (e) => {
      e.stopPropagation();
      closeTab(id);
    });

    term.onData((data) => {
      if (s.writable === false) return;
      if (s.zmodemActive) return;
      sendInput(s, data);
    });
    term.onResize(({ cols, rows }) => sendControl(s, { type: "resize", cols, rows }));

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
      try { s.fit.fit(); } catch { /* hidden */ }
      s.term.focus();
      sendControl(s, { type: "resize", cols: s.term.cols, rows: s.term.rows });
    });
  }

  async function closeTab(id) {
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

    try { await api("DELETE", `/api/sessions/${id}`); } catch { /* already gone */ }

    if (activeId === id) {
      const next = sessions.keys().next();
      if (!next.done) activateTab(next.value);
      else activeId = null;
    }
  }

  async function newSession() {
    const info = await api("POST", "/api/sessions", {});
    createTab(info.id, info.name || "shell", { recording: info.recording });
    return info;
  }

  window.addEventListener("resize", () => {
    const s = activeId && sessions.get(activeId);
    if (s) {
      try { s.fit.fit(); } catch { /* ignore */ }
    }
  });

  window.addEventListener("beforeunload", () => {
    for (const s of sessions.values()) {
      s.intentional = true;
      if (s.ws) s.ws.close();
    }
  });

  els.newTab.addEventListener("click", () => {
    newSession().catch((err) => setConnection(String(err.message || err), "bad"));
  });

  els.logout.addEventListener("click", async () => {
    try { await api("POST", "/api/logout"); } catch { /* ignore */ }
    location.reload();
  });

  function showLogin(message) {
    if (message) {
      els.loginError.textContent = message;
      els.loginError.classList.remove("hidden");
    }
    els.overlay.classList.remove("hidden");
    document.getElementById("username").focus();
  }

  function hideLogin() {
    els.overlay.classList.add("hidden");
    els.loginError.classList.add("hidden");
  }

  els.loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: document.getElementById("username").value,
          password: document.getElementById("password").value,
        }),
      });
      if (!res.ok) { showLogin("用户名或密码错误"); return; }
      hideLogin();
      bootstrap();
    } catch {
      showLogin("网络错误");
    }
  });

  async function bootstrap() {
    if (sharedMode) {
      document.body.classList.add("shared");
      hideLogin();
      els.whoami.textContent = "shared (read-only)";
      createTab(sharedSession, "shared", { share: shareToken, writable: false });
      return;
    }
    try {
      me = await api("GET", "/api/me");
    } catch {
      return;
    }
    els.whoami.textContent = `${me.username} (${me.role})`;
    hideLogin();
    try {
      const list = await api("GET", "/api/sessions");
      if (list.length) {
        list.forEach((s) => createTab(s.id, s.name, { recording: s.recording }));
      } else {
        await newSession();
      }
    } catch (err) {
      setConnection(String(err.message || err), "bad");
    }
  }

  // -- sharing ---------------------------------------------------------

  function showShareNote(text) {
    els.shareNote.textContent = text;
    els.shareNote.classList.remove("hidden");
  }

  async function openShare() {
    const s = activeId && sessions.get(activeId);
    if (!s) {
      setConnection("no active session", "bad");
      return;
    }
    shareSid = s.id;
    try {
      const info = await api("POST", `/api/sessions/${s.id}/share`, {
        writable: els.shareWrite.checked,
      });
      els.shareUrl.value =
        `${location.origin}/?session=${s.id}&share=${encodeURIComponent(info.token)}`;
      els.shareQr.src = `/api/sessions/${s.id}/qr.svg?origin=${encodeURIComponent(location.origin)}`;
      els.shareNote.classList.add("hidden");
      els.shareOverlay.classList.remove("hidden");
    } catch (err) {
      setConnection(String(err.message || err), "bad");
    }
  }

  els.shareBtn.addEventListener("click", openShare);
  els.shareClose.addEventListener("click", () => els.shareOverlay.classList.add("hidden"));
  els.shareCopy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(els.shareUrl.value);
      showShareNote("copied to clipboard");
    } catch {
      els.shareUrl.select();
      document.execCommand("copy");
    }
  });
  els.shareRevoke.addEventListener("click", async () => {
    if (!shareSid) return;
    try {
      await api("DELETE", `/api/sessions/${shareSid}/share`);
      els.shareOverlay.classList.add("hidden");
    } catch (err) {
      showShareNote(String(err.message || err));
    }
  });

  // -- recording -------------------------------------------------------

  let replayBlobUrl = null;
  let replayPlayer = null;
  let shareSid = null;

  els.recordBtn.addEventListener("click", async () => {
    const s = activeId && sessions.get(activeId);
    if (!s) {
      setConnection("no active session", "bad");
      return;
    }
    try {
      if (s.recording) {
        await api("POST", `/api/sessions/${s.id}/recording/stop`);
        s.recording = false;
        els.recordBtn.classList.remove("rec-on");
      } else {
        await api("POST", `/api/sessions/${s.id}/recording/start`, {});
        s.recording = true;
        els.recordBtn.classList.add("rec-on");
      }
    } catch (err) {
      setConnection(String(err.message || err), "bad");
    }
  });

  els.replayBtn.addEventListener("click", async () => {
    const s = activeId && sessions.get(activeId);
    if (!s) return;
    els.replayHost.innerHTML = "";
    els.replayNote.classList.add("hidden");
    els.replayOverlay.classList.remove("hidden");
    try {
      const res = await fetch(`/api/sessions/${s.id}/recording`);
      if (!res.ok) {
        els.replayNote.textContent = "no recording for this session";
        els.replayNote.classList.remove("hidden");
        return;
      }
      const text = await res.text();
      if (replayBlobUrl) URL.revokeObjectURL(replayBlobUrl);
      replayBlobUrl = URL.createObjectURL(new Blob([text], { type: "application/x-asciicast" }));
      if (window.AsciinemaPlayer) {
        replayPlayer = AsciinemaPlayer.create(replayBlobUrl, els.replayHost, {
          autoPlay: true,
          fit: "width",
          controls: true,
        });
      } else {
        els.replayNote.textContent = "player not available";
        els.replayNote.classList.remove("hidden");
      }
    } catch (err) {
      els.replayNote.textContent = String(err.message || err);
      els.replayNote.classList.remove("hidden");
    }
  });

  els.replayClose.addEventListener("click", () => {
    els.replayOverlay.classList.add("hidden");
    els.replayHost.innerHTML = "";
    if (replayPlayer && replayPlayer.dispose) {
      try { replayPlayer.dispose(); } catch { /* ignore */ }
    }
    replayPlayer = null;
    if (replayBlobUrl) {
      URL.revokeObjectURL(replayBlobUrl);
      replayBlobUrl = null;
    }
  });

  // -- zmodem (sz/rz file transfer) ------------------------------------

  function makeSentry(s) {
    if (!window.Zmodem) return null;
    return new Zmodem.Sentry({
      to_terminal: (octets) => s.term.write(new Uint8Array(octets)),
      sender: (octets) => {
        if (s.ws && s.ws.readyState === WebSocket.OPEN) s.ws.send(new Uint8Array(octets));
      },
      on_detect: (detection) => handleZmodem(s, detection),
      on_retract: () => {},
    });
  }

  function handleZmodem(s, detection) {
    if (!window.Zmodem) return;
    let zsession;
    try {
      zsession = detection.confirm();
    } catch {
      return;
    }
    s.zmodemActive = true;
    const done = () => {
      s.zmodemActive = false;
    };
    if (zsession.type === "send") {
      // we send files to the remote (rz)
      const picker = document.createElement("input");
      picker.type = "file";
      picker.multiple = true;
      picker.addEventListener("change", () => {
        Zmodem.Browser.send_files(zsession, picker.files, {})
          .then(() => zsession.close())
          .then(done, done);
      });
      picker.click();
    } else {
      // the remote sends files to us (sz)
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
    if (s.sentry) {
      s.sentry = null;
      s.zmodemActive = false;
      els.zmodemBtn.classList.remove("rec-on");
    } else {
      s.sentry = makeSentry(s);
      if (s.sentry) els.zmodemBtn.classList.add("rec-on");
    }
  });

  // -- preferences -----------------------------------------------------

  function runAction(action) {
    switch (action) {
      case "new_session":
        newSession().catch(() => {});
        break;
      case "close_session":
        if (activeId) closeTab(activeId);
        break;
      case "next_tab":
      case "prev_tab": {
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
      case "toggle_settings":
        els.settingsOverlay.classList.toggle("hidden");
        break;
      case "toggle_share":
        openShare();
        break;
      default:
        break;
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

  document.addEventListener(
    "keydown",
    (event) => {
      if (sharedMode) return;
      const combo = comboOf(event);
      if (!combo) return;
      for (const [action, binding] of Object.entries(prefs.keybindings)) {
        if (binding && binding.toLowerCase() === combo.toLowerCase()) {
          event.preventDefault();
          event.stopPropagation();
          runAction(action);
          return;
        }
      }
    },
    true,
  );

  const HOTKEY_LABELS = {
    new_session: "new session",
    close_session: "close session",
    next_tab: "next tab",
    prev_tab: "previous tab",
    toggle_files: "toggle files",
    toggle_settings: "toggle settings",
    toggle_share: "share session",
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
        prefs.keybindings[action] = input.value.trim();
        applyPrefs();
      });
      row.appendChild(label);
      row.appendChild(input);
      els.hotkeyList.appendChild(row);
    }
  }

  els.hotkeysReset.addEventListener("click", () => {
    prefs.keybindings = Object.assign({}, DEFAULT_KEYS);
    renderHotkeys();
    applyPrefs();
  });
  renderHotkeys();

  function renderThemeGallery() {
    els.themeGallery.innerHTML = "";
    const names = Object.keys(THEMES).concat(Object.keys(prefs.customThemes));
    for (const name of names) {
      const theme = prefs.customThemes[name] || THEMES[name];
      const button = document.createElement("button");
      button.type = "button";
      button.className = "theme-swatch" + (prefs.termTheme === name ? " active" : "");
      button.style.background = theme.background || "#000";
      button.style.color = theme.foreground || "#fff";
      button.textContent = name;
      button.addEventListener("click", () => {
        prefs.termTheme = name;
        applyPrefs();
        renderThemeGallery();
      });
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
      els.customThemeName.value = "";
      els.customThemeJson.value = "";
      applyPrefs();
      renderThemeGallery();
    } catch {
      setConnection("invalid theme JSON", "bad");
    }
  });
  renderThemeGallery();

  els.setTheme.value = prefs.theme;
  els.setFontsize.value = String(prefs.fontSize);
  els.settingsBtn.addEventListener("click", () => els.settingsOverlay.classList.remove("hidden"));
  els.settingsClose.addEventListener("click", () =>
    els.settingsOverlay.classList.add("hidden"));
  els.setTheme.addEventListener("change", () => {
    prefs.theme = els.setTheme.value;
    applyPrefs();
  });
  els.setFontsize.addEventListener("change", () => {
    prefs.fontSize = Number(els.setFontsize.value);
    applyPrefs();
  });
  applyPrefs();

  // -- file panel ------------------------------------------------------

  let filePath = "";

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);
  }

  function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let value = bytes / 1024;
    let i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
    return `${value.toFixed(1)} ${units[i]}`;
  }

  function renderCrumbs(path) {
    const parts = path ? path.split("/") : [];
    let acc = "";
    let html = '<button data-path="">root</button>';
    for (const part of parts) {
      acc = acc ? `${acc}/${part}` : part;
      html += ` / <button data-path="${escapeHtml(acc)}">${escapeHtml(part)}</button>`;
    }
    els.fileCrumbs.innerHTML = html;
    els.fileCrumbs.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => loadFiles(btn.dataset.path));
    });
  }

  async function loadFiles(path) {
    filePath = path || "";
    els.fileStatus.textContent = "loading...";
    try {
      const data = await api("GET", `/api/files?path=${encodeURIComponent(filePath)}`);
      renderCrumbs(filePath);
      els.fileList.innerHTML = "";
      if (!data.entries.length) {
        els.fileList.innerHTML = '<li class="fmeta">empty</li>';
      }
      for (const entry of data.entries) {
        const li = document.createElement("li");
        const icon = entry.type === "dir" ? "📁" : "📄";
        const meta = entry.type === "dir" ? "" : formatSize(entry.size);
        li.innerHTML =
          `<span class="ficon">${icon}</span>` +
          `<span class="fname">${escapeHtml(entry.name)}</span>` +
          `<span class="fmeta">${meta}</span>`;
        li.addEventListener("click", () => {
          const child = filePath ? `${filePath}/${entry.name}` : entry.name;
          if (entry.type === "dir") loadFiles(child);
          else window.location.href = `/api/files/download?path=${encodeURIComponent(child)}`;
        });
        els.fileList.appendChild(li);
      }
      els.fileStatus.textContent = `${data.entries.length} items`;
    } catch (err) {
      els.fileStatus.textContent = String(err.message || err);
    }
  }

  async function uploadFiles(files) {
    if (!files || !files.length) return;
    try {
      for (const file of files) {
        const form = new FormData();
        form.append("path", filePath);
        form.append("file", file);
        els.fileStatus.textContent = `uploading ${file.name}...`;
        const res = await fetch("/api/files/upload", { method: "POST", body: form });
        if (!res.ok) {
          let detail = res.statusText;
          try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
          els.fileStatus.textContent = `failed: ${detail}`;
          return;
        }
      }
      els.fileStatus.textContent = "upload complete";
      await loadFiles(filePath);
    } catch (err) {
      els.fileStatus.textContent = `failed: ${err.message || err}`;
    }
  }

  els.filesToggle.addEventListener("click", () => {
    const hidden = els.filePanel.classList.toggle("hidden");
    if (!hidden) loadFiles(filePath);
  });
  els.fileClose.addEventListener("click", () => els.filePanel.classList.add("hidden"));
  els.fileRefresh.addEventListener("click", () => loadFiles(filePath));
  els.fileUploadBtn.addEventListener("click", () => els.fileInput.click());
  els.fileInput.addEventListener("change", () => {
    uploadFiles(els.fileInput.files);
    els.fileInput.value = "";
  });
  els.fileList.addEventListener("dragover", (e) => {
    e.preventDefault();
    els.fileList.classList.add("dragover");
  });
  els.fileList.addEventListener("dragleave", () => els.fileList.classList.remove("dragover"));
  els.fileList.addEventListener("drop", (e) => {
    e.preventDefault();
    els.fileList.classList.remove("dragover");
    uploadFiles(e.dataTransfer.files);
  });

  bootstrap();
})();
