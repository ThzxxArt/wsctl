import { defineStore } from "pinia";
import { computed, ref, watch } from "vue";

export interface Prefs {
  theme: "auto" | "dark" | "light";
  termTheme: string;
  fontSize: number;
  fontFamily: string;
  termRenderer: "auto" | "webgl" | "canvas" | "dom";
  scrollback: number;
  notifySound: boolean;
  notifyTitle: boolean;
  notifySystem: boolean;
  copyOnSelect: boolean;
  rightClickPaste: boolean;
  cursorBlink: boolean;
  language: "zh-CN" | "en";
  keybindings: Record<string, string>;
  customThemes: Record<string, TerminalTheme>;
  seenInsecureNotice: boolean;
  sidebarCollapsed: boolean;
  highContrast: boolean;
  screenReader: boolean;
  mobileGestures: boolean;
}

/**
 * xterm measures a cell from the font it renders with. Passing ``undefined``
 * made it fall back to the page's proportional ``v-sans``: cells were as wide
 * as ``W`` (13.8px at 14px) while ``i`` was 3.9px, leaving a visible gap after
 * every character. Always hand xterm a concrete stack.
 */
export const DEFAULT_TERM_FONT =
  '"JetBrains Mono", "Cascadia Code", "Fira Code", "SFMono-Regular", Menlo, Consolas, monospace';

export function terminalFontFamily(custom: string): string {
  return custom.trim() || DEFAULT_TERM_FONT;
}

export interface TerminalTheme {
  background: string;
  foreground: string;
  cursor?: string;
  selectionBackground?: string;
  black?: string;
  red?: string;
  green?: string;
  yellow?: string;
  blue?: string;
  magenta?: string;
  cyan?: string;
  white?: string;
  brightBlack?: string;
  brightRed?: string;
  brightGreen?: string;
  brightYellow?: string;
  brightBlue?: string;
  brightMagenta?: string;
  brightCyan?: string;
  brightWhite?: string;
}

