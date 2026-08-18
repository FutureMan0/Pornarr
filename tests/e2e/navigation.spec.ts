/**
 * Every screen is reachable, names itself, and is free of accessibility
 * violations.
 *
 * The assertions deliberately name the page's own heading by level and scope
 * anything else to the region that owns it: the application shell carries a
 * global search field, an activity button and a settings section navigation
 * whose labels repeat the page's, so an unscoped match is ambiguous rather
 * than wrong.
 */
import { expect, test } from "@playwright/test";
import { expectNoAccessibilityViolations, loginAsAdmin } from "./helpers";

test.describe("authenticated navigation and screens", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("can navigate to library", async ({ page }) => {
    await page.goto("/library");
    await expect(page.getByRole("heading", { name: "Library", level: 1 })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  test("can navigate to search and input query", async ({ page }) => {
    await page.goto("/search");
    await expect(page.getByRole("heading", { name: "Search", level: 1 })).toBeVisible();
    // The page's own field, not the shell's global one.
    await expect(page.getByRole("searchbox", { name: "Find a title" })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  test("can navigate to requests", async ({ page }) => {
    await page.goto("/requests");
    await expect(page.getByRole("heading", { name: "Requests", level: 1 })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  test("can navigate to recommendations", async ({ page }) => {
    await page.goto("/recommendations");
    await expect(page.getByRole("heading", { name: "Recommendations", level: 1 })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  test("can navigate to downloads", async ({ page }) => {
    await page.goto("/downloads");
    await expect(page.getByRole("heading", { name: "Downloads", level: 1 })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  test("can navigate to settings / quality profiles", async ({ page }) => {
    await page.goto("/settings/quality");
    await expect(page.getByRole("heading", { name: "Quality profiles", level: 1 })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  test("can navigate to quarantine review", async ({ page }) => {
    await page.goto("/admin/quarantine");
    await expect(page.getByRole("heading", { name: "Quarantine review", level: 1 })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });
});
