import { expect, test } from "@playwright/test";

test.describe("visual and network resilience", () => {
  test("settings page matches the committed visual baseline", async ({
    page,
    browserName,
    isMobile,
  }) => {
    test.skip(
      browserName !== "chromium" || isMobile === true,
      "one deterministic desktop baseline; fonts differ per engine",
    );
    await page.goto("/");
    await expect(page.locator(".ws-shell")).toBeVisible();
    await page.getByTitle("设置").click();
    await expect(page.getByText("外观", { exact: true })).toBeVisible();
    await expect(page).toHaveScreenshot("settings.png", {
      maxDiffPixelRatio: 0.02,
      animations: "disabled",
    });
  });

  test("going offline shows a banner and coming back clears it", async ({ page, context }) => {
    await page.goto("/");
    await expect(page.locator(".ws-shell")).toBeVisible();
    await page.getByTitle("新建会话").first().click();
    await expect(page.locator(".ws-pane").first()).toBeVisible();

    await context.setOffline(true);
    await expect(page.locator(".offline-bar")).toBeVisible();

    await context.setOffline(false);
    await expect(page.locator(".offline-bar")).toHaveCount(0);
  });
});
