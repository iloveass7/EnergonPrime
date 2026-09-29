import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  use: { baseURL: process.env.UI_URL ?? "http://localhost:3000", viewport: { width: 1280, height: 720 } },
  reporter: [["list"]],
});
