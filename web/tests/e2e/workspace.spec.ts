import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.describe("terminal workspace", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("wsctl-debug", "1"));
    await page.goto("/");
    await expect(page.locator(".ws-shell")).toBeVisible();
  });

  async function terminalText(page: import("@playwright/test").Page): Promise<string> {
    return page.evaluate(() => {
      const registry = (window as unknown as { __wsctlTerminals?: Record<string, unknown> })
        .__wsctlTerminals;
      if (!registry) return "";
      return Object.values(registry)
        .map((value) => {
          const term = value as {
            buffer: {
              active: {
                length: number;
                getLine: (
                  index: number,
                ) => { translateToString: (trim: boolean) => string } | undefined;
              };
            };
          };
          const buffer = term.buffer.active;
          let out = "";
          for (let index = 0; index < buffer.length; index += 1) {
            out += `${buffer.getLine(index)?.translateToString(true) ?? ""}\n`;
          }
          return out;
        })
        .join("\n");
    });
  }

  test("creates a session and runs a command", async ({ page, isMobile }) => {
    test.skip(isMobile === true, "desktop keyboard covers the PTY input path");
    await page.getByTitle("新建会话").first().click();
    const pane = page.locator(".ws-pane").first();
    await expect(pane).toBeVisible();
    await expect(pane).toHaveAttribute("data-state", "connected");
    const textarea = pane.locator("textarea.xterm-helper-textarea");
    await textarea.waitFor({ state: "attached" });
    await pane.click();
    await textarea.focus();
    await page.keyboard.type("printf 'E2E-MARKER\\n'");
    await page.keyboard.press("Enter");
    await expect.poll(() => terminalText(page), { timeout: 15_000 }).toContain("E2E-MARKER");
  });

  test("splits the focused pane", async ({ page }) => {
    await page.getByTitle("新建会话").first().click();
    await expect(page.locator(".ws-pane")).toHaveCount(1);
    await page.locator(".ws-pane").first().getByTitle("向右分屏").click();
    await expect(page.locator(".ws-pane")).toHaveCount(2);
  });

  test("keeps chorded shortcuts working while the terminal is focused", async ({
    page,
    isMobile,
  }) => {
    test.skip(isMobile === true, "desktop keyboard shortcut");
    await page.getByTitle("新建会话").first().click();
    const pane = page.locator(".ws-pane").first();
    await expect(pane).toHaveAttribute("data-state", "connected");
    const textarea = pane.locator("textarea.xterm-helper-textarea");
    await textarea.waitFor({ state: "attached" });
    await pane.click();
    await textarea.focus();
    // xterm focuses a hidden <textarea>; the old guard treated that as "the
    // user is typing in a form" and suppressed every global shortcut.
    await page.keyboard.press("Control+Shift+F");
    await expect(pane.locator(".pane-search-input")).toBeVisible();
  });

  test("opens the command palette and settings", async ({ page }) => {
    await page.keyboard.press("Control+Shift+P");
    await expect(page.locator(".palette")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator(".palette")).toHaveCount(0);
    await page.getByTitle("设置").click();
    await expect(page.locator(".n-modal")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("外观", { exact: true })).toBeVisible();
  });

  test("opens the file panel", async ({ page }) => {
    await page.getByTitle("文件", { exact: true }).click();
    await expect(page.getByText("名称", { exact: true })).toBeVisible();
  });

  test("maximize and restore keep the terminal connected", async ({ page, isMobile }) => {
    test.skip(isMobile === true, "the session sidebar is an overlay on mobile");
    await page.getByTitle("新建会话").first().click();
    const pane = page.locator(".ws-pane").first();
    await expect(pane).toHaveAttribute("data-state", "connected");

    await pane.getByRole("button", { name: "最大化" }).click();
    await expect(page.locator(".ws-pane")).toHaveCount(1);
    await expect(page.locator(".ws-pane").first()).toHaveAttribute("data-state", "connected");

    // The remount must not leave a stale viewer behind that the 5s auth/access
    // recheck would evict.
    await page.waitForTimeout(6_000);
    await expect(page.locator(".ws-pane").first()).toHaveAttribute("data-state", "connected");

    await page.locator(".ws-pane").first().getByRole("button", { name: "最大化" }).click();
    await expect(page.locator(".ws-pane").first()).toHaveAttribute("data-state", "connected");
  });

  test("uses the in-app dialog for prompts and confirmations", async ({ page }) => {
    await page.getByTitle("文件", { exact: true }).click();
    await expect(page.getByText("名称", { exact: true })).toBeVisible();

    const name = `e2e-${Date.now()}`;
    await page.getByRole("button", { name: "新建文件夹" }).click();
    const dialog = page.locator(".n-modal").filter({ has: page.locator(".app-dialog") });
    await expect(dialog.getByText("新建文件夹", { exact: true })).toBeVisible();
    await dialog.locator("input").fill(name);
    await dialog.getByRole("button", { name: "确定" }).click();

    const row = page.locator(".frow").filter({ hasText: name });
    await expect(row).toBeVisible();

    await row.getByRole("button", { name: "删除", exact: true }).click();
    await expect(dialog.getByText("目录必须为空", { exact: false })).toBeVisible();
    await dialog.getByRole("button", { name: "取消" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(row).toBeVisible();

    // Leave the directory clean for the next run.
    await row.getByRole("button", { name: "删除", exact: true }).click();
    await dialog.getByRole("button", { name: "删除", exact: true }).click();
    await expect(row).toHaveCount(0);
  });

  test("has no critical accessibility violations on the workspace", async ({ page }) => {
    await page.getByTitle("新建会话").first().click();
    const results = await new AxeBuilder({ page })
      .disableRules(["color-contrast"]) // xterm's canvas is opaque to axe
      .analyze();
    const critical = results.violations.filter((violation) => violation.impact === "critical");
    expect(critical, JSON.stringify(critical, null, 2)).toEqual([]);
  });
});
