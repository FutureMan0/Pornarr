/**
 * Playback and transcoding against the live stack (piece 08).
 *
 * Three things this file refuses to do, because the piece it covers is where
 * they are most tempting.
 *
 * It never asserts a status code alone. `429` is not the claim - the claim is
 * `TRANSCODE_LIMIT_REACHED` with the limit that was hit and no FFmpeg text, and
 * that lives in the body. It never compares a limit against a value the test
 * itself just wrote: `/api/admin/transcode/limits` is read for what the product
 * detected. And it never waits out a real minute. The sixty-second session TTL
 * is forced through Redis and polled to convergence, the way Jellyfin's
 * `SyncPlayLostWebSocketTests` back-dates a keep-alive.
 *
 * Fixtures come in through two different doors, and the difference is the
 * finding. A root-folder scan puts a file where the product will stream it
 * (`Settings.library_path`, hardcoded to `<data>/library`) but records no
 * technical metadata, so nothing about it can be direct-played. The import
 * pipeline records all of it but files the title under the configured root
 * folder, which `/stream` and the transcode start both refuse. Every seed here
 * therefore says which door it used. See D1 and D2 in
 * `.gauntlet/pieces/08-playback/BUILD.md`.
 */
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync, mkdirSync, rmSync, statSync, utimesSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { type APIResponse, type Browser, type Page, expect, test } from "@playwright/test";
import {
  apiDelete,
  apiGet,
  apiPatch,
  apiPostRaw,
  canDriveStackJobs,
  canDriveStackRedis,
  canPlaceCompletedDownloads,
  canReadStackDatabase,
  databaseScalar,
  downloadsRoot,
  enabledRootFolder,
  enqueueStackJob,
  hasFfmpeg,
  hostDataRoot,
  libraryItemByTitle,
  loginAsAdmin,
  releaseTranscodeSessions,
  stackRedis,
  transcodeCommandLine,
  transcodeProcessCount,
  writeCompletedDownload,
} from "./helpers";

type MediaDetail = {
  readonly id: string;
  readonly title: string;
  readonly path: string;
  readonly size: number;
  readonly codecs: Record<string, unknown> | null;
};
type PlaybackInfoBody = { readonly direct_play: boolean; readonly reasons: readonly string[] };
type ErrorBody = {
  readonly code: string;
  readonly status: number;
  readonly context: Record<string, unknown>;
};
type StartedSession = { readonly session_id: string; readonly playlist_url: string };
type AdminSession = {
  readonly id: string;
  readonly user_id: string;
  readonly media_id: string;
  readonly username: string | null;
  readonly media_title: string | null;
  readonly mode: string;
  readonly hardware: boolean;
  readonly created_at: string;
  readonly elapsed_seconds: number;
};
type MySession = {
  readonly session_id: string;
  readonly media_id: string;
  readonly media_title: string;
  readonly mode: string;
  readonly hardware: boolean;
  readonly started_at: string;
};
type Limits = {
  readonly hardware: number;
  readonly software: number;
  readonly per_user: number;
  readonly hardware_in_use: number;
  readonly software_in_use: number;
  readonly configured_hardware: number | null;
  readonly configured_software: number | null;
  readonly configured_per_user: number;
  readonly effective_hardware: number;
  readonly effective_software: number;
  readonly effective_per_user: number;
};
type Capabilities = {
  readonly methods: readonly {
    readonly acceleration: string;
    readonly codecs: readonly unknown[];
  }[];
  readonly rejections: readonly { readonly acceleration: string | null; readonly reason: string }[];
  readonly nvidia_gpus: readonly string[];
};
type Failure = {
  readonly session_id: string;
  readonly media_id: string;
  readonly mode: string;
  readonly exit_code: number;
  readonly reason: string;
};

/** `SESSION_TTL_SECONDS` in `packages/media/pornarr_media/sessions.py`. */
const SESSION_TTL_SECONDS = 60;
/** `transcode_cleanup_min_age_seconds` default in `pornarr_shared/config.py`. */
const CLEANUP_MIN_AGE_SECONDS = 300;

const PLAYABLE_TITLE = "Pornarr Piece08 Clip";
const SECOND_TITLE = "Pornarr Piece08 Second Clip";
const BROKEN_TITLE = "Pornarr Piece08 Undecodable";
/**
 * Long enough that FFmpeg is still encoding when the test kills it. The
 * transcode runs at libx264's default preset, so a low-resolution twenty-minute
 * source keeps a process alive for well over half a minute while staying small
 * on disk.
 */
const PLAYABLE_SECONDS = 1200;
const SECOND_SECONDS = 20;
const SEED_TIMEOUT_MILLISECONDS = 180_000;
const SCAN_RETRY_MILLISECONDS = 20_000;

/**
 * The one title in this file that carries technical metadata, and the studio it
 * is filed under. Only the import pipeline writes `MediaFile.codecs`, so the
 * direct-play matrix has to come in through a completed download rather than
 * through a scan.
 */
const PROBED_STUDIO = "Piece Eight Studio";
const PROBED_TITLE = "Piece Eight Probe";
const IMPORT_TIMEOUT_MILLISECONDS = 240_000;

/**
 * Long enough to contain two of the documented fifteen-second heartbeats, so a
 * player that beats on the documented cadence is observed doing it twice and one
 * that does not is observed missing both.
 */
const HEARTBEAT_OBSERVATION_MILLISECONDS = 40_000;
/** Fifteen seconds plus room for a slow first paint on a loaded stack. */
const HEARTBEAT_TOLERANCE_SECONDS = 18;

/** This file's own corner of the watched download tree. */
function importStaging(): string {
  const staging = join(downloadsRoot(), "piece08");
  mkdirSync(staging, { recursive: true });
  return staging;
}

const NO_FIXTURE =
  "The suite cannot write a fixture into the stack's library directory: either ffmpeg is " +
  "missing or the data volume is not reachable from the test runner.";
const NO_REDIS =
  "The stack's Redis is not reachable from the test runner, so the session TTL cannot be " +
  "forced and the test would have to wait out a real minute.";
const NO_JOBS =
  "The stack's worker is not reachable from the test runner, so the nightly cleanup job " +
  "cannot be put on its own queue.";
const NO_DATABASE =
  "The stack's PostgreSQL is not reachable from the test runner, so the connections the API " +
  "holds cannot be counted.";
const NO_DOWNLOAD_VOLUME =
  "The suite cannot stage a completed download for the import worker to pick up: either " +
  "ffmpeg is missing or the stack's download tree is not writable from here. Only the import " +
  "pipeline writes the technical metadata this decision reads.";

/** Where the product will actually stream from, whatever the root folder says. */
function streamableRoot(): string | null {
  const root = join(hostDataRoot(), "library");
  try {
    mkdirSync(root, { recursive: true });
    return root;
  } catch {
    return null;
  }
}

