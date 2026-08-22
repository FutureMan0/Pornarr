/**
 * Rating a recommendation.
 *
 * Recommendations are derived from the tags and performers of watched media, so
 * they cannot be seeded directly: the flow is only real once the library holds
 * media with metadata and the recommendation refresh has run.
 */
import { type Page, expect, test } from "@playwright/test";
import {
  type LibraryItem,
  apiPost,
  canDriveStackJobs,
  enqueueStackJob,
  expectNoAccessibilityViolations,
  firstRecommendation,
  loginAsAdmin,
  seedLibraryMedia,
} from "./helpers";

const RUN = `r${Date.now().toString(36)}`;

/**
 * Give this account something to be recommended, the way the product makes one.
 *
 * A recommendation is scored from the tags, performers and studio of what an
 * account has watched, so it cannot be written directly. Two titles are given
 * one tag in common, one of them is marked a favourite - the one event
 * `POST /api/account/events` accepts, because the rest are recorded by the
 * product action itself - and the two nightly jobs are asked to run now rather
 * than at half past two.
 *
 * Returns the title that should come back, or `null` when this environment
 * cannot drive the stack's jobs.
 */
async function seedARecommendation(page: Page): Promise<LibraryItem | null> {
  if (!canDriveStackJobs()) return null;
  const watched = await seedLibraryMedia(page);
  const other = await seedLibraryMedia(page, `Recommendable ${RUN}`);
  if (watched === null || other === null) return null;

  const shared = `recommendable-${RUN}`;
  for (const media of [watched, other]) {
    await apiPost(page, `/api/media/${media.id}/tags`, { name: shared });
  }
  await apiPost(page, "/api/account/events", {
    event_type: "favourite",
    media_id: watched.id,
  });

  // `once: false`: both jobs take no arguments, so the idempotent enqueue
  // would answer with the last hour's run rather than reading what was just
  // recorded.
  for (const job of ["refresh_interest_profiles_job", "refresh_recommendations_job"]) {
    expect(enqueueStackJob(job, [], "pornarr:default", { once: false })).toBe(true);
  }
  await expect
    .poll(async () => (await firstRecommendation(page)) !== null, {
      timeout: 120_000,
      intervals: [2_000],
      message:
        "Two titles share a tag, one of them is a favourite, and both nightly jobs have run - " +
        "and nothing was recommended. See refresh_interest_profiles_job and " +
        "refresh_recommendations_job.",
    })
    .toBe(true);
  return other;
}

test.describe("recommendations", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("the recommendations screen reports what it has", async ({ page }) => {
    await page.goto("/recommendations");
    // Scoped to the screen's own region: the shell renders the primary
    // navigation as a list, so an unscoped listitem is the sidebar.
    const feed = page.getByRole("region", { name: "Recommendations" });
    // The screen's h1 is the top bar's, not the region's: the design puts the
    // screen name in the bar and `usePageTitle` moves the heading with it, so a
    // screen that rendered its own would announce the name twice. See
    // `apps/web/src/shell/page-title.tsx`.
    await expect(
      page.getByRole("banner").getByRole("heading", { name: "Recommendations", level: 1 }),
    ).toBeVisible();
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
    test.setTimeout(300_000);
    const seeded = await seedARecommendation(page);
    test.skip(
      seeded === null,
      "This environment cannot drive the stack's jobs, so no recommendation can be produced.",
    );
    const recommendation = await firstRecommendation(page);
    expect(recommendation, "A recommendation was seeded and the list is empty.").not.toBeNull();
    if (recommendation === null) return;

    await page.goto("/recommendations");
    // Matched on the whole heading, not on a substring of it. A scan and an
    // import of the same download leave two library rows whose names contain
    // each other - `Probe Studio - E2E Import X (2026) 1080p` beside
    // `E2E Import X` - and `hasText` matches both.
    const entry = page
      .getByRole("region", { name: "Recommendations" })
      .getByRole("listitem")
      .filter({
        has: page.getByRole("heading", { name: recommendation.title, exact: true }),
      });
    await expect(entry).toBeVisible();

    await entry.getByRole("button", { name: "Not interested" }).click();

    // The rating is feedback, not a hide-in-the-browser: the list is refetched
    // and the rated title is gone from what the API returns.
    await expect(entry).toHaveCount(0);
    const remaining = await firstRecommendation(page);
    expect(remaining?.media_id).not.toBe(recommendation.media_id);
  });
});
