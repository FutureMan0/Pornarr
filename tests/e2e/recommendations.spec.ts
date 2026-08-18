/**
 * Rating a recommendation.
 *
 * Recommendations are derived from the tags and performers of watched media, so
 * they cannot be seeded directly: the flow is only real once the library holds
 * media with metadata and the recommendation refresh has run.
 */
import { expect, test } from "@playwright/test";
import { expectNoAccessibilityViolations, firstRecommendation, loginAsAdmin } from "./helpers";

test.describe("recommendations", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("the recommendations screen reports what it has", async ({ page }) => {
    await page.goto("/recommendations");
    // Scoped to the screen's own region: the shell renders the primary
    // navigation as a list, so an unscoped listitem is the sidebar.
    const feed = page.getByRole("region", { name: "Recommendations" });
    await expect(feed.getByRole("heading", { name: "Recommendations", level: 1 })).toBeVisible();
    await expect(
      feed
        .getByRole("listitem")
        .first()
        .or(
          feed.getByText("Watch and rate some media first. Your recommendations will appear here."),
        ),
    ).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  test("a recommendation can be rated as not interesting", async ({ page }) => {
    const recommendation = await firstRecommendation(page);
    test.skip(
      recommendation === null,
      "No recommendation exists. They are generated from the tags and performers of watched library media, and the scan importer records no metadata at all, so nothing on this instance can produce one.",
    );
    if (recommendation === null) return;

    await page.goto("/recommendations");
    const entry = page
      .getByRole("region", { name: "Recommendations" })
      .getByRole("listitem")
      .filter({ hasText: recommendation.title });
    await expect(entry).toBeVisible();

    await entry.getByRole("button", { name: "Not interested" }).click();

    // The rating is feedback, not a hide-in-the-browser: the list is refetched
    // and the rated title is gone from what the API returns.
    await expect(entry).toHaveCount(0);
    const remaining = await firstRecommendation(page);
    expect(remaining?.media_id).not.toBe(recommendation.media_id);
  });
});