function writeFixture(target: string, seconds: number, size: string): void {
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
      `testsrc2=size=${size}:rate=15:duration=${seconds}`,
      "-f",
      "lavfi",
      "-i",
      `sine=frequency=440:duration=${seconds}`,
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

/**
 * Put one file the product will stream into the library and return it.
 *
 * The file is adopted by the same root-folder scan an operator would run, so
 * the library holds something the application itself decided to hold. The
 * title is stable, which makes a second run a no-op rather than another row.
 */
async function seedStreamable(
  page: Page,
  title: string,
  seconds: number,
  size = "640x360",
): Promise<MediaDetail | null> {
  const root = streamableRoot();
  const folder = await enabledRootFolder(page);
  if (root === null || folder === null || !hasFfmpeg()) return null;

  const target = join(root, `${title}.mp4`);
  const existing = await libraryItemByTitle(page, title);
  if (existing === null || !existsSync(target)) {
    test.setTimeout(Math.max(test.info().timeout, SEED_TIMEOUT_MILLISECONDS + 120_000));
    if (!existsSync(target)) writeFixture(target, seconds, size);
    await scanUntilAdopted(page, folder.id, folder.path, title, target);
  }
  const item = await libraryItemByTitle(page, title);
  if (item === null) return null;
  return apiGet<MediaDetail>(page, `/api/media/${item.id}`);
}

async function scanUntilAdopted(
  page: Page,
  folderId: string,
  folderPath: string,
  title: string,
  target: string,
): Promise<void> {
  // A scan request made in the same wall-clock minute as the last one is
  // collapsed and answered 202 without doing anything, so asking again on a
  // slower cadence is what gets the new file seen.
  let lastScan = 0;
  await expect
    .poll(
      async () => {
        if (Date.now() - lastScan > SCAN_RETRY_MILLISECONDS) {
          lastScan = Date.now();
          const scan = await apiPostRaw(page, `/api/admin/library/root-folders/${folderId}/scan`);
          expect([202, 409]).toContain(scan.status());
        }
        return (await libraryItemByTitle(page, title)) !== null;
      },
      {
        timeout: SEED_TIMEOUT_MILLISECONDS,
        // Two seconds, not one: `libraryItemByTitle` walks the whole shared
        // library a page at a time, and polling it every second is several
        // requests per second against a stack fourteen builders share.
        intervals: [2_000],
        message: `A scan of ${folderPath} did not adopt ${target}. See apps/worker/pornarr_worker/jobs/scan.py.`,
      },
    )
    .toBe(true);
}

async function errorBody(response: APIResponse): Promise<ErrorBody> {
  return (await response.json()) as ErrorBody;
}

async function limits(page: Page): Promise<Limits> {
  return apiGet<Limits>(page, "/api/admin/transcode/limits");
}

async function adminSessions(page: Page): Promise<AdminSession[]> {
  return apiGet<AdminSession[]>(page, "/api/admin/transcode/sessions");
}

async function startSession(page: Page, mediaId: string): Promise<StartedSession> {
  const response = await apiPostRaw(page, `/api/transcode/media/${mediaId}/sessions`);
  expect(
    response.status(),
    `Starting a transcode for ${mediaId} answered ${response.status()}: ${await response.text()}`,
  ).toBe(201);
  return (await response.json()) as StartedSession;
}

/** Shorten the heartbeat key rather than waiting sixty real seconds for it. */
function silenceSession(sessionId: string): void {
  const key = `pornarr:transcode:session:${sessionId}:heartbeat`;
  expect(stackRedis(["PEXPIRE", key, "1"]), `Redis did not expire ${key}.`).toBe("1");
}

function heartbeatTtl(sessionId: string): number {
  const ttl = stackRedis(["TTL", `pornarr:transcode:session:${sessionId}:heartbeat`]);
  return ttl === null ? -2 : Number.parseInt(ttl, 10);
}

/**
 * PostgreSQL backends the API is holding inside an open transaction.
 *
 * Restricted to the statement `get_current_user` runs, because that is the one
 * a streaming response leaves behind: every other query in the product is
 * inside a request that ends.
 */
function heldTransactions(): number {
  const counted = databaseScalar(
    "select count(*) from pg_stat_activity where datname = 'pornarr' " +
      "and state = 'idle in transaction' and query like 'SELECT users%'",
  );
  return counted === null ? 0 : Number.parseInt(counted, 10);
}

function segmentDirectory(sessionId: string): string {
  return join(hostDataRoot(), "transcodes", sessionId);
}

test.describe("playback and transcoding", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    // One stack, one session cap. A session an earlier test left open would
    // make this one fail for a reason that is not its own.
    await releaseTranscodeSessions(page);
  });

  test.afterAll(() => {
    // The library keeps its rows and the hardlink the import made; only the
    // source this file staged goes, so the watched download tree does not grow
    // a fixture per run. The library fixtures under `<data>/library` stay: they
    // are what a rerun reuses instead of encoding twenty minutes again.
    rmSync(join(downloadsRoot(), "piece08"), { recursive: true, force: true });
  });

  test.describe("the direct-play decision", () => {
    test("compares the container, both codecs, the profile and the level", async ({ page }) => {
      const probeMedia = await seedProbedMedia(page);
      test.skip(probeMedia === null, NO_DOWNLOAD_VOLUME);
      if (probeMedia === null) return;

      const codecs = probeMedia.codecs as {
        container: string;
        video: { codec: string; profile: string; level: number };
        audio: { codec: string };
      };
      // All five properties transcode.md L5-7 names are recorded, or the matrix
      // below would be varying capabilities against nothing.
      expect(
        {
          container: typeof codecs.container,
          video_codec: typeof codecs.video.codec,
          audio_codec: typeof codecs.audio.codec,
          profile: typeof codecs.video.profile,
          level: typeof codecs.video.level,
        },
        `${probeMedia.title} was imported without the full technical metadata the decision needs.`,
      ).toEqual({
        container: "string",
        video_codec: "string",
        audio_codec: "string",
        profile: "string",
        level: "number",
      });

      const full = new URLSearchParams({
        containers: codecs.container,
        video_codecs: codecs.video.codec,
        audio_codecs: codecs.audio.codec,
        video_profiles: codecs.video.profile,
        maximum_video_level: String(codecs.video.level),
      });

      // (what the player declares, what it must be told) - every row differs
      // from the matching player in exactly one field, so the reason names the
      // field that was withheld and nothing else.
      const rows: readonly [string, Record<string, string>, readonly string[]][] = [
        ["a player that declares everything the file is", {}, []],
        [
          "a player that cannot open the container",
          { containers: "webm" },
          ["container_unsupported"],
        ],
        ["a player without the video codec", { video_codecs: "vp9" }, ["video_codec_unsupported"]],
        ["a player without the audio codec", { audio_codecs: "opus" }, ["audio_codec_unsupported"]],
        [
          "a player without the video profile",
          { video_profiles: "baseline" },
          ["video_profile_unsupported"],
        ],
        [
          "a player capped below the file's level",
          { maximum_video_level: String(codecs.video.level - 1) },
          ["video_level_unsupported"],
        ],
        [
          "a player that declares the container in a different case",
          { containers: codecs.container.toUpperCase() },
          [],
        ],
        [
          "a player that declares the profile in a different case",
          { video_profiles: codecs.video.profile.toLowerCase() },
          [],
        ],
        [
          "a player capped exactly at the file's level",
          { maximum_video_level: String(codecs.video.level) },
          [],
        ],
        [
          "a player that shares none of the file's four decoded properties",
          {
            containers: "webm",
            video_codecs: "av1",
            audio_codecs: "flac",
            video_profiles: "baseline",
            maximum_video_level: "1",
          },
          [
            "container_unsupported",
            "video_codec_unsupported",
            "audio_codec_unsupported",
            "video_profile_unsupported",
            "video_level_unsupported",
          ],
        ],
      ];

      for (const [description, override, expected] of rows) {
        const query = new URLSearchParams(full);
        for (const [key, value] of Object.entries(override)) query.set(key, value);
        const info = await apiGet<PlaybackInfoBody>(
          page,
          `/api/media/${probeMedia.id}/playback-info?${query.toString()}`,
        );
        expect(
          { case: description, ...info },
          `${description}: playback-info did not answer with the expected reasons.`,
        ).toEqual({ case: description, direct_play: expected.length === 0, reasons: expected });
      }
    });

    test("a scanned title is probed, so the decision has something to compare", async ({
      page,
    }) => {
      const media = await seedStreamable(page, PLAYABLE_TITLE, PLAYABLE_SECONDS);
      test.skip(media === null, NO_FIXTURE);
      if (media === null) return;

      // The scan used to write a MediaFile with a path, a size and an mtime and
      // never probe, so `codecs` was null for every file adopted from disk and
      // the direct-play comparison docs/pipelines/transcode.md L5-7 describes
      // had no left-hand side: every scanned title answered
      // `container_unknown` and went to FFmpeg. Adopting an existing library
      // from disk is the ordinary way to start with this product, so that was
      // most of the media on most instances.
      //
      // Polled: the probe is a queued job rather than part of the scan, so that
      // a scan of a large library is not held behind ffprobe.
      let codecs: Record<string, unknown> | null = null;
      await expect
        .poll(
          async () => {
            codecs = (await apiGet<MediaDetail>(page, `/api/media/${media.id}`)).codecs as Record<
              string,
              unknown
            > | null;
            return codecs !== null;
          },
          {
            timeout: 90_000,
            intervals: [2_000],
            message:
              "A scanned file never got technical metadata. See `queue_missing_technical_metadata` " +
              "in apps/worker/pornarr_worker/jobs/scan.py.",
          },
        )
        .toBe(true);

      // All four properties the decision reads, so a later failure can be told
      // apart from a probe that only half ran.
      const video = (codecs as unknown as { video: Record<string, unknown> }).video;
      expect({
        container: typeof (codecs as unknown as { container: unknown }).container,
        codec: typeof video.codec,
        profile: typeof video.profile,
        level: typeof video.level,
      }).toEqual({ container: "string", codec: "string", profile: "string", level: "number" });

      const info = await apiGet<PlaybackInfoBody>(page, `/api/media/${media.id}/playback-info`);
      expect(
        info.reasons,
        "A probed file still answers as though nothing about it were known.",
      ).not.toContain("container_unknown");
      expect(info.reasons).not.toContain("video_codec_unknown");
      expect(info.reasons).not.toContain("video_profile_unknown");
      expect(info.reasons).not.toContain("video_level_unknown");
    });

    test("a title that does not exist is a 404, not an unknown-everything decision", async ({
      page,
    }) => {
      const response = await page.request.get(`/api/media/${randomUUID()}/playback-info`);
      expect(response.status()).toBe(404);
      expect((await errorBody(response)).code).toBe("NOT_FOUND");
    });

    test("the browser baseline direct-plays an ordinary H.264 MP4", async ({ page }) => {
      // PRODUCT DEFECT D7/D8, and the one that decides the whole piece. A
      // player that sends no capability query gets `DEFAULT_CLIENT_CAPABILITIES`
      // (packages/core/pornarr_core/playback.py:48-54), which the product's own
      // comment calls "the MP4/H.264/AAC combination every modern browser
      // handles". It cannot match anything ffprobe produces:
      //   - `container` is compared with string equality against `"mp4"`, while
      //     ffprobe reports the demuxer family `"mov,mp4,m4a,3gp,3g2,mj2"`;
      //   - `maximum_video_level` is 4.2, while ffprobe reports H.264 levels in
      //     tenths, so level 4.0 arrives as 40 and 40 > 4.2.
      // The web player only asks for a transcode when this answers false
      // (apps/web/src/components/player/video-player.tsx:148-151), so every
      // playback on this instance goes to FFmpeg - the exact opposite of
      // ADR 0035's "most playback never touches the GPU".
      const media = await seedProbedMedia(page);
      test.skip(media === null, NO_DOWNLOAD_VOLUME);
      if (media === null) return;

      const codecs = media.codecs as {
        container: string;
        video: { codec: string; profile: string; level: number };
        audio: { codec: string };
      };
      // The fixture really is the combination the baseline names, or this test
      // would be pinning the wrong thing.
      expect({
        video: codecs.video.codec,
        audio: codecs.audio.codec,
        container: codecs.container,
      }).toEqual({
        video: "h264",
        audio: "aac",
        container: "mov,mp4,m4a,3gp,3g2,mj2",
      });

      const info = await apiGet<PlaybackInfoBody>(page, `/api/media/${media.id}/playback-info`);
      expect(
        info,
        "An H.264/AAC MP4 is refused direct play by the product's own default player matrix.",
      ).toEqual({ direct_play: true, reasons: [] });
    });
  });

  test.describe("byte-range streaming", () => {
    test("serves the whole file, single ranges, suffix ranges and multipart", async ({ page }) => {
      const media = await seedStreamable(page, SECOND_TITLE, SECOND_SECONDS);
      test.skip(media === null, NO_FIXTURE);
      if (media === null) return;
      const size = statSync(join(streamableRoot() ?? "", `${SECOND_TITLE}.mp4`)).size;
      expect(media.size, "The library disagrees with the file on disk.").toBe(size);
      const url = `/api/media/${media.id}/stream`;

      const whole = await page.request.get(url);
      expect(whole.status()).toBe(200);
      expect(whole.headers()["accept-ranges"]).toBe("bytes");
      expect(whole.headers()["content-length"]).toBe(String(size));
      expect(whole.headers()["content-type"]).toBe("video/mp4");
      const body = await whole.body();
      expect(body.byteLength).toBe(size);

      const first = await page.request.get(url, { headers: { Range: "bytes=0-1023" } });
      expect(first.status()).toBe(206);
      expect(first.headers()["content-range"]).toBe(`bytes 0-1023/${size}`);
      expect(first.headers()["content-length"]).toBe("1024");
      const firstBody = await first.body();
      expect(firstBody.byteLength).toBe(1024);
      // The bytes are the file's, not a placeholder of the right length.
      expect(firstBody.equals(body.subarray(0, 1024))).toBe(true);

      // Open-ended: everything from the offset to the last byte.
      const tail = await page.request.get(url, { headers: { Range: `bytes=${size - 512}-` } });
      expect(tail.status()).toBe(206);
      expect(tail.headers()["content-range"]).toBe(`bytes ${size - 512}-${size - 1}/${size}`);
      expect((await tail.body()).equals(body.subarray(size - 512))).toBe(true);

      // Suffix: the last n bytes, which is how a player reads an MP4 index.
      const suffix = await page.request.get(url, { headers: { Range: "bytes=-512" } });
      expect(suffix.status()).toBe(206);
      expect(suffix.headers()["content-range"]).toBe(`bytes ${size - 512}-${size - 1}/${size}`);
      expect((await suffix.body()).equals(body.subarray(size - 512))).toBe(true);

      // A range that runs past the end is clamped rather than refused.
      const clamped = await page.request.get(url, {
        headers: { Range: `bytes=${size - 10}-${size + 10_000}` },
      });
      expect(clamped.status()).toBe(206);
      expect(clamped.headers()["content-range"]).toBe(`bytes ${size - 10}-${size - 1}/${size}`);

      const multi = await page.request.get(url, { headers: { Range: "bytes=0-9,20-29" } });
      expect(multi.status()).toBe(206);
      const contentType = multi.headers()["content-type"];
      expect(contentType).toMatch(/^multipart\/byteranges; boundary=[0-9a-f]{32}$/);
      const boundary = contentType.split("boundary=")[1];
      const multiBody = (await multi.body()).toString("latin1");
      expect(multiBody).toContain(
        `--${boundary}\r\nContent-Type: video/mp4\r\nContent-Range: bytes 0-9/${size}\r\n\r\n`,
      );
      expect(multiBody).toContain(
        `--${boundary}\r\nContent-Type: video/mp4\r\nContent-Range: bytes 20-29/${size}\r\n\r\n`,
      );
      expect(multiBody.endsWith(`--${boundary}--\r\n`)).toBe(true);
      expect(multi.headers()["content-length"]).toBe(String(multiBody.length));
    });

    test("refuses a range it cannot satisfy with 416 and the file's size", async ({ page }) => {
      const media = await seedStreamable(page, SECOND_TITLE, SECOND_SECONDS);
      test.skip(media === null, NO_FIXTURE);
      if (media === null) return;
      const size = media.size;
      const url = `/api/media/${media.id}/stream`;

      // (header, why it cannot be satisfied)
      const unsatisfiable: readonly [string, string][] = [
        [`bytes=${size}-`, "the first byte is past the end of the file"],
        [`bytes=${size + 1000}-`, "the whole range is past the end of the file"],
        ["bytes=5-3", "the last byte precedes the first"],
        ["items=0-1", "the unit is not bytes"],
        ["bytes=-0", "a zero-length suffix selects nothing"],
        ["bytes=abc-def", "the offsets are not numbers"],
        ["bytes=", "no range was given at all"],
      ];

      for (const [header, why] of unsatisfiable) {
        const response = await page.request.get(url, { headers: { Range: header } });
        expect(response.status(), `${header} (${why}) was not refused.`).toBe(416);
        // RFC 9110: the refusal states the length the client should have asked
        // within, or the client has no way to correct itself.
        expect(response.headers()["content-range"]).toBe(`bytes */${size}`);
        expect(await errorBody(response)).toEqual({
          code: "RANGE_NOT_SATISFIABLE",
          status: 416,
          context: {},
        });
      }
    });

    test("is refused entirely without a session", async ({ page, browser }) => {
      const media = await seedStreamable(page, SECOND_TITLE, SECOND_SECONDS);
      test.skip(media === null, NO_FIXTURE);
      if (media === null) return;

      const anonymous = await browser.newContext({ baseURL: test.info().project.use.baseURL });
      try {
        const response = await anonymous.request.get(`/api/media/${media.id}/stream`, {
          headers: { Range: "bytes=0-1023" },
        });
        expect(response.status()).toBe(401);
        expect((await errorBody(response)).code).toBe("NOT_AUTHENTICATED");
      } finally {
        await anonymous.close();
      }
    });

    test("an imported title streams from the root folder it was imported into", async ({
      page,
    }) => {
      // This was PRODUCT DEFECT D1, and is now the assertion that keeps it shut.
      // `Settings.library_path` is hardcoded to `<data>/library`
      // (packages/shared/pornarr_shared/config.py:148-149), while the operator
      // names a root folder in the wizard and the importer files titles under
      // *that* (apps/worker/pornarr_worker/jobs/import_media.py:222). `/stream`
      // and the transcode start compared a file against `library_path` alone, so
      // on this instance - root folder `/data`, the path the wizard offers -
      // nothing the product had imported could be played at all. Both now ask
      // `pornarr_db.root_folders.library_roots`, which is `library_path` plus
      // every enabled root folder, and neither will serve a file outside them.
      const imported = await seedProbedMedia(page);
      test.skip(imported === null, NO_DOWNLOAD_VOLUME);
      if (imported === null) return;

      // The library calls it playable and the player will offer it, which is
      // what made the refusal a trap rather than a missing feature.
      expect(await apiGet<{ playable: boolean }>(page, `/api/media/${imported.id}`)).toMatchObject({
        playable: true,
      });

      const response = await page.request.get(`/api/media/${imported.id}/stream`, {
        headers: { Range: "bytes=0-1023" },
      });
      expect(
        response.status(),
        `${imported.path} was imported by the product and must be streamable by it.`,
      ).toBe(206);
    });

    test("a transcode starts for the same imported title, from the same roots", async ({
      page,
    }) => {
      // The other half of D1. `/stream` and the transcode start shared the
      // `library_path` guard, so an imported title had no playback path at all:
      // not direct, not transcoded. They share `library_roots` now, so this is
      // the same claim from the other side.
      const imported = await seedProbedMedia(page);
      test.skip(imported === null, NO_DOWNLOAD_VOLUME);
      if (imported === null) return;

      const response = await apiPostRaw(page, `/api/transcode/media/${imported.id}/sessions`);
      expect(
        response.status(),
        `${imported.path} was imported by the product and must be transcodable by it.`,
      ).toBe(201);
      await releaseTranscodeSessions(page);
    });
  });

  test.describe("transcode sessions", () => {
    test("records every documented field and shows it to the owner and the administrator", async ({
      page,
    }) => {
      const media = await seedStreamable(page, PLAYABLE_TITLE, PLAYABLE_SECONDS);
      test.skip(media === null, NO_FIXTURE);
      if (media === null) return;

      const started = await startSession(page, media.id);
      try {
        expect(started.playlist_url).toBe(
          `/api/transcode/sessions/${started.session_id}/hls/master.m3u8`,
        );

        const admin = (await adminSessions(page)).find(
          (session) => session.id === started.session_id,
        );
        expect(
          admin,
          "The session the API just created is not in the administrator's list.",
        ).toBeDefined();
        if (admin === undefined) return;
        expect(admin.media_id).toBe(media.id);
        expect(admin.media_title).toBe(media.title);
        expect(admin.username).toBe("gauntlet");
        // No hardware encoder passed the startup capability check on this host,
        // so the session the product chose has to be a software one.
        expect(admin.hardware).toBe(false);
        expect(admin.elapsed_seconds).toBeGreaterThanOrEqual(0);
        expect(Date.parse(admin.created_at)).not.toBeNaN();

        const mine = await apiGet<MySession[]>(page, "/api/playback/sessions/mine");
        const own = mine.find((session) => session.session_id === started.session_id);
        expect(own, "The owner cannot see their own session under Playing on.").toBeDefined();
        expect(own?.media_title).toBe(media.title);
        expect(own?.hardware).toBe(false);

        // The record docs/pipelines/transcode.md L16-17 promises names a
        // `variant` and a `mode (hw|sw)`. The product writes the variant into
        // the field it calls `mode`, and carries hardware-or-software in a
        // separate boolean, so the two documented names do not both exist.
        expect(
          { mode: admin.mode, hardware: admin.hardware },
          "The session record's shape changed; docs/pipelines/transcode.md L16-17 and D3 in " +
            "BUILD.md describe what it was.",
        ).toEqual({ mode: "hls", hardware: false });

        const record = stackRedis(["GET", `pornarr:transcode:session:${started.session_id}`]);
        expect(record, "The session is not in Redis at all.").not.toBeNull();
        const stored = JSON.parse(record ?? "{}") as Record<string, unknown>;
        expect(Object.keys(stored).sort()).toEqual([
          "created_at",
          "device_label",
          "hardware",
          "id",
          "media_id",
          "mode",
          "process_id",
          "user_id",
        ]);
        expect(stored.process_id).toBeGreaterThan(0);
      } finally {
        await releaseTranscodeSessions(page);
      }
    });

    test("hands FFmpeg the documented software HLS command line", async ({ page }) => {
      const media = await seedStreamable(page, PLAYABLE_TITLE, PLAYABLE_SECONDS);
      test.skip(media === null, NO_FIXTURE);
      if (media === null) return;

      const started = await startSession(page, media.id);
      try {
        const command = transcodeCommandLine(started.session_id);
        test.skip(
          command === null,
          "The FFmpeg process is not visible from the test runner, so the command line the " +
            "decision produced cannot be read.",
        );
        if (command === null) return;

        // Asserted as strings, the way Jellyfin asserts an encoding argument
        // fragment rather than the process: this is the decision's output.
        expect(command).toContain(`-i ${media.path} `);
        // No hardware encoder survived detection on this host, so the software
        // encoder is the one the ladder must have chosen.
        expect(command).toContain("-c:v libx264");
        expect(command).not.toContain("nvenc");
        expect(command).not.toContain("vaapi");
        expect(command).not.toContain("-init_hw_device");
        expect(command).toContain("-c:a aac");
        expect(command).toContain("-f hls");
        expect(command).toContain("-hls_time 4");
        expect(command).toContain("-hls_playlist_type event");
        expect(command).toContain(
          `-hls_segment_filename /data/transcodes/${started.session_id}/segment_%05d.ts`,
        );
        expect(command).toContain("-master_pl_name master.m3u8");
        expect(command).toContain(`/data/transcodes/${started.session_id}/variant.m3u8`);
      } finally {
        await releaseTranscodeSessions(page);
      }
    });

    test("serves exactly master.m3u8, variant.m3u8 and segment_N.ts", async ({ page }) => {
      const media = await seedStreamable(page, PLAYABLE_TITLE, PLAYABLE_SECONDS);
      test.skip(media === null, NO_FIXTURE);
      if (media === null) return;

      const started = await startSession(page, media.id);
      const base = `/api/transcode/sessions/${started.session_id}/hls`;
      try {
        const master = await page.request.get(`${base}/master.m3u8`);
        expect(master.status()).toBe(200);
        expect(master.headers()["content-type"]).toContain("application/vnd.apple.mpegurl");
        const masterBody = await master.text();
        expect(masterBody).toContain("#EXTM3U");
        expect(masterBody).toContain("variant.m3u8");

        const variant = await page.request.get(`${base}/variant.m3u8`);
        expect(variant.status()).toBe(200);
        const variantBody = await variant.text();
        expect(variantBody).toContain("#EXT-X-TARGETDURATION");
        expect(variantBody).toContain("segment_00000.ts");

        await expect
          .poll(async () => (await page.request.get(`${base}/segment_00000.ts`)).status(), {
            timeout: 30_000,
            message: "FFmpeg never produced a first segment.",
          })
          .toBe(200);
        const segment = await page.request.get(`${base}/segment_00000.ts`);
        expect(segment.headers()["content-type"]).toContain("video/mp2t");
        expect((await segment.body()).byteLength).toBeGreaterThan(0);
      } finally {
        await releaseTranscodeSessions(page);
      }
    });

    test("no segment name can reach outside the session's own directory", async ({ page }) => {
      const media = await seedStreamable(page, PLAYABLE_TITLE, PLAYABLE_SECONDS);
      test.skip(media === null, NO_FIXTURE);
      if (media === null) return;

      const started = await startSession(page, media.id);
      const base = `/api/transcode/sessions/${started.session_id}/hls`;
      try {
        // Percent-encoded so the separators survive to the handler rather than
        // being resolved by the router - the guard is the subject, not routing.
        // (asset, what it would reach if the guard were missing)
        const refused: readonly [string, string][] = [
          ["..%2f..%2f..%2fetc%2fpasswd", "a file outside the data tree"],
          ["%2fetc%2fpasswd", "an absolute path"],
          ["..%2fmaster.m3u8", "another session's playlist one level up"],
          [
            `..%2f${started.session_id}-sibling%2fsegment_00000.ts`,
            "a directory whose name merely starts with this session's id",
          ],
          ["segment_00000.ts%2f..%2f..%2fmaster.m3u8", "a traversal hidden behind a valid prefix"],
          ["master.m3u8x", "a name that merely starts with the playlist's"],
          ["xmaster.m3u8", "a name that merely ends with the playlist's"],
          ["segment_.ts", "a segment with no number"],
          ["segment_-1.ts", "a segment with a negative number"],
          ["segment_00000.TS", "the right segment under the wrong extension case"],
          ["segment_00000.txt", "the right segment under the wrong extension"],
        ];

        for (const [asset, reach] of refused) {
          const response = await page.request.get(`${base}/${asset}`, { maxRedirects: 0 });
          expect(response.status(), `${asset} (${reach}) was not refused.`).toBe(404);
          expect(await response.text()).not.toContain("root:");
        }

        // The negative space: the names that are allowed still are.
        expect((await page.request.get(`${base}/master.m3u8`)).status()).toBe(200);
      } finally {
        await releaseTranscodeSessions(page);
      }
    });

    test("only the owner may refresh, read or stop a session", async ({ page, browser }) => {
      const media = await seedStreamable(page, PLAYABLE_TITLE, PLAYABLE_SECONDS);
      test.skip(media === null, NO_FIXTURE);
      if (media === null) return;

      const started = await startSession(page, media.id);
      const anonymous = await browser.newContext({ baseURL: test.info().project.use.baseURL });
      const viewer = await signedInViewer(page, browser);
      try {
        const hls = `/api/transcode/sessions/${started.session_id}/hls/master.m3u8`;
        const heartbeat = `/api/transcode/sessions/${started.session_id}/heartbeat`;

        const unauthenticated = await anonymous.request.get(hls);
        expect(unauthenticated.status(), "A caller with no session was served the stream.").toBe(
          401,
        );
        expect((await errorBody(unauthenticated)).code).toBe("NOT_AUTHENTICATED");
        // The double-submit check runs in front of authentication, so an
        // unsafe request without a session is refused as a forged one rather
        // than as an anonymous one. Asserted as it is, not as it might read.
        const forged = await anonymous.request.post(heartbeat);
        expect(forged.status()).toBe(403);
        expect((await errorBody(forged)).code).toBe("CSRF_FAILED");

        test.skip(
          viewer === null,
          "A second account could not be created through the invite flow, so the boundary " +
            "between two signed-in users cannot be exercised - only the anonymous one above.",
        );
        if (viewer === null) return;

        // A different signed-in account is refused each of the three things the
        // owner may do, and told it is a permission problem rather than a
        // missing session - the session plainly exists.
        const read = await viewer.get(hls);
        expect(read.status(), "Another account was served somebody else's stream.").toBe(403);
        expect((await errorBody(read)).code).toBe("FORBIDDEN");

        const refreshed = await viewer.post(heartbeat);
        expect(refreshed.status(), "Another account refreshed somebody else's session.").toBe(403);
        expect((await errorBody(refreshed)).code).toBe("FORBIDDEN");

        const stopped = await viewer.delete(`/api/transcode/sessions/${started.session_id}`);
        expect(stopped.status(), "Another account stopped somebody else's session.").toBe(403);

        // Re-read rather than trust the refusals: the session is still running.
        expect((await adminSessions(page)).map((session) => session.id)).toContain(
          started.session_id,
        );
        expect(transcodeProcessCount(started.session_id)).toBe(1);

        // And the administration surface is not theirs either.
        const listed = await viewer.get("/api/admin/transcode/sessions");
        expect(listed.status()).toBe(403);
        // Their own "Playing on" is empty, because none of it is theirs.
        const mine = await viewer.get("/api/playback/sessions/mine");
        expect(mine.status()).toBe(200);
        expect(await mine.json()).toEqual([]);

        // A session that does not exist is a 404 for its would-be owner too.
        const missing = await apiPostRaw(page, `/api/transcode/sessions/${randomUUID()}/heartbeat`);
        expect(missing.status()).toBe(404);
      } finally {
        await anonymous.close();
        await viewer?.close();
        await releaseTranscodeSessions(page);
      }
    });

    test("a heartbeat puts the sixty-second TTL back and the session outlives it", async ({
      page,
    }) => {
      test.skip(!canDriveStackRedis(), NO_REDIS);
      const media = await seedStreamable(page, PLAYABLE_TITLE, PLAYABLE_SECONDS);
      test.skip(media === null, NO_FIXTURE);
      if (media === null) return;

      const started = await startSession(page, media.id);
      try {
        expect(
          heartbeatTtl(started.session_id),
          "A new session's heartbeat key does not carry the documented sixty-second TTL.",
        ).toBe(SESSION_TTL_SECONDS);

        // Wind the key down to well under a minute, then let the player do what
        // it does every fifteen seconds and watch the TTL go back to sixty.
        expect(
          stackRedis(["EXPIRE", `pornarr:transcode:session:${started.session_id}:heartbeat`, "5"]),
        ).toBe("1");
        expect(heartbeatTtl(started.session_id)).toBeLessThanOrEqual(5);

        const beat = await apiPostRaw(
          page,
          `/api/transcode/sessions/${started.session_id}/heartbeat`,
        );
        expect(beat.status()).toBe(204);
        expect(
          heartbeatTtl(started.session_id),
          "A heartbeat did not restore the session's full TTL.",
        ).toBe(SESSION_TTL_SECONDS);

        // Past the point the un-refreshed key would have died, the session is
        // still the product's own answer for what is running.
        expect((await adminSessions(page)).map((session) => session.id)).toContain(
          started.session_id,
        );
        expect(transcodeProcessCount(started.session_id)).toBe(1);
      } finally {
        await releaseTranscodeSessions(page);
      }
    });

    test("silence kills FFmpeg and expires the segments", async ({ page }) => {
      test.skip(!canDriveStackRedis(), NO_REDIS);
      const media = await seedStreamable(page, PLAYABLE_TITLE, PLAYABLE_SECONDS);
      test.skip(media === null, NO_FIXTURE);
      if (media === null) return;

      const started = await startSession(page, media.id);
      const directory = segmentDirectory(started.session_id);
      await expect
        .poll(() => transcodeProcessCount(started.session_id), {
          timeout: 15_000,
          message: "FFmpeg never started for this session.",
        })
        .toBe(1);
      expect(existsSync(directory)).toBe(true);

      // The clock is forced rather than waited out: the same key the product
      // gives sixty seconds is given one millisecond, so what follows is the
      // product's own silence teardown running at its own speed.
      silenceSession(started.session_id);

      await expect
        .poll(async () => (await adminSessions(page)).map((session) => session.id), {
          timeout: 30_000,
          message:
            "A session with no heartbeat is still listed as active. See the reaper in " +
            "apps/api/pornarr_api/lifespan.py:34-37.",
        })
        .not.toContain(started.session_id);

      await expect
        .poll(() => transcodeProcessCount(started.session_id), {
          timeout: 30_000,
          message: "FFmpeg outlived the session that owned it.",
        })
        .toBe(0);
      await expect
        .poll(() => existsSync(directory), {
          timeout: 30_000,
          message: "The segment directory outlived the session that owned it.",
        })
        .toBe(false);

      // The player's next request for a segment gets an answer, not a hang.
      const asset = await page.request.get(
        `/api/transcode/sessions/${started.session_id}/hls/segment_00000.ts`,
      );
      expect(asset.status()).toBe(404);
    });

    test("one viewer on one title reuses the session they already have", async ({ page }) => {
      const media = await seedStreamable(page, PLAYABLE_TITLE, PLAYABLE_SECONDS);
      test.skip(media === null, NO_FIXTURE);
      if (media === null) return;

      const first = await startSession(page, media.id);
      try {
        const second = await startSession(page, media.id);
        expect(
          second.session_id,
          "A remount asked for a second session instead of being handed the running one.",
        ).toBe(first.session_id);
        expect(
          (await adminSessions(page)).filter((session) => session.media_id === media.id),
        ).toHaveLength(1);
      } finally {
        await releaseTranscodeSessions(page);
      }
    });

    test("an administrator can end somebody's session", async ({ page }) => {
      const media = await seedStreamable(page, PLAYABLE_TITLE, PLAYABLE_SECONDS);
      test.skip(media === null, NO_FIXTURE);
      if (media === null) return;

      const started = await startSession(page, media.id);
      const directory = segmentDirectory(started.session_id);
      const killed = await apiDelete(page, `/api/admin/transcode/sessions/${started.session_id}`);
      expect(killed.status()).toBe(204);

      // Re-read rather than believe the response to the delete.
      expect((await adminSessions(page)).map((session) => session.id)).not.toContain(
        started.session_id,
      );
      await expect.poll(() => existsSync(directory), { timeout: 15_000 }).toBe(false);
      expect(transcodeProcessCount(started.session_id)).toBe(0);

      const again = await apiDelete(page, `/api/admin/transcode/sessions/${started.session_id}`);
      expect(again.status(), "Ending a session that is gone is not an error.").toBe(404);
    });
  });

  /**
   * The half of the pipeline that lives in the browser.
   *
   * "Direct play is checked before any transcode" and "the player refreshes the
   * session every fifteen seconds" are claims about the player, not about the
   * API, and neither can be proven by calling an endpoint: the ordering and the
   * cadence only exist in `apps/web/src/components/player/video-player.tsx`.
   * These drive the real player in a real browser and watch the requests it
   * makes.
   */
  test.describe("the player", () => {
    test("asks what the file needs before it asks for a transcode", async ({ page }) => {
      const media = await seedStreamable(page, PLAYABLE_TITLE, PLAYABLE_SECONDS);
      test.skip(media === null, NO_FIXTURE);
      if (media === null) return;

      const calls: string[] = [];
      page.on("request", (request) => {
        const path = new URL(request.url()).pathname;
        if (path === `/api/media/${media.id}/playback-info`) calls.push("playback-info");
        if (
          path === `/api/transcode/media/${media.id}/sessions` &&
          request.method() === "POST" &&
          !calls.includes("transcode")
        ) {
          calls.push("transcode");
        }
      });

      try {
        await page.goto(`/library/${media.id}`);
        await expect
          .poll(() => calls, {
            timeout: 30_000,
            message:
              "The player never asked for a transcode, so the ordering claim cannot be read " +
              "from it. See apps/web/src/components/player/video-player.tsx.",
          })
          .toContain("transcode");

        // ADR 0035 L9: "Direct play is checked before any transcode." The
        // decision is not merely consulted somewhere - it is the first thing
        // the player asks, and the transcode is what it does when the answer
        // comes back false.
        expect(
          calls[0],
          "The player asked for a transcode before it asked whether one was needed.",
        ).toBe("playback-info");
        expect(calls.indexOf("playback-info")).toBeLessThan(calls.indexOf("transcode"));
      } finally {
        await page.goto("/library");
        await releaseTranscodeSessions(page);
      }
    });

    test("refreshes its session every fifteen seconds", async ({ page }) => {
      // PRODUCT DEFECT D9. docs/pipelines/transcode.md L12-13: the session TTL
      // is "refreshed by a heartbeat from the player every fifteen seconds".
      // `apps/web/src/components/player/video-player.tsx:234` sets the interval
      // to 25_000. The session survives - sixty seconds is still more than
      // twenty-five - but the documented margin of four missed beats before a
      // kill is in fact one and a half, so a viewer whose tab is throttled or
      // whose network stalls for thirty seconds loses the stream the document
      // says they would keep.
      const media = await seedStreamable(page, PLAYABLE_TITLE, PLAYABLE_SECONDS);
      test.skip(media === null, NO_FIXTURE);
      if (media === null) return;
      test.setTimeout(Math.max(test.info().timeout, HEARTBEAT_OBSERVATION_MILLISECONDS + 120_000));

      let sessionStarted = 0;
      const heartbeats: number[] = [];
      page.on("requestfinished", (request) => {
        const path = new URL(request.url()).pathname;
        if (path === `/api/transcode/media/${media.id}/sessions` && sessionStarted === 0) {
          sessionStarted = Date.now();
        }
        if (/^\/api\/transcode\/sessions\/[0-9a-f-]+\/heartbeat$/.test(path)) {
          heartbeats.push(Date.now());
        }
      });

      try {
        await page.goto(`/library/${media.id}`);
        await expect
          .poll(() => sessionStarted, {
            timeout: 30_000,
            message: "The player never started a transcode session to refresh.",
          })
          .toBeGreaterThan(0);
        await page.waitForTimeout(HEARTBEAT_OBSERVATION_MILLISECONDS);

        const watched = HEARTBEAT_OBSERVATION_MILLISECONDS / 1000;
        const firstDelay =
          heartbeats.length === 0 ? null : Math.round((heartbeats[0] - sessionStarted) / 1000);
        const observed =
          firstDelay === null
            ? `no heartbeat at all in ${watched} seconds`
            : `its first heartbeat after ${firstDelay} seconds, ${heartbeats.length} in ${watched}`;
        expect(
          firstDelay,
          `The player sent ${observed}. docs/pipelines/transcode.md L12-13 says every fifteen seconds; apps/web/src/components/player/video-player.tsx:234 sets the interval to 25_000.`,
        ).toBeLessThanOrEqual(HEARTBEAT_TOLERANCE_SECONDS);
      } finally {
        await page.goto("/library");
        await releaseTranscodeSessions(page);
      }
    });

    test("tells a refused viewer why the stream did not start", async ({ page }) => {
      // PRODUCT DEFECT D11. The API refuses correctly - 429,
      // `TRANSCODE_LIMIT_REACHED`, `context.limit` - and none of it reaches the
      // viewer. `video-player.tsx:319-331` renders one string,
      // `player.unavailable`, for every failure, and `apps/web/src/i18n/en.json`
      // carries no `errors.TRANSCODE_LIMIT_REACHED` and no `errorSteps` entry
      // for it, so the code has no translation to map to either. api-contract.md
      // L28-29 says the frontend maps codes to translated messages, and
      // troubleshooting.md L16-21 expects the reader to decide between raising
      // the software limit and lowering the per-user one. Neither is possible
      // from what is on screen.
      const media = await seedStreamable(page, PLAYABLE_TITLE, PLAYABLE_SECONDS);
      const other = await seedStreamable(page, SECOND_TITLE, SECOND_SECONDS);
      test.skip(media === null || other === null, NO_FIXTURE);
      if (media === null || other === null) return;

      const state = await limits(page);
      test.skip(
        state.effective_hardware + state.effective_software > 1,
        `This host reports ${state.effective_hardware + state.effective_software} transcode slots, so one held session does not saturate it.`,
      );

      const held = await startSession(page, media.id);
      try {
        await page.goto(`/library/${other.id}`);

        // The player failed rather than played: no labelled player region, an
        // alert in its place.
        const alert = page.getByRole("alert").first();
        await expect(alert, "The refused player rendered no alert at all.").toBeVisible({
          timeout: 30_000,
        });
        await expect(page.getByRole("region", { name: `Player for ${other.title}` })).toHaveCount(
          0,
        );
        // The session the refusal was about is still the one that is running.
        expect((await adminSessions(page)).map((session) => session.id)).toEqual([held.session_id]);

        expect(
          (await alert.innerText()).toLowerCase(),
          "The viewer is told nothing about why the stream was refused or what to do about it.",
        ).toMatch(/transcode|limit|slot|in use|later/);
      } finally {
        await page.goto("/library");
        await releaseTranscodeSessions(page);
      }
    });

    test("the event stream tells a reverse proxy not to buffer it", async ({ page }) => {
      // api-contract.md L51-52 and troubleshooting.md L35-39: a progress bar
      // that only moves on reload is a buffering proxy, and the API's own
      // defence against it is this header. Read from the browser, because
      // `GET /api/events` never completes and a request that waits for a body
      // would hang.
      const stream = await page.evaluate(async () => {
        const controller = new AbortController();
        const response = await fetch("/api/events", { signal: controller.signal });
        const headers = {
          status: response.status,
          accelBuffering: response.headers.get("x-accel-buffering"),
          contentType: response.headers.get("content-type"),
          cacheControl: response.headers.get("cache-control"),
        };
        controller.abort();
        return headers;
      });

      expect(stream.status).toBe(200);
      expect(stream.contentType).toContain("text/event-stream");
      expect(
        stream.accelBuffering,
        "Without X-Accel-Buffering: no, nginx buffers the event stream and every progress " +
          "bar in the application stalls. api-contract.md L51-52.",
      ).toBe("no");
      expect(stream.cacheControl).toContain("no-cache");
    });

    test("an open event stream does not hold a database connection", async ({ page }) => {
      // `GET /api/events` used to authenticate through `get_current_user`,
      // which depends on `database_session` - a dependency with `yield`, whose
      // exit code FastAPI runs only once the response has finished. A
      // `StreamingResponse` finishes when the viewer leaves, so every
      // subscriber held one `AsyncSession`, and one pooled PostgreSQL
      // connection in an open transaction, for as long as their tab was open.
      // The pool is `pool_size=5, max_overflow=10`
      // (packages/db/pornarr_db/session.py:31-35), so the fifteenth open tab
      // took the last connection and every `/api` request after it waited out
      // `pool_timeout`. `/health` kept answering because `SetupMiddleware` lets
      // it through without a session, which is what made the symptom read as
      // "the stack is fine, my test is broken".
      //
      // The route takes `get_streaming_user` now, which hands the connection
      // back before the response is returned. This case is the measurement at
      // the database: `streaming.spec.ts` proves the rest of the application
      // keeps answering, and this proves why.
      test.skip(!canReadStackDatabase(), NO_DATABASE);
      // Three, not one: the count is shared with every other builder, and a
      // single concurrent close elsewhere could otherwise cancel out the one
      // connection this test is looking for.
      const streams = 3;
      const before = heldTransactions();

      // `fetch` settles as soon as the response head arrives, so awaiting it
      // proves the API really answered each stream while leaving the body open.
      // Without that the assertion below would also pass against a server that
      // refused all three.
      const answered = await page.evaluate(async (count) => {
        const scope = window as unknown as { __streams?: AbortController[] };
        scope.__streams = [];
        const opened: number[] = [];
        for (let index = 0; index < count; index += 1) {
          const controller = new AbortController();
          scope.__streams.push(controller);
          const response = await fetch("/api/events", { signal: controller.signal });
          opened.push(response.status);
        }
        return opened;
      }, streams);
      expect(answered, "GET /api/events did not answer, so there is nothing to measure.").toEqual([
        200, 200, 200,
      ]);

      try {
        // Held open for a moment first: the session would be taken while the
        // response is being produced, so a count read in the same instant the
        // heads arrived could miss one that is about to appear.
        await page.waitForTimeout(3_000);
        expect(
          heldTransactions() - before,
          `${streams} open event streams are holding that many PostgreSQL transactions open; fifteen of them is the whole pool, and every /api request after that waits out pool_timeout while /health carries on answering.`,
        ).toBe(0);
      } finally {
        await page.evaluate(() => {
          const scope = window as unknown as { __streams?: AbortController[] };
          for (const controller of scope.__streams ?? []) controller.abort();
          scope.__streams = undefined;
        });
        await expect
          .poll(heldTransactions, { timeout: 20_000, intervals: [1_000] })
          .toBeLessThanOrEqual(before);
      }
    });
  });

  test.describe("limits and capabilities", () => {
    test("names every acceleration method it rejected, and why", async ({ page }) => {
      const capabilities = await apiGet<Capabilities>(page, "/api/admin/transcode/capabilities");

      // "Why is it transcoding on CPU" has to be answerable from this response
      // alone (docs/pipelines/transcode.md L38-40), so every one of the three
      // documented methods is accounted for: it either works, or it says why not.
      const accounted = new Set([
        ...capabilities.methods.map((method) => method.acceleration),
        ...capabilities.rejections
          .map((rejection) => rejection.acceleration)
          .filter((value): value is string => value !== null),
      ]);
      for (const method of ["nvenc", "vaapi", "qsv"]) {
        expect(accounted, `${method} is neither offered nor rejected.`).toContain(method);
      }
      for (const rejection of capabilities.rejections) {
        expect(
          rejection.reason.length,
          "A rejection with an empty reason explains nothing.",
        ).toBeGreaterThan(0);
      }
      for (const method of capabilities.methods) {
        expect(
          method.codecs.length,
          `${method.acceleration} is offered with no codecs, which cannot encode anything.`,
        ).toBeGreaterThan(0);
      }

      // What this host is: no GPU is passed into the stack, so detection has to
      // land on software only, and the limit endpoint has to agree with it.
      expect(capabilities.methods).toEqual([]);
      expect(capabilities.nvidia_gpus).toEqual([]);
      expect(capabilities.rejections.map((rejection) => rejection.acceleration).sort()).toEqual([
        "nvenc",
        "qsv",
        "vaapi",
      ]);
      expect((await limits(page)).effective_hardware).toBe(0);
    });

    test("is visible only to an administrator", async ({ browser }) => {
      const anonymous = await browser.newContext({ baseURL: test.info().project.use.baseURL });
      try {
        for (const path of [
          "/api/admin/transcode/capabilities",
          "/api/admin/transcode/limits",
          "/api/admin/transcode/sessions",
          "/api/admin/transcode/failures",
        ]) {
          const response = await anonymous.request.get(path);
          expect(response.status(), `${path} answered a caller with no session.`).toBe(401);
        }
      } finally {
        await anonymous.close();
      }
    });

    test("is shown in the administration area", async ({ page }) => {
      // PRODUCT DEFECT D10. transcode.md L38-40 says what the machine supports
      // "is shown in the administration area, because 'why is it transcoding on
      // CPU' is otherwise unanswerable"; deployment.md L50-52 and
      // installation.md L110-112 both send the operator there to confirm the
      // GPU passthrough worked. Nothing in `apps/web/src` fetches
      // `/api/admin/transcode/capabilities` - the whole word "transcode"
      // appears in exactly three web files, and the only administration surface
      // that mentions it is the settings form's three session-limit fields.
      // The endpoint exists and answers; no screen reads it.
      const detected = await apiGet<Capabilities>(page, "/api/admin/transcode/capabilities");
      const named = [
        ...detected.methods.map((method) => method.acceleration),
        ...detected.rejections
          .map((rejection) => rejection.acceleration)
          .filter((value): value is string => value !== null),
      ];
      expect(named.length, "Detection named no acceleration method at all.").toBeGreaterThan(0);

      // Walked by clicking, from one document load, rather than by eleven
      // `page.goto` calls. That is not tidiness: every full page load opens an
      // event stream the server never closes, and fifteen of those exhaust the
      // API's database pool for everybody on this shared stack - defect D12,
      // pinned in `tests/api/test_events_pool.py`. Clicking is also what an
      // operator following deployment.md L50-52 actually does.
      await page.goto("/admin");
      const navigation = page.getByRole("navigation", { name: "Primary" });
      await expect(navigation).toBeVisible();
      const destinations = await navigation
        .locator("a[href^='/']")
        .evaluateAll((links) => links.map((link) => link.getAttribute("href") ?? ""));
      expect(
        destinations,
        "The administrator navigation is empty, so nothing was actually looked at.",
      ).toContain("/settings");

      const visited: string[] = [];
      const seen: string[] = [];
      for (const destination of destinations) {
        await navigation.locator(`a[href="${destination}"]`).first().click();
        await expect(page).toHaveURL(new RegExp(`${destination.replaceAll("/", "\\/")}$`));
        await expect(page.getByRole("main")).toBeVisible();
        visited.push(destination);
        // The whole screen, not just `main`: a capability panel could live in a
        // header, a drawer or a status strip and still count as shown.
        const text = (await page.locator("body").innerText()).toLowerCase();
        if (named.some((method) => text.includes(method))) seen.push(destination);
      }
      expect(visited.length, "No administration screen was visited at all.").toBeGreaterThan(3);

      expect(
        seen,
        `No administration screen names any of ${named.join(", ")}. Visited: ${visited.join(", ")}.`,
      ).not.toEqual([]);
    });

    test("reports what it detected, not what was configured", async ({ page }) => {
      const state = await limits(page);

      // Nothing is configured for the two session caps on this stack, so what
      // the endpoint reports for them is detection's own answer.
      expect(state.configured_hardware).toBeNull();
      expect(state.configured_software).toBeNull();
      expect(state.effective_hardware).toBe(state.hardware);
      expect(state.effective_software).toBe(state.software);
      expect(state.effective_per_user).toBe(state.per_user);
      expect(state.hardware_in_use).toBe(0);
      expect(state.software_in_use).toBe(0);

      // max_per_user is documented as two by default and is the one cap this
      // instance sets explicitly, so both halves must agree.
      expect(state.configured_per_user).toBe(2);
      expect(state.effective_per_user).toBe(2);
    });

    test("a saturated stack refuses with a code and a named limit, never an FFmpeg error", async ({
      page,
    }) => {
      const media = await seedStreamable(page, PLAYABLE_TITLE, PLAYABLE_SECONDS);
      const other = await seedStreamable(page, SECOND_TITLE, SECOND_SECONDS);
      test.skip(media === null || other === null, NO_FIXTURE);
      if (media === null || other === null) return;

      const state = await limits(page);
      // Read, never written: the point of this test is what the product
      // detected, and a cap the test set itself would prove only that the
      // setting round-trips.
      const capacity = state.effective_hardware + state.effective_software;
      expect(capacity, "The stack reports no transcode capacity at all.").toBeGreaterThan(0);
      test.skip(
        capacity > 1,
        `This host reports ${capacity} concurrent transcode slots, and saturating them from one account would first hit the per-user cap of ${state.effective_per_user}. Seed more fixtures and more accounts before removing this skip.`,
      );

      const first = await startSession(page, media.id);
      try {
        const refused = await apiPostRaw(page, `/api/transcode/media/${other.id}/sessions`);
        expect(refused.status()).toBe(429);

        const body = await errorBody(refused);
        expect(body.code).toBe("TRANSCODE_LIMIT_REACHED");
        expect(body.status).toBe(429);
        // The cause: which of the three caps was hit. Without it the operator
        // cannot tell "raise the software limit" from "lower the per-user one",
        // which is exactly what the troubleshooting entry asks them to decide.
        expect(body.context).toEqual({ limit: "software" });

        // And never a raw FFmpeg failure: no command line, no log, no exit code.
        const text = await refused.text();
        expect(text.toLowerCase()).not.toContain("ffmpeg");
        expect(text).not.toContain("libx264");
        expect(text).not.toContain("/data/");

        // The refusal did not disturb the session that was already running.
        expect((await adminSessions(page)).map((session) => session.id)).toEqual([
          first.session_id,
        ]);
        expect((await limits(page)).software_in_use).toBe(1);
      } finally {
        await releaseTranscodeSessions(page);
      }
    });

    test("a failed transcode is listed with a reason and no FFmpeg output", async ({ page }) => {
      const broken = await seedUndecodable(page);
      test.skip(broken === null, NO_FIXTURE);
      if (broken === null) return;

      const response = await apiPostRaw(page, `/api/transcode/media/${broken.id}/sessions`);
      // Starting succeeds: the limit check passes and FFmpeg is launched. What
      // fails is FFmpeg itself, moments later.
      expect(response.status()).toBe(201);
      const started = (await response.json()) as StartedSession;

      await expect
        .poll(
          async () =>
            (await apiGet<Failure[]>(page, "/api/admin/transcode/failures")).some(
              (failure) => failure.session_id === started.session_id,
            ),
          {
            timeout: 30_000,
            message: "A transcode that could not decode its input was not recorded.",
          },
        )
        .toBe(true);

      const failure = (await apiGet<Failure[]>(page, "/api/admin/transcode/failures")).find(
        (item) => item.session_id === started.session_id,
      );
      expect(failure?.media_id).toBe(broken.id);
      expect(failure?.exit_code).not.toBe(0);
      // A reason a human can read, derived from the exit status rather than
      // copied out of FFmpeg's stderr.
      expect(failure?.reason).toMatch(
        /^FFmpeg (exited with status \d+|was terminated by signal \d+)$/,
      );
      expect(failure?.reason.toLowerCase()).not.toContain("invalid data");
      expect(failure?.reason).not.toContain("/data/");

      // The failed session released its slot rather than holding it.
      expect((await adminSessions(page)).map((session) => session.id)).not.toContain(
        started.session_id,
      );
    });

    test("the software session cap is CPU cores divided by two", async ({ page }) => {
      // PRODUCT DEFECT D4. docs/pipelines/transcode.md L30 says
      // "`max_sw_sessions` - CPU cores divided by two".
      // `packages/media/pornarr_media/sessions.py:116-118` falls back to the
      // literal 1 when nothing is configured. On this sixteen-core host the
      // documented default is eight and the product enforces one, which is also
      // why the per-user cap of two can never be the binding constraint here.
      const cores = Number.parseInt(
        execFileSync("docker", [
          "compose",
          "-p",
          process.env.E2E_COMPOSE_PROJECT ?? "pornarr_gauntlet",
          "exec",
          "-T",
          "api",
          "python",
          "-c",
          "import os; print(len(os.sched_getaffinity(0)))",
        ])
          .toString()
          .trim(),
        10,
      );
      expect(cores).toBeGreaterThan(1);

      expect(
        (await limits(page)).effective_software,
        `The API container sees ${cores} cores, so the documented software cap is ${Math.floor(cores / 2)}.`,
      ).toBe(Math.floor(cores / 2));
    });

    test("a configured session cap is the one that is enforced", async ({ page }) => {
      // PRODUCT DEFECT D5. transcode.md L25 says the limits are "overridable in
      // configuration", `RuntimeSettings` persists all three
      // (packages/db/pornarr_db/settings.py:14-18, 108-110) and
      // `/api/admin/settings` accepts and echoes them - but every enforcement
      // path reads `request.app.state.settings`, the process environment, and
      // never `get_runtime_settings` (routers/transcode.py:283, 340-349). An
      // override is therefore stored, shown back to the operator, and ignored.
      const before = await apiGet<{ transcode_max_sw_sessions: number | null }>(
        page,
        "/api/admin/settings",
      );
      const asked = 4;
      await apiPatch(page, "/api/admin/settings", { transcode_max_sw_sessions: asked });
      try {
        // Stored and echoed - that much works. Polled rather than read once:
        // one run of this test saw the value it had just written come back as
        // the old one, which is recorded UNVERIFIED in BUILD.md because it has
        // not been reproduced since. This is the precondition, not the subject,
        // and it must not decide the verdict below.
        await expect
          .poll(
            async () =>
              (
                await apiGet<{ transcode_max_sw_sessions: number | null }>(
                  page,
                  "/api/admin/settings",
                )
              ).transcode_max_sw_sessions,
            {
              timeout: 10_000,
              message: "A session cap written through /api/admin/settings is not stored at all.",
            },
          )
          .toBe(asked);

        const state = await limits(page);
        expect(
          { configured: state.configured_software, effective: state.effective_software },
          "A configured software session cap is not what the transcode limit endpoint enforces.",
        ).toEqual({ configured: asked, effective: asked });
      } finally {
        await apiPatch(page, "/api/admin/settings", {
          transcode_max_sw_sessions: before.transcode_max_sw_sessions,
        });
      }
    });
  });

  test.describe("the nightly cleanup", () => {
    test("removes an aged orphan directory and leaves a live session alone", async ({ page }) => {
      test.skip(!canDriveStackJobs(), NO_JOBS);
      const media = await seedStreamable(page, PLAYABLE_TITLE, PLAYABLE_SECONDS);
      test.skip(media === null, NO_FIXTURE);
      if (media === null) return;

      const orphanId = randomUUID();
      const orphan = segmentDirectory(orphanId);
      mkdirSync(orphan, { recursive: true });
      writeFileSync(join(orphan, "segment_00000.ts"), "left behind by a session nobody holds");
      // Older than `transcode_cleanup_min_age_seconds`, because a directory a
      // running transcode wrote a second ago must survive.
      const aged = new Date(Date.now() - (CLEANUP_MIN_AGE_SECONDS + 600) * 1000);
      utimesSync(join(orphan, "segment_00000.ts"), aged, aged);
      utimesSync(orphan, aged, aged);

      const young = segmentDirectory(randomUUID());
      mkdirSync(young, { recursive: true });
      writeFileSync(join(young, "segment_00000.ts"), "written moments ago");

      const started = await startSession(page, media.id);
      const live = segmentDirectory(started.session_id);
      try {
        await expect.poll(() => existsSync(live), { timeout: 15_000 }).toBe(true);

        expect(
          enqueueStackJob("cleanup_transcodes", [], "pornarr:default", { once: false }),
          "The nightly cleanup job could not be put on the default queue.",
        ).toBe(true);

        await expect
          .poll(() => existsSync(orphan), {
            timeout: 60_000,
            message:
              "The nightly job left an aged orphan directory behind. See " +
              "apps/worker/pornarr_worker/cleanup.py:75-83.",
          })
          .toBe(false);

        // The negative space, which is the half that actually matters: a live
        // session's segments and a directory younger than the minimum age both
        // survive the same run.
        expect(existsSync(live), "The nightly job deleted a live session's segments.").toBe(true);
        expect(
          existsSync(young),
          "The nightly job deleted a directory younger than the minimum age.",
        ).toBe(true);
        expect((await adminSessions(page)).map((session) => session.id)).toContain(
          started.session_id,
        );
      } finally {
        rmSync(young, { recursive: true, force: true });
        rmSync(orphan, { recursive: true, force: true });
        await releaseTranscodeSessions(page);
      }
    });
  });
});

