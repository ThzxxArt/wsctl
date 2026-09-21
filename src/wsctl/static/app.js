"use strict";

(() => {
  const statusEl = document.getElementById("status");
  const sessionLabel = document.getElementById("session-label");
  const overlay = document.getElementById("login-overlay");
  const loginForm = document.getElementById("login-form");
  const loginError = document.getElementById("login-error");

  const encoder = new TextEncoder();
  const decoder = new TextDecoder();

  let ws = null;
  let sessionId = null;
  let reconnectDelay = 500;
  let reconnectTimer = null;
  let heartbeat = null;
  let intentionalClose = false;

  const term = new Terminal({
    cursorBlink: true,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "DejaVu Sans Mono", monospace',
    fontSize: 14,
    theme: { background: "#10131a", foreground: "#d7dae0" },
    scrollback: 10000,
  });
  const fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  if (window.WebLinksAddon) {
    term.loadAddon(new WebLinksAddon.WebLinksAddon());
  }
  term.open(document.getElementById("terminal"));
  fitAddon.fit();

  function setStatus(text, cls) {
    statusEl.textContent = text;
    statusEl.className = "status" + (cls ? " " + cls : "");
  }

  function wsUrl() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}/ws`;
  }

  function sendControl(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
    }
  }

  function sendInput(data) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(encoder.encode(data));
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 8000);
  }

  function connect() {
    intentionalClose = false;
    setStatus("connecting", "");
    ws = new WebSocket(wsUrl());
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
      reconnectDelay = 500;
      setStatus("connected", "ok");
      sendControl({ type: "attach", session: sessionId, cols: term.cols, rows: term.rows });
      if (heartbeat) clearInterval(heartbeat);
      heartbeat = setInterval(() => sendControl({ type: "ping" }), 25000);
    };

    ws.onmessage = (event) => {
      if (typeof event.data === "string") {
        let msg;
        try { msg = JSON.parse(event.data); } catch { return; }
        handleControl(msg);
      } else {
        term.write(new Uint8Array(event.data));
      }
    };

    ws.onclose = (event) => {
      if (heartbeat) { clearInterval(heartbeat); heartbeat = null; }
      if (event.code === 4401 || event.code === 4403) {
        setStatus("unauthorized", "bad");
        showLogin("请先登录");
        return;
      }
      setStatus("disconnected", "bad");
      if (!intentionalClose) scheduleReconnect();
    };

    ws.onerror = () => setStatus("error", "bad");
  }

  function handleControl(msg) {
    switch (msg.type) {
      case "attached":
        sessionId = msg.session;
        sessionLabel.textContent = `${msg.name} · ${msg.session}`;
        break;
      case "exit":
        term.write(`\r\n\x1b[33m[wsctl] session exited (code ${msg.code})\x1b[0m\r\n`);
        term.write("\x1b[90m[wsctl] 会话已结束，正在新建会话...\x1b[0m\r\n");
        sessionId = null;
        break;
      case "error":
        term.write(`\r\n\x1b[31m[wsctl] ${msg.msg}\x1b[0m\r\n`);
        break;
      default:
        break;
    }
  }

  term.onData(sendInput);

  const resizeObserver = new ResizeObserver(() => {
    try { fitAddon.fit(); } catch { /* ignore */ }
    sendControl({ type: "resize", cols: term.cols, rows: term.rows });
  });
  resizeObserver.observe(document.getElementById("terminal-wrap"));

  function showLogin(message) {
    if (message) {
      loginError.textContent = message;
      loginError.classList.remove("hidden");
    }
    overlay.classList.remove("hidden");
    document.getElementById("username").focus();
  }

  function hideLogin() {
    overlay.classList.add("hidden");
    loginError.classList.add("hidden");
  }

  loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = document.getElementById("username").value;
    const password = document.getElementById("password").value;
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!res.ok) {
        showLogin("用户名或密码错误");
        return;
      }
      hideLogin();
      connect();
    } catch {
      showLogin("网络错误");
    }
  });

  async function bootstrap() {
    try {
      const res = await fetch("/api/me");
      if (res.ok) {
        hideLogin();
        connect();
      } else {
        showLogin("");
      }
    } catch {
      showLogin("");
    }
  }

  bootstrap();
})();
