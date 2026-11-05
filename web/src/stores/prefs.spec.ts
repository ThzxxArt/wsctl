import { describe, expect, it } from "vitest";

import { DEFAULT_TERM_FONT, terminalFontFamily } from "./prefs";

describe("terminal font selection", () => {
  it("uses a concrete monospace stack when the preference is empty", () => {
    const font = terminalFontFamily("");
    expect(font).toBe(DEFAULT_TERM_FONT);
    expect(font).toContain("monospace");
    expect(font).not.toContain("v-sans");
  });

  it("treats whitespace as empty instead of passing it to xterm", () => {
    expect(terminalFontFamily("   ")).toBe(DEFAULT_TERM_FONT);
  });

  it("keeps a custom family but trims accidental padding", () => {
    expect(terminalFontFamily('  "Fira Code", monospace  ')).toBe('"Fira Code", monospace');
  });
});
