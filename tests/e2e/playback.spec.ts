/**
 * Playback end-to-end coverage (issue #65).
 *
 * Everything here needs a file the product will actually serve, which is not
 * the same thing as a file in the library. `Settings.library_path` is hardcoded
 * to `<data>/library` and both `/stream` and the transcode start refuse
 * anything outside it, while the scanner adopts anything under the configured
 * root folder - on this instance `/data`, one level up. A fixture written where
 * `seedLibraryMedia` writes it is therefore listed in the library, reported
 * `playable: true`, and then answered 404 by every playback route: product
 * defect D1 in `.gauntlet/pieces/08-playback/BUILD.md`. `seedPlayable` below
 * writes into `<data>/library` instead, which is still inside the configured
 * root folder, so the same scan adopts it and the product can serve it. D1 is
 * not hidden by that: it is pinned on the titles the import pipeline really
 * produces, by `transcode.spec.ts` > "an imported title is refused by its own
 * stream endpoint".
 *
 * The suite closes every transcode session before each test. On a host with no
 * usable hardware encoder the effective software session cap is one, so a
 * session an earlier test left open would make the next one fail for a reason
 * that is not its own.
 */
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { type Page, expect, test } from "@playwright/test";
import {
  apiGet,
  apiPatch,
  apiPostRaw,
  enabledRootFolder,
  hasFfmpeg,
  hostDataRoot,
  libraryItemByTitle,
  loginAsAdmin,
  playbackInfo,
  releaseTranscodeSessions,
  transcodeSessions,
} from "./helpers";

type MediaDetail = {
  readonly id: string;
  readonly title: string;
  readonly playable: boolean;
};
type PlaybackProgress = { readonly position_seconds: number | null };
type TranscodeLimits = { readonly effective_software: number };

const FIXTURE_TITLE = "Pornarr Playback Fixture";
// Deliberately not a variation on the main fixture's name: other specs
// match that one by pattern and a near-namesake makes them ambiguous.
const SECOND_FIXTURE_TITLE = "Pornarr Playback Cap Clip";
/** Long enough that the player can be seeked past the progress threshold. */
const FIXTURE_SECONDS = 30;
const SEED_TIMEOUT_MILLISECONDS = 180_000;
const SCAN_RETRY_MILLISECONDS = 20_000;
const NO_FIXTURE =
  "The suite cannot write a fixture into the stack's library directory: either ffmpeg is missing or the stack's data volume is not reachable from here.";

/**
 * Put one file the product will stream into the library and return it.
 *
 * Adopted by the same root-folder scan an operator would run, so the library
 * holds something the application itself decided to hold; written under
 * `<data>/library` for the reason at the top of this file.
 */
async function seedPlayable(page: Page, title: string): Promise<MediaDetail | null> {
  const folder = await enabledRootFolder(page);
  if (folder === null || !hasFfmpeg()) return null;
  const root = join(hostDataRoot(), "library");
  try {
    mkdirSync(root, { recursive: true });
  } catch {
    return null;
  }

  const target = join(root, `${title}.mp4`);
  const existing = await libraryItemByTitle(page, title);
  if (existing === null || !existsSync(target)) {
    test.setTimeout(Math.max(test.info().timeout, SEED_TIMEOUT_MILLISECONDS + 120_000));
    if (!existsSync(target)) writeFixture(target);
    // A scan asked for in the same wall-clock minute as the last one is
    // collapsed and answered without doing anything, so asking again on a
    // slower cadence is what gets the new file seen.
    let lastScan = 0;
    await expect
      .poll(
        async () => {
          if (Date.now() - lastScan > SCAN_RETRY_MILLISECONDS) {
            lastScan = Date.now();
            const scan = await apiPostRaw(
              page,
              `/api/admin/library/root-folders/${folder.id}/scan`,
            );
            expect([202, 409]).toContain(scan.status());
          }
          return (await libraryItemByTitle(page, title)) !== null;
        },
        {
          timeout: SEED_TIMEOUT_MILLISECONDS,
          intervals: [2_000],
          message: `A scan of ${folder.path} did not adopt ${target}. See apps/worker/pornarr_worker/jobs/scan.py.`,
        },
      )
      .toBe(true);
  }
  const item = await libraryItemByTitle(page, title);
  if (item === null) return null;
  const detail = await apiGet<MediaDetail>(page, `/api/media/${item.id}`);
  expect(
    detail.playable,
    `${detail.title} is in the library but the API calls it unplayable.`,
  ).toBe(true);
  return detail;
}

