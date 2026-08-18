/**
 * The search half of the nine flows: running a search, the local results it
 * finds in the library, the external results indexers return, and grabbing one
 * of them.
 *
 * The first three run against the real stack. Grabbing does not: it needs an
 * indexer that returns a release and a download client that accepts it, and the
 * local Compose stack ships neither, so it states that in its skip rather than
 * asserting something weaker and calling it covered.
 */
import { expect, test } from "@playwright/test";
import {
  TESTING_RELEASE_TITLE,
  apiGet,
  apiPostRaw,
  configuredIndexerCount,
  expectNoAccessibilityViolations,
  libraryItems,
  loginAsAdmin,
  queueJobs,
  seedLibraryMedia,
  testingDownloadClient,
  testingIndexer,
} from "./helpers";

/** Verifying a two-hundred-megabyte fixture is the slow part, not the network. */
const ACQUISITION_TIMEOUT_MILLISECONDS = 240_000;

const NO_FIXTURE =
  "The suite cannot write a fixture into a configured root folder: either ffmpeg is missing or the stack's data volume is not reachable from here.";

test.describe("search", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("a typed query searches the library and reports what it found", async ({ page }) => {
    await page.goto("/search");
    await expect(page.getByRole("heading", { name: "Search", level: 1 })).toBeVisible();

    const localResults = page.getByRole("region", { name: "In your library" });
    await expect(
      localResults.getByText("Enter a title to search your local library."),
    ).toBeVisible();

    await page.getByRole("searchbox", { name: "Find a title" }).fill("scene");

    // The query lives in the URL, so the search is shareable and survives a reload.
    await expect(page).toHaveURL(/[?&]q=scene/);
    // Debounced, then either rows or the "nothing matched" line - never the
    // pre-search prompt, which is what proves the search actually ran.
    await expect(
      localResults
        .getByRole("table")
        .or(
          localResults.getByText(
            "No matching library items. Indexer results may still find a release.",
          ),
        ),
    ).toBeVisible();

    await expectNoAccessibilityViolations(page);
  });

  test("local results list a library item that matches the query", async ({ page }) => {
    const media = await seedLibraryMedia(page);
    test.skip(media === null, NO_FIXTURE);
    if (media === null) return;

    // The same query against the API the screen calls, so a failure below is the
    // screen's and not the search's.
    const direct = await apiGet<{ items: { title: string }[] }>(
      page,
      `/api/search/local?q=${encodeURIComponent(media.title)}&sort=relevance`,
    );
    expect(direct.items.map((item) => item.title)).toContain(media.title);

    await page.goto(`/search?q=${encodeURIComponent(media.title)}`);
    const localResults = page.getByRole("region", { name: "In your library" });
    await expect(
      localResults.getByRole("cell", { name: media.title }),
      "The search screen found nothing the API returns for the same query. It sends every " +
        "unset numeric filter as zero, and a maximum size of zero bytes excludes every file - " +
        "see numberValue in apps/web/src/routes/search/search-route.tsx.",
    ).toBeVisible();
  });

  test("an external search is dispatched and the screen reports each indexer", async ({ page }) => {
    // The dispatch itself is testable without an indexer: the API answers 202
    // with a search id, and the screen polls it and renders the result table.
    const started = await apiPostRaw(page, "/api/search/indexers", { q: "scene" });
    expect(started.status()).toBe(202);
    expect((await started.json()) as { id: string }).toHaveProperty("id");

    await page.goto("/search?q=scene");
    const externalResults = page.getByRole("region", { name: "From indexers" });
    await expect(externalResults.getByRole("table")).toBeVisible();

    const indexers = await configuredIndexerCount(page);
    if (indexers === 0) {
      // No indexer is configured, so the honest report is that nothing answered.
      await expect(
        externalResults.getByText("No indexer has returned a matching release yet."),
      ).toBeVisible();
      return;
    }
    await expect(externalResults.getByRole("row").nth(1)).toBeVisible();
  });

  test("a release from an indexer is grabbed, downloaded and imported", async ({ page }) => {
    // The whole acquisition chain in one test, because every link in it only
    // means something with the others: an indexer that answers, a client that
    // accepts the release, a completion the product notices, and a file that
    // ends up in the library.
    test.setTimeout(ACQUISITION_TIMEOUT_MILLISECONDS + 60_000);
    const [indexer, client] = await Promise.all([
      testingIndexer(page),
      testingDownloadClient(page),
    ]);
    test.skip(
      indexer === null || client === null,
      "Grabbing needs an indexer and a download client: start them with " +
        "docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.testing.yml up -d",
    );

    await page.goto(`/search?q=${encodeURIComponent("Compose Test Scene")}`);
    const externalResults = page.getByRole("region", { name: "From indexers" });
    const row = externalResults.getByRole("row").filter({ hasText: TESTING_RELEASE_TITLE });
    await expect(
      row,
      `The indexer answered but ${TESTING_RELEASE_TITLE} never reached the screen.`,
    ).toBeVisible({ timeout: 30_000 });

    await row.getByRole("button", { name: "Grab" }).click();

    // The client verifies the data it was handed and reports the download as
    // finished; a torrent client that keeps seeding says "seeding", not
    // "completed", and both mean the file is on disk.
    await expect
      .poll(
        async () =>
          (await queueJobs(page)).some((job) =>
            ["completed", "seeding", "importing"].includes(job.status),
          ),
        {
          timeout: ACQUISITION_TIMEOUT_MILLISECONDS,
          intervals: [2_000],
          message: "The grabbed release never finished in the download client.",
        },
      )
      .toBe(true);

    await expect
      .poll(
        async () => (await libraryItems(page)).some((item) => item.title.includes("Compose Test")),
        {
          timeout: ACQUISITION_TIMEOUT_MILLISECONDS,
          intervals: [2_000],
          message:
            "The download finished and nothing reached the library. See docs/pipelines/import.md.",
        },
      )
      .toBe(true);
  });
});
