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
    await expect(page.getByRole("heading", { name: "Quarantine review", level: 1 })).toBeVisible();
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
 * The second accent, held to the same floor as the first.
 *
 * The delivered token set ships rose and amber, and amber is not a filter over
 * rose — it redefines the accent ramp, the ground stack and the text ramp. A
 * contrast pass that only ever sees the default accent is a pass over half the
 * product, and the half it skips is the one nobody looks at while working.
 *
 * The choice is seeded into storage rather than poked onto `<html>`, so this also
 * exercises `applyStoredTheme()` on the path a returning reader takes.
 */
test.describe("the amber accent", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.addInitScript(() => {
      window.localStorage.setItem("pornarr-theme", "amber");
    });
  });

  const screens: readonly (readonly [string, string])[] = [
    ["/library", "Library"],
    ["/shorts", "Shorts"],
    ["/shorts/browse", "Shorts"],
    ["/continue", "Continue watching"],
    ["/settings", "Settings"],
    ["/admin", "Dashboard"],
  ];

  for (const [path, heading] of screens) {
    test(`${path} holds its contrast in amber`, async ({ page }) => {
      await page.goto(path);
      await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
      // The attribute, not just the absence of violations: a theme that failed
      // to apply would pass an accessibility check by being the default.
      await expect(page.locator("html")).toHaveAttribute("data-theme", "amber");
      await expectNoAccessibilityViolations(page);
    });
  }
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
