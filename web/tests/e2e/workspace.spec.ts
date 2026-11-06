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

  test("splits the focused pane", async ({ page, browserName }) => {
    await openSoloSession(page, `split-${browserName}-${Date.now()}`);
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

  async function openSoloSession(
    page: import("@playwright/test").Page,
    name: string,
  ): Promise<string> {
    const id = await page.evaluate(async (sessionName) => {
      const response = await fetch("/api/v2/sessions", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: sessionName }),
      });
      if (!response.ok) throw new Error(await response.text());
      return (await response.json()).id as string;
    }, name);
    await page.reload();
    // The sidebar starts collapsed on narrow screens. Check the sidebar itself,
    // not the row: an open sidebar whose list is still loading must not be
    // toggled closed by a false-negative row check.
    if ((await page.locator(".sidebar").count()) === 0) {
      await page.locator(".sidebar-toggle").click();
    }
    const row = page.locator(".row").filter({ hasText: name }).first();
    await expect(row).toBeVisible();
    await row.click();
    await expect(page.locator(".ws-pane").first()).toHaveAttribute("data-state", "connected");
    return id;
  }

  test("ended-session overlay buttons are clickable", async ({ page, isMobile, browserName }) => {
    test.skip(isMobile === true, "the session sidebar is an overlay on mobile");
    const sid = await openSoloSession(page, `ghost-close-${browserName}-${Date.now()}`);
    await page.evaluate(async (id) => fetch(`/api/v2/sessions/${id}`, { method: "DELETE" }), sid);
    const ghost = page.locator(".pane-ghost");
    await expect(ghost).toBeVisible();
    // The xterm link-layer canvas used to sit on top of this overlay and eat
    // every click: the buttons looked present but did nothing.
    await ghost.getByRole("button", { name: "关闭分屏" }).click();
    await expect(ghost).toHaveCount(0);
  });

  test("ended-session overlay can open the session picker", async ({
    page,
    isMobile,
    browserName,
  }) => {
    test.skip(isMobile === true, "the session sidebar is an overlay on mobile");
    const sid = await openSoloSession(page, `ghost-picker-${browserName}-${Date.now()}`);
    await page.evaluate(async (id) => fetch(`/api/v2/sessions/${id}`, { method: "DELETE" }), sid);

    const ghost = page.locator(".pane-ghost");
    await expect(ghost).toBeVisible();
    await ghost.getByRole("button", { name: "连接其他会话" }).click();
    await expect(page.locator(".picker-overlay")).toBeVisible();
  });

  test("brand and sidebar toggle are centered on one line", async ({ page, isMobile }) => {
    test.skip(isMobile === true, "the brand is hidden on narrow screens");
    const brand = page.locator(".brand");
    await expect(brand).toHaveText("WsCTL");
    const toggle = page.locator(".sidebar-toggle");
    const [brandBox, toggleBox] = await Promise.all([brand.boundingBox(), toggle.boundingBox()]);
    expect(brandBox && toggleBox).toBeTruthy();
    if (!brandBox || !toggleBox) return;
    const brandCenter = brandBox.y + brandBox.height / 2;
    const toggleCenter = toggleBox.y + toggleBox.height / 2;
    expect(Math.abs(brandCenter - toggleCenter)).toBeLessThan(2);
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

  test("switching tabs rebuilds the terminal and keeps each screen", async ({
    page,
    isMobile,
    browserName,
  }) => {
    test.skip(isMobile === true, "desktop tab switching");
    // Unique names: create is idempotent by request body, so reusing "alpha"
    // across browser projects replayed an earlier (possibly deleted) session.
    const suffix = `${browserName}-${Date.now()}`;
    const made = await page.evaluate(async (tag) => {
      const create = async (name: string) =>
        (
          await (
            await fetch("/api/v2/sessions", {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({ name }),
            })
          ).json()
        ).id;
      return { alpha: await create(`alpha-${tag}`), beta: await create(`beta-${tag}`) };
    }, suffix);
    await page.evaluate(
      (ids) =>
        localStorage.setItem(
          "wsctl-layout-v2",
          JSON.stringify({
            version: 2,
            activeTabId: "t1",
            tabs: [
              {
                id: "t1",
                name: "alpha",
                root: { type: "leaf", id: "p1", sessionId: ids.alpha, title: "alpha", share: null },
                focus: "p1",
              },
              {
                id: "t2",
                name: "beta",
                root: { type: "leaf", id: "p2", sessionId: ids.beta, title: "beta", share: null },
                focus: "p2",
              },
            ],
          }),
        ),
      made,
    );
    await page.reload();
    const pane = page.locator(".ws-pane").first();
    await expect(page.locator(".tab").filter({ hasText: "alpha" })).toBeVisible();
    await expect(pane).toHaveAttribute("data-state", "connected");

    const run = async (command: string, marker: string) => {
      await pane.click();
      await pane.locator("textarea.xterm-helper-textarea").focus();
      await page.keyboard.type(command);
      await page.keyboard.press("Enter");
      await expect.poll(() => terminalText(page), { timeout: 10_000 }).toContain(marker);
    };
    await run("printf 'ALPHA-TAB-MARKER\\n'", "ALPHA-TAB-MARKER");
    const alphaTerm = await page.evaluate(
      () =>
        (window as unknown as { __wsctlTerminals?: Record<string, unknown> }).__wsctlTerminals?.p1,
    );

    await page.locator(".tab").filter({ hasText: "beta" }).click();
    await expect(page.locator(".ws-pane").first()).toHaveAttribute("data-state", "connected");
    await run("printf 'BETA-TAB-MARKER\\n'", "BETA-TAB-MARKER");

    await page.locator(".tab").filter({ hasText: "alpha" }).click();
    await expect(page.locator(".ws-pane").first()).toHaveAttribute("data-state", "connected");
    await expect.poll(() => terminalText(page)).toContain("ALPHA-TAB-MARKER");
    const fresh = await page.evaluate((old) => {
      const terms = (window as unknown as { __wsctlTerminals?: Record<string, unknown> })
        .__wsctlTerminals;
      return Boolean(old) && terms?.p1 !== old;
    }, alphaTerm);
    expect(fresh, "the tab switch must create a fresh xterm, not reuse stale pixels").toBe(true);
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
