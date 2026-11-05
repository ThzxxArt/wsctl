function normaliseKey(key: string): string {
  if (key === " ") return "Space";
  if (key.length === 1) return key.toUpperCase();
  return key;
}

export function matchesBinding(event: KeyboardEvent, binding: string): boolean {
  if (!binding) return false;
  const parts = binding
    .split("+")
    .map((part) => part.trim())
    .filter(Boolean);
  const key = parts.pop() ?? "";
  const modifiers = new Set(parts.map((part) => part.toLowerCase()));
  const expectedKey = normaliseKey(key);
  const eventKey = normaliseKey(event.key);
  const plusAsEquals = expectedKey === "=" && eventKey === "+";
  if (eventKey !== expectedKey && !plusAsEquals) return false;
  if (modifiers.has("ctrl") !== event.ctrlKey) return false;
  if (modifiers.has("alt") !== event.altKey) return false;
  if (!plusAsEquals && modifiers.has("shift") !== event.shiftKey) return false;
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