function writeFixture(target: string): void {
  execFileSync(
    "ffmpeg",
    [
      "-hide_banner",
      "-loglevel",
      "error",
      "-y",
      "-f",
      "lavfi",
      "-i",
      `testsrc2=size=640x360:rate=15:duration=${FIXTURE_SECONDS}`,
      "-f",
      "lavfi",
      "-i",
      `sine=frequency=440:duration=${FIXTURE_SECONDS}`,
      "-c:v",
      "libx264",
      "-preset",
      "ultrafast",
      "-crf",
      "34",
      "-pix_fmt",
      "yuv420p",
      "-c:a",
      "aac",
      "-shortest",
      target,
    ],
    { stdio: "ignore" },
  );
  if (!existsSync(target)) throw new Error(`ffmpeg did not produce a fixture at ${target}.`);
}

/** The seeded fixture, as the API describes it once it is in the library. */
async function playableMedia(page: Page): Promise<MediaDetail | null> {
  return seedPlayable(page, FIXTURE_TITLE);
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
    // Patience, not five seconds: the source is an HLS ladder FFmpeg is still
    // writing, so the first frames arrive when the first segment does. This
    // test could never reach this line before - the player it drives failed at
    // the session start - so the default was never actually exercised.
    await expect
      .poll(async () => video.evaluate((element: HTMLVideoElement) => element.currentTime), {
        timeout: 60_000,
        intervals: [500],
        message: "The player loaded the stream and then never advanced past the first frame.",
      })
      .toBeGreaterThan(0);

    // Seeking past the progress-reporting threshold, so the position the server
    // stores is one the player actually reached.
    await video.evaluate((element: HTMLVideoElement) => {
      element.currentTime = 15;
    });
    await expect
      .poll(async () => reportedPosition(page, media.id), {
        timeout: 60_000,
        intervals: [1_000],
        message:
          "The player seeked past the reporting threshold and the server never stored a " +
          "position. See POST /api/playback/{media_id}/progress.",
      })
      .toBeGreaterThan(10);
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
    // Three independent product defects have to be fixed before this can run,
    // all of them executed as expected failures in `transcode.spec.ts` and
    // written up in `.gauntlet/pieces/08-playback/HOLES.md`:
    // D2, a scan records path, size and mtime and never probes, so `codecs` is
    // null for everything a scan can produce; D1, the only writer of
    // `codecs` is the import pipeline, which files titles under the configured
    // root folder while `/stream` only serves `<data>/library`; and D7/D8, the
    // default player matrix compares ffprobe's container family against the
    // literal "mp4" and its level in tenths against 4.2, so it cannot match a
    // real file even when the metadata is there.
    test.skip(
      !info.direct_play,
      "Nothing in this library can be direct-played: a scanned file carries no container, " +
        "codec, profile or level to compare, the imported files that do carry them are outside " +
        "the directory /stream serves, and the default player matrix cannot match ffprobe's " +
        "vocabulary anyway. Defects D1, D2 and D7/D8 in .gauntlet/pieces/08-playback/HOLES.md.",
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
    const other = await seedPlayable(page, SECOND_FIXTURE_TITLE);
    test.skip(media === null || other === null, NO_FIXTURE);
    if (media === null || other === null) return;

    const before = await apiGet<{ transcode_max_sw_sessions: number | null }>(
      page,
      "/api/admin/settings",
    );
    await apiPatch(page, "/api/admin/settings", { transcode_max_sw_sessions: 1 });
    try {
      // Not proof that the write took effect: the undetected default is also
      // one, and a configured cap is in fact ignored by every enforcement path
      // (defect D5, pinned by transcode.spec.ts › "a configured session cap is
      // the one that is enforced"). What it establishes is only the precondition
      // this test needs - that exactly one software slot exists.
      expect(
        (await apiGet<TranscodeLimits>(page, "/api/admin/transcode/limits")).effective_software,
      ).toBe(1);

      const first = await apiPostRaw(page, `/api/transcode/media/${media.id}/sessions`);
      expect(first.status()).toBe(201);
      const second = await apiPostRaw(page, `/api/transcode/media/${other.id}/sessions`);

      // The claim is the body, not the status. transcode.md L33-34 and
      // troubleshooting.md L16-21: a saturated instance says which limit was
      // hit, so the operator can tell "raise the software limit" from "lower
      // the per-user one", and it never leaks FFmpeg's own failure text.
      expect(second.status()).toBe(429);
      expect(await second.json()).toEqual({
        code: "TRANSCODE_LIMIT_REACHED",
        status: 429,
        context: { limit: "software" },
      });
      const body = await second.text();
      expect(body.toLowerCase()).not.toContain("ffmpeg");
      expect(body).not.toContain("libx264");
      expect(body).not.toContain("/data/");
    } finally {
      await releaseTranscodeSessions(page);
      await apiPatch(page, "/api/admin/settings", {
        transcode_max_sw_sessions: before.transcode_max_sw_sessions,
      });
    }
  });
});
