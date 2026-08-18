/**
 * Playback end-to-end coverage (issue #65).
 *
 * Everything here needs a playable file in the library. `seedLibraryMedia`
 * provides one the way the product does, by scanning a configured root folder,
 * so these tests describe real playback rather than a player rendering an
 * error.
 *
 * The suite closes every transcode session before each test. On a host with no
 * usable hardware encoder the effective software session cap is one, so a
 * session an earlier test left open would make the next one fail for a reason
 * that is not its own.
 */
import { type Page, expect, test } from "@playwright/test";
import {
  apiGet,
  apiPatch,
  apiPostRaw,
  loginAsAdmin,
  playbackInfo,
  releaseTranscodeSessions,
  seedLibraryMedia,
  transcodeSessions,
} from "./helpers";

type MediaDetail = {
  readonly id: string;
  readonly title: string;
  readonly playable: boolean;
};
type PlaybackProgress = { readonly position_seconds: number | null };
type TranscodeLimits = { readonly effective_software: number };

// Deliberately not a variation on the main fixture's name: other specs
// match that one by pattern and a near-namesake makes them ambiguous.
const SECOND_FIXTURE_TITLE = "Pornarr Cap Clip";
const NO_FIXTURE =
  "The suite cannot write a fixture into a configured root folder: either ffmpeg is missing or the stack's data volume is not reachable from here.";

/** The seeded fixture, as the API describes it once it is in the library. */
async function playableMedia(page: Page): Promise<MediaDetail | null> {
  const item = await seedLibraryMedia(page);
  if (item === null) return null;
  const detail = await apiGet<MediaDetail>(page, `/api/media/${item.id}`);
  expect(
    detail.playable,
    `${detail.title} is in the library but the API calls it unplayable.`,
  ).toBe(true);
  return detail;
}

async function reportedPosition(page: Page, mediaId: string): Promise<number> {
  const response = await page.request.get(`/api/playback/${mediaId}/progress`);
  if (response.status() === 404) return 0;
  expect(response.ok()).toBe(true);
  return ((await response.json()) as PlaybackProgress).position_seconds ?? 0;
}

test.describe("playback", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await releaseTranscodeSessions(page);
  });

  test("the player opens, plays, seeks and reports progress", async ({ page }) => {
    test.slow();
    const media = await playableMedia(page);
    test.skip(media === null, NO_FIXTURE);
    if (media === null) return;

    await page.goto(`/library/${media.id}`);
    const player = page.getByRole("region", { name: `Player for ${media.title}` });
    await expect(
      player,
      `The detail screen rendered no player. Check the response to POST /api/transcode/media/${media.id}/sessions.`,
    ).toBeVisible();

    const video = player.locator("video");
    await expect(video).toHaveJSProperty("readyState", 4, { timeout: 60_000 });

    // Muted so the headless browser's autoplay policy does not veto the play.
    await video.evaluate(async (element: HTMLVideoElement) => {
      element.muted = true;
      await element.play();
    });
    await expect
      .poll(async () => video.evaluate((element: HTMLVideoElement) => element.currentTime))
      .toBeGreaterThan(0);

    // Seeking past the progress-reporting threshold, so the position the server
    // stores is one the player actually reached.
    await video.evaluate((element: HTMLVideoElement) => {
      element.currentTime = 15;
    });
    await expect.poll(async () => reportedPosition(page, media.id)).toBeGreaterThan(10);
  });

  test("leaving the player releases the transcode session it opened", async ({ page }) => {
    const media = await playableMedia(page);
    test.skip(media === null, NO_FIXTURE);
    if (media === null) return;

    const info = await playbackInfo(page, media.id);
    test.skip(
      info.direct_play,
      "The file plays directly, so no transcode session is opened to clean up.",
    );

    await page.goto(`/library/${media.id}`);
    await expect
      .poll(async () => (await transcodeSessions(page)).length, { timeout: 30_000 })
      .toBeGreaterThan(0);

    await page.goto("/library");
    await expect
      .poll(async () => (await transcodeSessions(page)).length, {
        timeout: 30_000,
        message:
          "A transcode session outlived the player that opened it. Every session the player " +
          "starts has to be deleted when it unmounts, including one whose creation was still " +
          "in flight - see apps/web/src/components/player/video-player.tsx.",
      })
      .toBe(0);
  });

  test("direct play serves the file without opening a transcode session", async ({ page }) => {
    const media = await playableMedia(page);
    test.skip(media === null, NO_FIXTURE);
    if (media === null) return;

    const info = await playbackInfo(page, media.id);
    test.skip(
      !info.direct_play,
      "No file in the library can be direct-played. A scan records a file's path and size but " +
        "never probes its container, codec, profile or level, so playback-info has to assume " +
        "every scanned file needs transcoding.",
    );

    const before = await transcodeSessions(page);
    await page.goto(`/library/${media.id}`);
    const video = page.getByRole("region", { name: `Player for ${media.title}` }).locator("video");
    await expect(video).toHaveJSProperty("readyState", 4, { timeout: 60_000 });

    // The player is reading the file itself; nothing was handed to FFmpeg.
    await expect(video).toHaveAttribute("src", `/api/media/${media.id}/stream`);
    expect(await transcodeSessions(page)).toHaveLength(before.length);
  });

  test("a second playback is refused once the software session cap is one", async ({ page }) => {
    const media = await playableMedia(page);
    // A second title, because one viewer watching one title deliberately
    // reuses its running session and would never reach the cap.
    const other = await seedLibraryMedia(page, SECOND_FIXTURE_TITLE);
    test.skip(media === null || other === null, NO_FIXTURE);
    if (media === null || other === null) return;

    const before = await apiGet<{ transcode_max_sw_sessions: number | null }>(
      page,
      "/api/admin/settings",
    );
    await apiPatch(page, "/api/admin/settings", { transcode_max_sw_sessions: 1 });
    try {
      expect(
        (await apiGet<TranscodeLimits>(page, "/api/admin/transcode/limits")).effective_software,
      ).toBe(1);

      const first = await apiPostRaw(page, `/api/transcode/media/${media.id}/sessions`);
      expect(first.status()).toBe(201);
      const second = await apiPostRaw(page, `/api/transcode/media/${other.id}/sessions`);
      expect(second.status()).toBe(429);
    } finally {
      await releaseTranscodeSessions(page);
      await apiPatch(page, "/api/admin/settings", {
        transcode_max_sw_sessions: before.transcode_max_sw_sessions,
      });
    }
  });
});
