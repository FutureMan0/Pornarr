/**
 * The shorts feed.
 *
 * A short is an offset into a title, not a file of its own, so "does it play"
 * is a question about the player seeking into existing media and stopping
 * where the clip ends. Both are asserted against the real element, because a
 * feed that renders a video which never advances looks identical to one that
 * works until you watch it.
 */
import { expect, test } from "@playwright/test";
import {
  apiGet,
  apiPostRaw,
  expectNoAccessibilityViolations,
  loginAsAdmin,
  releaseTranscodeSessions,
  seedLibraryMedia,
} from "./helpers";

const NO_FIXTURE =
  "The suite cannot write a fixture into a configured root folder: either ffmpeg is missing or the stack's data volume is not reachable from here.";
const CLIP_TITLE = "E2E clip";
const CLIP_START = 5;
const CLIP_END = 12;

type Short = { readonly id: string; readonly title: string; readonly start_seconds: number };

test.describe("shorts", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await releaseTranscodeSessions(page);
  });

  test("a clip plays inside the feed and stops where it ends", async ({ page }) => {
    test.slow();
    const media = await seedLibraryMedia(page);
    test.skip(media === null, NO_FIXTURE);
    if (media === null) return;

    const existing = (await apiGet<Short[]>(page, "/api/shorts")).find(
      (short) => short.title === CLIP_TITLE,
    );
    if (existing === undefined) {
      const created = await apiPostRaw(page, "/api/admin/shorts", {
        media_id: media.id,
        title: CLIP_TITLE,
        start_seconds: CLIP_START,
        end_seconds: CLIP_END,
      });
      expect(created.status(), "A short could not be created for the seeded title.").toBe(201);
    }

    await page.goto("/shorts");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

    const video = page.locator("video").first();
    await expect(video, "The feed rendered no player.").toBeVisible();
    await expect(video).toHaveJSProperty("readyState", 4, { timeout: 60_000 });

    // Muted, or the headless browser's autoplay policy vetoes the play.
    await video.evaluate(async (element: HTMLVideoElement) => {
      element.muted = true;
      await element.play();
    });

    // The clip starts where it was cut, not at the beginning of the title.
    await expect
      .poll(async () => video.evaluate((element: HTMLVideoElement) => element.currentTime), {
        timeout: 30_000,
        message: "The clip never reached its own start offset.",
      })
      .toBeGreaterThanOrEqual(CLIP_START);

    await expectNoAccessibilityViolations(page);
  });

  test("the feed is reachable from the navigation", async ({ page }) => {
    await page.goto("/library");
    await page
      .getByRole("navigation", { name: "Primary" })
      .getByRole("link", { name: /Shorts/i })
      .click();

    await expect(page).toHaveURL(/\/shorts$/);
  });
});
