const KEY_ALIASES: Record<string, string> = {
  Right: "ArrowRight",
  Left: "ArrowLeft",
  Up: "ArrowUp",
  Down: "ArrowDown",
  Esc: "Escape",
};

//: Shifted US-layout symbols produced by the same physical key. A binding
//: written as ``Alt+Shift+-`` must match ``event.key === "_"``; comparing only
//: the literal key made every shifted-symbol binding unreachable.
const SHIFTED: Record<string, string> = {
  _: "-",
  "+": "=",
  "{": "[",
  "}": "]",
  "|": "\\",
  ":": ";",
  '"': "'",
  "<": ",",
  ">": ".",
  "?": "/",
  "~": "`",
  "!": "1",
  "@": "2",
  "#": "3",
  $: "4",
  "%": "5",
  "^": "6",
  "&": "7",
  "*": "8",
  "(": "9",
  ")": "0",
};

function normaliseKey(key: string): string {
  if (key === " ") return "Space";
  const aliased = KEY_ALIASES[key] ?? key;
  if (aliased.length === 1) return aliased.toUpperCase();
  return aliased;
}

function keyMatches(event: KeyboardEvent, expected: string): boolean {
  const eventKey = normaliseKey(event.key);
  if (eventKey === expected) return true;
  if (SHIFTED[event.key] && normaliseKey(SHIFTED[event.key]) === expected) return true;
  // ``Alt+C`` on macOS produces ``ç`` while the physical key is still KeyC.
  // The physical code is the stable identity for chorded letter shortcuts.
  if (
    expected.length === 1 &&
    /^[A-Z]$/.test(expected) &&
    event.code === `Key${expected}` &&
    (event.ctrlKey || event.altKey || event.metaKey)
  ) {
    return true;
  }
  return false;
}

export function matchesBinding(event: KeyboardEvent, binding: string): boolean {
  if (!binding) return false;
  const parts = binding
    .split("+")
    .map((part) => part.trim())
    .filter(Boolean);
  const key = parts.pop() ?? "";
  const modifiers = new Set(parts.map((part) => part.toLowerCase()));
  if ((event as KeyboardEvent & { altGraphKey?: boolean }).altGraphKey && modifiers.has("alt")) {
    return false;
  }
  if (!keyMatches(event, normaliseKey(key))) return false;
  if (modifiers.has("ctrl") !== event.ctrlKey) return false;
  if (modifiers.has("alt") !== event.altKey) return false;
  const shiftedSymbol = event.key in SHIFTED;
  const implicitShift = shiftedSymbol && !modifiers.has("shift") && event.shiftKey;
  if (modifiers.has("shift") !== event.shiftKey && !implicitShift) return false;
  if (modifiers.has("meta") !== event.metaKey) return false;
  return true;
}

export function formatBinding(binding: string): string {
  return binding
    .split("+")
    .map((part) => {
      const lower = part.toLowerCase();
      if (lower === "ctrl") return "Ctrl";
      if (lower === "alt") return "Alt";
      if (lower === "shift") return "Shift";
      if (lower === "meta") return "Cmd";
      return part.length === 1 ? part.toUpperCase() : part;
    })
    .join("+");
}
