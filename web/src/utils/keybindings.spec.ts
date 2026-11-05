import { describe, expect, it } from "vitest";
import { formatBinding, matchesBinding } from "@/utils/keybindings";

function event(init: KeyboardEventInit): KeyboardEvent {
  return new KeyboardEvent("keydown", init);
}

describe("keybindings", () => {
  it("matches ctrl+shift+t", () => {
    expect(matchesBinding(event({ key: "T", ctrlKey: true, shiftKey: true }), "Ctrl+Shift+T")).toBe(
      true,
    );
  });

  it("does not match when a modifier is missing", () => {
    expect(matchesBinding(event({ key: "T", ctrlKey: true }), "Ctrl+Shift+T")).toBe(false);
  });

  it("matches alt+arrow", () => {
    expect(matchesBinding(event({ key: "ArrowRight", altKey: true }), "Alt+ArrowRight")).toBe(true);
  });

  it("treats + as = for font increase", () => {
    expect(matchesBinding(event({ key: "+", ctrlKey: true, shiftKey: true }), "Ctrl+=")).toBe(true);
  });

  it("formats a chord for display", () => {
    expect(formatBinding("Ctrl+Shift+P")).toBe("Ctrl+Shift+P");
  });
});
