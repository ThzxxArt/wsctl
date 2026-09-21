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
    dark: { background: "#10131a", foreground: "#d7dae0" },
    light: { background: "#f6f7f9", foreground: "#1b2029" },
  };

  const prefs = Object.assign(
    { theme: "dark", fontSize: 14 },
    JSON.parse(localStorage.getItem("wsctl-prefs") || "{}"),
  );

  function applyPrefs() {
    document.documentElement.dataset.theme = prefs.theme;
    for (const s of sessions.values()) {
      s.term.options.fontSize = prefs.fontSize;
      s.term.options.theme = THEMES[prefs.theme] || THEMES.dark;
      try { s.fit.fit(); } catch { /* hidden */ }
    }
    localStorage.setItem("wsctl-prefs", JSON.stringify(prefs));
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
      showLogin("请先登录");
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
        s.term.write(new Uint8Array(event.data));
      }
    };

    ws.onclose = (event) => {
      if (s.heartbeat) { clearInterval(s.heartbeat); s.heartbeat = null; }
      if (event.code === 4401 || event.code === 4403) {
        setConnection("unauthorized", "bad");
        showLogin("请先登录");
        return;
      }
      setConnection("disconnected", "bad");
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
      theme: THEMES[prefs.theme] || THEMES.dark,
    }));
    const fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    if (window.WebLinksAddon) term.loadAddon(new WebLinksAddon.WebLinksAddon());
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
    createTab(info.id, info.name || "shell");
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
        list.forEach((s) => createTab(s.id, s.name));
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
    try {
      const info = await api("POST", `/api/sessions/${s.id}/share`, {});
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
    const s = activeId && sessions.get(activeId);
    if (!s) return;
    try {
      await api("DELETE", `/api/sessions/${s.id}/share`);
      els.shareOverlay.classList.add("hidden");
    } catch (err) {
      showShareNote(String(err.message || err));
    }
  });

  // -- preferences -----------------------------------------------------

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
