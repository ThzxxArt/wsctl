import { chromium } from "@playwright/test";

const base = process.env.WSCTL_SHOT_URL || "http://127.0.0.1:7803";
const out = new URL("../../docs/screenshots/", import.meta.url).pathname;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
await page.addInitScript(() => {
  localStorage.setItem(
    "wsctl-prefs",
    JSON.stringify({ theme: "dark", termTheme: "dark", fontSize: 14 }),
  );
});

async function closeModal() {
  const close = page.getByRole("button", { name: "关闭", exact: true }).last();
  if (await close.count()) {
    await close.click();
    await page.waitForTimeout(250);
    return;
  }
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
}

await page.goto(base);
await page.waitForSelector(".login-card");
await page.screenshot({ path: `${out}01-login.png` });

await page.fill('input[autocomplete="username"]', "admin");
await page.fill('input[autocomplete="current-password"]', process.env.WSCTL_SHOT_PASSWORD || "shotpass123");
await page.click("button.submit");
await page.waitForSelector(".ws-shell");

await page.getByTitle("新建会话").first().click();
const pane = page.locator(".ws-pane").first();
await pane.waitFor();
await page.waitForFunction(() => document.querySelector(".ws-pane")?.getAttribute("data-state") === "connected");
await pane.click();
// Hide the echo of the demo command itself, then clear the screen: the
// screenshot should show a clean prompt, the branded line and a file listing,
// not the typed escape sequences wrapped across the pane.
await page.keyboard.type("stty -echo; clear\n");
await page.waitForTimeout(150);
await page.keyboard.type(
  "printf '\\033[1;32mwsctl 0.2.0\\033[0m  terminal workspace\\n'; command ls --color=never -F\n",
);
await page.waitForTimeout(400);
// Drop the echoed setup lines from scrollback too: resizing a split pane
// reflows the buffer, and leftover command text would otherwise reappear in
// the screenshots that follow.
await page.keyboard.type("printf '\\033[3J'; stty echo\n");
await page.waitForTimeout(400);
await page.screenshot({ path: `${out}02-terminal.png` });

await page.getByTitle("文件", { exact: true }).click();
await page.waitForSelector("text=名称");
await page.screenshot({ path: `${out}03-files.png` });
await closeModal();
await page.waitForTimeout(200);

await page.getByTitle("会话").first().click();
if (!(await page.locator(".sidebar").count())) {
  await page.getByTitle("会话").first().click();
}
await page.waitForTimeout(300);
await page.screenshot({ path: `${out}04-sessions.png` });

await page.getByTitle("管理").click();
await page.waitForSelector(".n-tabs-tab");
await page.locator(".n-tabs-tab").filter({ hasText: "用户" }).click();
await page.waitForTimeout(300);
await page.screenshot({ path: `${out}05-admin-users.png` });
await page.locator(".n-tabs-tab").filter({ hasText: "审计" }).click();
await page.waitForTimeout(300);
await page.screenshot({ path: `${out}06-admin-audit.png` });
await closeModal();
await page.waitForTimeout(200);

await page.getByTitle("设置").click();
await page.waitForSelector("text=外观");
await page.waitForTimeout(300);
await page.screenshot({ path: `${out}07-settings.png` });

await browser.close();