export const THEMES: Record<string, TerminalTheme> = {
  dark: {
    background: "#0e1117",
    foreground: "#e6e9ef",
    cursor: "#4f9cf9",
    selectionBackground: "#2a3446",
  },
  light: {
    background: "#f6f7f9",
    foreground: "#1b2029",
    cursor: "#2f6fd0",
    selectionBackground: "#cfd8e6",
  },
  dracula: {
    background: "#282a36",
    foreground: "#f8f8f2",
    cursor: "#f8f8f2",
    selectionBackground: "#44475a",
    black: "#21222c",
    red: "#ff5555",
    green: "#50fa7b",
    yellow: "#f1fa8c",
    blue: "#bd93f9",
    magenta: "#ff79c6",
    cyan: "#8be9fd",
    white: "#f8f8f2",
    brightBlack: "#6272a4",
    brightRed: "#ff6e6e",
    brightGreen: "#69ff94",
    brightYellow: "#ffffa5",
    brightBlue: "#d6acff",
    brightMagenta: "#ff92df",
    brightCyan: "#a4ffff",
    brightWhite: "#ffffff",
  },
  solarized_dark: {
    background: "#002b36",
    foreground: "#839496",
    cursor: "#93a1a1",
    selectionBackground: "#073642",
    black: "#073642",
    red: "#dc322f",
    green: "#859900",
    yellow: "#b58900",
    blue: "#268bd2",
    magenta: "#d33682",
    cyan: "#2aa198",
    white: "#eee8d5",
    brightBlack: "#586e75",
    brightRed: "#cb4b16",
    brightGreen: "#586e75",
    brightYellow: "#657b83",
    brightBlue: "#839496",
    brightMagenta: "#6c71c4",
    brightCyan: "#93a1a1",
    brightWhite: "#fdf6e3",
  },
  solarized_light: {
    background: "#fdf6e3",
    foreground: "#657b83",
    cursor: "#586e75",
    selectionBackground: "#eee8d5",
    black: "#073642",
    red: "#dc322f",
    green: "#859900",
    yellow: "#b58900",
    blue: "#268bd2",
    magenta: "#d33682",
    cyan: "#2aa198",
    white: "#eee8d5",
    brightBlack: "#002b36",
    brightRed: "#cb4b16",
    brightGreen: "#586e75",
    brightYellow: "#657b83",
    brightBlue: "#839496",
    brightMagenta: "#6c71c4",
    brightCyan: "#93a1a1",
    brightWhite: "#fdf6e3",
  },
  nord: {
    background: "#2e3440",
    foreground: "#d8dee9",
    cursor: "#d8dee9",
    selectionBackground: "#434c5e",
    black: "#3b4252",
    red: "#bf616a",
    green: "#a3be8c",
    yellow: "#ebcb8b",
    blue: "#81a1c1",
    magenta: "#b48ead",
    cyan: "#88c0d0",
    white: "#e5e9f0",
    brightBlack: "#4c566a",
    brightRed: "#bf616a",
    brightGreen: "#a3be8c",
    brightYellow: "#ebcb8b",
    brightBlue: "#81a1c1",
    brightMagenta: "#b48ead",
    brightCyan: "#8fbcbb",
    brightWhite: "#eceff4",
  },
  gruvbox: {
    background: "#282828",
    foreground: "#ebdbb2",
    cursor: "#ebdbb2",
    selectionBackground: "#504945",
    black: "#282828",
    red: "#cc241d",
    green: "#98971a",
    yellow: "#d79921",
    blue: "#458588",
    magenta: "#b16286",
    cyan: "#689d6a",
    white: "#a89984",
    brightBlack: "#928374",
    brightRed: "#fb4934",
    brightGreen: "#b8bb26",
    brightYellow: "#fabd2f",
    brightBlue: "#83a598",
    brightMagenta: "#d3869b",
    brightCyan: "#8ec07c",
    brightWhite: "#ebdbb2",
  },
  monokai: {
    background: "#272822",
    foreground: "#f8f8f2",
    cursor: "#f8f8f0",
    selectionBackground: "#49483e",
    black: "#272822",
    red: "#f92672",
    green: "#a6e22e",
    yellow: "#f4bf75",
    blue: "#66d9ef",
    magenta: "#ae81ff",
    cyan: "#a1efe4",
    white: "#f8f8f2",
    brightBlack: "#75715e",
    brightRed: "#f92672",
    brightGreen: "#a6e22e",
    brightYellow: "#f4bf75",
    brightBlue: "#66d9ef",
    brightMagenta: "#ae81ff",
    brightCyan: "#a1efe4",
    brightWhite: "#f9f8f5",
  },
  one_dark: {
    background: "#282c34",
    foreground: "#abb2bf",
    cursor: "#528bff",
    selectionBackground: "#3e4451",
    black: "#282c34",
    red: "#e06c75",
    green: "#98c379",
    yellow: "#e5c07b",
    blue: "#61afef",
    magenta: "#c678dd",
    cyan: "#56b6c2",
    white: "#abb2bf",
    brightBlack: "#5c6370",
    brightRed: "#e06c75",
    brightGreen: "#98c379",
    brightYellow: "#e5c07b",
    brightBlue: "#61afef",
    brightMagenta: "#c678dd",
    brightCyan: "#56b6c2",
    brightWhite: "#ffffff",
  },
  tokyo_night: {
    background: "#1a1b26",
    foreground: "#c0caf5",
    cursor: "#c0caf5",
    selectionBackground: "#33467c",
    black: "#15161e",
    red: "#f7768e",
    green: "#9ece6a",
    yellow: "#e0af68",
    blue: "#7aa2f7",
    magenta: "#bb9af7",
    cyan: "#7dcfff",
    white: "#a9b1d6",
    brightBlack: "#414868",
    brightRed: "#f7768e",
    brightGreen: "#9ece6a",
    brightYellow: "#e0af68",
    brightBlue: "#7aa2f7",
    brightMagenta: "#bb9af7",
    brightCyan: "#7dcfff",
    brightWhite: "#c0caf5",
  },
};

