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
  apiGet,
  apiPostRaw,
  configuredDownloadClientCount,
  configuredIndexerCount,
  expectNoAccessibilityViolations,
  loginAsAdmin,
  seedLibraryMedia,
} from "./helpers";

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

  test("a release from an indexer can be grabbed", async ({ page }) => {
    const [indexers, clients] = await Promise.all([
      configuredIndexerCount(page),
      configuredDownloadClientCount(page),
    ]);
    test.skip(
      indexers === 0 || clients === 0,
      "Grabbing needs both an indexer to return a release and a download client to accept it; the local Compose stack provides neither.",
    );

    await page.goto("/search?q=scene");
    const externalResults = page.getByRole("region", { name: "From indexers" });
    const firstGrab = externalResults.getByRole("button", { name: "Grab" }).first();
    await expect(firstGrab).toBeVisible();
    await firstGrab.click();

    await page.goto("/downloads");
    const downloads = page.getByRole("region", { name: "Downloads" });
    await expect(downloads.getByRole("listitem").first()).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });
});
