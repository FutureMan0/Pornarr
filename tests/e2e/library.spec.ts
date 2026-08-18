/**
 * Opening media, and the two routes media can take into the library.
 *
 * The first is a scan of a configured root folder, which is how the product
 * adopts files that are already on disk. That is the suite's seeding path: it
 * goes through `POST /api/admin/library/root-folders/{id}/scan` with the admin
 * session, so the library only ever holds what the application itself decided
 * to hold.
 *
 * The second is the import pipeline, which is what a finished download goes
 * through. It has its own test because a product that can only adopt files an
 * operator placed by hand has not imported anything.
 */
import { expect, test } from "@playwright/test";
import {
  apiGet,
  canPlaceCompletedDownloads,
  expectNoAccessibilityViolations,
  libraryItems,
  loginAsAdmin,
  placeCompletedDownload,
  seedLibraryMedia,
} from "./helpers";

/** Intake, probe, metadata, filters and placement, with the workers busy. */
const IMPORT_TIMEOUT_MILLISECONDS = 120_000;

const NO_FIXTURE =
  "The suite cannot write a fixture into a configured root folder: either ffmpeg is missing or the stack's data volume is not reachable from here.";

test.describe("library", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("the library screen lists what has been scanned into it", async ({ page }) => {
    const media = await seedLibraryMedia(page);
    test.skip(media === null, NO_FIXTURE);
    if (media === null) return;

    await page.goto("/library");
    await expect(page.getByRole("heading", { name: "Library", level: 1 })).toBeVisible();
    await expect(page.getByRole("link", { name: new RegExp(media.title) })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  test("opening a library item shows its detail screen", async ({ page }) => {
    const media = await seedLibraryMedia(page);
    test.skip(media === null, NO_FIXTURE);
    if (media === null) return;

    await page.goto("/library");
    await page.getByRole("link", { name: new RegExp(media.title) }).click();

    await expect(page).toHaveURL(new RegExp(`/library/${media.id}$`));
    await expect(page.getByRole("heading", { name: media.title, level: 1 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "File", level: 2 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Tags", level: 2 })).toBeVisible();
    await expect(page.getByText(`${media.title}.mp4`)).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  test("a completed download is taken through the import pipeline", async ({ page }) => {
    test.setTimeout(IMPORT_TIMEOUT_MILLISECONDS + 30_000);
    test.skip(
      !canPlaceCompletedDownloads(),
      "The suite cannot reach the stack's download volume, or ffmpeg is missing, so no completed download can be staged.",
    );

    const name = `Probe Studio - E2E Import ${Date.now()} (2026) 1080p`;
    placeCompletedDownload(name);

    // Either outcome is the pipeline finishing: a clean file is placed in the
    // library, a suspicious one is held for review. Both are states the product
    // documents; staying in neither is not.
    await expect
      .poll(
        async () => {
          const library = await libraryItems(page);
          const quarantine = await apiGet<unknown[]>(page, "/api/admin/quarantine");
          return (
            library.filter((item) => item.title.includes("E2E Import")).length + quarantine.length
          );
        },
        {
          timeout: IMPORT_TIMEOUT_MILLISECONDS,
          message:
            "A completed download was never imported into the library nor held in quarantine. " +
            "See docs/pipelines/import.md steps 2-11.",
        },
      )
      .toBeGreaterThan(0);
  });
});
