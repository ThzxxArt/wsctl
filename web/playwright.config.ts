import { defineConfig, devices } from "@playwright/test";

const python = process.env.WSCTL_PYTHON || "python";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://127.0.0.1:7801",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command:
      `bash -c 'rm -rf .e2e-data && ${python} -m wsctl serve --foreground --no-auth --host 127.0.0.1 --port 7801'`,
    url: "http://127.0.0.1:7801/healthz",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: { WSCTL_DATA_DIR: ".e2e-data" },
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "firefox", use: { ...devices["Desktop Firefox"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
});
