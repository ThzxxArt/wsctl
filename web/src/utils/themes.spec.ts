import { describe, expect, it } from "vitest";
import { parseTheme, themeToJson } from "@/utils/themes";

describe("theme import", () => {
  it("imports a Windows Terminal JSON scheme", () => {
    const { name, theme } = parseTheme(
      JSON.stringify({
        name: "Ocean",
        background: "#001122",
        foreground: "#ddeeff",
        red: "#ff0000",
        brightBlue: "#8899ff",
      }),
      "ocean.json",
    );
    expect(name).toBe("Ocean");
    expect(theme.background).toBe("#001122");
    expect(theme.foreground).toBe("#ddeeff");
    expect(theme.red).toBe("#ff0000");
    expect(theme.brightBlue).toBe("#8899ff");
  });

  it("imports iTerm2 .itermcolors plist colours", () => {
    const plist = `<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Background Color</key><dict><key>Red Component</key><real>0</real><key>Green Component</key><real>0.1</real><key>Blue Component</key><real>0.2</real></dict>
  <key>Foreground Color</key><dict><key>Red Component</key><real>1</real><key>Green Component</key><real>1</real><key>Blue Component</key><real>1</real></dict>
  <key>Ansi 1 Color</key><dict><key>Red Component</key><real>1</real><key>Green Component</key><real>0</real><key>Blue Component</key><real>0</real></dict>
</dict></plist>`;
    const { name, theme } = parseTheme(plist, "Dracula.itermcolors");
    expect(name).toBe("Dracula");
    expect(theme.background).toBe("#001a33");
    expect(theme.red).toBe("#ff0000");
  });

  it("imports a GNOME Terminal dconf profile", () => {
    const profile = `[/]
visible-name='Solarized'
background-color='#002b36'
foreground-color='#839496'
cursor-color='#93a1a1'
palette=['#073642', '#dc322f', '#859900', '#b58900', '#268bd2', '#d33682', '#2aa198', '#eee8d5', '#002b36', '#cb4b16', '#586e75', '#657b83', '#839496', '#6c71c4', '#93a1a1', '#fdf6e3']
`;
    const { name, theme } = parseTheme(profile, "gnome.dconf");
    expect(name).toBe("Solarized");
    expect(theme.background).toBe("#002b36");
    expect(theme.red).toBe("#dc322f");
    expect(theme.brightWhite).toBe("#fdf6e3");
  });

  it("round-trips the exported JSON", () => {
    const theme = { background: "#111111", foreground: "#eeeeee" };
    const parsed = parseTheme(themeToJson("Mine", theme), "mine.json");
    expect(parsed.name).toBe("Mine");
    expect(parsed.theme.background).toBe("#111111");
  });

  it("rejects malformed JSON", () => {
    expect(() => parseTheme("{oops", "bad.json")).toThrow(/解析失败/);
  });
});