/**
 * A second signed-in account, so the ownership boundary is exercised between
 * two users rather than only between a user and nobody.
 *
 * Created the way the product creates one - an administrator issues an invite,
 * the invitee redeems it - and reused across runs, so a repeated run adds a
 * session rather than another account. Returns null when the invite flow is not
 * available, and the caller says so in its skip.
 */
const VIEWER_USERNAME = "piece08-viewer";
const VIEWER_PASSWORD = "Piece08-Viewer-2026!";

type ViewerClient = {
  readonly get: (path: string) => Promise<APIResponse>;
  readonly post: (path: string) => Promise<APIResponse>;
  readonly delete: (path: string) => Promise<APIResponse>;
  readonly close: () => Promise<void>;
};

async function signedInViewer(page: Page, browser: Browser): Promise<ViewerClient | null> {
  const context = await browser.newContext({ baseURL: test.info().project.use.baseURL });
  const credentials = { username: VIEWER_USERNAME, password: VIEWER_PASSWORD };

  let session = await context.request.post("/api/auth/login", { data: credentials });
  if (!session.ok()) {
    const invite = await apiPostRaw(page, "/api/admin/invites", {
      valid_days: 1,
      note: "piece 08 ownership boundary",
    });
    if (invite.status() !== 201) {
      await context.close();
      return null;
    }
    const { token } = (await invite.json()) as { token: string };
    // Invite redemption is exempt from the CSRF check, because the redeemer has
    // no session to bind a token to yet.
    const redeemed = await context.request.post(`/api/invites/${token}/redeem`, {
      data: credentials,
    });
    if (!redeemed.ok()) {
      await context.close();
      return null;
    }
    session = await context.request.post("/api/auth/login", { data: credentials });
  }
  if (!session.ok()) {
    await context.close();
    return null;
  }

  const csrf = async (): Promise<Record<string, string>> => {
    const cookie = (await context.cookies()).find((item) => item.name === "pornarr_csrf");
    return cookie === undefined ? {} : { "X-CSRF-Token": cookie.value };
  };
  return {
    get: (path) => context.request.get(path),
    post: async (path) => context.request.post(path, { headers: await csrf(), data: {} }),
    delete: async (path) => context.request.delete(path, { headers: await csrf() }),
    close: () => context.close(),
  };
}