export const DEFAULT_KEYBINDINGS: Record<string, string> = {
  // Ctrl+Shift+T/W are the browser's own reopen/close-window shortcuts; a
  // page cannot reliably intercept them, and the terminal loses a tab or the
  // whole window. Chorded Alt+Shift avoids that.
  "tab.new": "Alt+Shift+T",
  "tab.close": "Alt+Shift+W",
  "tab.duplicate": "Alt+Shift+D",
  "pane.splitRight": "Alt+Shift+ArrowRight",
  "pane.splitDown": "Alt+Shift+ArrowDown",
  "pane.maximize": "Alt+Enter",
  "pane.focusNext": "Alt+Shift+L",
  "pane.focusPrev": "Alt+Shift+H",
  "pane.grow": "Alt+Shift+=",
  "pane.shrink": "Alt+Shift+-",
  "terminal.copy": "Alt+C",
  "terminal.paste": "Alt+Shift+V",
  "terminal.search": "Ctrl+Shift+F",
  "terminal.clear": "Ctrl+Shift+K",
  "palette.open": "Ctrl+Shift+P",
  "palette.openAlt": "Ctrl+K",
  quickSwitch: "Ctrl+P",
  "settings.open": "Ctrl+,",
  "font.increase": "Ctrl+=",
  "font.decrease": "Ctrl+-",
  "font.reset": "Ctrl+0",
};

const STORAGE_KEY = "wsctl-prefs";

function readPrefs(): Partial<Prefs> {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export const usePrefsStore = defineStore("prefs", () => {
  const stored = readPrefs();
  const prefs = ref<Prefs>({
    theme: (stored.theme as Prefs["theme"]) ?? "auto",
    termTheme: stored.termTheme ?? "dark",
    fontSize: Number(stored.fontSize) || 14,
    fontFamily: stored.fontFamily ?? "",
    termRenderer: (stored.termRenderer as Prefs["termRenderer"]) ?? "auto",
    scrollback: Number(stored.scrollback) || 10000,
    notifySound: stored.notifySound ?? true,
    notifyTitle: stored.notifyTitle ?? true,
    notifySystem: stored.notifySystem ?? true,
    copyOnSelect: stored.copyOnSelect ?? true,
    rightClickPaste: stored.rightClickPaste ?? false,
    cursorBlink: stored.cursorBlink ?? true,
    language: stored.language ?? "zh-CN",
    keybindings: { ...DEFAULT_KEYBINDINGS, ...(stored.keybindings ?? {}) },
    customThemes: stored.customThemes ?? {},
    seenInsecureNotice: stored.seenInsecureNotice ?? false,
    sidebarCollapsed: stored.sidebarCollapsed ?? false,
    highContrast: stored.highContrast ?? false,
    screenReader: stored.screenReader ?? false,
    mobileGestures: stored.mobileGestures ?? true,
  });

  const terminalTheme = computed<TerminalTheme>(
    () =>
      prefs.value.customThemes[prefs.value.termTheme] ??
      THEMES[prefs.value.termTheme] ??
      THEMES.dark,
  );

  const effectivePageTheme = computed<"dark" | "light">(() => {
    if (prefs.value.theme !== "auto") return prefs.value.theme;
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  });

  function persist(): void {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs.value));
    } catch {
      /* storage full or disabled */
    }
  }

  function apply(): void {
    document.documentElement.dataset.theme = effectivePageTheme.value;
    document.documentElement.dataset.contrast = prefs.value.highContrast ? "high" : "normal";
    document.documentElement.dataset.a11y = prefs.value.screenReader ? "screen-reader" : "normal";
    document.documentElement.style.setProperty("--term-font-size", `${prefs.value.fontSize}px`);
    if (prefs.value.fontFamily) {
      document.documentElement.style.setProperty("--term-font-family", prefs.value.fontFamily);
    } else {
      document.documentElement.style.removeProperty("--term-font-family");
    }
    persist();
  }

  function fontSizeUp(): void {
    prefs.value.fontSize = Math.min(32, prefs.value.fontSize + 1);
    apply();
  }

  function fontSizeDown(): void {
    prefs.value.fontSize = Math.max(8, prefs.value.fontSize - 1);
    apply();
  }

  function fontSizeReset(): void {
    prefs.value.fontSize = 14;
    apply();
  }

  watch(prefs, apply, { deep: true });
  apply();

  return {
    prefs,
    terminalTheme,
    effectivePageTheme,
    apply,
    persist,
    fontSizeUp,
    fontSizeDown,
    fontSizeReset,
  };
});
