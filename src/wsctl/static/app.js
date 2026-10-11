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
    previewImage: $("preview-image"), previewImg: $("preview-img"),
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
    tabMenu: $("tab-menu"), termMenu: $("term-menu"), setRightclick: $("set-rightclick"),
    setRenderer: $("set-renderer"), rendererNote: $("renderer-note"),
    moreBtn: $("more-btn"), moreMenu: $("more-menu"),
    settingsBtn: $("settings-btn"), settingsOverlay: $("settings-overlay"),
    setTheme: $("set-theme"), setFontsize: $("set-fontsize"), setFont: $("set-font"),
    setNotifySound: $("set-notify-sound"), setNotifyTitle: $("set-notify-title"),
    setNotifySystem: $("set-notify-system"),
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
    pickOverlay: $("pick-overlay"), pickTitle: $("pick-title"), pickNote: $("pick-note"),
    pickSelect: $("pick-select"), pickOk: $("pick-ok"), pickCancel: $("pick-cancel"),
    pickClose: $("pick-close"),
    qrOverlay: $("qr-overlay"), qrTitle: $("qr-title"), qrSecret: $("qr-secret"),
    qrImage: $("qr-image"), qrDone: $("qr-done"), qrClose: $("qr-close"),
    qrCode: $("qr-code"), qrError: $("qr-error"),
    toasts: $("toasts"),
    desyncBar: $("desync-bar"), desyncNote: $("desync-note"), desyncBtn: $("desync-btn"),
    newSessionOverlay: $("new-session-overlay"), newSessionForm: $("new-session-form"),
    newSessionClose: $("new-session-close"), newSessionCancel: $("new-session-cancel"),
    newSessionQuick: $("new-session-quick"), newSessionError: $("new-session-error"),
    nsName: $("ns-name"), nsCommand: $("ns-command"), nsCwd: $("ns-cwd"),
    nsBackend: $("ns-backend"), nsSshBlock: $("ns-ssh-block"),
    nsSshHost: $("ns-ssh-host"), nsSshUser: $("ns-ssh-user"), nsSshPort: $("ns-ssh-port"),
    nsSshIdentity: $("ns-ssh-identity"), nsSshOptions: $("ns-ssh-options"),
  };

  // Copy/paste live on Alt+C / Alt+V, not Ctrl+Shift+C / Ctrl+Shift+V.
  // Chrome, Edge and Firefox reserve Ctrl+Shift+C for the DevTools inspector
  // and handle it at the browser level -- `preventDefault()` in the page never
  // gets the chance, so the terminal's copy silently became "open the console".
  // Alt+C / Alt+V are unclaimed by every browser and complete the mnemonic set
  // this product already uses (Alt+N/W/F/S/H/L).
  const DEFAULT_KEYS = {
    new_session: "alt+n", close_session: "alt+w", kill_session: "alt+shift+w",
    next_tab: "alt+ArrowRight", prev_tab: "alt+ArrowLeft", toggle_files: "alt+f",
    toggle_sessions: "alt+l", toggle_settings: "alt+s", toggle_share: "alt+h",
    search: "ctrl+shift+f",
    copy_selection: "alt+c", paste_clipboard: "alt+v",
    font_inc: "alt+e", font_dec: "alt+d", clear_screen: "alt+r",
    show_help: "alt+?",
    // Split panes. ``Alt+\`` splits the current pane to the right,
    // ``Alt+Shift+\`` splits it downward -- a mnemonic pair on the same key,
    // and neither is claimed by any browser. Focus movement is
    // ``Alt+Shift+<arrow>``, which is free because the bare ``Alt+<arrow>``
    // pair is already the tab switcher and must stay that way.
    split_right: "alt+\\", split_down: "alt+shift+\\",
    focus_left: "alt+shift+ArrowLeft", focus_right: "alt+shift+ArrowRight",
    focus_up: "alt+shift+ArrowUp", focus_down: "alt+shift+ArrowDown",
  };

  // Which chords a browser may swallow before the page ever sees them. The
  // help sheet says so out loud instead of advertising a shortcut that opens
  // the developer console.
  function prettyCombo(combo) {
    // ``alt+c`` is how the binding is stored; "Alt+C" is how a human reads it.
    // The help sheet used to print the raw key, which is why it looked like the
    // shortcuts were shouting at random.
    if (!combo || combo === "未绑定") return combo;
    return combo
      .split("+")
      .map((part) => {
        if (part === "ArrowRight") return "→";
        if (part === "ArrowLeft") return "←";
        if (part === "ArrowUp") return "↑";
        if (part === "ArrowDown") return "↓";
        if (part.length === 1) return part.toUpperCase();
        return part.charAt(0).toUpperCase() + part.slice(1);
      })
      .join("+");
  }

  const CHORD_RELIABILITY = {
    copy_selection: "warn",   // Alt+C is ours; Ctrl+Shift+C is the browser's
    paste_clipboard: "warn",
  };

  // Which chords a browser claims before the page ever sees them. Binding one of
  // these looks fine in Settings and silently does nothing at runtime -- which
  // is how people ended up opening the DevTools inspector instead of copying.
  //
  // All entries are **lowercase** and lookups must lowercase too: the arrow
  // entries used to sit here in mixed case (``alt+ArrowLeft``) while the lookup
  // compared against ``value.toLowerCase()``, so the warning never fired --
  // and the same mixed-case entries also contradicted ``DEFAULT_KEYS``, whose
  // tab-switch chords *are* ``alt+ArrowLeft/Right``. Those two are gone from
  // this set: the page receives their keydown and ``preventDefault()`` cancels
  // history navigation, unlike chrome-level chords (DevTools) below. This set
  // is the single source of truth for the help sheet's ✅/⚠️/❌ marks.
  const BROWSER_RESERVED = new Set([
    "ctrl+shift+c", "ctrl+shift+j", "ctrl+shift+i", "ctrl+shift+p",
    "ctrl+shift+n", "ctrl+shift+t", "ctrl+shift+w", "ctrl+shift+o",
    "ctrl+shift+delete", "ctrl+w", "ctrl+t", "ctrl+n", "ctrl+r",
    "ctrl+u", "ctrl+h", "ctrl+d", "ctrl+l", "ctrl+p", "f5", "f12",
    "ctrl+tab", "ctrl+shift+tab",
  ]);

  /** Reliable (ok) / partially claimed (warn) / browser-reserved (bad). */
  function chordGrade(action, combo) {
    if (!combo) return "ok";  // unbound: nothing can swallow a chord that is not there
    if (BROWSER_RESERVED.has(String(combo).toLowerCase())) return "bad";
    if (action && CHORD_RELIABILITY[action]) return "warn";
    return "ok";
  }

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

  // One range for the font, in one place. The README promises 12-18, the
  // Settings picker offered 12/13/14/16/18 and the Alt+E/D hotkey clamped at
  // 9-24 -- so a hotkey could reach a size the picker could not show, and the
  // docs described neither. The picker is generated from the same bounds.
  const FONT_MIN = 12;
  const FONT_MAX = 18;

  const encoder = new TextEncoder();
  const sessions = new Map();
  // Session ids ordered least-recently-used first, tracking live sockets.
  const liveOrder = [];
  let activeId = null;
  let me = null;
  let menuSid = null;
  let menuOpenedAt = 0;
  let shareSid = null;
  let adminTab = "overview";

  // Latin glyphs come from a real monospace; CJK falls through to a CJK
  // monospace whose advance width is exactly twice the Latin one. Without a
  // CJK face in the stack the browser substitutes a *proportional* face, one
  // character ends up narrower or wider than its cell, and every column after
  // it slides -- which is what "花屏" looks like when the text is Chinese.
  // `@xterm/addon-unicode11` (loaded in createTab) supplies the matching
  // width table; the font stack is the other half of the same fix.
  const DEFAULT_MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, "DejaVu Sans Mono", "Cascadia Mono", "Sarasa Mono SC", "Noto Sans Mono CJK SC", "Microsoft YaHei Mono", monospace';

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
    {
        theme: "auto", termTheme: "dark", fontSize: 14, fontFamily: "",
        keybindings: {}, customThemes: {}, rightClickMode: "menu",
        // auto -> WebGL, then Canvas, then the DOM renderer. Pinning "dom"
        // disables acceleration; "webgl" asks for WebGL and falls back if it
        // will not initialise rather than leaving a blank terminal.
        termRenderer: "auto",
        // Background-presence signals. On by default (that is the feature);
        // the system notification is the one a browser may refuse, and it
        // degrades to the badge and the title rather than failing.
        notifySound: true, notifyTitle: true, notifySystem: true,
      },
    readPrefs(),
  );
  prefs.keybindings = Object.assign({}, DEFAULT_KEYS, prefs.keybindings);
  prefs.customThemes = prefs.customThemes || {};
  // A stored value outside the supported range is a value the picker cannot
  // represent; clamp it rather than let the hotkey and the picker disagree
  // about what the current size is.
  prefs.fontSize = Math.min(FONT_MAX, Math.max(FONT_MIN, Number(prefs.fontSize) || 14));

  function readPrefs() {
    try {
      const parsed = JSON.parse(localStorage.getItem("wsctl-prefs") || "{}");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch { return {}; }
  }

  /** The font-size picker, generated from FONT_MIN..FONT_MAX. */
  function fillFontSizes() {
    if (!els.setFontsize) return;
    els.setFontsize.replaceChildren();
    for (let size = FONT_MIN; size <= FONT_MAX; size += 1) {
      const opt = document.createElement("option");
      opt.value = String(size);
      opt.textContent = String(size);
      els.setFontsize.appendChild(opt);
    }
    els.setFontsize.value = String(prefs.fontSize);
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
      // Only the panes on screen are re-measured now. Re-fitting every
      // session at once re-laid out N xterm instances in a single turn, which
      // is what made a theme or font change flash across the whole tab bar.
      // A split shows several at once, so this is "shown", not "focused".
      if (isPaneShown(s)) {
        try { s.fit.fit(); } catch { /* 隐藏时忽略 */ }
      } else {
        s.needsRefit = true;
      }
    }
    try { localStorage.setItem("wsctl-prefs", JSON.stringify(prefs)); } catch { /* 忽略 */ }
  }

  window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
    if (prefs.theme === "auto") applyPrefs();
  });

  // -- Toast / 弹窗 -----------------------------------------------------

  // Toast lifetimes by severity. Errors used to stay until clicked, on the
  // theory that a failure the user was not looking at must not be swallowed by
  // a timer. In practice one batch operation pinned a dozen toasts on screen
  // permanently and buried the ones worth reading -- the opposite of the
  // intent. Everything ages out now; the timer pauses while the pointer is
  // over the stack so a long message can still be read, and the stack folds
  // past four so it can never cover the terminal.
  const TOAST_MS = { ok: 2500, info: 4000, warn: 6000, error: 10000 };
  const TOAST_MAX = 4;
  const TOAST_DEDUPE_MS = 5000;
  /** kind -> [{el, hideTimer, remaining, message, count, lastAt}] */
  const toastLive = [];
  let toastOverflow = null;

  function toastDismiss(entry) {
    const index = toastLive.indexOf(entry);
    if (index >= 0) toastLive.splice(index, 1);
    if (entry.hideTimer) clearTimeout(entry.hideTimer);
    entry.el.remove();
    toastUnfold();
  }

  function toastSchedule(entry) {
    if (entry.hideTimer) clearTimeout(entry.hideTimer);
    if (entry.remaining <= 0) return;
    entry.hideTimer = setTimeout(() => toastDismiss(entry), entry.remaining);
  }

  /** Collapse everything past TOAST_MAX behind one "N more" chip. */
  function toastUnfold() {
    const hidden = toastLive.slice(0, Math.max(0, toastLive.length - TOAST_MAX));
    for (const entry of toastLive) entry.el.classList.toggle("toast-folded", hidden.includes(entry));
    if (!hidden.length) {
      if (toastOverflow) { toastOverflow.remove(); toastOverflow = null; }
      return;
    }
    if (!toastOverflow) {
      toastOverflow = document.createElement("button");
      toastOverflow.type = "button";
      toastOverflow.className = "toast toast-more";
      toastOverflow.addEventListener("click", () => {
        for (const entry of toastLive) entry.el.classList.remove("toast-folded");
        if (toastOverflow) { toastOverflow.remove(); toastOverflow = null; }
      });
      els.toasts.appendChild(toastOverflow);
    }
    toastOverflow.textContent = `还有 ${hidden.length} 条通知`;
  }

  function toast(message, kind) {
    const level = kind === "error" || kind === "warn" || kind === "ok" ? kind : "info";
    // A message repeating within ``TOAST_DEDUPE_MS`` is the same event, not a
    // new one: count it instead of stacking a second identical card. Outside
    // that window it is a *new* occurrence of a recurring problem and gets its
    // own card -- which is what the constant was declared for and never used.
    const now = Date.now();
    const recent = toastLive.find(
      (e) => e.message === message && e.level === level && now - e.lastAt < TOAST_DEDUPE_MS,
    );
    if (recent) {
      recent.count += 1;
      recent.countEl.textContent = `×${recent.count}`;
      recent.countEl.classList.remove("hidden");
      recent.lastAt = now;
      recent.remaining = TOAST_MS[level];
      toastSchedule(recent);
      toastUnfold();
      return;
    }

    const el = document.createElement("div");
    el.className = `toast ${level}`;
    el.setAttribute("role", level === "error" || level === "warn" ? "alert" : "status");
    const text = document.createElement("span");
    text.className = "toast-msg";
    text.textContent = message;
    const countEl = document.createElement("span");
    countEl.className = "toast-count hidden";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast-x";
    close.setAttribute("aria-label", "关闭通知");
    close.textContent = "×";
    el.append(text, countEl, close);
    els.toasts.appendChild(el);

    const entry = {
      el, message, level, countEl, count: 1,
      hideTimer: null, remaining: TOAST_MS[level], lastAt: Date.now(),
    };
    // Click anywhere dismisses (kept from the first version); the × is for
    // discoverability and for anyone who does not realise the card is a button.
    el.addEventListener("click", () => toastDismiss(entry));
    toastLive.push(entry);
    toastSchedule(entry);
    toastUnfold();
  }

  // Hovering the stack pauses every timer, so a message that needs reading is
  // not lost to the clock. Leaving restarts it from where it stopped.
  els.toasts.addEventListener("mouseenter", () => {
    for (const entry of toastLive) {
      if (entry.hideTimer) { clearTimeout(entry.hideTimer); entry.hideTimer = null; }
    }
  });
  els.toasts.addEventListener("mouseleave", () => {
    for (const entry of toastLive) toastSchedule(entry);
  });

  // Dialogs must never overwrite the input the user is typing into (that
  // actually shipped as a rename that silently sent the *old* name back to the
  // server). The first guard answered every second caller with
  // `Promise.resolve(null)`, which the caller reads as "the user cancelled" --
  // so the operation silently did not happen and nothing anywhere said so.
  // A rename opened twice therefore never reached the server at all.
  // The fix is to serialise them: a second dialog waits its turn.
  let renamePromptOpen = false;
  let confirmDialogOpen = false;
  let dialogQueue = Promise.resolve();

  function afterCurrentDialog(fn) {
    const run = dialogQueue.then(fn, fn);
    dialogQueue = run.then(() => undefined, () => undefined);
    return run;
  }

  // ``variant`` decides the confirm button's weight. It used to be red for
  // every confirmation -- including "generate a new share link", which is
  // recoverable -- so the colour stopped meaning "this destroys something" and
  // became noise. ``danger`` is reserved for irreversible loss.
  //   danger -> irreversible (kill a session, delete a user/file/recording)
  //   warn   -> invalidates or overwrites something (regenerate a link, overwrite)
  //   primary-> ordinary confirmation
  function confirmDialog(message, title, opts) {
    const variant = (opts && opts.variant) || "danger";
    return afterCurrentDialog(() => new Promise((resolve) => {
      confirmDialogOpen = true;
      els.confirmTitle.textContent = title || "确认";
      els.confirmMessage.textContent = message;
      els.confirmOk.className = variant === "danger" ? "danger" : variant === "warn" ? "warn-btn" : "primary";
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
    }));
  }

  // Dangerous actions ask for the *name* typed back, the way a hosting panel
  // makes you type the repository name before deleting it. "确定 / 取消" alone
  // is one stray click away from an irreversible delete.
  function confirmByName(message, expected, title) {
    return afterCurrentDialog(() => new Promise((resolve) => {
      renamePromptOpen = true;
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
    }));
  }

  // "Which one?" -- the question a split of *existing* panes has to ask.
  // A free-text prompt would be a trap (typo = wrong session moved); a list
  // of what is actually open is the only honest answer.
  function pickDialog(title, options, note) {
    return afterCurrentDialog(() => new Promise((resolve) => {
      els.pickTitle.textContent = title || "选择";
      els.pickNote.textContent = note || "";
      els.pickNote.classList.toggle("hidden", !note);
      els.pickSelect.replaceChildren();
      for (const option of options) {
        const el = document.createElement("option");
        el.value = option.value;
        el.textContent = option.label;
        els.pickSelect.appendChild(el);
      }
      els.pickOverlay.classList.remove("hidden");
      els.pickSelect.focus();
      const done = (value) => {
        els.pickOverlay.classList.add("hidden");
        els.pickOk.removeEventListener("click", onOk);
        els.pickCancel.removeEventListener("click", onCancel);
        els.pickClose.removeEventListener("click", onCancel);
        els.pickSelect.removeEventListener("keydown", onKey);
        resolve(value);
      };
      const onOk = () => done(els.pickSelect.value || null);
      const onCancel = () => done(null);
      const onKey = (e) => {
        if (e.key === "Enter") { e.preventDefault(); done(els.pickSelect.value || null); }
      };
      els.pickOk.addEventListener("click", onOk);
      els.pickCancel.addEventListener("click", onCancel);
      els.pickClose.addEventListener("click", onCancel);
      els.pickSelect.addEventListener("keydown", onKey);
    }));
  }

  function promptDialog(title, value, opts) {
    return afterCurrentDialog(() => new Promise((resolve) => {
      renamePromptOpen = true;
      const masked = Boolean(opts && opts.masked);
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
    }));
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
    "pick-overlay": () => els.pickCancel.click(),
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
    // Explicit states ("已连接" / "未授权" / ...) are never "click to retry";
    // clear the affordances so a previous backoff cannot leave a stale one.
    els.connection.title = "";
    els.connection.style.cursor = "";
    setConnection(text, cls);
  }

  // One writer for the indicator *and* its affordances (title / cursor), so a
  // background tab can never clobber the visible one's "click to retry" state.
  function paintConnection(s) {
    const [text, cls] = describeConnection(s);
    const retrying = Boolean(s && s.reconnectTimer);
    els.connection.title = retrying ? "点击立即重连" : "";
    els.connection.style.cursor = retrying ? "pointer" : "";
    setConnection(text, cls);
  }
  function paintConnectionFor(s) {
    if (s && activeId && s.id !== activeId) return;
    paintConnection(s);
  }

  // Derive the indicator from a session's actual state, so switching tabs
  // repaints it correctly regardless of which callbacks happened to fire.
  function describeConnection(s) {
    if (!s) return ["空闲", ""];
    if (s.exited) return ["已结束", "bad"];
    if (s.reconnectTimer) {
      // Backoff is a state, not silence: "已断开" could not be told apart from
      // "gave up", and a remote operator has no way to act on a bare red word.
      return [`重连中（${Math.ceil((s.retryIn || 0) / 1000)}s 后重试）`, ""];
    }
    if (s.standby || !s.ws) return ["未连接", ""];
    if (s.ws.readyState === WebSocket.OPEN) return ["已连接", "ok"];
    if (s.ws.readyState === WebSocket.CONNECTING) return ["连接中", ""];
    return ["已断开", s.intentional ? "" : "bad"];
  }

  // Clicking the indicator during backoff retries *now*. The timer is the
  // polite path; the click is for "I fixed the network, stop waiting".
  els.connection.addEventListener("click", () => {
    const s = activeId && sessions.get(activeId);
    if (!s || !s.reconnectTimer) return;
    clearTimeout(s.reconnectTimer);
    s.reconnectTimer = null;
    s.retryIn = 0;
    connect(s);
  });
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
  // A paste is not a keystroke. Sending 500 KB as one frame fills the PTY
  // write buffer in a single call (its cap is checked only on entry), after
  // which every keystroke is dropped as "input too fast" -- a keyboard that
  // appears dead. Chunk it; one WebSocket is ordered, so the pieces arrive as
  // one stream. Byte slicing is safe here: the child reads stdin as a byte
  // stream and reassembles UTF-8 across reads.
  const PASTE_CHUNK = 32 * 1024;
  function sendPaste(s, text) {
    const bytes = encoder.encode(text);
    if (bytes.length <= PASTE_CHUNK) {
      if (s.ws && s.ws.readyState === WebSocket.OPEN) s.ws.send(bytes);
      return;
    }
    let offset = 0;
    const pump = () => {
      if (!s.ws || s.ws.readyState !== WebSocket.OPEN) return;
      if (offset >= bytes.length) return;
      const end = Math.min(offset + PASTE_CHUNK, bytes.length);
      s.ws.send(bytes.subarray(offset, end));
      offset = end;
      if (offset < bytes.length) setTimeout(pump, 0);
    };
    pump();
    toast(`粘贴 ${formatSize(bytes.length)}，已分片发送`, "info");
  }
  // The tab entry describes the *group*, not one pane: a split tab is one
  // entry whose state is the worst of its panes. Each session records its own
  // state and the group aggregates, so a dead pane in a split cannot hide
  // behind a live sibling.
  function markTab(s, state) {
    if (!s) return;
    s.tabState = state;
    const group = groupOf(s);
    if (!group) return;
    paintGroupTab(group);
    group.tabEl.title = state === "standby" ? "未连接（点击打开）" : "";
  }

  // -- 出站写合批 -------------------------------------------------------
  //
  // One `term.write` per WebSocket message is one parse-and-diff pass per
  // message. A `seq 1 50000` produces thousands of small messages, so the
  // main thread spends all its time re-entering the parser and never gets to
  // paint -- the terminal appears to freeze, then lurch. Queuing the frames and
  // handing xterm a single buffer per animation frame turns N passes into one.
  const WRITE_FLUSH_MS = 16;
  // One `term.write` per flush, capped. A 4 MiB scrollback replay coalesced
  // whole is a multi-megabyte parse on the main thread -- one long frame (or
  // worse) exactly when the screen is being rebuilt. Cap the bite, leave the
  // rest queued for the next frame, keep byte order.
  const WRITE_MAX_BYTES = 256 * 1024;

  // Every screen wipe goes through here. "The screen is blank and nothing
  // says why" is the single worst failure this terminal can have, and with
  // three call sites it has been hard to say which one did it. The log is
  // part of ``__wsctlDiag`` for exactly that reason.
  function resetTerm(s, tag) {
    if (!s.resetLog) s.resetLog = [];
    s.resetLog.push({ tag, wrote: s.wroteBytes || 0, at: Date.now() % 100000 });
    if (s.resetLog.length > 32) s.resetLog.shift();  // bounded, like any log
    try { s.term.reset(); } catch { /* 忽略 */ }
  }

  function enqueueWrite(s, bytes) {
    // Between asking for a resync and its ``resync-begin`` marker, live frames
    // are duplicates of what the replay is about to repeat -- drop them. From
    // ``resync-begin`` on, the frames *are* the replay and must be written.
    if (s.resyncing) return;
    // Any output is news if nobody is looking at it. (A replay is not: the
    // user asked for that.) Measured here rather than in ``flushWrite`` so a
    // queued frame counts even before it is painted.
    noteActivity(s, "output");
    s.writeFrames += 1;
    s.writeQueue.push(bytes);
    if (s.writeTimer !== null) return;
    s.writeTimer = setTimeout(() => flushWrite(s), WRITE_FLUSH_MS);
  }

  function flushWrite(s) {
    // Always disarm first. Called on the timer *and* directly (resync paths);
    // a direct call that merely nulls the id leaves the old timer armed, and
    // the next `enqueueWrite` then sees a non-null id and refuses to schedule
    // -- frames sit unflushed until the orphan fires.
    if (s.writeTimer !== null) { clearTimeout(s.writeTimer); s.writeTimer = null; }
    if (!s.writeQueue.length || s.resyncing) return;
    // Merge at most WRITE_MAX_BYTES; a single oversized chunk still goes (the
    // first one is always taken) so nothing is stranded.
    let total = 0;
    let count = 0;
    for (const chunk of s.writeQueue) {
      if (count > 0 && total + chunk.length > WRITE_MAX_BYTES) break;
      total += chunk.length;
      count += 1;
    }
    const chunks = s.writeQueue.splice(0, count);
    s.writeBatches += 1;
    // One merged buffer, one call: xterm's parser is far happier with a few
    // large writes than with many small ones.
    const merged = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) { merged.set(chunk, offset); offset += chunk.length; }
    if (s.sentry) {
      // A ZMODEM transfer consumes the bytes as protocol; they are not
      // rendered, so they do not count towards "reached the terminal".
      try { s.sentry.consume(merged); }
      catch {
        try { s.term.write(merged); s.wroteBytes = (s.wroteBytes || 0) + total; } catch { /* 忽略 */ }
      }
      // Any traffic counts as transfer progress; re-arm the watchdog that
      // releases the input lock if a transfer dies without `session_end`.
      if (s.zmodemActive) armZmodemWatchdog(s);
    } else {
      try { s.term.write(merged); s.wroteBytes = (s.wroteBytes || 0) + total; } catch { /* 忽略 */ }
    }
    if (s.writeQueue.length && s.writeTimer === null) {
      s.writeTimer = setTimeout(() => flushWrite(s), WRITE_FLUSH_MS);
    }
  }

  // -- 尺寸合流 ---------------------------------------------------------
  //
  // Both directions matter: the window resize handler must not call `fit()`
  // per pixel, and the terminal's own `onResize` must not forward per-pixel
  // changes to the server (each one is a SIGWINCH and a full repaint over
  // there). A size that did not change is not sent at all.
  const RESIZE_MS = 120;

  function scheduleResize(s, cols, rows) {
    s.pendingCols = cols;
    s.pendingRows = rows;
    if (s.resizeTimer !== null) return;
    s.resizeTimer = setTimeout(() => {
      s.resizeTimer = null;
      const c = s.pendingCols;
      const r = s.pendingRows;
      if (!c || !r || (c === s.sentCols && r === s.sentRows)) return;
      s.sentCols = c;
      s.sentRows = r;
      sendControl(s, { type: "resize", cols: c, rows: r });
    }, RESIZE_MS);
  }

  // -- 盒子观察 ---------------------------------------------------------
  //
  // Every way a pane's box can change used to need its own ``fit()`` call,
  // and every way added later forgot to add one: a window resize, a split bar
  // dragged, a pane closed beside it, the font swapped, a theme change that
  // alters the scrollbar, a tab first shown at a width it was never measured
  // at. Each omission left a terminal drawing a screen that did not fill --
  // or overflowed -- its own box: columns and rows cut off the right and the
  // bottom, which is what a TUI's sidebar and input box look like when they
  // stop existing. Watch the box itself instead of enumerating the reasons it
  // may change, and no future layout change can forget again.
  const paneObserver = (typeof ResizeObserver !== "undefined")
    ? new ResizeObserver(onPaneBoxChanged) : null;
  const paneFitTimer = new Map();

  function sessionOfPane(pane) {
    for (const s of sessions.values()) {
      if (s.pane === pane) return s;
    }
    return null;
  }

  function onPaneBoxChanged(entries) {
    for (const entry of entries) {
      const pane = entry.target;
      const s = sessionOfPane(pane);
      if (!s) continue;
      const box = entry.contentRect;
      // A box that vanished (or is mid-reparent and momentarily 0x0) cannot be
      // measured. It must be measured when it comes back, so the flag is set,
      // never cleared -- clearing here is how a pane was left at the geometry
      // of the day it was created.
      if (!box || box.width < 1 || box.height < 1) {
        s.needsRefit = true;
        continue;
      }
      if (!isPaneShown(s)) { s.needsRefit = true; continue; }
      if (paneFitTimer.get(pane) != null) continue;
      paneFitTimer.set(pane, setTimeout(() => {
        paneFitTimer.delete(pane);
        if (isPaneShown(s)) fitNow(s);
        else s.needsRefit = true;
      }, RESIZE_MS));
    }
  }

  function watchPaneBox(s) {
    if (!paneObserver || !s || !s.pane) return;
    try { paneObserver.observe(s.pane); } catch { /* 旧环境忽略 */ }
  }

  function unwatchPaneBox(s) {
    if (!paneObserver || !s || !s.pane) return;
    const t = paneFitTimer.get(s.pane);
    if (t != null) { clearTimeout(t); paneFitTimer.delete(s.pane); }
    try { paneObserver.unobserve(s.pane); } catch { /* 旧环境忽略 */ }
  }

  let windowResizeTimer = null;

  /**
   * Fit one pane, and say honestly whether it happened.
   *
   * ``fit()`` returns *silently* when it can do nothing -- a pane that is
   * mid-reparent measures 0x0, a pane still hidden has no box. The callers
   * used to clear ``needsRefit`` regardless, so a pane measured while it had
   * no box was never measured again once it did: it kept the geometry of the
   * day it was created and drew a screen that did not fill (or overflowed)
   * its box. ``proposeDimensions`` is the only side-effect-free way to ask
   * "would this fit do anything", and it is why the flag is cleared *here*,
   * on success, and nowhere else.
   */
  function fitNow(s) {
    if (!s || !s.fit) return false;
    try {
      const d = s.fit.proposeDimensions();
      if (!d || !Number.isFinite(d.cols) || !Number.isFinite(d.rows)
          || d.cols < 2 || d.rows < 1) {
        s.needsRefit = true;
        return false;
      }
      s.fit.fit();
      s.needsRefit = false;
      return true;
    } catch {
      s.needsRefit = true;
      return false;
    }
  }

  function fitActiveSoon() {
    if (windowResizeTimer !== null) clearTimeout(windowResizeTimer);
    windowResizeTimer = setTimeout(() => {
      windowResizeTimer = null;
      // Every hidden tab is now the wrong size too. Marking them dirty is what
      // makes ``needsRefit`` load-bearing instead of decorative: without this
      // a tab switched to after a window resize would keep its old geometry.
      for (const other of sessions.values()) {
        if (!isPaneShown(other)) other.needsRefit = true;
      }
      // Every pane on screen is resized by a window resize, not just the one
      // holding the keyboard.
      for (const other of sessions.values()) {
        if (!isPaneShown(other)) continue;
        fitNow(other);
      }
    }, RESIZE_MS);
  }

  // -- 失步与重新同步 ---------------------------------------------------
  //
  // Shedding frames keeps the connection alive, but a full-screen application
  // then holds a screen built from an incomplete stream: its next partial
  // redraw paints the wrong cells and the display stays wrong until something
  // forces a full repaint. Nothing could, so say so and offer one.
  function markDesynced(s, note) {
    s.desynced = true;
    // Both: the pane carries its own mark (so in a split you can see *which*
    // half is incomplete) and the tab carries the group's worst, so a problem
    // in any pane is still visible from the tab bar.
    s.pane.classList.add("desync");
    paintGroupDesync(s);
    if (s.id === activeId) showDesyncBar(note);
  }

  function clearDesync(s) {
    s.desynced = false;
    s.pane.classList.remove("desync");
    paintGroupDesync(s);
    if (s.id === activeId) hideDesyncBar();
  }

  function paintGroupDesync(s) {
    const group = groupOf(s);
    if (!group) return;
    const any = leafIds(group.root).some((id) => {
      const other = sessions.get(id);
      return Boolean(other && other.desynced);
    });
    group.tabEl.classList.toggle("desync", any);
  }

  function showDesyncBar(note) {
    els.desyncNote.textContent = note || "输出过快，部分内容已省略";
    els.desyncBar.classList.remove("hidden");
  }

  function hideDesyncBar() {
    els.desyncBar.classList.add("hidden");
  }

  // Ask the server to replay what it still holds into a cleared screen. That
  // is an honest best effort, not a guarantee: bytes the server also shed are
  // gone for good, and a full-screen application may still need its own
  // repaint (Ctrl+L, or `resize`) afterwards. The bar says exactly that.
  const RESYNC_TIMEOUT_MS = 4000;

  function requestResync(s) {
    if (!s || s.resyncing) return;  // a second click must not reset mid-replay
    flushWrite(s);
    // flushWrite may have re-armed for the overflow; the queue is about to go
    // and `resyncing` will drop anything new, so disarm before that happens.
    if (s.writeTimer !== null) { clearTimeout(s.writeTimer); s.writeTimer = null; }
    s.writeQueue = [];
    s.resyncing = true;
    resetTerm(s, "requestResync");
    sendControl(s, { type: "resync" });
    // The bar stays up until the replay actually lands. Clearing it on the
    // *request* left a failed resync with a blank screen and no way to know
    // what had happened -- the opposite of what this whole feature is for.
    // If the reply never comes, the terminal must not stay write-locked and
    // the bar must come back so the user can act.
    if (s.resyncTimer) clearTimeout(s.resyncTimer);
    s.resyncTimer = setTimeout(() => {
      s.resyncTimer = null;
      s.resyncing = false;
      if (s.desynced) showDesyncBar("重新同步未完成，屏幕内容可能仍不完整");
    }, RESYNC_TIMEOUT_MS);
  }

  els.desyncBtn.addEventListener("click", () => requestResync(activeId && sessions.get(activeId)));

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
    resetTransients(s);
    if (s.ws) { try { s.ws.close(); } catch { /* 忽略 */ } s.ws = null; }
    forgetLive(s.id);
    // Unmounting frees the layout box. Never unmount a pane that is on screen
    // right now -- `.term-pane.mounted.shown` is what makes it visible, so
    // dropping `mounted` from a visible pane blanks the screen the user is
    // reading. A split puts several panes on screen at once, so "visible" is
    // the question; the focused pane is always visible, which is why the older
    // form of this rule (`s.id !== activeId`) is still true and is still
    // written out here rather than folded away.
    if (!isPaneShown(s)) {
      if (s.id !== activeId) s.pane.classList.remove("mounted");
    }
    s.needsRefit = true;
    markTab(s, "standby");
    setConnectionFor(s, "空闲", "");
  }

  // Everything that belongs to *one* socket attempt rather than to the
  // session. A new connection must not inherit the previous attempt's
  // backoff timer (that is how one tab ended up with two WebSockets), its
  // queued terminal bytes (those would interleave with the new replay) or
  // its resync lock (the new attach replay is a superset of any resync and
  // would be *dropped* by it -- a blank screen until the resync timed out).
  function resetTransients(s) {
    if (s.reconnectTimer) { clearTimeout(s.reconnectTimer); s.reconnectTimer = null; }
    s.retryIn = 0;
    if (s.writeTimer) { clearTimeout(s.writeTimer); s.writeTimer = null; }
    s.writeQueue = [];
    if (s.resyncTimer) { clearTimeout(s.resyncTimer); s.resyncTimer = null; }
    s.resyncing = false;
  }

  function ensureConnected(s) {
    if (!s || s.exited) return;
    if (s.ws && s.ws.readyState <= WebSocket.OPEN) { noteLive(s); return; }
    while (liveOrder.length >= AUTO_CONNECT_MAX) {
      // The budget is about what to keep *warm in the background*, not about
      // how much of the screen may be live at once. A split puts several
      // sessions on screen together and every one of them needs its socket:
      // suspending one would blank a pane the user is looking at right now,
      // which is the exact failure ``suspendTab`` refuses to do to the
      // focused pane -- a visible sibling deserves the same.
      const victim = liveOrder
        .map((id) => sessions.get(id))
        .find((v) => v && v !== s && !isPaneShown(v));
      if (!victim) {
        // Everything else is on screen. Exceeding the budget here is the
        // right trade: the user asked to see these panes, and an extra socket
        // is cheaper than a blank half of the window.
        break;
      }
      suspendTab(victim);
    }
    s.standby = false;
    noteLive(s);
    connect(s);
  }

  function scheduleReconnect(s) {
    if (s.exited || s.reconnectTimer) return;
    const wait = s.delay;
    s.retryIn = wait;
    s.reconnectTimer = setTimeout(() => {
      s.reconnectTimer = null;
      s.retryIn = 0;
      connect(s);
    }, wait);
    s.delay = Math.min(s.delay * 2, 8000);
    paintConnectionFor(s);
  }

  function connect(s) {
    // A fresh attempt starts from a known state -- see resetTransients.
    resetTransients(s);
    if (s.ws) {
      // Replacing a live socket without closing it leaked the old one: the
      // server kept it attached and its handlers kept writing into `s`.
      try { s.ws.close(); } catch { /* 忽略 */ }
      s.ws = null;
    }
    s.intentional = false;
    const ws = new WebSocket(wsUrl(s));
    ws.binaryType = "arraybuffer";
    s.ws = ws;
    s.attached = false;
    s.shedSinceOpen = false;
    // Paint *after* `s.ws` is set: before it, `describeConnection` says
    // "未连接" and the indicator flickers between two wrong states.
    paintConnectionFor(s);

    // Every handler below is scoped to *this* socket. A replaced or closed
    // socket's callbacks must be inert: without the generation check a stale
    // `onclose` scheduled a second reconnect alongside the live one and a
    // stale `onmessage` wrote the old connection's bytes into the new
    // connection's screen.
    const current = () => s.ws === ws;

    ws.onopen = () => {
      if (!current()) return;
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
      // A new attach replay supersedes any in-flight resync and is a superset
      // of it. Leaving ``resyncing`` set would drop that replay on the floor.
      s.resyncing = false;
      if (s.resyncTimer) { clearTimeout(s.resyncTimer); s.resyncTimer = null; }
      // Remember which rename generation this attach belongs to.
      s.attachNameVersion = s.nameVersion || 0;
      noteLive(s);
      s.pane.classList.add("mounted");
      markTab(s, "connected");
      setConnectionFor(s, "已连接", "ok");
      sendControl(s, { type: "attach", session: s.id, cols: s.term.cols, rows: s.term.rows, share: s.share || undefined });
      if (s.heartbeat) clearInterval(s.heartbeat);
      s.heartbeat = setInterval(() => {
        if (!current()) return;
        // Piggy-back the client's write-batching counters on the heartbeat that
        // already goes out every 25s. They say whether coalescing is actually
        // happening in the field, which is the one number that proves the
        // frame-batching fix is working on a real page rather than in a test.
        const coalesced = s.writeBatches > 0 ? s.writeFrames - s.writeBatches : 0;
        s.writeFrames = 0;
        s.writeBatches = 0;
        sendControl(s, { type: "ping", coalesced });
      }, 25000);
    };

    ws.onmessage = (event) => {
      if (!current()) return;
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
          resetTerm(s, "handleBinary:pendingReplayClear");
        }
        enqueueWrite(s, bytes);
      }
    };

    ws.onclose = (event) => {
      if (!current()) return;
      if (s.heartbeat) { clearInterval(s.heartbeat); s.heartbeat = null; }
      forgetLive(s.id);
      s.ws = null;
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
      if (!s.intentional && !s.exited) {
        scheduleReconnect(s);
      } else {
        setConnectionFor(s, s.intentional ? "空闲" : "已断开", s.intentional ? "" : "bad");
      }
    };
    ws.onerror = () => { if (current()) setConnectionFor(s, "连接错误", "bad"); };
  }

  function handleControl(s, msg) {
    switch (msg.type) {
      case "attached":
        s.attached = true;
        s.attachedAt = Date.now();
        // Did the attach replay deliver anything? ``pendingReplayClear`` is
        // cleared by the first replayed byte; still set here means the replay
        // was empty -- and the block below wipes the screen to the blank
        // state that promise was made against.
        const replayArrived = !s.pendingReplayClear;
        if (s.pendingReplayClear) {
          s.pendingReplayClear = false;
          resetTerm(s, "attached:pendingReplayClear");
        }
        // The attach replay is the full scrollback. Whatever a previous
        // connection shed, this replay repairs it -- so a desync that was
        // raised before the reconnect is now a lie and must go. Unless the
        // replay *itself* shed (``desync`` arrived after this socket opened,
        // or the server says so): then the screen is still incomplete.
        if (msg.incomplete || s.shedSinceOpen) {
          // Not "重连回放": the first attach is not a reconnect either. The
          // bar must say what is true for both paths.
          markDesynced(s, "会话回放未完整，部分内容已省略");
        } else if (!replayArrived && s.desynced) {
          // We just wiped a screen the user was told was incomplete, and
          // nothing came back to fill it. Clearing the bar here is the
          // "silent blank" this whole feature exists to prevent.
          showDesyncBar("回放为空，屏幕内容可能仍不完整");
        } else {
          clearDesync(s);
        }
        // A live resync cannot outlive the socket that was asked for it.
        s.resyncing = false;
        if (s.resyncTimer) { clearTimeout(s.resyncTimer); s.resyncTimer = null; }
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
        // "The command finished" is the single most useful thing a background
        // session can tell you. It used to be visible only if you were looking.
        noteActivity(s, "exit");
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
        toast(msg.msg || "提示", msg.level === "error" ? "error" : msg.level === "warn" ? "warn" : "info");
        break;
      case "resync-begin":
        // The replay starts here. Everything before it was live output that the
        // replay is about to repeat, so it was dropped; from here on the frames
        // *are* the replay and must be written. (Asking for a resync and then
        // dropping the answer is what the first version managed to do.)
        s.resyncing = false;
        break;
      case "resynced":
        // The replay has been fully enqueued behind this marker. Live output
        // resumes from here and only here, so nothing can land on top of it.
        s.resyncing = false;
        if (s.resyncTimer) { clearTimeout(s.resyncTimer); s.resyncTimer = null; }
        if (msg.incomplete) {
          // The server shed part of *this* replay too. Announcing success
          // would be the same lie the bar exists to avoid.
          showDesyncBar("重新同步未完整，部分内容仍缺失");
        } else {
          clearDesync(s);
        }
        flushWrite(s);
        break;
      case "desync":
        // Distinct from a notice: the screen is now known to be incomplete, and
        // the remedy is a resync rather than an acknowledgement. Surfaced as a
        // bar with a button, not a toast that ages out before it is read.
        s.shedSinceOpen = true;
        markDesynced(s, msg.msg);
        toast(msg.msg || "输出过快，部分内容已省略", "warn");
        break;
      case "evicted":
        // A notice, not terminal output: writing it into the buffer would make
        // it show up again on every reconnect replay. ``backpressure`` used to
        // arrive as nothing at all -- the socket simply closed "normally".
        //
        // Do *not* mark the tab standby here: 4410 / memory eviction are
        // recoverable and `onclose` is about to auto-reconnect. Claiming
        // "未连接" in between was a state that contradicted what the code did
        // next. The toast is the message; the connection indicator is
        // `onclose`'s job alone.
        toast(
          msg.msg || (msg.reason === "backpressure"
            ? "输出过快，连接已释放（会话仍在运行，重连即可恢复）"
            : "连接已被释放（会话仍在运行）"),
          "error",
        );
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

  // -- 分屏布局 ---------------------------------------------------------
  //
  // A tab owns a *layout tree* of panes. The tree is either one leaf (the
  // default, and what every existing behaviour assumes) or a split of two
  // subtrees. 2x2 is two splits, not a third node type.
  //
  //   leaf  = { type: "leaf", sessionId }
  //   split = { type: "split", dir: "row"|"col", ratio, a, b }
  //
  // The DOM is rebuilt from the tree on every structural change and the
  // ``.term-pane`` elements are re-parented into it -- moving a node keeps a
  // live xterm instance intact, which is why a split does not need to
  // rebuild terminals. ``ratio`` is the first child's share of the split.
  const tabGroups = new Map();
  let activeTabId = null;

  function groupOf(s) {
    return (s && s.groupId && tabGroups.get(s.groupId)) || null;
  }

  /** Session ids in the tree, left-to-right / top-to-bottom. */
  function leafIds(node, out) {
    // The accumulator must be *reused* across the recursion, not rebound per
    // call: `out = out || []` inside a branch creates a private array that the
    // caller never sees, and every split then collects as "no panes at all".
    out = out || [];
    if (!node) return out;
    if (node.type === "leaf") { out.push(node.sessionId); return out; }
    leafIds(node.a, out);
    leafIds(node.b, out);
    return out;
  }

  function findLeaf(node, sessionId) {
    if (!node) return null;
    if (node.type === "leaf") return node.sessionId === sessionId ? node : null;
    return findLeaf(node.a, sessionId) || findLeaf(node.b, sessionId);
  }

  /** The split that directly contains ``sessionId``, and on which side. */
  function findParent(node, sessionId, parent) {
    if (!node) return null;
    if (node.type === "leaf") return null;
    if (node.a && findLeaf(node.a, sessionId)) return { node, side: "a", parent };
    if (node.b && findLeaf(node.b, sessionId)) return { node, side: "b", parent };
    return findParent(node.a, sessionId, node) || findParent(node.b, sessionId, node);
  }

  function isPaneShown(s) {
    const g = groupOf(s);
    return Boolean(g && g.id === activeTabId && findLeaf(g.root, s.id));
  }

  /** A leaf whose session is no longer there: the slot, and an explanation. */
  function buildGhostPane(sessionId) {
    const ghost = document.createElement("div");
    ghost.className = "term-pane mounted shown ghost";
    ghost.dataset.session = sessionId;
    const note = document.createElement("div");
    note.className = "ghost-note";
    note.innerHTML = "<b>会话已结束</b><span>这一栏保留了布局；关闭它可让旁边的一栏占满。</span>";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "text-btn";
    close.textContent = "关闭本分屏";
    close.addEventListener("click", () => closeGhostPane(sessionId));
    ghost.append(note, close);
    return ghost;
  }

  /** Is this id a leaf in any tab's tree? */
  function findLeafFromGroups(sessionId) {
    for (const g of tabGroups.values()) {
      if (findLeaf(g.root, sessionId)) return true;
    }
    return false;
  }

  /** Drop a ghost leaf and let its sibling take the space. */
  function closeGhostPane(sessionId) {
    for (const g of tabGroups.values()) {
      if (!findLeaf(g.root, sessionId)) continue;
      closePane(sessionId, g);
      if (!g.root) {
        if (g.tabEl) g.tabEl.remove();
        g.host.remove();
        tabGroups.delete(g.id);
      } else {
        if (g.focus === sessionId) g.focus = leafIds(g.root).find((id) => sessions.has(id)) || null;
        renderLayout(g);
        syncLayoutClasses(g);
        fitGroup(g);
      }
      break;
    }
    saveLayout();
  }

  function buildLayoutNode(node) {
    if (node.type === "leaf") {
      const slot = document.createElement("div");
      slot.className = "pane-slot";
      slot.dataset.session = node.sessionId;
      const s = sessions.get(node.sessionId);
      if (s) {
        slot.appendChild(s.pane);
      } else {
        // The session behind this leaf is gone (it ended and was reaped, or it
        // belonged to an instance that is no longer here). The *shape* of the
        // workbench is still the user's, so the slot stays and says what
        // happened rather than quietly collapsing the split beside it.
        slot.appendChild(buildGhostPane(node.sessionId));
      }
      return slot;
    }
    const el = document.createElement("div");
    el.className = `split ${node.dir}`;
    const a = buildLayoutNode(node.a);
    const b = buildLayoutNode(node.b);
    const bar = document.createElement("div");
    bar.className = "split-bar";
    bar.title = "拖动调整大小（双击复位）";
    applySplitRatio(node, a, b);
    el.append(a, bar, b);
    node.el = el; node.aEl = a; node.bEl = b; node.barEl = bar;
    wireSplitBar(node);
    return el;
  }

  function applySplitRatio(node, a, b) {
    // Write the clamped value back: a stored ratio outside the range used to
    // render at the limit while persisting the out-of-range number, so what
    // was saved was not what was drawn.
    node.ratio = Math.min(0.85, Math.max(0.15, node.ratio || 0.5));
    (a || node.aEl).style.flex = `${node.ratio} 1 0`;
    (b || node.bEl).style.flex = `${1 - node.ratio} 1 0`;
  }

  function wireSplitBar(node) {
    const bar = node.barEl;
    if (!bar || bar.dataset.wired) return;
    bar.dataset.wired = "1";
    const onMove = (ev) => {
      const host = node.el.getBoundingClientRect();
      const point = node.dir === "row" ? ev.clientX : ev.clientY;
      const start = node.dir === "row" ? host.left : host.top;
      const size = node.dir === "row" ? host.width : host.height;
      if (size <= 0) return;
      node.ratio = Math.min(0.85, Math.max(0.15, (point - start) / size));
      applySplitRatio(node);
    };
    const stop = () => {
      bar.classList.remove("dragging");
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", stop);
      fitGroup(groupOfNode(node));
      saveLayout();
    };
    bar.addEventListener("mousedown", (ev) => {
      ev.preventDefault();
      bar.classList.add("dragging");
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", stop);
    });
    bar.addEventListener("dblclick", () => {
      node.ratio = 0.5;
      applySplitRatio(node);
      fitGroup(groupOfNode(node));
      saveLayout();
    });
  }

  // A node does not know its group; the tree is walked from every group's
  // root instead, which is cheap (the trees are tiny) and cannot be stale.
  function groupOfNode(node) {
    for (const g of tabGroups.values()) {
      if (containsNode(g.root, node)) return g;
    }
    return null;
  }
  function containsNode(root, node) {
    if (!root) return false;
    if (root === node) return true;
    return root.type === "split" && (containsNode(root.a, node) || containsNode(root.b, node));
  }

  function renderLayout(group) {
    if (!group) return;
    group.host.replaceChildren();
    group.host.appendChild(buildLayoutNode(group.root));
    syncLayoutClasses(group);
  }

  /** Which panes are visible and which one has the keyboard. */
  function syncLayoutClasses(group) {
    if (!group) return;
    const shown = new Set(leafIds(group.root));
    for (const s of sessions.values()) {
      const on = shown.has(s.id) && group.id === activeTabId;
      s.pane.classList.toggle("shown", on);
      // ``active`` means "has the keyboard" and stays exclusive even in a
      // split -- it is how a click finds the terminal to type into.
      s.pane.classList.toggle("active", on && s.id === group.focus);
      s.pane.classList.toggle("focused", on && s.id === group.focus);
    }
    for (const g of tabGroups.values()) {
      g.host.classList.toggle("active", g.id === activeTabId);
    }
    paintGroupTab(group);
  }

  function paintGroupTab(group) {
    if (!group) return;
    const ids = leafIds(group.root);
    // Worst state wins: a group with one dead pane and one live pane is not
    // "connected", it is a tab that needs attention. Start from the *best*
    // state and escalate -- starting from "standby" made every tab look
    // suspended, which painted the 未连接 badge on all of them and widened
    // the row until a click aimed at the tab landed on its close button.
    const rank = { connected: 0, standby: 1, exited: 2 };
    let worst = "connected";
    for (const id of ids) {
      const s = sessions.get(id);
      if (!s) continue;
      const state = s.tabState || "standby";
      if (rank[state] > rank[worst]) worst = state;
    }
    if (!ids.length) worst = "standby";
    group.tabEl.classList.toggle("connected", worst === "connected");
    group.tabEl.classList.toggle("exited", worst === "exited");
    group.tabEl.classList.toggle("standby", worst === "standby");
    const label = group.tabEl.querySelector(".label");
    if (label) {
      const s = sessions.get(group.focus) || sessions.get(ids[0]);
      label.textContent = s ? s.name : group.name;
    }
    let badge = group.tabEl.querySelector(".pane-count");
    if (ids.length > 1) {
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "pane-count";
        group.tabEl.appendChild(badge);
      }
      badge.textContent = `⧉${ids.length}`;
    } else if (badge) {
      badge.remove();
    }
  }

  function fitGroup(group) {
    if (!group) return;
    for (const id of leafIds(group.root)) {
      const s = sessions.get(id);
      if (!s) continue;
      fitNow(s);
    }
  }

  /**
   * Split ``sessionId``'s pane in ``dir``, putting ``newId`` beside it.
   * ``newId`` may already exist (another session moved in) or be a fresh one.
   */
  function splitPane(sessionId, dir, newId) {
    const s = sessions.get(sessionId);
    const group = groupOf(s);
    if (!group) return false;
    const leaf = findLeaf(group.root, sessionId);
    if (!leaf) return false;
    const joined = sessions.get(newId);
    // Moving a pane that lives in another tab: take it out of that tab first,
    // or the old group keeps a leaf pointing at a pane that is no longer
    // there -- and a tab entry for a session that has moved.
    const donor = joined ? groupOf(joined) : null;
    if (donor && donor !== group) {
      closePane(newId, donor);
      if (!donor.root) {
        if (donor.tabEl) donor.tabEl.remove();
        donor.host.remove();
        tabGroups.delete(donor.id);
      } else {
        if (donor.focus === newId) donor.focus = leafIds(donor.root)[0] || null;
        renderLayout(donor);
        syncLayoutClasses(donor);
      }
    }
    const replacement = {
      type: "split", dir, ratio: 0.5,
      a: { type: "leaf", sessionId },
      b: { type: "leaf", sessionId: newId },
    };
    replaceNode(group.root, leaf, replacement, group);
    if (joined) {
      joined.groupId = group.id;
      // Two sessions, one tab: the other entry goes away and everything it
      // owned (label, state) becomes the group's.
      if (joined.tabEl && joined.tabEl !== group.tabEl) joined.tabEl.remove();
      joined.tabEl = group.tabEl;
    }
    renderLayout(group);
    group.focus = newId;
    syncLayoutClasses(group);
    fitGroup(group);
    saveLayout();
    return true;
  }

  function replaceNode(root, target, replacement, group) {
    if (root === target) { group.root = replacement; return true; }
    if (!root || root.type !== "split") return false;
    if (root.a === target) { root.a = replacement; return true; }
    if (root.b === target) { root.b = replacement; return true; }
    return replaceNode(root.a, target, replacement, group)
      || replaceNode(root.b, target, replacement, group);
  }

  /** Remove a pane from its group. Returns the session that now owns the tab. */
  function closePane(sessionId, knownGroup) {
    const group = knownGroup || groupOf(sessions.get(sessionId));
    if (!group) return null;
    const found = findParent(group.root, sessionId, null, null);
    if (!found) {
      group.root = null;
      return null;
    }
    const keep = found.side === "a" ? found.node.b : found.node.a;
    if (found.parent) {
      if (found.parent.a === found.node) found.parent.a = keep;
      else found.parent.b = keep;
    } else {
      group.root = keep;
    }
    return leafIds(group.root)[0] || null;
  }

  function moveFocus(dir) {
    const s = activeId && sessions.get(activeId);
    const group = groupOf(s);
    if (!group) return;
    const ids = leafIds(group.root);
    if (ids.length < 2) return;
    // Spatial, not list order: a 2x2 grid has a different "next" depending on
    // whether the direction is horizontal or vertical.
    const current = s.pane.getBoundingClientRect();
    const cx = current.left + current.width / 2;
    const cy = current.top + current.height / 2;
    let best = null;
    let bestScore = Infinity;
    for (const id of ids) {
      if (id === activeId) continue;
      const other = sessions.get(id);
      if (!other) continue;
      const box = other.pane.getBoundingClientRect();
      const ox = box.left + box.width / 2;
      const oy = box.top + box.height / 2;
      const along = (dir === "left" || dir === "right") ? ox - cx : oy - cy;
      const wrongWay = (dir === "right" || dir === "down") ? along <= 0 : along >= 0;
      if (wrongWay) continue;
      const across = (dir === "left" || dir === "right") ? Math.abs(oy - cy) : Math.abs(ox - cx);
      const score = Math.abs(along) + across * 2;
      if (score < bestScore) { bestScore = score; best = id; }
    }
    if (best) activateTab(best);
  }

  /**
   * Split the focused pane, putting a *new* session beside it.
   *
   * This is the fast path (Alt+\\ / Alt+Shift+\\): no dialog, a default shell
   * appears next to the one you were on, exactly like Alt+N but beside it
   * rather than as a new tab.
   */
  async function splitActive(dir) {
    const s = activeId && sessions.get(activeId);
    if (!s) { toast("没有活动会话", "error"); return; }
    if (sharedMode) { toast("共享会话不支持分屏", ""); return; }
    try {
      const info = await api("POST", "/api/sessions", {});
      // Build the pane first so the split has something to put in it, then
      // hand it to the existing tab rather than creating a second tab entry.
      const pane = createTab(info.id, info.name || "终端", {
        recording: info.recording,
        autoConnect: true,
        activate: false,
        groupId: s.groupId,
      });
      splitPane(s.id, dir, info.id);
      activateTab(info.id);
      return pane;
    } catch (err) { toast(String(err.message || err), "error"); }
    return null;
  }

  /**
   * Split the focused pane with a session that is already open.
   *
   * The picker is the "and which one?" half of the feature: without it the
   * only way to get two sessions side by side is to close one and start over.
   */
  async function splitWithExisting(dir) {
    const s = activeId && sessions.get(activeId);
    if (!s) { toast("没有活动会话", "error"); return; }
    const group = groupOf(s);
    const inGroup = new Set(leafIds(group && group.root));
    const candidates = [...sessions.values()].filter((o) => !inGroup.has(o.id));
    if (!candidates.length) {
      toast("没有可并入的其他会话，试试直接分屏新建", "");
      return;
    }
    const picked = await pickDialog(
      "与哪个会话分屏？",
      candidates.map((o) => ({ value: o.id, label: o.name })),
    );
    if (!picked) return;
    const other = sessions.get(picked);
    if (!other) return;
    if (!splitPane(s.id, dir, picked)) { toast("分屏失败", "error"); return; }
    activateTab(picked);
    fitGroup(group);
  }

  // -- 分屏布局的持久化 -------------------------------------------------
  //
  // Sessions outlive their connections, so a reload can rebuild the workbench:
  // the tree references session ids that are still alive on the server. A
  // session that has ended is dropped from the tree rather than left as a hole
  // that makes the whole layout look broken.
  const LAYOUT_KEY = "wsctl-layout";

  function serializeNode(node) {
    if (!node) return null;
    if (node.type === "leaf") return { type: "leaf", sessionId: node.sessionId };
    return {
      type: "split", dir: node.dir, ratio: node.ratio,
      a: serializeNode(node.a), b: serializeNode(node.b),
    };
  }
  function saveLayout() {
    try {
      localStorage.setItem(LAYOUT_KEY, JSON.stringify({
        version: 1,
        activeTabId,
        activeId,
        groups: [...tabGroups.values()].map((g) => ({
          id: g.id, focus: g.focus, name: g.name, root: serializeNode(g.root),
        })),
      }));
    } catch { /* 忽略 */ }
  }
  function loadLayout() {
    try {
      const parsed = JSON.parse(localStorage.getItem(LAYOUT_KEY) || "null");
      return parsed && parsed.version === 1 ? parsed : null;
    } catch { return null; }
  }

  // Rebuild one node from a snapshot.
  //
  // Every leaf in the snapshot comes back, including the ones whose session is
  // gone: the *shape* of the workbench is the user's, and a pane that is no
  // longer backed by a session degrades to a placeholder rather than silently
  // taking its half of the split with it.
  function deserializeNode(node) {
    if (!node) return null;
    if (node.type === "leaf") return { type: "leaf", sessionId: node.sessionId };
    const a = deserializeNode(node.a);
    const b = deserializeNode(node.b);
    if (a && b) return { type: "split", dir: node.dir, ratio: node.ratio, a, b };
    return a || b;
  }

  // -- 后台动静可见 -----------------------------------------------------
  //
  // Before this, a background session was silent in every sense: output,
  // BEL, a title change and the session exiting all left the tab bar, the
  // page title and the favicon untouched. An operator who put a long build in
  // one tab and worked in another had no way to know it had finished, other
  // than by clicking through every tab.
  //
  // xterm already reports every one of those facts (``onBell``,
  // ``onTitleChange``, ``onWriteParsed``); nothing was listening.
  const ACTIVITY_QUIET_MS = 4000;
  // How long after ``attached`` output is still "housekeeping", not news.
  //
  // Two things arrive in that window and neither is the shell saying
  // something: the attach replay (the screen the user already had) and the
  // repaint the server triggers right after it (``core.session.nudge_repaint``,
  // two real SIGWINCHes so a TUI redraws itself). The replay is marked by
  // ``attached`` coming after it; the repaint is not marked on the wire at
  // all, so the client cannot tell it from real output except by when it
  // arrives. One second is far longer than a repaint and far shorter than
  // anything a person would call "an event".
  const ACTIVITY_GRACE_MS = 1000;
  let unreadTimer = null;

  // Presence is a *derived view* of state that changes on every output frame.
  // Painting it inline would mean per-frame DOM work -- a full ``sessions``
  // walk, a querySelector each, a ``document.title`` write and a favicon
  // redraw -- in exactly the hot path 0.1.15–0.1.21 spent a release getting
  // out of (frame coalescing, write caps, resize merging). So this records
  // state and schedules **one** paint per animation frame, the same shape as
  // ``flushWrite``.
  let presenceFrame = 0;
  let lastFaviconCount = -1;

  function schedulePresence() {
    if (presenceFrame) return;
    presenceFrame = 1;
    // Two triggers, one paint. ``requestAnimationFrame`` is frame-aligned and
    // is the right tick **while the page is visible** -- but it is *paused*
    // for a hidden page, which is exactly when the page title and the favicon
    // are the only signals anyone can see. So a timer rides along, and
    // whichever fires first does the paint and disarms the other.
    const run = () => {
      if (!presenceFrame) return;
      presenceFrame = 0;
      paintPresence();
    };
    requestAnimationFrame(run);
    setTimeout(run, 50);
  }

  /** Output, a bell or an exit happened in ``s``. */
  function noteActivity(s, kind) {
    if (!s) return;
    // Before ``attached``, everything arriving is the *attach replay*: the
    // screen the user already had, re-delivered after a reconnect. The server
    // enqueues the replay and then ``attached`` under the same lock
    // (``core/session.py``), so ``attached`` is the fact that says the replay
    // is over. Counting replayed bytes as news made every network blip ring
    // every background tab with "有新输出"; the repaint that follows a replay
    // is the same false alarm, and is covered by ``ACTIVITY_GRACE_MS`` below.
    if (!s.attached) return;
    if (Date.now() - (s.attachedAt || 0) < ACTIVITY_GRACE_MS) return;
    s.lastActivityAt = Date.now();
    if (s.id === activeId) {
      // Nothing to report: the user is looking right at it. Still repaint so
      // the activity glow on the tab stays accurate.
      schedulePresence();
      return;
    }
    if (kind === "bell") s.bellCount = (s.bellCount || 0) + 1;
    s.unread = true;
    maybeNotify(s, kind);
    schedulePresence();
  }

  /** The user is looking at this pane now: whatever it had to say is read. */
  function clearActivity(s) {
    if (!s) return;
    s.unread = false;
    s.bellCount = 0;
    s.lastActivityAt = 0;
    schedulePresence();
  }

  /** Everything that says "there is something you have not looked at".

  Runs at most once per animation frame (see ``schedulePresence``), and every
  write below is idempotent -- so being called again with the same state costs
  nothing but the walk.
  */
  function paintPresence() {
    let count = 0;
    let firstName = "";
    let bell = false;
    let chatty = false;
    // Aggregate **per tab entry**, not per session. A split puts several
    // sessions on one ``tabEl``, so writing it from the per-session walk let
    // whichever session the Map happened to reach last win: a live pane's
    // glow was switched off by a quiet sibling, and a sibling with no bells
    // deleted the badge another one had just created. Both are "the tab says
    // nothing", which is the one outcome this whole section exists to avoid.
    const perTab = new Map();
    for (const s of sessions.values()) {
      const active = Boolean(s.lastActivityAt)
        && Date.now() - s.lastActivityAt <= ACTIVITY_QUIET_MS;
      const entry = perTab.get(s.tabEl) || { glow: false, bells: 0 };
      entry.glow = entry.glow || active;
      entry.bells += s.bellCount || 0;
      perTab.set(s.tabEl, entry);
      if (active) chatty = true;
      if (s.bellCount > 0) bell = true;
      if (!s.unread) continue;
      count += 1;
      if (!firstName) firstName = s.name;
    }
    for (const [tabEl, entry] of perTab) {
      tabEl.classList.toggle("activity", entry.glow);
      let badge = tabEl.querySelector(".bell-badge");
      if (entry.bells > 0) {
        if (!badge) {
          badge = document.createElement("span");
          badge.className = "bell-badge";
          tabEl.appendChild(badge);
        }
        badge.textContent = entry.bells > 9 ? "9+" : String(entry.bells);
        badge.title = `响铃 ${entry.bells} 次`;
      } else if (badge) {
        badge.remove();
      }
    }
    // The page title is what an operator sees from another browser tab or
    // from the taskbar, so it is the one signal that works while the page is
    // not visible at all.
    const root = "wsctl";
    const title = (prefs.notifyTitle !== false && count > 0)
      ? `${bell ? "\u{1F514}" : "\u25CF"} ${count}${firstName ? ` ${firstName}` : ""} \u2014 ${root}`
      : root;
    if (document.title !== title) document.title = title;
    paintFavicon(count);
    // The glow fades on its own so a chatty session does not keep it lit for
    // ever -- one timer for the whole stack, re-armed only while it is lit.
    if (unreadTimer) { clearTimeout(unreadTimer); unreadTimer = null; }
    if (chatty) {
      unreadTimer = setTimeout(() => {
        unreadTimer = null;
        schedulePresence();
      }, ACTIVITY_QUIET_MS + 50);
    }
  }

  // The favicon carries the unread count, which is the only signal that
  // survives being minimised. Drawn on a canvas over the shipped icon rather
  // than shipping N more files.
  let faviconLink = null;
  let faviconBase = null;
  function paintFavicon(count) {
    // Only on a *change* of the number. Redrawing on every paint meant an
    // Image allocation, a canvas pass and a ``toDataURL`` per output frame
    // during a flood -- thousands of them, all on the main thread.
    if (count === lastFaviconCount) return;
    lastFaviconCount = count;
    try {
      if (!faviconLink) {
        faviconLink = document.querySelector("link[rel='icon']") || null;
        if (!faviconLink) return;
        faviconBase = faviconLink.href;
      }
      if (!count) {
        faviconLink.href = faviconBase;
        return;
      }
      const img = new Image();
      img.onload = () => {
        const size = 64;
        const canvas = document.createElement("canvas");
        canvas.width = size;
        canvas.height = size;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        ctx.drawImage(img, 0, 0, size, size);
        ctx.beginPath();
        ctx.arc(size * 0.78, size * 0.22, size * 0.26, 0, Math.PI * 2);
        ctx.fillStyle = "#e5484d";
        ctx.fill();
        ctx.fillStyle = "#fff";
        ctx.font = `bold ${Math.round(size * 0.3)}px sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(count > 9 ? "9+" : String(count), size * 0.78, size * 0.23);
        faviconLink.href = canvas.toDataURL("image/png");
      };
      img.src = faviconBase;
    } catch { /* \u753b\u4e0d\u51fa\u6765\u5c31\u7b97\u4e86 */ }
  }

  // A system notification is opt-in: browsers require a permission prompt and
  // a page that spams them is worse than one that says nothing. Denied or
  // unsupported is a silent, honest no-op -- never an error.
  function maybeNotify(s, kind) {
    if (prefs.notifySystem === false) return;
    if (sharedMode) return;  // a viewer is watching, not operating
    if (!("Notification" in window)) return;
    const body = kind === "bell"
      ? "\u4f1a\u8bdd\u54cd\u94c3"
      : kind === "exit"
        ? "\u4f1a\u8bdd\u5df2\u9000\u51fa"
        : "\u6709\u65b0\u8f93\u51fa";
    const title = `wsctl \u00b7 ${s.name}`;
    try {
      const fire = () => {
        try {
          new Notification(title, { body, tag: `wsctl-${s.id}`, silent: true });
        } catch { /* \u5ffd\u7565 */ }
      };
      if (Notification.permission === "granted") fire();
      else if (Notification.permission === "default") {
        Notification.requestPermission().then((p) => { if (p === "granted") fire(); }).catch(() => {});
      }
    } catch { /* \u6d4f\u89c8\u5668\u4e0d\u7ed9\u5c31\u7b97\u4e86 */ }
  }

  // A short synthesised "ding". No asset to ship and no autoplay policy to
  // fight beyond the first gesture -- and if the context will not start, the
  // bell badge still says what happened.
  function playBell() {
    if (prefs.notifySound === false) return;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      if (!window.__wsctlAudio) window.__wsctlAudio = new Ctx();
      const ctx = window.__wsctlAudio;
      if (ctx.state === "suspended") ctx.resume().catch(() => {});
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.0001, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.09, ctx.currentTime + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.18);
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.2);
    } catch { /* \u58f0\u97f3\u662f\u53ef\u9009\u7684 */ }
  }

  // Construct a terminal in ``pane``: width table, addons, renderer, open.
  //
  // Extracted from ``createTab`` so it can be run a second time on the same
  // pane -- switching renderer under a live session is a rebuild of this
  // object, not a reload of the page. Every ordering promise lives in here and
  // is checked on the source: the width table is active before any data, and a
  // renderer that will not initialise degrades to the next one instead of
  // leaving a blank terminal.
  function buildTerminal(pane) {
    const term = new Terminal(Object.assign({}, TERM_OPTIONS, {
      fontSize: prefs.fontSize,
      fontFamily: prefs.fontFamily || DEFAULT_MONO,
      theme: resolveTheme(),
    }));
    // Width table first: without it xterm uses its bundled Unicode v6 data,
    // whose East Asian widths are years out of date. Every CJK glyph then
    // occupies the wrong number of cells and the whole line slides -- the
    // Chinese-text version of 花屏. Must be active before any data is written.
    if (window.Unicode11Addon && window.Unicode11Addon.Unicode11Addon) {
      try {
        term.loadAddon(new Unicode11Addon.Unicode11Addon());
        term.unicode.activeVersion = "11";
      } catch { /* 旧核心不支持时退回内置宽度表 */ }
    }
    // Which width table is actually in force. xterm's bundled one is Unicode v6
    // and its East Asian widths are years out of date; "Unicode 11" here is the
    // difference between aligned CJK columns and a line that slides. Surfaced in
    // Settings because that is a fact a support conversation needs.
    const unicodeVersion = (term.unicode && term.unicode.activeVersion) || "6";
    const fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    if (window.WebLinksAddon) term.loadAddon(new WebLinksAddon.WebLinksAddon());
    if (window.ImageAddon) term.loadAddon(new ImageAddon.ImageAddon());
    // Renderer: WebGL first (orders of magnitude faster under a flood), then
    // Canvas for machines where WebGL will not initialise (remote desktop,
    // software-only GL, locked-down browsers), then xterm's DOM renderer. A
    // failure to accelerate must degrade, never leave the terminal blank.
    let renderer = "dom";
    if (prefs.termRenderer !== "dom" && window.WebglAddon && window.WebglAddon.WebglAddon) {
      try {
        term.loadAddon(new WebglAddon.WebglAddon());
        renderer = "webgl";
      } catch { renderer = "dom"; }
    }
    if (renderer === "dom" && prefs.termRenderer === "webgl") {
      // Explicitly asked for WebGL and it failed: fall through to canvas.
      prefs.termRenderer = "auto";
    }
    if (renderer === "dom" && prefs.termRenderer !== "dom" && window.CanvasAddon && window.CanvasAddon.CanvasAddon) {
      try {
        term.loadAddon(new CanvasAddon.CanvasAddon());
        renderer = "canvas";
      } catch { renderer = "dom"; }
    }
    term.open(pane);
    return { term, fit, renderer, unicodeVersion };
  }

  // Handlers that belong to a terminal *instance* rather than to the pane.
  // A rebuild (``recreateTerminal``) creates a new instance and must wire it
  // again; anything wired to the pane instead is wired once and stays.
  function wireTerminal(s) {
    s.term.onData((data) => {
      if (s.writable === false) return;
      if (s.zmodemActive) return;
      sendInput(s, data);
    });
    // Coalesced: `fit()` fires this on every layout change, and dragging a
    // window produces one per pixel. Each one became a `resize` control message
    // and a SIGWINCH at the far end -- a full-screen app then repaints on every
    // one of them, which is both the flicker and the freeze. Also skip the
    // round trip entirely when the size did not actually change.
    s.term.onResize(({ cols, rows }) => scheduleResize(s, cols, rows));
    // The application said "look at me" (BEL) and named itself (OSC title).
    // Both were being thrown away.
    s.term.onBell(() => {
      playBell();
      noteActivity(s, "bell");
    });
    s.term.onTitleChange((title) => {
      if (!title) return;
      s.termTitle = String(title).slice(0, 120);
      // A program changing its title while you are elsewhere is exactly the
      // "something is happening over there" signal this section exists for.
      noteActivity(s, "output");
    });
  }

  // Swap the renderer under a live session.
  //
  // The renderer is baked into a Terminal at construction, so switching it is
  // a rebuild of that object -- and a rebuilt terminal starts blank. Bytes
  // cannot reconstruct a TUI screen (see ``requestResync``), so the rebuild
  // asks for a replay plus the server's application repaint, exactly like a
  // desync. A session that is not connected simply picks the renderer up on
  // its next attach, which replays the same way.
  function recreateTerminal(s) {
    if (!s) return;
    if (s.zmodemActive) {
      toast("文件传输进行中，无法切换渲染器", "warn");
      return;
    }
    try { s.term.dispose(); } catch { /* 忽略 */ }
    const built = buildTerminal(s.pane);
    s.term = built.term;
    s.fit = built.fit;
    s.renderer = built.renderer;
    s.unicodeVersion = built.unicodeVersion;
    // The search addon held a reference to the old instance; drop it so the
    // next search lazily binds to the new one instead of driving a disposed
    // terminal.
    s.searchAddon = null;
    wireTerminal(s);
    s.needsRefit = true;
    if (s.id === activeId) {
      fitNow(s);
      s.everShown = true;
    }
    updateRendererNote(s);
    if (s.ws && s.ws.readyState === WebSocket.OPEN) requestResync(s);
  }

  function createTab(id, name, opts) {
    const options = opts || {};
    const pane = document.createElement("div");
    pane.className = "term-pane";

    const built = buildTerminal(pane);
    const { term, fit, renderer, unicodeVersion } = built;

    // One pane is one group until something splits it. The group id is the
    // first session's id, so an unsplit tab is indistinguishable from the
    // tab-per-session model every other part of this file still speaks.
    const groupId = options.groupId || id;
    const existing = tabGroups.get(groupId);
    let tabEl;
    let group;
    if (existing) {
      // Joining an existing tab (a split, or a restore): the tab entry already
      // exists and must not be duplicated.
      group = existing;
      tabEl = existing.tabEl;
    } else {
      tabEl = document.createElement("div");
      tabEl.className = "tab";
      tabEl.innerHTML = '<span class="dot"></span><span class="label"></span><span class="close">\u00d7</span>';
      tabEl.querySelector(".label").textContent = name;
        els.tabs.appendChild(tabEl);
      group = {
        id: groupId,
        host: document.createElement("div"),
        root: null,
        focus: id,
        tabEl,
        name,
      };
      group.host.className = "tab-host mounted";
      els.wrap.appendChild(group.host);
      tabGroups.set(groupId, group);
    }
    if (!group.root) group.root = { type: "leaf", sessionId: id };

    const s = {
      id, name, term, fit, pane, tabEl, renderer, unicodeVersion,
      groupId,
      ws: null, heartbeat: null, reconnectTimer: null, delay: 500, retryIn: 0,
      intentional: false, exited: false, standby: false, attached: false,
      pendingReplayClear: false, shedSinceOpen: false,
      nameVersion: 0, attachNameVersion: 0,
      share: options.share || null,
      writable: options.writable !== false,
      recording: Boolean(options.recording),
      sentry: null, zmodemActive: false, zmodemWatchdog: null, everConnected: false,
      searchAddon: null,
      autoConnect: options.autoConnect !== false,
      // Outbound-terminal write batching (see ws.onmessage): frames queue here
      // and are handed to xterm once per animation frame instead of once per
      // WebSocket message.
      writeQueue: [], writeTimer: null, writeBatches: 0, writeFrames: 0,
      // A pane that has never been laid out must be measured before its first
      // show; after that ``needsRefit`` is what decides whether anything
      // actually changed (a theme tweak must not re-measure every hidden tab).
      needsRefit: false, everShown: false, resetLog: [],
      // Set while a resync replay is in flight; live frames are dropped, not
      // queued, because the replay is a superset of them.
      resyncing: false, resyncTimer: null,
      // Last size reported to the server, so a refit that changes nothing does
      // not become a resize storm (and a SIGWINCH) at the far end.
      sentCols: 0, sentRows: 0, resizeTimer: null,
      desynced: false,
      // The tab's visual state is the worst of the group's panes, so each
      // session remembers its own and the group aggregates them.
      tabState: options.autoConnect === false ? "standby" : "connected",
    };
    sessions.set(id, s);
    // The pane's box is now the authority on when this terminal must be
    // re-measured. See ``onPaneBoxChanged``: enumerating the operations that
    // change a box is what let some of them forget to call ``fit()``.
    watchPaneBox(s);
    renderLayout(group);

    tabEl.addEventListener("click", (e) => {
      if (e.target.classList.contains("close")) return;
      // A split tab is one entry: clicking it shows the group and puts the
      // keyboard where the user last left it.
      activateTab(group.focus || id);
    });
    tabEl.addEventListener("dblclick", async (e) => {
      if (e.target.classList.contains("close")) return;
      e.stopPropagation();
      // Renaming a tab renames the session that has the keyboard in it: the
      // tab label is derived from a session, and a split of two sessions has
      // no name of its own.
      const target = sessions.get(group.focus) || s;
      const name = await promptDialog("重命名会话", target.name);
      if (!name || name === target.name) return;
      // Mark the rename generation *before* the round trip so a concurrent
      // `attached` from a reconnect cannot clobber the result.
      target.nameVersion = (target.nameVersion || 0) + 1;
      const version = target.nameVersion;
      try {
        const info = await api("PATCH", `/api/sessions/${target.id}`, { name });
        if (version !== target.nameVersion) return;  // a newer rename won
        target.name = info.name;
        tabEl.querySelector(".label").textContent = info.name;
      } catch (err) { toast(String(err.message || err), "error"); }
    });
    // Closing a tab detaches everything in it (never kills: 0.1.1's promise is
    // that a connection and a session are different things).
    tabEl.querySelector(".close").addEventListener("click", (e) => {
      e.stopPropagation();
      detachGroup(group);
    });
    tabEl.addEventListener("contextmenu", (e) => { e.preventDefault(); openTabMenu(e.clientX, e.clientY, group.focus || id); });
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

    // Handlers that live on the *terminal instance*: rebuilt with it (see
    // ``recreateTerminal``), never accumulated on the pane.
    wireTerminal(s);

    // Terminal right-click. The old behaviour ("copy if there is a selection,
    // otherwise paste") is still available, but it was completely undiscoverable
    // and it is not what every user wants -- so it is one of three modes and
    // the default is a real menu. Wired once per pane: it addresses ``s``, not
    // the terminal instance, and must not double up on a rebuild.
    pane.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      const mode = prefs.rightClickMode || "menu";
      if (mode === "quick") {
        quickCopyOrPaste(s);
        return;
      }
      if (mode === "paste") {
        doPaste(s);
        return;
      }
      openTermMenu(e.clientX, e.clientY, s);
    });

    if (s.autoConnect) ensureConnected(s);
    else markTab(s, "standby");
    // `activate: false` keeps bulk creation (session restore) from cycling the
    // LRU: each created tab would otherwise come online and evict the last.
    if (options.activate !== false) activateTab(id);
    return s;
  }

  function activateTab(id) {
    const focused = sessions.get(id);
    if (!focused) return;
    activeId = id;
    const group = groupOf(focused);
    if (!group) return;
    activeTabId = group.id;
    group.focus = id;
    // One tab entry per group. Highlighting it per session would toggle the
    // same element N times for a split tab and end on whichever session the
    // map happened to visit last.
    for (const g of tabGroups.values()) {
      g.tabEl.classList.toggle("active", g.id === group.id);
      if (g.id === group.id) g.host.classList.add("mounted");
    }
    syncLayoutClasses(group);
    // Mount *before* measuring. `.term-pane` is `display:none` until then, and
    // `.term-pane.mounted.shown` is what makes it visible at all -- so a tab
    // activated before its socket finished opening (a standby tab, or the
    // moment between `createTab` and `onopen`) would otherwise be measured at
    // zero size and painted as nothing. A split shows several panes at once,
    // so every pane in the tab is brought online and measured, not just the
    // one holding the keyboard.
    for (const paneId of leafIds(group.root)) {
      const s = sessions.get(paneId);
      if (s) s.pane.classList.add("mounted");
    }
    els.recordBtn.classList.toggle("rec-on", Boolean(focused.recording));
    // Looking at a *tab* is what "read" means for everything it shows. A
    // split puts several panes on screen at once, and clearing only the
    // focused one left its visible siblings still reporting unread -- for
    // output that is right in front of the user.
    for (const paneId of leafIds(group.root)) clearActivity(sessions.get(paneId));
    // Opening a tab is the user saying "show me these". A split with a
    // suspended half is a split with a blank half.
    for (const paneId of leafIds(group.root)) {
      const s = sessions.get(paneId);
      if (s) ensureConnected(s);
    }
    // `connect()` may have skipped its own status write (it ran before
    // `activeId` pointed here), so repaint from the resulting state.
    paintConnection(focused);
    // Fit *synchronously* after the pane becomes visible. Deferring it to
    // requestAnimationFrame let the browser paint the tab switch first -- at
    // which point the terminal still had the hidden pane's zero size, so every
    // switch showed one blank frame and then jumped. Reading the layout here
    // forces the measurement before that paint can happen.
    // Only measure when something could have changed. A mounted pane keeps its
    // layout box while hidden (see ``.term-pane.mounted``), so a plain tab
    // switch does not need a re-measure -- and re-measuring is what used to
    // produce the blank frame.
    for (const paneId of leafIds(group.root)) {
      const s = sessions.get(paneId);
      if (!s) continue;
      if (s.needsRefit || !s.everShown) {
        fitNow(s);
        s.everShown = true;
      }
    }
    try { focused.term.refresh(0, Math.max(0, focused.term.rows - 1)); } catch { /* 忽略 */ }
    scheduleResize(focused, focused.term.cols, focused.term.rows);
    if (focused.desynced) showDesyncBar(); else hideDesyncBar();
    syncZmodemButton(focused);
    updateRendererNote(focused);
    requestAnimationFrame(() => focused.term.focus());
    saveLayout();
  }

  // "Which renderer am I actually on" is not a guess: WebGL falls back silently
  // on machines without it, and the difference is the whole story when someone
  // reports a slow terminal.
  const RENDERER_LABELS = { webgl: "WebGL", canvas: "Canvas", dom: "DOM" };

  function updateRendererNote(s) {
    if (!els.rendererNote) return;
    const label = RENDERER_LABELS[s && s.renderer] || "DOM";
    const uni = (s && s.unicodeVersion) || "6";
    els.rendererNote.textContent = `当前：${label} · Unicode ${uni}`;
  }

  // Renderer-independent screen text. The WebGL and Canvas renderers paint to
  // a canvas and leave no ``.xterm-rows`` DOM behind at all, so "what does the
  // terminal actually say" cannot be answered by reading the page -- which is
  // the first thing any support conversation asks for, and the only way to
  // assert on terminal content without pinning a renderer. Reads xterm's own
  // buffer, which holds the same text whichever renderer drew it.
  //
  // Which terminal: the one holding the keyboard. A split shows several at
  // once, and "the terminal" in any of this product's commands and toolbars
  // means the focused one -- so this reads the same one those act on.
  window.__wsctlScreen = () => {
    const s = activeId && sessions.get(activeId);
    if (!s || !s.term || !s.term.buffer) return "";
    const buf = s.term.buffer.active;
    const lines = [];
    for (let i = 0; i < buf.length; i += 1) {
      const line = buf.getLine(i);
      if (line) lines.push(line.translateToString(true));
    }
    return lines.join("\n");
  };

  // Per-session connection state, for support and for the reconnect/resync
  // tests. Same rationale as ``__wsctlScreen``: the questions a support
  // conversation starts with ("is it connected, is a replay pending, is the
  // write path locked?") cannot be answered by reading the DOM.
  window.__wsctlDiag = () => {
    const out = {};
    for (const [id, s] of sessions) {
      out[id] = {
        connected: Boolean(s.ws) && s.ws.readyState === WebSocket.OPEN,
        resyncing: Boolean(s.resyncing),
        pendingReplayClear: Boolean(s.pendingReplayClear),
        queued: s.writeQueue ? s.writeQueue.length : 0,
        queuedBytes: s.writeQueue
          ? s.writeQueue.reduce((n, c) => n + (c.length || 0), 0) : 0,
        desynced: Boolean(s.desynced),
        shedSinceOpen: Boolean(s.shedSinceOpen),
        everConnected: Boolean(s.everConnected),
        exited: Boolean(s.exited),
        wroteBytes: s.wroteBytes || 0,
        resetLog: s.resetLog || [],
      };
    }
    return out;
  };

  // The workbench as data: every tab's layout tree, where the keyboard is,
  // and how many sockets are live. Same rationale as the other hooks -- "why
  // is this pane here / why is that one not connected" is the first question a
  // support conversation about a split asks, and the DOM cannot answer it.
  window.__wsctlLayout = () => {
    const tabs = [];
    for (const g of tabGroups.values()) {
      tabs.push({
        id: g.id,
        focus: g.focus,
        name: g.name,
        root: serializeNode(g.root),
        panes: leafIds(g.root),
      });
    }
    return {
      activeTabId,
      activeId,
      liveSockets: liveOrder.length,
      autoConnectMax: AUTO_CONNECT_MAX,
      tabs,
    };
  };

  // Per-cell characters and widths for one row. Text alone cannot answer "do
  // the columns line up": ``translateToString`` gives one entry per *character*,
  // while a wide CJK glyph occupies two *cells*. Comparing where a marker
  // column lands in a CJK row against an ASCII row is what actually proves the
  // width table is right -- and it is renderer-independent, because it reads
  // xterm's buffer rather than anything that was painted. Same terminal as
  // ``__wsctlScreen``: the focused one.
  window.__wsctlRowCells = (row) => {
    const s = activeId && sessions.get(activeId);
    if (!s || !s.term || !s.term.buffer) return [];
    const line = s.term.buffer.active.getLine(row);
    if (!line) return [];
    const cells = [];
    for (let x = 0; x < line.length; x += 1) {
      const cell = line.getCell(x);
      if (!cell) break;
      cells.push({ x, ch: cell.getChars(), w: cell.getWidth() });
    }
    return cells;
  };

  // Detach one pane. In a split the tab survives with its remaining panes; a
  // tab's close button detaches the whole group instead (``detachGroup``).
  // Neither ever kills: 0.1.1's promise is that a connection and a session
  // are different things.
  function detachTab(id) {
    const s = sessions.get(id);
    if (!s) {
      // Not a live session: a placeholder left by one that ended. It still
      // occupies a slot and still has to come out when its tab goes.
      if (id && findLeafFromGroups(id)) closeGhostPane(id);
      return;
    }
    // Captured before the session is deleted: ``closePane`` finds the group
    // *through* the session, and by then it is gone.
    const group = tabGroups.get(s.groupId);
    s.intentional = true;
    if (s.heartbeat) clearInterval(s.heartbeat);
    resetTransients(s);
    if (s.zmodemWatchdog) { clearTimeout(s.zmodemWatchdog); s.zmodemWatchdog = null; }
    if (s.ws) { try { s.ws.close(); } catch { /* 忽略 */ } s.ws = null; }
    forgetLive(id);
    unwatchPaneBox(s);
    s.term.dispose();
    s.pane.remove();
    sessions.delete(id);
    closePane(id, group || undefined);
    // A leaf can be a placeholder for a session that is gone. Focusing one is
    // a no-op (``activateTab`` needs a live session), so the fallback must
    // skip them -- otherwise ``activeId`` keeps pointing at the pane we just
    // removed and every toolbar action silently does nothing.
    const nextLive = group
      ? (leafIds(group.root).find((paneId) => sessions.has(paneId)) || null)
      : null;
    if (group) {
      if (!group.root) {
        // The last pane went: the tab goes with it.
        if (group.tabEl) group.tabEl.remove();
        group.host.remove();
        tabGroups.delete(group.id);
      } else {
        if (group.focus === id) group.focus = nextLive;
        renderLayout(group);
        syncLayoutClasses(group);
        fitGroup(group);
      }
    } else if (s.tabEl) {
      s.tabEl.remove();
    }
    if (activeId === id) {
      const fallback = nextLive || sessions.keys().next().value;
      if (fallback) activateTab(fallback);
      else { activeId = null; activeTabId = null; }
    }
    saveLayout();
  }

  /** Detach every pane in a tab. */
  function detachGroup(group) {
    if (!group) return;
    // Snapshot: each detach rewrites the tree this list is read from.
    for (const paneId of leafIds(group.root).slice()) detachTab(paneId);
  }

  async function killTab(id) {
    const s = sessions.get(id);
    const name = s ? s.name : id;
    const ok = await confirmDialog(
      `确定终止会话「${name}」？该 shell 进程会被结束。`, "终止会话", { variant: "danger" },
    );
    if (!ok) return;
    detachTab(id);
    try { await api("DELETE", `/api/sessions/${id}`); toast("会话已终止", "ok"); }
    catch { /* 已不存在 */ }
    if (!els.sessionsOverlay.classList.contains("hidden")) refreshSessions();
  }

  // -- 终端右键菜单 -----------------------------------------------------

  function hasSelection(s) {
    return Boolean(s && s.term && s.term.getSelection && s.term.getSelection());
  }

  function doCopy(s) {
    const sel = s.term.getSelection && s.term.getSelection();
    if (!sel) { toast("没有选中内容", ""); return; }
    navigator.clipboard.writeText(sel)
      .then(() => toast("已复制", "ok"))
      .catch(() => toast("复制失败：浏览器拒绝了剪贴板访问", "error"));
  }

  function doPaste(s) {
    if (s.writable === false) { toast("会话为只读", ""); return; }
    navigator.clipboard.readText()
      .then((text) => { if (text) sendPaste(s, text); else toast("剪贴板为空", ""); })
      .catch(() => toast("粘贴失败：浏览器拒绝了剪贴板访问", "error"));
  }

  function quickCopyOrPaste(s) {
    if (hasSelection(s)) doCopy(s);
    else doPaste(s);
  }

  function changeFont(s, delta) {
    // Same bounds as the Settings picker and the README. A hotkey that could
    // reach a size the picker cannot show is how the three of them drifted.
    prefs.fontSize = Math.min(FONT_MAX, Math.max(FONT_MIN, prefs.fontSize + delta));
    applyPrefs();
    toast(`字号 ${prefs.fontSize}`, "");
  }

  function clearScreen(s) {
    // term.clear() wipes the scrollback too; what an operator usually wants is
    // a clean screen with history intact, which is Ctrl+L's job.
    sendInput(s, "\x0c");
  }

  let menuSession = null;
  // A context menu must never open where its own items are unreachable. The
  // position is the pointer, but the menu is taller and wider than a click --
  // near the bottom or the right edge the later items fall outside the window
  // and cannot be clicked at all (which is exactly how "关闭本分屏" became
  // unclickable once the menu grew). Measure after showing and flip/clamp.
  function placeMenu(el, x, y) {
    el.style.left = "0px";
    el.style.top = "0px";
    el.classList.remove("hidden");
    const box = el.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const pad = 8;
    const left = Math.min(Math.max(pad, x), Math.max(pad, vw - box.width - pad));
    const top = Math.min(Math.max(pad, y), Math.max(pad, vh - box.height - pad));
    el.style.left = `${Math.round(left)}px`;
    el.style.top = `${Math.round(top)}px`;
  }

  function openTermMenu(x, y, s) {
    menuSession = s;
    const sel = hasSelection(s);
    // Splitting only makes sense when there is a pane to split and room for
    // another one; "close this pane" only matters when there is more than one.
    const group = groupOf(s);
    const panes = group ? leafIds(group.root).length : 1;
    els.termMenu.querySelectorAll("button").forEach((b) => {
      const act = b.dataset.taction;
      if (act === "copy") b.disabled = !sel;
      if (act === "paste") b.disabled = s.writable === false;
      if (act === "detach" || act === "kill") b.disabled = sharedMode;
      if (act === "split-r" || act === "split-d"
          || act === "split-pick-r" || act === "split-pick-d") b.disabled = sharedMode;
      if (act === "close-pane") b.disabled = panes < 2;
    });
    placeMenu(els.termMenu, x, y);
  }
  function closeTermMenu() { els.termMenu.classList.add("hidden"); menuSession = null; }

  els.termMenu.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const s = menuSession;
      const act = btn.dataset.taction;
      closeTermMenu();
      if (!s) return;
      switch (act) {
        case "copy": doCopy(s); break;
        case "paste": doPaste(s); break;
        case "selectall":
          try { s.term.selectAll(); } catch { /* 忽略 */ }
          break;
        case "find": toggleSearch(true); break;
        case "clear": clearScreen(s); break;
        case "font+": changeFont(s, +1); break;
        case "font-": changeFont(s, -1); break;
        case "zmodem": toggleZmodem(s); break;
        case "split-r": splitActive("row").catch((e) => toast(String(e.message || e), "error")); break;
        case "split-d": splitActive("col").catch((e) => toast(String(e.message || e), "error")); break;
        case "split-pick-r": splitWithExisting("row").catch((e) => toast(String(e.message || e), "error")); break;
        case "split-pick-d": splitWithExisting("col").catch((e) => toast(String(e.message || e), "error")); break;
        case "close-pane": detachTab(s.id); break;
        case "detach": detachTab(s.id); break;
        case "kill": killTab(s.id); break;
        default: break;
      }
    });
  });

  function openTabMenu(x, y, id) {
    menuSid = id; menuOpenedAt = Date.now();
    placeMenu(els.tabMenu, x, y);
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
    closeTermMenu();
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

  window.addEventListener("resize", fitActiveSoon);
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
      if (!list.length) { clearLayout(); await newSession(true); return; }
      // Most recently used first: those are the ones that get a live socket.
      // The rest stay in the tab bar as "未连接" and attach when opened, so a
      // user with dozens of sessions does not open dozens of sockets.
      const ordered = list.slice().sort((a, b) => (b.last_active || 0) - (a.last_active || 0));
      const known = new Set(ordered.map((info) => info.id));
      const saved = loadLayout();
      const restored = saved && restoreLayout(saved, known, ordered);
      if (!restored) {
        clearLayout();
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
      }
    } catch (err) { setConnection(String(err.message || err), "bad"); }
  }

  /**
   * Rebuild the workbench from a saved layout.
   *
   * Sessions outlive their connections, so a reload can put back exactly what
   * was on screen: the same tabs, the same splits, the same ratios. A session
   * that has ended since is simply absent from ``known`` and is dropped from
   * the tree rather than left as a blank hole.
   */
  function restoreLayout(saved, known, ordered) {
    const groups = (saved.groups || []).filter((g) => g && g.root);
    if (!groups.length) return false;
    for (const g of groups) {
      // ``deserializeNode`` checks ``sessions.has``, so the panes must exist
      // first; until then the *raw* snapshot is what names them.
      for (const id of rawLeafIds(g.root)) {
        const info = ordered.find((row) => row.id === id);
        if (!info) continue;
        createTab(id, info.name, {
          recording: info.recording,
          // Attach what was on screen; the rest stay as "未连接" and come
          // online when opened, exactly as they would have before the reload.
          autoConnect: Boolean(known.has(id)),
          activate: false,
          groupId: g.id,
        });
      }
    }
    // ``createTab`` puts each new pane in as a single leaf; the saved tree is
    // the real shape and is applied once every pane exists.
    let any = false;
    for (const g of groups) {
      const live = tabGroups.get(g.id);
      if (!live) continue;
      const root = deserializeNode(g.root);
      if (!root) continue;
      live.root = root;
      if (g.focus && sessions.has(g.focus)) live.focus = g.focus;
      renderLayout(live);
      any = true;
    }
    if (!any) return false;
    const focus = saved.activeId && sessions.has(saved.activeId)
      ? saved.activeId
      : (groups[0] && rawLeafIds(groups[0].root)[0]);
    if (focus && sessions.has(focus)) activateTab(focus);
    return true;
  }

  /** Session ids named by a *snapshot* node (no liveness check). */
  function rawLeafIds(node, out) {
    out = out || [];
    if (!node) return out;
    if (node.type === "leaf") { out.push(node.sessionId); return out; }
    rawLeafIds(node.a, out);
    rawLeafIds(node.b, out);
    return out;
  }

  /** Forget every tab and pane; used when starting from an empty workbench. */
  function clearLayout() {
    for (const s of [...sessions.values()]) detachTab(s.id);
    tabGroups.clear();
    activeTabId = null;
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
    if (!(await confirmDialog(
      "生成新链接会立即使旧链接失效，是否继续？", "生成新链接", { variant: "warn" },
    ))) return;
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

  // ``argv`` is a list on both the live and the history path now. Accept the
  // old JSON-string shape too so a stale response cannot throw mid-render --
  // one bad row used to take the whole "已结束" list down with it.
  function argvLabel(argv) {
    try {
      const list = typeof argv === "string" ? JSON.parse(argv) : argv;
      return Array.isArray(list) ? list.join(" ") : "";
    } catch { return ""; }
  }

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
    // Row payloads live here, keyed by id, so the list can use one delegated
    // listener instead of three closures per row (see the file panel).
    const sessionRowData = new Map();

    els.sessionsList.addEventListener("click", async (e) => {
      const btn = e.target && e.target.closest ? e.target.closest("button[data-act]") : null;
      if (!btn) return;
      const act = btn.dataset.act;
      const row = sessionRowData.get(btn.dataset.sid);
      if (!row) return;
      if (act === "open") {
        if (sessions.has(row.id)) activateTab(row.id);
        else createTab(row.id, row.name, { recording: row.recording });
        els.sessionsOverlay.classList.add("hidden");
      } else if (act === "kill") {
        if (sessions.has(row.id)) { await killTab(row.id); }
        else {
          if (!(await confirmDialog(
            `确定终止会话「${row.name}」？`, "终止会话", { variant: "danger" },
          ))) return;
          try { await api("DELETE", `/api/sessions/${row.id}`); toast("会话已终止", "ok"); } catch { /* 忽略 */ }
          refreshSessions();
        }
      } else if (act === "detail") {
        try {
          const info = await api("GET", `/api/sessions/${encodeURIComponent(row.id)}/detail`);
          toast(`${info.name}：${info.status}，运行 ${formatDuration(info.duration)}`, "info");
        } catch (err) { toast(String(err.message || err), "error"); }
      } else if (act === "reopen") {
        try {
          // The server replays the *recorded* argv and backend; the client
          // only supplies display overrides. Sending our own argv used to be
          // how this worked, which made the sid decorative and let the caller
          // dictate what would be spawned.
          const info = await api("POST", `/api/sessions/${encodeURIComponent(row.id)}/reopen`, {
            name: row.name, cwd: row.cwd || undefined,
          });
          els.sessionsOverlay.classList.add("hidden");
          sessionTab = "live";
          createTab(info.id, info.name || "终端", { recording: info.recording });
        } catch (err) { toast(String(err.message || err), "error"); }
      }
    });

    function sessionButton(act, sid, label, cls) {
      const b = document.createElement("button");
      b.className = "text-btn" + (cls ? " " + cls : "");
      b.dataset.act = act;
      b.dataset.sid = sid;
      b.textContent = label;
      return b;
    }

    async function refreshSessions() {
      els.sessionsList.innerHTML = '<li class="sk"></li><li class="sk"></li><li class="sk"></li>';
      if (sessionTab === "history") return refreshSessionHistory();
      let list;
      try { list = await api("GET", "/api/sessions"); }
      catch (err) { toast(String(err.message || err), "error"); return; }
      const filter = (els.sessionFilter.value || "").trim().toLowerCase();
      const now = Date.now() / 1000;
      els.sessionsList.innerHTML = "";
      sessionRowData.clear();
      const shown = list.filter((i) =>
        !filter || i.name.toLowerCase().includes(filter) || i.id.toLowerCase().includes(filter));
      if (!shown.length) { els.sessionsList.innerHTML = '<li class="empty state-block"><span class="state-icon">🗂</span>暂无会话</li>'; return; }
      for (const info of shown) {
        sessionRowData.set(info.id, info);
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
        li.append(
          label, meta,
          sessionButton("open", info.id, sessions.has(info.id) ? "切换" : "打开"),
          sessionButton("kill", info.id, "终止", "danger"),
        );
        els.sessionsList.appendChild(li);
      }
    }

    async function refreshSessionHistory() {
      let rows;
      try { rows = await api("GET", "/api/sessions/history"); }
      catch (err) { toast(String(err.message || err), "error"); return; }
      const filter = (els.sessionFilter.value || "").trim().toLowerCase();
      els.sessionsList.innerHTML = "";
      sessionRowData.clear();
      const shown = rows.filter((r) =>
        !filter || String(r.name || "").toLowerCase().includes(filter)
          || String(r.id || "").toLowerCase().includes(filter));
      if (!shown.length) { els.sessionsList.innerHTML = '<li class="empty state-block"><span class="state-icon">🗂</span>暂无已结束的会话</li>'; return; }
      const STATUS_LABELS = { killed: "已终止", expired: "已过期", stopped: "已停止", interrupted: "已中断" };
      for (const r of shown) {
        sessionRowData.set(r.id, r);
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
        li.title = `命令：${r.command || argvLabel(r.argv) || "默认 shell"}`;
        // An SSH session needs its structured target (host/user/port/identity)
        // which history does not keep. Reopening it as a local shell would run
        // the remote command on this machine -- so the button says why instead.
        const reopen = sessionButton("reopen", r.id, "重新打开");
        if (r.backend === "ssh") {
          reopen.disabled = true;
          reopen.title = "SSH 会话需在「新建会话」中重新填写目标后打开";
        }
        li.append(label, meta, sessionButton("detail", r.id, "详情"), reopen);
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

  els.sessionFilter.addEventListener("input", debounceFilter(() => refreshSessions()));
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

  // A filter box refetches and rebuilds the whole list. On every keystroke
  // that is one request and one full DOM rebuild per character; with a few
  // hundred rows it is also a stutter per character.
  function debounceFilter(fn, ms) {
    const wait = ms === undefined ? 250 : ms;
    let timer = null;
    return () => {
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(() => { timer = null; fn(); }, wait);
    };
  }

  // The *table's* direction state is the only source of truth for sort order.
  // ``spec`` is the column definition and has never carried a direction --
  // reading ``spec._dir`` made every "descending" sort a lie (the header
  // showed ↓ while the rows stayed in ascending order).
  function sortRows(rows, key, spec, state) {
    const { field, numeric } = spec[key] || {};
    if (!field) return rows.slice();
    const dir = state && state.sort === key && state.dir === -1 ? -1 : 1;
    return rows.slice().sort((a, b) => {
      const x = a[field]; const y = b[field];
      if (numeric) return ((Number(x) || 0) - (Number(y) || 0)) * dir;
      return String(x ?? "").localeCompare(String(y ?? ""), "zh-Hans-CN") * dir;
    });
  }

  function buildTable(host, id, columns, rows, spec, renderRow) {
    const state = tableState[id] || (tableState[id] = { offset: 0, sort: null });
    const sorted = state.sort ? sortRows(rows, state.sort, spec, state) : rows.slice();
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

  // ``adminTab`` is the single source of truth; the buttons' ``active`` class
  // is *derived* from it. Two sources is exactly what shipped: ``index.html``
  // marked 概览 as selected while ``adminTab`` still said ``"users"``, so the
  // panel opened with 概览 highlighted and the user table underneath. The
  // selector is also scoped to ``[data-tab]`` -- a bare ``.tab2`` matches the
  // session dialog's 运行中/已结束 tabs too and used to clear their highlight.
  function setAdminTab(name) {
    adminTab = name;
    for (const t of document.querySelectorAll("#admin-overlay .tab2")) {
      t.classList.toggle("active", t.dataset.tab === adminTab);
    }
  }

  async function openAdmin() {
    els.adminOverlay.classList.remove("hidden");
    adminNote("");
    setAdminTab("overview");
    await renderAdmin();
  }
  els.adminBtn.addEventListener("click", openAdmin);
  els.adminClose.addEventListener("click", () => els.adminOverlay.classList.add("hidden"));
  document.querySelectorAll("#admin-overlay .tab2").forEach((tab) => {
    tab.addEventListener("click", () => {
      setAdminTab(tab.dataset.tab);
      renderAdmin();
    });
  });

  async function renderAdmin() {
    // Belt and braces: however ``renderAdmin`` is reached, the highlight and
    // the content must agree.
    setAdminTab(adminTab);
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

  // The QR dialog is a *two-step* enrolment: the secret is only provisioned
  // once a code from the authenticator verifies. Closing the dialog without
  // confirming discards the pending secret, so the account is never left
  // half-enrolled and locked out of its own login.
  let qrUsername = null;
  let qrConfirmed = false;

  function closeQrDialog(refresh) {
    const username = qrUsername;
    const confirmed = qrConfirmed;
    qrUsername = null;
    qrConfirmed = false;
    els.qrOverlay.classList.add("hidden");
    els.qrImage.innerHTML = "";
    if (els.qrCode) els.qrCode.value = "";
    if (els.qrError) { els.qrError.textContent = ""; els.qrError.classList.add("hidden"); }
    if (username && !confirmed) {
      api("DELETE", `/api/users/${encodeURIComponent(username)}/totp`).catch(() => {});
    }
    if (refresh) renderAdmin();
  }

  function showQrDialog(username, info) {
    qrUsername = username;
    qrConfirmed = false;
    els.qrTitle.textContent = `为 ${username} 启用 2FA`;
    els.qrSecret.textContent = info.secret;
    els.qrImage.innerHTML = info.qr_svg;
    els.qrOverlay.classList.remove("hidden");
    (els.qrCode || els.qrDone).focus();
  }

  els.qrDone.addEventListener("click", async () => {
    const username = qrUsername;
    if (!username) { closeQrDialog(true); return; }
    const code = (els.qrCode ? els.qrCode.value : "").trim();
    if (!code) {
      els.qrError.textContent = "请输入认证器上的 6 位验证码";
      els.qrError.classList.remove("hidden");
      return;
    }
    try {
      await api("POST", `/api/users/${encodeURIComponent(username)}/totp/confirm`, { code });
      qrConfirmed = true;
      closeQrDialog(true);
      toast("两步验证已启用", "ok");
    } catch (err) {
      els.qrError.textContent = String(err.message || err);
      els.qrError.classList.remove("hidden");
    }
  });
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
          tb.innerHTML = '<tr><td colspan="6" class="empty state-block"><span class="state-icon">🗒</span>暂无记录</td></tr>';
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
    if (!rows.length) { host.innerHTML = '<div class="empty state-block"><span class="state-icon">🎬</span>暂无录制</div>'; return; }
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

  // -- 配置（可在线写入） -----------------------------------------------
  //
  // The panel used to be a read-only table with a reload button: an operator
  // could see that ``max_sessions`` was wrong and then go and find a shell.
  // It is now editable, through the same writer the CLI's ``config set``
  // uses (``core/config_edit``) -- so both entry points keep the author's
  // inline comments, type-check the value and roll back rather than publish
  // a config the server cannot start from.
  //
  // Only fields a reload can reach are writable. A ``restart`` field says so
  // instead of accepting the edit and silently doing nothing.

  // A change that deserves a second look before it lands. Not "high risk" in
  // the abstract -- these are the ones that open a door or stop one opening.
  const CONFIG_DANGEROUS = {
    auth_required: (v) => v === false,
    allowed_origins: (v) => Array.isArray(v) && v.length === 0,
    allowed_ips: (v) => Array.isArray(v) && v.length === 0,
    trust_proxy: (v) => v === true,
    metrics_require_auth: (v) => v === false,
    cookie_secure: (v) => v === false,
    audit_input: (v) => v === true,
    record_input: (v) => v === true,
  };
  const CONFIG_DANGEROUS_WHY = {
    auth_required: "关闭认证后任何人都能打开终端。确定？",
    allowed_origins: "清空来源白名单后任何站点都能发起 WebSocket 连接。确定？",
    allowed_ips: "清空 IP 白名单后任何地址都能访问。确定？",
    trust_proxy: "信任反向代理头意味着客户端 IP 由代理声明。仅在确有可信代理时开启。",
    metrics_require_auth: "关闭后任何人无需登录即可读取监控指标。",
    cookie_secure: "关闭后 Cookie 可能经明文 HTTP 传输。",
    audit_input: "开启后会把终端按键内容写入审计日志，可能包含密码等敏感输入。",
    record_input: "开启后录制文件会包含终端输入，可能包含密码等敏感输入。",
  };

  // Values the settings model will only accept from a fixed set. A free-text
  // box for these means the operator types "infoo", gets a 400, and has to
  // guess the vocabulary -- the control should carry the vocabulary.
  const CONFIG_CHOICES = {
    default_backend: ["local", "tmux", "ssh"],
    log_level: ["debug", "info", "warning", "error", "critical"],
  };

  function configControl(field) {
    const raw = field.raw;
    const wrap = document.createElement("span");
    wrap.className = "config-control";
    if (typeof raw === "boolean") {
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = raw;
      wrap.appendChild(input);
      wrap.input = input;
      return wrap;
    }
    const choices = CONFIG_CHOICES[field.key];
    if (Array.isArray(choices)) {
      const input = document.createElement("select");
      input.className = "config-input";
      for (const value of choices) {
        const opt = document.createElement("option");
        opt.value = value;
        opt.textContent = value;
        input.appendChild(opt);
      }
      input.value = String(raw);
      wrap.appendChild(input);
      wrap.input = input;
      return wrap;
    }
    const input = document.createElement("input");
    input.type = typeof raw === "number" ? "number" : "text";
    input.value = Array.isArray(raw) ? JSON.stringify(raw) : (raw === null || raw === undefined ? "" : String(raw));
    input.className = "config-input";
    wrap.appendChild(input);
    wrap.input = input;
    return wrap;
  }

  function configReadback(field, wrap) {
    const input = wrap.input;
    if (typeof field.raw === "boolean") return Boolean(input.checked);
    const text = input.value.trim();
    if (Array.isArray(field.raw)) {
      // An empty box is an empty list, which is a real value (`allowed_origins
      // = []` means "no origin is allowed") -- not "unset".
      if (!text) return [];
      const parsed = JSON.parse(text);
      if (!Array.isArray(parsed)) throw new Error("需要一个数组，例如 [\"a\", \"b\"]");
      return parsed;
    }
    // An empty box on a scalar means "unset", and the writer removes the key.
    // Returning "" instead turned `default_cwd` from "no cwd" into "cwd of the
    // empty string", and asked an int field to be `""`.
    if (text === "") return null;
    if (typeof field.raw === "number") {
      const n = Number(text);
      if (!Number.isFinite(n)) throw new Error("需要一个数字");
      return Number.isInteger(field.raw) ? Math.trunc(n) : n;
    }
    return text;
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
    // One readout for both verbs. A save that reloads and a reload are the
    // same question ("what did that actually change, and did it complain?"),
    // and answering it in a toast alone means the answer is gone in 2.5s --
    // which is useless when the operator is reading the table below it.
    const status = document.createElement("span");
    status.className = "file-status";
    const report = (changed, errors, okWord) => {
      if (errors && errors.length) {
        status.textContent = `${okWord}，但重载有问题：` + errors.join("；");
        status.style.color = "var(--danger)";
      } else {
        status.textContent = `${okWord}；变更：` + ((changed || []).join("、") || "无");
        status.style.color = "var(--ok)";
      }
    };
    reloadBtn.addEventListener("click", async () => {
      try {
        const info = await api("POST", "/api/config/reload");
        report(info.changed, info.errors, "已重载");
        if ((info.errors || []).length) toast("配置重载存在问题", "error");
        else toast("配置已重载", "ok");
      } catch (err) {
        status.textContent = String(err.message || err);
        status.style.color = "var(--danger)";
      }
    });
    reloadBar.append(reloadBtn, status);
    host.appendChild(reloadBar);

    const table = document.createElement("table");
    table.className = "config-table";
    table.innerHTML = "<thead><tr><th>配置项</th><th>值</th><th>生效方式</th><th></th></tr></thead>";
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
      restart: "需要重启服务才生效（网络、TLS、数据目录、日志、Webhook 等），不支持在线写入。",
    };
    for (const field of data.fields || []) {
      const tr = document.createElement("tr");
      const writable = field.kind !== "restart";
      const nameCell = document.createElement("td");
      nameCell.textContent = field.key;
      const valCell = document.createElement("td");
      valCell.className = "val";
      const kindCell = document.createElement("td");
      kindCell.innerHTML = `<span class="kind-badge ${field.kind}" title="${esc(KIND_TIPS[field.kind] || KIND_TIPS.restart)}">${KIND_LABELS[field.kind] || "需重启"}</span>`;
      const actCell = document.createElement("td");

      let control = null;
      if (writable) {
        control = configControl(field);
        valCell.appendChild(control);
      } else {
        valCell.textContent = field.value === "" ? "（空）" : String(field.value);
        valCell.classList.add("readonly");
      }

      if (writable) {
        const save = document.createElement("button");
        save.type = "button";
        save.className = "text-btn";
        save.textContent = "保存";
        // ``element.hidden`` does not hide this: ``.text-btn`` declares
        // ``display: inline-flex``, which outranks the UA ``[hidden]`` rule.
        // The project's ``.hidden`` class is ``display: none !important`` and
        // is what every other toggle in this file uses.
        save.classList.add("hidden");
        const mark = () => {
          let next;
          try { next = configReadback(field, control); } catch { save.classList.remove("hidden"); return; }
          save.classList.toggle("hidden", JSON.stringify(next) === JSON.stringify(field.raw));
        };
        control.input.addEventListener("input", mark);
        control.input.addEventListener("change", mark);
        save.addEventListener("click", async () => {
          let value;
          try { value = configReadback(field, control); }
          catch (err) { toast(String(err.message || err), "error"); return; }
          const danger = CONFIG_DANGEROUS[field.key];
          if (danger && danger(value)) {
            const ok = await confirmDialog(
              CONFIG_DANGEROUS_WHY[field.key] || `确定修改 ${field.key}？`,
              "高风险配置", { variant: "warn" },
            );
            if (!ok) return;
          }
          try {
            const info = await api("PUT", "/api/config", { items: [{ key: field.key, value }] });
            field.raw = value;
            field.value = String(value);
            save.classList.add("hidden");
            report(info.changed, info.errors, `已保存 ${field.key}`);
            const errors = info.errors || [];
            if (errors.length) toast("已写入，但重载有问题：" + errors.join("；"), "warn");
            else toast(`已保存 ${field.key}`, "ok");
          } catch (err) {
            status.textContent = String(err.message || err);
            status.style.color = "var(--danger)";
            toast(String(err.message || err), "error");
          }
        });
        actCell.appendChild(save);
      } else {
        const note = document.createElement("span");
        note.className = "hint";
        note.textContent = "改配置文件后重启";
        note.title = KIND_TIPS.restart;
        actCell.appendChild(note);
      }

      tr.append(nameCell, valCell, kindCell, actCell);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    host.appendChild(table);
    if (me && me.role !== "admin") {
      adminNote("配置为只读视图", false);
      for (const el of host.querySelectorAll("input,button")) el.disabled = true;
    }
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
        if (s.searchAddon) s.searchAddon.clearDecorations();
        try { s.term.clearSelection(); } catch { /* 忽略 */ }
      }
    }
  }
  els.searchBtn.addEventListener("click", () => toggleSearch());
  els.searchClose.addEventListener("click", () => toggleSearch(false));
  els.searchNext.addEventListener("click", () => runSearch(1));
  els.searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (searchDebounce.timer !== null) {
        clearTimeout(searchDebounce.timer);
        searchDebounce.timer = null;
      }
      runSearch(e.shiftKey ? -1 : 1);
    }
    else if (e.key === "Escape") { e.preventDefault(); toggleSearch(false); }
  });
  // Debounced like every other filter box. "All matches" highlighting on every
  // keystroke is a full buffer scan plus a decoration rebuild; against a
  // flood-filled session that is a stutter per character.
  const searchDebounce = { timer: null };
  els.searchInput.addEventListener("input", () => {
    if (searchDebounce.timer !== null) clearTimeout(searchDebounce.timer);
    if (!els.searchInput.value) {
      searchDebounce.timer = null;
      els.searchCount.textContent = "";
      if (s && s.searchAddon) s.searchAddon.clearDecorations();
      return;
    }
    searchDebounce.timer = setTimeout(() => {
      searchDebounce.timer = null;
      runSearch(1);
    }, 250);
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
        releaseZmodem(s);
        toast("文件传输已中断，终端输入已恢复", "error");
      }
    }, ZMODEM_WATCHDOG_MS);
  }

  function releaseZmodem(s) {
    if (s.zmodemWatchdog) { clearTimeout(s.zmodemWatchdog); s.zmodemWatchdog = null; }
    s.zmodemActive = false;
    syncZmodemButton(s);
  }

  // The transfer switch must be unusable *while* a transfer runs. Unloading
  // the sentry mid-session handed the protocol bytes straight to
  // `term.write`, which rendered the ZMODEM handshake as text -- genuine
  // 花屏 -- and released the input lock so keystrokes went into the protocol
  // stream as well. There is now no code path that can do either.
  function syncZmodemButton(s) {
    const busy = Boolean(s && s.zmodemActive);
    els.zmodemBtn.disabled = busy;
    els.zmodemBtn.title = busy ? "传输进行中，结束后才能关闭" : "切换 ZMODEM 文件传输（sz/rz）";
    els.zmodemBtn.classList.toggle("locked", busy);
    const item = els.termMenu.querySelector('[data-taction="zmodem"]');
    if (item) {
      item.disabled = busy;
      item.title = busy ? "传输进行中，结束后才能关闭" : "切换 ZMODEM 传输";
    }
  }

  function toggleZmodem(s) {
    if (!s || s.zmodemActive) return;
    if (s.sentry) {
      s.sentry = null;
      releaseZmodem(s);
      els.zmodemBtn.classList.remove("rec-on");
    } else {
      s.sentry = makeSentry(s);
      els.zmodemBtn.classList.toggle("rec-on", Boolean(s.sentry));
    }
    syncZmodemButton(s);
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
    syncZmodemButton(s);
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
  els.zmodemBtn.addEventListener("click", () => toggleZmodem(activeId && sessions.get(activeId)));

  // -- 快捷键 -----------------------------------------------------------

  function runAction(action) {
    switch (action) {
      case "new_session":
        // Never swallow: a shortcut that fails silently is a shortcut that
        // looks broken rather than a shortcut that reported a problem.
        newSession(true).catch((e) => toast(String(e.message || e), "error"));
        break;
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
      case "copy_selection": if (activeId) doCopy(sessions.get(activeId)); break;
      case "paste_clipboard": if (activeId) doPaste(sessions.get(activeId)); break;
      case "font_inc": if (activeId) changeFont(sessions.get(activeId), +1); break;
      case "font_dec": if (activeId) changeFont(sessions.get(activeId), -1); break;
      case "clear_screen": if (activeId) clearScreen(sessions.get(activeId)); break;
      case "show_help": toggleHotkeyHelp(); break;
      case "split_right": splitActive("row").catch((e) => toast(String(e.message || e), "error")); break;
      case "split_down": splitActive("col").catch((e) => toast(String(e.message || e), "error")); break;
      case "focus_left": moveFocus("left"); break;
      case "focus_right": moveFocus("right"); break;
      case "focus_up": moveFocus("up"); break;
      case "focus_down": moveFocus("down"); break;
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
    const sel = s && s.term && s.term.getSelection ? s.term.getSelection() : "";

    // Ctrl+C with a selection is the terminal convention and is safe.
    if (s && sel && event.ctrlKey && !event.shiftKey && !event.altKey
        && event.key.toLowerCase() === "c") {
      event.preventDefault();
      doCopy(s);
      return;
    }
    // Ctrl+Shift+C / Ctrl+Shift+V are handled here on a best-effort basis.
    // Chrome, Edge and Firefox reserve Ctrl+Shift+C for the DevTools inspector
    // *at the browser level*, so this code often never runs -- which is why the
    // defaults moved to Alt+C / Alt+V and the help sheet marks these unreliable.
    if (s && event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "c") {
      event.preventDefault();
      doCopy(s);
      return;
    }
    if (s && event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "v") {
      event.preventDefault();
      doPaste(s);
      return;
    }
    // The traditional xterm pair. Unclaimed by every browser, so this is the
    // one keyboard path DevTools cannot steal.
    if (s && event.ctrlKey && event.key === "Insert") {
      event.preventDefault();
      doCopy(s);
      return;
    }
    if (s && event.shiftKey && !event.ctrlKey && event.key === "Insert") {
      event.preventDefault();
      doPaste(s);
      return;
    }

    // Escape closes any open context menu. This lives in the *capture* phase
    // on purpose: xterm.js handles Escape itself and calls stopPropagation,
    // so a bubble-phase listener here never sees the key and the menus would
    // not close.
    if (event.key === "Escape") {
      closeFileMenu();
      closeTermMenu();
      closeTabMenu();
    }

    // `?` opens the cheatsheet -- but only when the user is not typing into a
    // terminal. It used to swallow the keystroke unconditionally, so `?` never
    // reached the shell (`ls ?` became an overlay).
    const typingInTerminal = Boolean(
      event.target && event.target.closest && event.target.closest(".xterm"),
    );
    if (event.key === "?" && !event.ctrlKey && !event.altKey && !event.metaKey
        && !typingInTerminal) {
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
    toggle_sessions: "会话列表", toggle_settings: "设置", toggle_share: "分享会话",
    search: "搜索终端",
    copy_selection: "复制选区", paste_clipboard: "粘贴剪贴板",
    font_inc: "字号 +", font_dec: "字号 −", clear_screen: "清屏",
    show_help: "快捷键帮助",
    split_right: "向右分屏", split_down: "向下分屏",
    focus_left: "焦点向左", focus_right: "焦点向右",
    focus_up: "焦点向上", focus_down: "焦点向下",
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
    // Say which chords a browser may swallow. Advertising Ctrl+Shift+C as the
    // copy shortcut is how people ended up opening the DevTools inspector.
    const legend = document.createElement("p");
    legend.className = "legend";
    legend.innerHTML =
      "<span><b>✅</b> 可靠</span>" +
      "<span><b>⚠️</b> 部分浏览器可用</span>" +
      "<span><b>❌</b> 浏览器占用，请改用其它键</span>";
    els.hotkeyHelp.appendChild(legend);
    // Fixed chords, in raw form so the grade comes from the same source
    // (``chordGrade`` → ``BROWSER_RESERVED``) as the editable ones. Hardcoded
    // grades here are how the sheet and the reserved set drifted apart.
    const fixed = [
      ["alt+?", "打开 / 关闭本帮助（打字时用它，不抢 ? 键）"],
      ["?", "打开 / 关闭本帮助（仅在终端未聚焦时）"],
      ["Escape", "关闭最上层弹窗或搜索"],
      ["ctrl+c", "复制选区（有选区时）"],
      ["ctrl+shift+c", "复制选区"],
      ["ctrl+shift+v", "粘贴剪贴板"],
      ["ctrl+Insert", "复制（传统终端）"],
      ["shift+Insert", "粘贴（传统终端）"],
    ];
    const rows = Object.entries(prefs.keybindings)
      .map(([action, binding]) => [
        prettyCombo(binding) || "未绑定",
        HOTKEY_LABELS[action] || action,
        chordGrade(action, binding),
      ])
      .concat(
        fixed.map(([raw, label]) => [prettyCombo(raw), label, chordGrade(null, raw)]),
        [["终端右键", "弹出操作菜单（可在设置里改为快捷复制粘贴）", "ok"]],
      );
    for (const [combo, label, grade] of rows) {
      const row = document.createElement("div");
      row.className = "hotkey-row";
      const name = document.createElement("span");
      name.textContent = label;
      const kbd = document.createElement("kbd");
      kbd.textContent =
        (grade === "bad" ? "❌ " : grade === "warn" ? "⚠️ " : "") + combo;
      if (grade === "bad") kbd.title = "该组合键被浏览器占用（例如 DevTools），页面拦不住";
      else if (grade === "warn") kbd.title = "已尽力接管，但部分浏览器会抢先处理";
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
        // Also warn when the chord is one the browser claims first. Without
        // this the setting accepted it happily and the shortcut never worked.
        // The set is lowercase; the field is not necessarily (``alt+ArrowRight``).
        if (value && BROWSER_RESERVED.has(value.toLowerCase())) {
          toast(
            `⚠️ ${value} 被浏览器占用（例如 DevTools / 关闭标签页），实际会拦不住。建议换一组键`,
            "error",
          );
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
  els.settingsBtn.addEventListener("click", () => {
    els.settingsOverlay.classList.remove("hidden");
    updateRendererNote(activeId && sessions.get(activeId));
  });
  els.settingsClose.addEventListener("click", () => els.settingsOverlay.classList.add("hidden"));
  els.settingsDone.addEventListener("click", () => els.settingsOverlay.classList.add("hidden"));
  els.setRightclick.value = prefs.rightClickMode || "menu";
  els.setRightclick.addEventListener("change", () => {
    prefs.rightClickMode = els.setRightclick.value;
    applyPrefs();
  });
  // The renderer is baked into a Terminal when it is constructed, so a switch
  // is a rebuild of the terminal object. That used to mean "reload the page";
  // now every live session is rebuilt in place and asks for a replay, so the
  // change lands immediately instead of being a promise the user has to
  // fulfil by pressing F5.
  els.setRenderer.value = prefs.termRenderer || "auto";
  updateRendererNote(activeId && sessions.get(activeId));
  els.setRenderer.addEventListener("change", () => {
    prefs.termRenderer = els.setRenderer.value;
    applyPrefs();
    let rebuilt = 0;
    let deferred = 0;
    for (const s of sessions.values()) {
      // A suspended tab has no screen to lose; it picks the renderer up when
      // it is next attached, which replays the scrollback the same way.
      if (!s.everShown && s.standby) { deferred += 1; continue; }
      recreateTerminal(s);
      rebuilt += 1;
    }
    const parts = [];
    if (rebuilt) parts.push(`${rebuilt} 个会话已就地切换`);
    if (deferred) parts.push(`${deferred} 个未连接会话将在打开时生效`);
    toast(parts.length ? `渲染器已更改：${parts.join("，")}` : "渲染器已更改", "info");
  });
  els.setTheme.addEventListener("change", () => { prefs.theme = els.setTheme.value; applyPrefs(); });
  els.setFontsize.addEventListener("change", () => { prefs.fontSize = Number(els.setFontsize.value); applyPrefs(); });
  els.setFont.addEventListener("change", () => { prefs.fontFamily = els.setFont.value; applyPrefs(); });

  // Background-presence switches. Reading them back is what keeps the dialog
  // honest: the state on screen is the state in force.
  function syncNotifyControls() {
    if (els.setNotifySound) els.setNotifySound.checked = prefs.notifySound !== false;
    if (els.setNotifyTitle) els.setNotifyTitle.checked = prefs.notifyTitle !== false;
    if (els.setNotifySystem) els.setNotifySystem.checked = prefs.notifySystem !== false;
  }
  syncNotifyControls();
  const onNotifyChange = () => {
    prefs.notifySound = Boolean(els.setNotifySound && els.setNotifySound.checked);
    prefs.notifyTitle = Boolean(els.setNotifyTitle && els.setNotifyTitle.checked);
    prefs.notifySystem = Boolean(els.setNotifySystem && els.setNotifySystem.checked);
    applyPrefs();
    paintPresence();
    if (prefs.notifySystem && "Notification" in window && Notification.permission === "default") {
      Notification.requestPermission().catch(() => {});
    }
  };
  for (const el of [els.setNotifySound, els.setNotifyTitle, els.setNotifySystem]) {
    if (el) el.addEventListener("change", onNotifyChange);
  }
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
  let previewDirtyBase = null;
  let previewImageUrl = null;
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
    // No click handler: the crumb *buttons* navigate (wired above) and a
    // plain click on the bar itself has nothing to do. A handler that
    // immediately returns is a no-op wearing a handler's clothes.
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
        els.fileList.innerHTML = `<li class="empty state-block"><span class="state-icon">📁</span>${filter ? "没有匹配的条目" : "空目录"}</li>`;
      }
      for (const entry of data.entries) {
        const li = document.createElement("li");
        li.dataset.name = entry.name;
        li.dataset.type = entry.type;
        const icon = entry.type === "dir" ? "📁" : "📄";
        const meta = entry.type === "dir" ? "" : formatSize(entry.size);
        li.innerHTML = `<span class="ficon">${icon}</span><span class="fname">${esc(entry.name)}</span><span class="fmeta">${meta}</span>`;
        els.fileList.appendChild(li);
      }
      if (data.truncated) els.fileStatus.textContent = `${total} 项（已过滤）`;
      else if (!filter) els.fileStatus.textContent = `${total} 项`;
      else els.fileStatus.textContent = `${total} 项匹配`;
    } catch (err) { els.fileStatus.textContent = String(err.message || err); }
  }

  // One listener per container, not one per row. A 200-entry directory used to
  // install 200 click + 200 contextmenu + up to 200 drag handlers -- 800 live
  // listeners rebuilt on every page turn and every keystroke of the filter.
  // The row's identity comes from its dataset, so the list content is plain
  // markup and nothing needs a closure per entry.
  function fileEntryAt(target) {
    const li = target && target.closest ? target.closest("#file-list li[data-name]") : null;
    if (!li) return null;
    return { name: li.dataset.name, type: li.dataset.type, el: li };
  }

  function fileEntryPath(entry) {
    return filePath ? `${filePath}/${entry.name}` : entry.name;
  }

  els.fileList.addEventListener("click", (e) => {
    const entry = fileEntryAt(e.target);
    if (!entry) return;
    const child = fileEntryPath(entry);
    if (entry.type === "dir") { fileOffset = 0; loadFiles(child); }
    else window.location.href = `/api/files/download?path=${encodeURIComponent(child)}`;
  });

  els.fileList.addEventListener("contextmenu", (e) => {
    const entry = fileEntryAt(e.target);
    if (!entry) return;
    e.preventDefault();
    menuEntry = { name: entry.name, type: entry.type };
    openFileMenu(e.clientX, e.clientY);
  });

  // A directory is a drop target: dragging a file onto a folder puts it *in*
  // that folder, which is what people expect from a file manager.
  els.fileList.addEventListener("dragover", (e) => {
    const entry = fileEntryAt(e.target);
    els.fileList.classList.add("dragover");
    if (!entry || entry.type !== "dir") return;
    e.preventDefault();
    e.stopPropagation();
    entry.el.classList.add("dir-over");
  });
  els.fileList.addEventListener("dragleave", (e) => {
    const entry = fileEntryAt(e.target);
    if (entry) entry.el.classList.remove("dir-over");
  });
  els.fileList.addEventListener("drop", (e) => {
    const entry = fileEntryAt(e.target);
    e.preventDefault();
    els.fileList.classList.remove("dragover");
    if (entry) entry.el.classList.remove("dir-over");
    const dest = entry && entry.type === "dir" ? fileEntryPath(entry) : filePath;
    uploadFiles(e.dataTransfer.files, dest);
  });

  // -- 上下文菜单 -------------------------------------------------------
  function openFileMenu(x, y) {
    placeMenu(els.fileMenu, x, y);
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


  // -- 预览 / 编辑 ------------------------------------------------------
  //
  // A bitmap used to be download-or-nothing. It is now previewed in place --
  // and typed by the *server* from the file's leading bytes, because a name
  // is one `mv` away from lying. SVG is not a bitmap and stays in the text
  // pane, which is what it is.
  const IMAGE_NAME = /\.(png|jpe?g|gif|webp|bmp|avif)$/i;

  function showPreviewImage(url, bytes) {
    els.previewBody.classList.add("hidden");
    els.previewImage.classList.remove("hidden");
    els.previewImg.src = url;
    els.previewSave.disabled = true;
    els.previewSave.classList.add("hidden");
    els.previewNote.textContent = `图片 · ${formatSize(bytes)} · 只读`;
  }
  function showPreviewText() {
    els.previewImage.classList.add("hidden");
    els.previewImg.removeAttribute("src");
    els.previewBody.classList.remove("hidden");
    els.previewSave.classList.remove("hidden");
  }

  async function openPreview(rel) {
    previewPath = rel;
    els.previewTitle.textContent = rel.split("/").pop();
    els.previewNote.textContent = "读取中…";
    els.previewBody.value = "";
    showPreviewText();
    els.previewOverlay.classList.remove("hidden");

    // Bitmaps first. If the server refuses (a script named .png, an oversized
    // file, an SVG), fall through to the text preview, which explains why in
    // its own words rather than leaving a blank box.
    if (IMAGE_NAME.test(rel)) {
      try {
        const res = await fetch(`/api/files/image?path=${encodeURIComponent(rel)}`);
        if (res.ok && (res.headers.get("content-type") || "").startsWith("image/")) {
          const blob = await res.blob();
          if (previewImageUrl) URL.revokeObjectURL(previewImageUrl);
          previewImageUrl = URL.createObjectURL(blob);
          showPreviewImage(previewImageUrl, blob.size);
          return;
        }
      } catch { /* fall through to the text path */ }
    }

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
      previewDirtyBase = text;
      const encoding = res.headers.get("X-Wsctl-Encoding") || "utf-8";
      els.previewNote.textContent = `编码 ${encoding} · ${formatSize(new Blob([text]).size)} · 修改后点「保存」`;
      els.previewSave.disabled = false;
      els.previewBody.focus();
    } catch (err) {
      els.previewNote.textContent = String(err.message || err);
      els.previewSave.disabled = true;
    }
  }
  function closePreview(force) {
    // Losing a page of edits to a stray click on the backdrop is not a
    // confirmation-worthy trade. Ask when -- and only when -- the buffer
    // differs from what was loaded.
    const finish = () => {
      els.previewOverlay.classList.add("hidden");
      previewPath = null;
      previewDirtyBase = null;
      els.previewBody.value = "";
      els.previewSave.disabled = false;
      showPreviewText();
      if (previewImageUrl) { URL.revokeObjectURL(previewImageUrl); previewImageUrl = null; }
    };
    if (!force && previewDirtyBase !== null && els.previewBody.value !== previewDirtyBase) {
      confirmDialog("放弃未保存的修改？", "关闭预览", { variant: "warn" }).then((ok) => {
        if (ok) finish();
      });
      return;
    }
    finish();
  }
  els.previewClose.addEventListener("click", () => closePreview(false));
  els.previewCancel.addEventListener("click", () => closePreview(false));
  els.previewDownload.addEventListener("click", () => {
    if (!previewPath) return;
    window.location.href = `/api/files/download?path=${encodeURIComponent(previewPath)}`;
  });
  els.previewSave.addEventListener("click", async () => {
    if (!previewPath) return;
    try {
      await api("PUT", "/api/files/content", { path: previewPath, content: els.previewBody.value });
      toast("已保存", "ok");
      closePreview(true);
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
    uploadNext(queue, dest, false, { sent: 0, skipped: 0, failed: 0 });
  }

  async function uploadNext(queue, dest, overwrite, outcome) {
    const tally = outcome || { sent: 0, skipped: 0, failed: 0 };
    if (!queue.length) {
      els.upProgress.classList.add("hidden");
      uploadXhr = null;
      // Reload first, *then* report: `loadFiles` writes its own status line
      // and would immediately overwrite the outcome.
      await loadFiles(filePath);
      // The message must say what actually happened. Reporting 上传完成 after
      // the user *declined* an overwrite -- or cancelled -- is a lie, and a
      // stale 上传完成 is indistinguishable from a fresh one to anything
      // watching this element.
      if (tally.sent > 0) {
        const extra = tally.skipped ? `（跳过 ${tally.skipped} 个同名文件）` : "";
        els.fileStatus.textContent = `上传完成${extra}`;
        toast(`上传完成：${tally.sent} 个文件`, "ok");
      } else if (tally.failed > 0) {
        els.fileStatus.textContent = `上传失败（${tally.failed} 个文件）`;
        toast("上传失败", "error");
      } else {
        els.fileStatus.textContent = "未上传任何文件（已跳过）";
        toast("已跳过，未上传任何文件", "");
      }
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
      tally.failed += 1;
      els.upProgress.classList.add("hidden");
      els.fileStatus.textContent = "上传失败：网络错误";
    };
    xhr.onabort = () => {
      els.upProgress.classList.add("hidden");
      els.fileStatus.textContent = "已取消上传";
      uploadXhr = null;
      // An abort is not a success: never fall through to the completion path.
      queue.length = 0;
    };
    xhr.onload = async () => {
      uploadXhr = null;
      if (xhr.status === 409 && !overwrite) {
        // Never silently replace: ask first, then retry with overwrite=1.
        const ok = await confirmDialog(
          `「${file.name}」已存在，是否覆盖？`, "覆盖文件", { variant: "warn" },
        );
        if (!ok) {
          tally.skipped += 1;
          queue.shift();
          uploadNext(queue, dest, false, tally);
          return;
        }
        uploadNext(queue, dest, true, tally);
        return;
      }
      if (xhr.status >= 400) {
        let detail = xhr.statusText;
        try { detail = JSON.parse(xhr.responseText).detail || detail; } catch { /* 忽略 */ }
        tally.failed += 1;
        els.upProgress.classList.add("hidden");
        els.fileStatus.textContent = `失败：${detail}`;
        return;
      }
      els.upFill.style.width = "100%";
      tally.sent += 1;
      queue.shift();
      uploadNext(queue, dest, false, tally);
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

  els.fileFilter.addEventListener("input", debounceFilter(() => { fileOffset = 0; loadFiles(filePath); }));
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
  els.fileList.addEventListener("dragleave", () => els.fileList.classList.remove("dragover"));

  fillFontSizes();
  paintPresence();
  bootstrap();
})();