/**
 * A title with real technical metadata, seeded through the import pipeline.
 *
 * The import job is the only writer of `MediaFile.codecs` anywhere in the
 * product - a root-folder scan records path, size and mtime and never probes -
 * so the five properties `docs/pipelines/transcode.md` L5-7 says the decision
 * compares only exist for a title a completed download produced. The fixture is
 * staged in the watched download tree exactly as `docs/pipelines/import.md`
 * describes and the library is polled for the title the pipeline chose; nothing
 * here writes to the database.
 *
 * The name is stable, so a second run finds the row the first run created and
 * stages nothing.
 */
async function seedProbedMedia(page: Page): Promise<MediaDetail | null> {
  const existing = await libraryItemByTitle(page, PROBED_TITLE);
  if (existing !== null) {
    const detail = await apiGet<MediaDetail>(page, `/api/media/${existing.id}`);
    if (detail.codecs !== null) return detail;
  }
  if (!canPlaceCompletedDownloads()) return null;

  test.setTimeout(Math.max(test.info().timeout, IMPORT_TIMEOUT_MILLISECONDS + 120_000));
  writeCompletedDownload(
    importStaging(),
    `${PROBED_STUDIO} - ${PROBED_TITLE} (2026-03-04) 1080p WEB-DL x264`,
  );
  let found: { id: string } | null = null;
  await expect
    .poll(
      async () => {
        found = await libraryItemByTitle(page, PROBED_TITLE);
        return found !== null;
      },
      {
        timeout: IMPORT_TIMEOUT_MILLISECONDS,
        intervals: [2_000],
        message: `The import pipeline produced no library title "${PROBED_TITLE}". It either quarantined the fixture or failed; see docs/pipelines/import.md and the import_triggers table.`,
      },
    )
    .toBe(true);
  if (found === null) return null;
  return apiGet<MediaDetail>(page, `/api/media/${(found as { id: string }).id}`);
}

/** A file the scan will adopt and FFmpeg will refuse, for the failure record. */
async function seedUndecodable(page: Page): Promise<MediaDetail | null> {
  const root = streamableRoot();
  const folder = await enabledRootFolder(page);
  if (root === null || folder === null) return null;

  const target = join(root, `${BROKEN_TITLE}.mp4`);
  const existing = await libraryItemByTitle(page, BROKEN_TITLE);
  if (existing === null || !existsSync(target)) {
    writeFileSync(target, "This is not a video file. FFmpeg has to say so rather than crash.\n");
    await scanUntilAdopted(page, folder.id, folder.path, BROKEN_TITLE, target);
  }
  const item = await libraryItemByTitle(page, BROKEN_TITLE);
  return item === null ? null : apiGet<MediaDetail>(page, `/api/media/${item.id}`);
}
