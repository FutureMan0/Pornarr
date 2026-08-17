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

/**
 * The screens built from the design.
 *
 * One case each, and each one is the same three things: the address resolves,
 * the top bar names the screen, and axe finds nothing. That is a thin test by
 * intent — the behaviour of each screen is covered by its own suite against a
 * mocked API, and what only a real browser can answer is whether the thing
 * renders at all and whether its colours hold. The contrast failure that broke
 * this suite lived in the sidebar, on every screen, and no unit test could see
 * it.
 */
test.describe("the designed screens render and hold their contrast", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  const screens: readonly (readonly [string, string])[] = [
    ["/feed", "Sent to you"],
    ["/continue", "Continue watching"],
    ["/shorts", "Shorts"],
    ["/collections", "Collections"],
    ["/watchlist", "Watchlist"],
    ["/settings", "Settings"],
    ["/admin", "Dashboard"],
    ["/admin/tags", "Tags"],
    ["/admin/moderation", "Ratings & comments"],
    ["/admin/scan", "Scan & import"],
    ["/admin/invites", "Invitations"],
  ];

  for (const [path, heading] of screens) {
    test(`${path} is reachable and clean`, async ({ page }) => {
      await page.goto(path);
      await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
      await expectNoAccessibilityViolations(page);
    });
  }

  test("a guest is not offered the administrator's screens", async ({ page }) => {
    await page.goto("/library");
    const nav = page.getByRole("navigation", { name: "Primary" });

    // The signed-in account is the administrator, so these must be present —
    // the negative case is covered in the unit suite, which can serve a guest
    // session without creating a second account on a live server.
    await expect(nav.getByRole("link", { name: "Dashboard" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Invitations" })).toBeVisible();
  });
});

/**
 * Joining, which is the one flow reachable without an account.
 */
test.describe("an invitation", () => {
  test("a link nobody issued is refused before anything is typed", async ({ page }) => {
    await page.goto("/join/not-a-real-token");

    await expect(page.getByText("This link no longer works.")).toBeVisible();
    // A page that takes a password and then refuses it has taken a password
    // for nothing.
    await expect(page.getByLabel("Password")).toBeHidden();
    await expectNoAccessibilityViolations(page);
  });
});
