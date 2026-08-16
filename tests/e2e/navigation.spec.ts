import { expect, test } from "@playwright/test";
import { expectNoAccessibilityViolations, loginAsAdmin } from "./helpers";

test.describe("authenticated navigation and screens", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("can navigate to library", async ({ page }) => {
    await page.goto("/library");
    await expect(page.getByRole("heading", { name: "Library" })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  test("can navigate to search and input query", async ({ page }) => {
    await page.goto("/search");
    await expect(page.getByRole("heading", { name: "Search" })).toBeVisible();
    await expect(page.getByPlaceholder(/Search library and indexers|Search/i)).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  test("can navigate to requests", async ({ page }) => {
    await page.goto("/requests");
    await expect(page.getByRole("heading", { name: "Requests" })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  test("can navigate to recommendations", async ({ page }) => {
    await page.goto("/recommendations");
    await expect(page.getByRole("heading", { name: "Recommendations" })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  test("can navigate to downloads / activity queue", async ({ page }) => {
    await page.goto("/downloads");
    await expect(page.getByRole("heading", { name: "Activity" })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  test("can navigate to settings / quality profiles", async ({ page }) => {
    await page.goto("/settings/quality");
    await expect(page.getByRole("heading", { name: "Quality profiles" })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  test("can navigate to quarantine review", async ({ page }) => {
    await page.goto("/admin/quarantine");
    await expect(page.getByRole("heading", { name: "Quarantine review" })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });
});
