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
    await expect(page.getByRole("heading", { name: "Search", level: 1 })).toBeVisible();
    // Scoped to the page: the shell's top bar carries a search box with the
    // same placeholder, and an unscoped locator matches both.
    await expect(page.getByRole("main").getByPlaceholder(/Search/i)).toBeVisible();
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
    // The screen is titled Downloads. "Activity" is the top bar's live-status
    // control, which is present on every screen and proves nothing here.
    await expect(page.getByRole("heading", { name: "Downloads", level: 1 })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  test("can navigate to settings / quality profiles", async ({ page }) => {
    await page.goto("/settings/quality");
    // The page title and the profile list share a name, so this has to say
    // which one it means rather than matching both.
    await expect(page.getByRole("heading", { name: "Quality profiles", level: 1 })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  test("can navigate to quarantine review", async ({ page }) => {
    await page.goto("/admin/quarantine");
    await expect(page.getByRole("heading", { name: "Quarantine review" })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });
});
