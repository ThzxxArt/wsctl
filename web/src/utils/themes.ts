import type { TerminalTheme } from "@/stores/prefs";

const ANSI = [
  "black",
  "red",
  "green",
  "yellow",
  "blue",
  "magenta",
  "cyan",
  "white",
  "brightBlack",
  "brightRed",
  "brightGreen",
  "brightYellow",
  "brightBlue",
  "brightMagenta",
  "brightCyan",
  "brightWhite",
] as const;

function normalize(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  let text = value.trim();
  if (!text) return undefined;
  if (/^[0-9a-fA-F]{6}$/.test(text)) text = `#${text}`;
  return text;
}

function fromWindowsTerminal(data: Record<string, unknown>): TerminalTheme {
  const theme: TerminalTheme = {
    background: normalize(data.background) ?? "#0e1117",
    foreground: normalize(data.foreground) ?? "#e6e9ef",
  };
  const cursor = normalize(data.cursorColor);
  if (cursor) theme.cursor = cursor;
  const selection = normalize(data.selectionBackground);
  if (selection) theme.selectionBackground = selection;
  for (const name of ANSI) {
    const value = normalize(data[name]);
    if (value) theme[name] = value;
  }
  return theme;
}

function component(node: Element, key: string): number | undefined {
  const children = Array.from(node.children);
  for (let index = 0; index < children.length; index += 1) {
    const child = children[index];
    if (child.tagName.toLowerCase() === "key" && child.textContent === key) {
      const value = children[index + 1];
      if (value) {
        const number = Number.parseFloat(value.textContent ?? "");
        if (Number.isFinite(number)) return Math.round(number * 255);
      }
    }
  }
  return undefined;
}

function colorNode(node: Element): string | undefined {
  const r = component(node, "Red Component");
  const g = component(node, "Green Component");
  const b = component(node, "Blue Component");
  if (r === undefined || g === undefined || b === undefined) return undefined;
  return `#${[r, g, b].map((value) => value.toString(16).padStart(2, "0")).join("")}`;
}

function fromGnomeTerminal(text: string): TerminalTheme {
  const values = new Map<string, string>();
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^\s*([a-z-]+)\s*=\s*(.+?)\s*$/i);
    if (!match) continue;
    let value = match[2].trim();
    if (
      (value.startsWith("'") && value.endsWith("'")) ||
      (value.startsWith('"') && value.endsWith('"'))
    ) {
      value = value.slice(1, -1);
    }
    values.set(match[1].toLowerCase(), value);
  }
  const theme: TerminalTheme = {
    background: normalize(values.get("background-color")) ?? "#000000",
    foreground: normalize(values.get("foreground-color")) ?? "#ffffff",
  };
  const cursor = normalize(values.get("cursor-color"));
  if (cursor) theme.cursor = cursor;
  const palette = values.get("palette");
  if (palette) {
    const colors = [...palette.matchAll(/#[0-9a-fA-F]{6}/g)].map((match) => match[0]);
    ANSI.forEach((name, index) => {
      if (colors[index]) theme[name] = colors[index];
    });
  }
  return theme;
}

export function isGnomeTerminalProfile(text: string): boolean {
  return /foreground-color\s*=/i.test(text) && /palette\s*=\s*\[/i.test(text);
}

function fromItermColors(text: string): TerminalTheme {
  const document = new DOMParser().parseFromString(text, "application/xml");
  if (document.querySelector("parsererror")) throw new Error("不是合法的 iTerm2 plist");
  const root = document.documentElement;
  const rootDict =
    root.tagName.toLowerCase() === "dict"
      ? root
      : (Array.from(root.children).find((child) => child.tagName.toLowerCase() === "dict") ?? root);
  const dicts = new Map<string, Element>();
  const children = Array.from(rootDict.children);
  for (let index = 0; index < children.length; index += 1) {
    const child = children[index];
    if (child.tagName.toLowerCase() === "key") {
      const value = children[index + 1];
      if (value && value.tagName.toLowerCase() === "dict") {
        dicts.set(child.textContent ?? "", value);
      }
    }
  }
  const theme: TerminalTheme = {
    background: colorNode(dicts.get("Background Color") ?? rootDict) ?? "#000000",
    foreground: colorNode(dicts.get("Foreground Color") ?? rootDict) ?? "#ffffff",
  };
  const cursor = colorNode(dicts.get("Cursor Color") ?? rootDict);
  if (cursor) theme.cursor = cursor;
  const selection = colorNode(dicts.get("Selection Color") ?? rootDict);
  if (selection) theme.selectionBackground = selection;
  ANSI.forEach((name, index) => {
    const value = dicts.get(`Ansi ${index} Color`);
    if (value) {
      const color = colorNode(value);
      if (color) theme[name] = color;
    }
  });
  return theme;
}

export function parseTheme(text: string, filename = ""): { name: string; theme: TerminalTheme } {
  const lower = filename.toLowerCase();
  if (isGnomeTerminalProfile(text)) {
    const match = text.match(/visible-name\s*=\s*['"]?([^'"\n]+)/i);
    const name =
      match?.[1]?.trim() || filename.replace(/\.(dconf|theme|txt)$/i, "") || "GNOME Terminal";
    return { name, theme: fromGnomeTerminal(text) };
  }
  if (lower.endsWith(".itermcolors") || text.includes("<plist")) {
    return {
      name: filename.replace(/\.itermcolors$/i, "") || "iTerm2",
      theme: fromItermColors(text),
    };
  }
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(text);
  } catch (error) {
    throw new Error(`配色 JSON 解析失败：${(error as Error).message}`);
  }
  if (!data || typeof data !== "object") throw new Error("配色必须是 JSON 对象");
  const name =
    typeof data.name === "string" ? data.name : filename.replace(/\.json$/i, "") || "custom";
  const nested = (data.theme ?? data.colors ?? data) as Record<string, unknown>;
  return { name, theme: fromWindowsTerminal(nested) };
}

export function themeToJson(name: string, theme: TerminalTheme): string {
  return JSON.stringify({ name, ...theme }, null, 2);
}
