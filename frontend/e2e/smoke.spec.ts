import { expect, test } from "@playwright/test";

test("operator console shows live state and every view renders", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("SIMULATED", { exact: true }).first()).toBeVisible();
  await expect(page.locator("header").getByText("Tick", { exact: true })).toBeVisible({ timeout: 10_000 });
  await expect(page.locator("header .num").first()).toHaveText(/^\d+$/);
  await expect(page.getByText("Service level", { exact: true })).toBeVisible();
  for (const view of ["Network", "Allocations", "Alerts", "Decisions", "System Health"]) {
    await page.getByRole("button", { name: new RegExp(`^${view}`) }).click();
    await expect(page.locator("main")).not.toContainText("undefined");
  }
  await expect(page.getByRole("heading", { name: "Components" })).toBeVisible();
});

test("a recommendation can be reviewed with its comparison panel", async ({ page, request }) => {
  // make the network need fuel: step the paused world 40 ticks through the test plane
  for (let i = 0; i < 40; i++) await request.post("/api/v1/test/simulator/step");
  await page.goto("/");
  await page.getByRole("button", { name: /^Recommendations/ }).click();
  const review = page.getByRole("button", { name: /Review recommendation/ }).first();
  await expect(review).toBeVisible({ timeout: 10_000 });
  await review.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("No new shipment")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Approve & submit" })).toBeVisible();
});
