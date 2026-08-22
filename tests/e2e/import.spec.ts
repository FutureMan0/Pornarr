/**
 * The import pipeline, executed against the live stack.
 *
 * `docs/pipelines/import.md` describes eleven steps and three properties that
 * hold across them: the pipeline is idempotent, an upgrade never changes
 * `media.id`, and a perceptual match is advisory. This file runs real files
 * through the real workers - real ffprobe, real ffmpeg, the real `/data` tree -
 * and asserts what ended up on disk and in the database, not what an endpoint
 * said while it was happening.
 *
 * What it deliberately does not do is stop at the library count. Every import
 * here is followed to its file: the inode, the link count, the exact path each
 * token produced, and the reason the pipeline recorded when it stopped early.
 *
 * The last three tests are marked `test.fail()`. They assert documented
 * behaviour the product does not have; they execute, they fail, and Playwright
 * records them as expected failures, so the day one of them starts passing the
 * run turns red and somebody has to delete the marker. Each is also a row in
 * `.gauntlet/pieces/05-import/HOLES.md`.
 *
 * The stack is shared and the library is not emptied between runs, so this file
 * removes everything it created - library rows, the hardlinks under them, its
 * quarantine items and its import triggers - when it is done, and removes what
 * an interrupted earlier run left behind before it starts. Nothing it asserts
 * depends on where its rows sit in the library ordering.
 */
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  renameSync,
  rmSync,
  statSync,
} from "node:fs";
import { join } from "node:path";
import { type Page, expect, test } from "@playwright/test";
import {
  apiGet,
  apiPost,
  apiPut,
  canDriveStackJobs,
  canPlaceCompletedDownloads,
  canReadStackDatabase,
  collectEvents,
  collectedEvents,
  databaseAttempt,
  databaseScalar,
  downloadsRoot,
  enabledRootFolder,
  enqueueStackJob,
  hostDataRoot,
  hostPathFor,
  importTriggerRow,
  loginAsAdmin,
  seedLibraryMedia,
  stopCollectingEvents,
  usenetRoot,
  writeCompletedDownload,
  writeSizedFile,
  writeUndecodableDownload,
} from "./helpers";

/** Intake waits a second for the file to settle; the watcher sweeps every 30s. */
const IMPORT_TIMEOUT = 150_000;
/** `IMPORT_QUEUE` / `TRANSCODE_QUEUE` in `packages/shared/pornarr_shared/jobs.py`. */
const IMPORT_QUEUE = "pornarr:import";
const TRANSCODE_QUEUE = "pornarr:transcode";
/** Every fixture in this file states it, so the library can be queried for them. */
/** `FilterProfileResponse` in apps/api/pornarr_api/routers/admin_filters.py. */
type FilterProfile = {
  readonly id: string;
  readonly scope: string;
  readonly rules: { readonly id: string; readonly kind: string }[];
};

const STUDIO = "Gauntlet Studio";
/** `import_media` in `apps/worker/pornarr_worker/jobs/import_media.py:171`. */
const IMPORT_MEDIA_JOB = "import_media";
/** `import_download` in `apps/worker/pornarr_worker/jobs/import_trigger.py:194`. */
const IMPORT_DOWNLOAD_JOB = "import_download";

const NO_DOWNLOAD_VOLUME =
  "The suite cannot reach the stack's download volume, or ffmpeg is missing, so no completed download can be staged.";
const NO_DATABASE =
  "The compose project is not reachable from the test runner, so the reason the pipeline recorded cannot be read.";
const NO_JOBS =
  "The compose project is not reachable from the test runner, so a job the product never enqueues cannot be run.";

/**
 * One marker per run. The import trigger table is keyed by source path and the
 * library is shared with every other spec, so each fixture has to be findable
 * and has to be one the pipeline has not already seen.
 */
const RUN = `g${Date.now().toString(36)}`;
/**
 * Resolved inside tests and hooks, never at module load: `hostDataRoot` reads
 * the Playwright config through `test.info()`, which does not exist while the
 * file is still being imported.
 */
function staging(): string {
  return join(downloadsRoot(), "gauntlet-import", RUN);
}

/**
 * Somewhere the download watcher does not look.
 *
 * A replacement staged inside `staging()` is a completed download like any
 * other: the watcher stages it, the pipeline imports it, and
 * `_fuzzy_duplicate` correctly recognises it as an upgrade of the title it
 * matches - so the automatic upgrade runs beside the one this test asks for
 * and `media_file_history` gains two rows for one replacement. The automatic
 * path is the product working as intended and has its own case; a claim about
 * one explicit upgrade has to be about one upgrade.
 */
function unwatched(): string {
  return join(hostDataRoot(), "gauntlet-upgrades", RUN);
}

function usenetStaging(): string {
  return join(usenetRoot(), "gauntlet-import", RUN);
}

type MediaDetail = {
  readonly id: string;
  readonly title: string;
  readonly studio: string | null;
  readonly release_date: string | null;
  readonly confidence: number | null;
  readonly tags: { readonly name: string }[];
  readonly path: string;
  readonly size: number;
  readonly codecs: { container?: string; video?: Record<string, unknown> } | null;
  readonly resolution: string | null;
  readonly bitrate: number | null;
  readonly duration_seconds: number | null;
};

type QuarantineItem = {
  readonly id: string;
  readonly original_path: string;
  readonly quarantine_path: string;
  readonly reasons: { code?: string; detail?: string }[];
};

type Notification = {
  readonly id: string;
  readonly kind: string;
  readonly payload: Record<string, unknown>;
};

/** The container path for a file the test wrote on the host side of the mount. */
function containerPath(hostPath: string): string {
  const root = hostPathFor("/data");
  if (root === null || !hostPath.startsWith(root)) {
    throw new Error(`${hostPath} is not inside the stack's data volume.`);
  }
  return `/data${hostPath.slice(root.length)}`;
}

function quote(value: string): string {
  return value.replaceAll("'", "''");
}

/**
 * Every library row this file can produce, and nothing else.
 *
 * The first arm is an import: the filename tier of the cascade reads
 * `Gauntlet Studio` out of every fixture name, so an imported row carries it as
 * its studio. The second and third are the same files adopted a second time by
 * `scan_root_folder`, which walks the whole of `/data` including the completed
 * download tree (BUILD.md defect 3) and keeps the raw file stem as the title.
 * Counting and deleting through this predicate is what keeps the spec's
 * footprint at zero and its assertions independent of what the other four
 * agents are doing to the same library.
 */
const OWN_MEDIA =
  "(studio = 'Gauntlet Studio' or title like 'Gauntlet Studio - %' " +
  "or title similar to '(tiny|sample|archive|notes|writing)-g[0-9a-z]+')";

/** How many library rows this spec is currently responsible for. */
async function ownMediaCount(): Promise<string | null> {
  return databaseScalar(`select count(*) from media where ${OWN_MEDIA}`);
}

/** The files behind those rows, as the containers name them. */
function ownMediaPaths(): string[] {
  const rows = databaseScalar(
    `select f.path from media_files f join media m on m.id = f.media_id where ${OWN_MEDIA}`,
  );
  return (rows ?? "").split("\n").filter((path) => path.startsWith("/data/"));
}

/**
 * Undo everything this file has ever put into the shared stack.
 *
 * There is no route that deletes a library item, so the rows go through the
 * database - the one write this suite makes, and it only ever removes what this
 * spec itself created. Runs before the first test as well as after the last, so
 * an interrupted run does not leave its rows for the next one to trip over.
 */
function purgeOwnState(): void {
  if (!canReadStackDatabase()) return;
  for (const path of ownMediaPaths()) {
    const host = hostPathFor(path);
    if (host !== null) rmSync(host, { force: true });
  }
  const quarantined = (
    databaseScalar(
      "select quarantine_path from quarantine_items where original_path like '%/gauntlet-import/%'",
    ) ?? ""
  )
    .split("\n")
    .filter((path) => path.startsWith("/data/"));
  for (const path of quarantined) {
    const host = hostPathFor(path);
    if (host !== null) rmSync(host, { force: true });
  }
  // The artwork the import handed to the transcode queue is keyed by media id.
  for (const id of (databaseScalar(`select id from media where ${OWN_MEDIA}`) ?? "")
    .split("\n")
    .filter((id) => /^[0-9a-f-]{36}$/.test(id))) {
    const artwork = hostPathFor(`/data/thumbnails/${id}`);
    if (artwork !== null) rmSync(artwork, { recursive: true, force: true });
  }
  databaseScalar(`delete from media where ${OWN_MEDIA}`);
  databaseScalar("delete from quarantine_items where original_path like '%/gauntlet-import/%'");
  databaseScalar("delete from import_triggers where source_path like '%/gauntlet-import/%'");
  // The token directories the placement built, which are this spec's alone.
  const root = databaseScalar(
    "select path from root_folders where enabled order by created_at, id",
  );
  const branch = hostPathFor(`${(root ?? "/data").split("\n")[0]}/${STUDIO}`);
  if (branch !== null) rmSync(branch, { recursive: true, force: true });
}

/**
 * The spine import, recorded by the test that makes it.
 *
 * Two later tests need an imported item to look at and must not spend a
 * hundred and fifty seconds polling the library for one that a failed
 * predecessor never created - a timeout that a `test.fail()` would count as its
 * expected failure and hide the absence it exists to record.
 */
let spine: MediaDetail | null = null;

/** This run's fixtures only. The library is shared, and it is not small. */
async function ownTitles(page: Page): Promise<{ id: string; title: string }[]> {
  const page1 = await apiGet<{ items: { id: string; title: string }[] }>(
    page,
    `/api/library?limit=100&offset=0&studio=${encodeURIComponent(STUDIO)}`,
  );
  return page1.items.filter((item) => item.title.includes(RUN));
}

/** Wait for the library item the pipeline should have produced, and open it. */
async function importedMedia(page: Page, title: string): Promise<MediaDetail> {
  let found: { id: string } | undefined;
  await expect
    .poll(
      async () => {
        found = (await ownTitles(page)).find((item) => item.title === title);
        return found !== undefined;
      },
      {
        timeout: IMPORT_TIMEOUT,
        intervals: [2_000],
        message: `No library item titled "${title}" appeared. The import pipeline either quarantined the file or failed; see docs/pipelines/import.md steps 2-11 and the import_triggers table.`,
      },
    )
    .toBe(true);
  if (found === undefined) throw new Error("unreachable");
  return apiGet<MediaDetail>(page, `/api/media/${found.id}`);
}

function libraryFile(media: MediaDetail): { host: string; inode: number; links: number } {
  const host = hostPathFor(media.path);
  if (host === null) throw new Error(`${media.path} is not inside the stack's data volume.`);
  expect(existsSync(host), `${media.path} is in the database but not on disk.`).toBe(true);
  const stat = statSync(host);
  return { host, inode: stat.ino, links: stat.nlink };
}

test.describe.configure({ mode: "serial" });

test.describe("import pipeline", () => {
  test.beforeAll(() => {
    rmSync(join(downloadsRoot(), "gauntlet-import"), { recursive: true, force: true });
    rmSync(join(usenetRoot(), "gauntlet-import"), { recursive: true, force: true });
    rmSync(join(hostDataRoot(), "gauntlet-upgrades"), { recursive: true, force: true });
    purgeOwnState();
    mkdirSync(staging(), { recursive: true });
  });

  test.afterAll(() => {
    rmSync(join(downloadsRoot(), "gauntlet-import"), { recursive: true, force: true });
    rmSync(join(usenetRoot(), "gauntlet-import"), { recursive: true, force: true });
    rmSync(join(hostDataRoot(), "gauntlet-upgrades"), { recursive: true, force: true });
    // Everything this run put in the library goes too. The stack is shared:
    // rows left behind are the next spec's flake, and the scanner adopting this
    // spec's own downloads a second time doubles the footprint of every run.
    purgeOwnState();
  });

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("a completed download is hardlinked into the documented layout and announced", async ({
    page,
  }) => {
    test.skip(!canPlaceCompletedDownloads(), NO_DOWNLOAD_VOLUME);
    test.setTimeout(IMPORT_TIMEOUT + 90_000);
    await collectEvents(page, ["import.started", "import.completed", "media.available"]);

    const title = `Import Spine ${RUN}`;
    const source = writeCompletedDownload(
      staging(),
      `${STUDIO} - ${title} (2026-03-04) 1080p WEB-DL x264`,
    );
    const sourceInode = statSync(source).ino;

    const media = await importedMedia(page, title);
    spine = media;

    // Step 6. The canonical title carries neither the release group nor the
    // resolution, source and codec tokens, nor the date the directory uses.
    expect(media.title).toBe(title);
    expect(media.studio).toBe(STUDIO);
    expect(media.release_date).toBe("2026-03-04");
    expect(media.title).not.toMatch(/1080p|WEB-DL|x264|2026-03-04/i);

    // Step 9. The documented token layout: one directory per token, the file
    // named after the title again. The root is the operator's enabled root
    // folder, which is what `_library_root` in import_media.py places into.
    const folder = await enabledRootFolder(page);
    expect(folder).not.toBeNull();
    expect(media.path).toBe(`${folder?.path}/${STUDIO}/2026/${title}/1080p/${title}.mp4`);

    // ...and it is a hardlink, not a copy: one inode, two names.
    const file = libraryFile(media);
    expect(file.inode).toBe(sourceInode);
    expect(file.links).toBeGreaterThanOrEqual(2);
    expect(media.size).toBe(statSync(source).size);

    // Step 4. ffprobe really ran, and every field the document names is there.
    expect(media.resolution).toBe("1920x1080");
    expect(media.duration_seconds ?? 0).toBeGreaterThan(19);
    expect(media.duration_seconds ?? 0).toBeLessThan(21);
    expect(media.bitrate ?? 0).toBeGreaterThan(0);
    expect(media.codecs?.container).toContain("mp4");
    expect(media.codecs?.video).toMatchObject({ codec: "h264" });

    // Step 5. With no metadata provider configured the cascade falls all the
    // way through to the filename tier, whose documented confidence is 0.30.
    expect(media.confidence).toBeCloseTo(0.3, 5);

    // Step 10. A poster and preview frames, served and on disk. Artwork is
    // handed to the transcode queue rather than made inline, so this waits for
    // that worker instead of assuming it has already run.
    await expect
      .poll(async () => (await page.request.get(`/api/media/${media.id}/poster`)).status(), {
        timeout: 120_000,
        intervals: [2_000],
        message: "The import never produced a poster. See ARTWORK_JOB_NAME in import_media.py.",
      })
      .toBe(200);
    const poster = await page.request.get(`/api/media/${media.id}/poster`);
    expect(poster.headers()["content-type"]).toContain("image");
    expect((await poster.body()).byteLength).toBeGreaterThan(1_000);
    const artwork = hostPathFor(`/data/thumbnails/${media.id}`);
    expect(artwork).not.toBeNull();
    expect(
      readdirSync(artwork ?? "").filter((name) => name.startsWith("frame-")).length,
    ).toBeGreaterThanOrEqual(1);

    // Step 11. The three documented events, carrying the media the run made.
    await expect
      .poll(async () => (await collectedEvents(page)).map((event) => event.type), {
        timeout: 30_000,
        message: "The import published no events on GET /api/events.",
      })
      .toEqual(expect.arrayContaining(["import.started", "import.completed", "media.available"]));
    const events = await collectedEvents(page);
    expect(
      events
        .filter((event) => event.type === "import.completed")
        .map((event) => JSON.parse(event.data) as { status: string; media_id: string | null }),
    ).toContainEqual(expect.objectContaining({ status: "imported", media_id: media.id }));
    expect(
      events
        .filter((event) => event.type === "media.available")
        .map((event) => (JSON.parse(event.data) as { media_id: string }).media_id),
    ).toContain(media.id);
    await stopCollectingEvents(page);
  });

  test("running the import twice on the same file changes nothing", async ({ page }) => {
    test.skip(!canPlaceCompletedDownloads(), NO_DOWNLOAD_VOLUME);
    test.skip(!canReadStackDatabase() || !canDriveStackJobs(), NO_JOBS);
    test.setTimeout(IMPORT_TIMEOUT + 180_000);

    const title = `Import Spine ${RUN}`;
    const media = spine ?? (await importedMedia(page, title));
    const file = libraryFile(media);
    const before = statSync(file.host);
    const mediaCount = await ownMediaCount();
    const rowCount = await databaseScalar(
      `select count(*) from media_files where path = '${quote(media.path)}'`,
    );
    const source = join(staging(), `${STUDIO} - ${title} (2026-03-04) 1080p WEB-DL x264.mp4`);
    const triggerId = databaseScalar(
      `select id from import_triggers where source_path = '${quote(containerPath(source))}'`,
    );
    expect(triggerId, "The spine import left no trigger to run a second time.").toMatch(
      /^[0-9a-f-]{36}$/,
    );

    // The stimulus. Nothing in the product ever offers an imported file to the
    // pipeline again - `dispatch_committed_triggers`
    // (import_trigger.py:258-277) only ever dispatches `pending` and `ready` -
    // so waiting is not a second run, it is nothing happening. This puts the
    // product's own job back on the product's own queue for the same trigger,
    // with `once: false` so ARQ cannot answer it out of the result of the first
    // one, and the real import worker executes steps 2 to 11 a second time on
    // the same file.
    await collectEvents(page, ["import.started", "import.completed", "media.available"]);
    expect(
      enqueueStackJob(IMPORT_DOWNLOAD_JOB, [triggerId ?? ""], IMPORT_QUEUE, { once: false }),
    ).toBe(true);
    expect(
      enqueueStackJob(IMPORT_MEDIA_JOB, [triggerId ?? ""], IMPORT_QUEUE, { once: false }),
    ).toBe(true);

    // The second run really happened: it announced itself, and it announced the
    // reason it stopped. `import_ready_trigger` (import_media.py:89-90) refuses
    // a trigger that is not `ready` and reports the status it found, so the
    // second `import.completed` says `imported` and carries no media at all -
    // no second record, and therefore no second artwork job either.
    await expect
      .poll(
        async () =>
          (await collectedEvents(page))
            .filter((event) => event.type === "import.completed")
            .map((event) => JSON.parse(event.data) as { trigger_id: string })
            .some((payload) => payload.trigger_id === triggerId),
        {
          timeout: 120_000,
          intervals: [1_000],
          message:
            "The second run of import_media on the already-imported trigger published no " +
            "import.completed, so the pipeline was never actually run twice.",
        },
      )
      .toBe(true);
    const second = (await collectedEvents(page))
      .filter((event) => event.type === "import.completed")
      .map((event) => JSON.parse(event.data) as Record<string, unknown>)
      .filter((payload) => payload.trigger_id === triggerId);
    expect(second).toContainEqual(
      expect.objectContaining({ trigger_id: triggerId, status: "imported", media_id: null }),
    );
    expect(
      (await collectedEvents(page))
        .filter((event) => event.type === "import.started")
        .map((event) => (JSON.parse(event.data) as { trigger_id: string }).trigger_id),
    ).toContain(triggerId);
    // No second publication either: `media.available` is only emitted for a
    // media the run produced.
    expect(
      (await collectedEvents(page))
        .filter((event) => event.type === "media.available")
        .map((event) => (JSON.parse(event.data) as { media_id: string }).media_id),
    ).not.toContain(media.id);
    await stopCollectingEvents(page);

    // ...and nothing changed. The record, the row, the file, its inode, its
    // modification time, its size and its link count.
    const after = await apiGet<MediaDetail>(page, `/api/media/${media.id}`);
    const now = statSync(file.host);
    expect(after.path).toBe(media.path);
    expect(after.size).toBe(media.size);
    expect(now.ino).toBe(file.inode);
    expect(now.mtimeMs).toBe(before.mtimeMs);
    expect(now.size).toBe(before.size);
    expect(now.nlink).toBe(before.nlink);
    expect(await ownMediaCount()).toBe(mediaCount);
    expect(
      await databaseScalar(`select count(*) from media_files where path = '${quote(media.path)}'`),
    ).toBe(rowCount);
    expect(importTriggerRow(containerPath(source))?.status).toBe("imported");
    // Nothing was placed beside it either: a second placement would have
    // reserved "<title> (1).mp4" in the same directory.
    const directory = hostPathFor(media.path.slice(0, media.path.lastIndexOf("/")));
    expect(readdirSync(directory ?? "")).toEqual([`${title}.mp4`]);
  });

  test("a byte-identical file at a different path is turned away as a duplicate", async ({
    page,
  }) => {
    test.skip(!canPlaceCompletedDownloads(), NO_DOWNLOAD_VOLUME);
    test.skip(!canReadStackDatabase(), NO_DATABASE);
    test.setTimeout(IMPORT_TIMEOUT + 90_000);

    // Step 3, first sentence: "An exact oshash match is a certain duplicate."
    // A copy rather than a second encode, so "the same bytes" is a fact and not
    // a hope, and at a path the trigger table has never seen, so the pipeline
    // runs from intake rather than short-circuiting on the trigger.
    const title = `Import Spine ${RUN}`;
    const media = spine ?? (await importedMedia(page, title));
    const mediaCount = await ownMediaCount();
    const twin = join(
      staging(),
      `${STUDIO} - Import Twin ${RUN} (2026-03-04) 2160p WEB-DL x264.mp4`,
    );
    // Built out of sight and renamed in, for the reason `placeAtomically` in
    // the harness exists: a file being written inside a watched tree is offered
    // to intake half-finished and rejected as `too_small` for good.
    const workshop = hostPathFor("/data/gauntlet-fixtures") ?? staging();
    mkdirSync(workshop, { recursive: true });
    const building = join(workshop, `import-twin-${RUN}.tmpfixture`);
    copyFileSync(
      join(staging(), `${STUDIO} - ${title} (2026-03-04) 1080p WEB-DL x264.mp4`),
      building,
    );
    renameSync(building, twin);

    await expect
      .poll(() => importTriggerRow(containerPath(twin))?.status ?? "unstaged", {
        timeout: IMPORT_TIMEOUT,
        intervals: [2_000],
        message: "The byte-identical second file never reached a terminal import status.",
      })
      .toBe("duplicate");
    const twinRow = importTriggerRow(containerPath(twin));
    expect(twinRow?.errorCode).toBe("duplicate");
    expect(twinRow?.errorDetail).toContain("same fingerprint");
    expect((await ownTitles(page)).filter((item) => item.title === title)).toHaveLength(1);
    expect(await ownMediaCount()).toBe(mediaCount);
    // Never placed: the copy still has exactly one name, and the library
    // directory still holds exactly the one file the first import put there.
    expect(statSync(twin).nlink).toBe(1);
    const directory = hostPathFor(media.path.slice(0, media.path.lastIndexOf("/")));
    expect(readdirSync(directory ?? "")).toEqual([`${title}.mp4`]);
  });

  test("intake rejects unknown, incomplete and undersized files with a stated reason", async ({
    page,
  }) => {
    test.skip(!canPlaceCompletedDownloads(), NO_DOWNLOAD_VOLUME);
    test.skip(!canReadStackDatabase(), NO_DATABASE);
    test.setTimeout(IMPORT_TIMEOUT + 90_000);

    const large = 60 * 1024 ** 2;
    const candidates = [
      {
        path: writeSizedFile(staging(), `archive-${RUN}.rar`, large),
        status: "rejected",
        why: "archive",
      },
      {
        path: writeSizedFile(staging(), `notes-${RUN}.txt`, large),
        status: "rejected",
        why: "unsupported_extension",
      },
      {
        path: writeSizedFile(staging(), `tiny-${RUN}.mp4`, 1024 ** 2),
        status: "rejected",
        why: "too_small",
      },
      {
        path: writeSizedFile(staging(), `sample-${RUN}.mp4`, large),
        status: "rejected",
        why: "sample",
      },
      // Still being written: retried rather than rejected, so the trigger stays
      // pending and carries the reason it is waiting.
      {
        path: writeSizedFile(staging(), `writing-${RUN}.part`, large),
        status: "pending",
        why: "writing",
      },
    ];
    const mediaCount = await ownMediaCount();
    const quarantined = (await apiGet<QuarantineItem[]>(page, "/api/admin/quarantine")).length;

    for (const candidate of candidates) {
      await expect
        .poll(() => importTriggerRow(containerPath(candidate.path))?.errorCode ?? null, {
          timeout: IMPORT_TIMEOUT,
          intervals: [2_000],
          message: `${candidate.path} never got an intake decision. See import_intake.py.`,
        })
        .toBe(candidate.why);
      const row = importTriggerRow(containerPath(candidate.path));
      expect(row?.status, `${candidate.path} reached the wrong status`).toBe(candidate.status);
      expect(row?.errorDetail ?? "").not.toBe("");
      // Rejected before anything expensive happened: nothing linked it anywhere.
      expect(statSync(candidate.path).nlink).toBe(1);
    }

    expect(await ownMediaCount()).toBe(mediaCount);
    expect((await apiGet<QuarantineItem[]>(page, "/api/admin/quarantine")).length).toBe(
      quarantined,
    );
  });

  test("a file ffprobe cannot read is quarantined instead of placed", async ({ page }) => {
    test.skip(!canPlaceCompletedDownloads(), NO_DOWNLOAD_VOLUME);
    test.setTimeout(IMPORT_TIMEOUT + 60_000);

    const title = `Unreadable ${RUN}`;
    const source = writeUndecodableDownload(staging(), `${STUDIO} - ${title} (2026-03-04) 1080p`);
    const original = containerPath(source);

    await expect
      .poll(
        async () =>
          (await apiGet<QuarantineItem[]>(page, "/api/admin/quarantine")).some(
            (item) => item.original_path === original,
          ),
        {
          timeout: IMPORT_TIMEOUT,
          intervals: [2_000],
          message:
            "An unprobeable file was neither quarantined nor rejected. docs/pipelines/import.md " +
            "step 8 says a quarantined outcome stops the import before placement.",
        },
      )
      .toBe(true);

    const item = (await apiGet<QuarantineItem[]>(page, "/api/admin/quarantine")).find(
      (candidate) => candidate.original_path === original,
    );
    expect(item).toBeDefined();
    // The reason, not just the verdict.
    expect(item?.reasons.map((reason) => reason.code)).toContain("unexpected_file_type");
    // Under /data/quarantine, and nowhere near the library.
    expect(item?.quarantine_path).toMatch(/^\/data\/quarantine\//);
    expect(existsSync(hostPathFor(item?.quarantine_path ?? "") ?? "")).toBe(true);
    expect((await ownTitles(page)).some((media) => media.title === title)).toBe(false);
  });

  test("a usenet download is moved rather than hardlinked", async ({ page }) => {
    test.skip(!canPlaceCompletedDownloads(), NO_DOWNLOAD_VOLUME);
    test.setTimeout(IMPORT_TIMEOUT + 90_000);

    const title = `Usenet Move ${RUN}`;
    const source = writeCompletedDownload(
      usenetStaging(),
      `${STUDIO} - ${title} (2026-03-04) 1080p`,
      { seconds: 14 },
    );

    const media = await importedMedia(page, title);
    const file = libraryFile(media);
    // Nothing seeds a usenet download, so the source is gone and the library
    // file is the only name its inode has.
    expect(existsSync(source)).toBe(false);
    expect(file.links).toBe(1);
    expect(media.path).toContain(`/${STUDIO}/2026/${title}/1080p/${title}.mp4`);
  });

  test("three downloads finishing together all import, with no coordination", async ({ page }) => {
    test.skip(!canPlaceCompletedDownloads(), NO_DOWNLOAD_VOLUME);
    test.setTimeout(IMPORT_TIMEOUT + 240_000);

    // Three different scenes, not three spellings of one. A month apart each:
    // same studio, same duration and a title that differs by a single word put
    // all three inside `detect_duplicate`'s thresholds, so the second and third
    // were correctly folded into the first as upgrades and never appeared under
    // their own names. What this case is about is three unrelated downloads
    // landing at once.
    const dates = ["2026-03-04", "2026-04-08", "2026-05-12"];
    const titles = ["Parallel One", "Parallel Two", "Parallel Three"].map(
      (name) => `${name} ${RUN}`,
    );
    for (const [index, title] of titles.entries()) {
      writeCompletedDownload(staging(), `${STUDIO} - ${title} (${dates[index]}) 1080p`, {
        seconds: 14,
        seed: index + 1,
      });
    }

    for (const title of titles) {
      const media = await importedMedia(page, title);
      expect(libraryFile(media).links).toBeGreaterThanOrEqual(2);
      expect(media.path).toContain(`/${title}/1080p/${title}.mp4`);
    }
  });

  test("perceptual matching records a candidate, notifies, and deletes nothing", async ({
    page,
  }) => {
    test.skip(!canPlaceCompletedDownloads(), NO_DOWNLOAD_VOLUME);
    test.skip(!canReadStackDatabase() || !canDriveStackJobs(), NO_JOBS);
    test.setTimeout(IMPORT_TIMEOUT + 300_000);

    // The same picture encoded twice at different quantisers: a different
    // oshash each, so both import, and one perceptual hash between them.
    const titles = [`Twin Alpha ${RUN}`, `Twin Beta ${RUN}`];
    writeCompletedDownload(staging(), `${STUDIO} - ${titles[0]} (2026-03-04) 1080p`, {
      seconds: 14,
      seed: 9,
      qp: 0,
    });
    writeCompletedDownload(staging(), `${STUDIO} - ${titles[1]} (2026-03-04) 1080p`, {
      seconds: 14,
      seed: 9,
      qp: 1,
    });

    const media = [await importedMedia(page, titles[0]), await importedMedia(page, titles[1])];
    expect(media[0].id).not.toBe(media[1].id);
    const files = media.map((item) => {
      const id = databaseScalar(`select id from media_files where path = '${quote(item.path)}'`);
      expect(id).toMatch(/^[0-9a-f-]{36}$/);
      return id ?? "";
    });

    for (const fileId of files) {
      expect(
        enqueueStackJob("generate_perceptual_hash_job", [fileId], TRANSCODE_QUEUE),
        "the perceptual-hash job could not be enqueued",
      ).toBe(true);
    }

    const pair = `'${files[0]}', '${files[1]}'`;
    await expect
      .poll(
        () =>
          databaseScalar(
            `select coalesce(min(hamming_distance), -1) from duplicate_candidates
             where media_file_id in (${pair}) and candidate_file_id in (${pair})`,
          ),
        {
          timeout: 240_000,
          intervals: [3_000],
          message:
            "Two visually identical files produced no duplicate candidate. " +
            "docs/pipelines/import.md says a Hamming distance of eight or less records one.",
        },
      )
      .not.toBe("-1");
    expect(
      Number(
        databaseScalar(
          `select min(hamming_distance) from duplicate_candidates
           where media_file_id in (${pair}) and candidate_file_id in (${pair})`,
        ),
      ),
    ).toBeLessThanOrEqual(8);

    // It raises an administrator notice...
    await expect
      .poll(
        async () =>
          (await apiGet<Notification[]>(page, "/api/notifications")).filter(
            (notice) =>
              notice.kind === "instance_notice" &&
              files.includes(String(notice.payload.media_file_id)),
          ).length,
        {
          timeout: 60_000,
          intervals: [2_000],
          message: "The perceptual match raised no administrator notice.",
        },
      )
      .toBeGreaterThan(0);

    // ...and it deletes nothing: both records, both files, both paths.
    for (const item of media) {
      const still = await apiGet<MediaDetail>(page, `/api/media/${item.id}`);
      expect(still.path).toBe(item.path);
      expect(existsSync(hostPathFor(still.path) ?? "")).toBe(true);
    }
  });

  test("an upgrade replaces the file and keeps media.id, its tags and its history", async ({
    page,
  }) => {
    test.skip(!canPlaceCompletedDownloads(), NO_DOWNLOAD_VOLUME);
    test.skip(!canReadStackDatabase() || !canDriveStackJobs(), NO_JOBS);
    test.setTimeout(IMPORT_TIMEOUT + 240_000);

    const title = `Upgrade Me ${RUN}`;
    writeCompletedDownload(staging(), `${STUDIO} - ${title} (2026-03-04) 720p`, {
      seconds: 14,
      seed: 4,
    });
    const before = await importedMedia(page, title);
    const previous = hostPathFor(before.path);
    expect(previous).not.toBeNull();

    // Something only the media record can carry, so its survival is visible.
    const marker = `upgrade-marker-${RUN}`;
    await apiPost(page, `/api/media/${before.id}/tags`, { name: marker });

    const folder = await enabledRootFolder(page);
    const replacement = writeCompletedDownload(
      unwatched(),
      `${STUDIO} - ${title} (2026-03-04) 2160p`,
      { seconds: 14, seed: 4, qp: 1 },
    );
    expect(
      enqueueStackJob(
        "upgrade_media_file_job",
        [before.id, containerPath(replacement), folder?.path ?? "/data", "2160p"],
        IMPORT_QUEUE,
      ),
    ).toBe(true);

    await expect
      .poll(async () => (await apiGet<MediaDetail>(page, `/api/media/${before.id}`)).path, {
        timeout: 180_000,
        intervals: [2_000],
        message: "The upgrade never activated a replacement file.",
      })
      .not.toBe(before.path);

    const after = await apiGet<MediaDetail>(page, `/api/media/${before.id}`);
    // The whole point of the claim: one media, one id, everything hanging off
    // it intact.
    expect(after.id).toBe(before.id);
    expect(after.title).toBe(before.title);
    expect(after.tags.map((tag) => tag.name)).toContain(marker);
    // The previous file is gone, and it went only after the new one verified.
    expect(existsSync(previous ?? "")).toBe(false);
    expect(existsSync(hostPathFor(after.path) ?? "")).toBe(true);
    expect(
      await databaseScalar(`select quality from media_files where path = '${quote(after.path)}'`),
    ).toBe("2160p");
    // Exactly one active row, and the replacement recorded against what it
    // replaced.
    expect(
      await databaseScalar(
        `select count(*) from media_files where media_id = '${before.id}' and is_active`,
      ),
    ).toBe("1");
    expect(
      await databaseScalar(
        `select count(*) from media_file_history h join media_files f
         on f.id = h.replacement_file_id where f.media_id = '${before.id}'`,
      ),
    ).toBe("1");
  });

  test("a scanned file is probed, and is not put through the import pipeline", async ({ page }) => {
    test.skip(!canReadStackDatabase(), NO_DATABASE);
    // docs/pipelines/import.md line 3 says the pipeline is triggered "by the
    // library scanner for files that are already on disk". Half of that is now
    // true and the half that is not is deliberate: `scan_root_folder` enqueues
    // `probe_media_file_job` for every file that has none, so a scanned title
    // carries the technical facts `decide_direct_play` needs - and nothing
    // else. No metadata cascade, so no studio and no confidence; no
    // fingerprint, so `detect_duplicate` cannot see it; no placement, so the
    // title is the file stem, tokens and all.
    //
    // This spec's own title, not the shared fixture: the claim is about what
    // the scanner leaves behind, and a fixture every other spec also uses gets
    // enriched the moment one of them asks it for something.
    const scanned = await seedLibraryMedia(page, `Gauntlet Studio - Scanned ${RUN}`);
    test.skip(scanned === null, NO_DOWNLOAD_VOLUME);
    if (scanned === null) return;

    // Probed, but by a job on the transcode queue rather than inline, so the
    // scan does not block behind ffmpeg on every file in a large library.
    await expect
      .poll(async () => (await apiGet<MediaDetail>(page, `/api/media/${scanned.id}`)).resolution, {
        timeout: 60_000,
        intervals: [2_000],
        message:
          "A scanned file was never probed. See `probe_media_file_job` in " +
          "apps/worker/pornarr_worker/jobs/scan.py.",
      })
      .not.toBeNull();

    const media = await apiGet<MediaDetail>(page, `/api/media/${scanned.id}`);
    expect(media.duration_seconds).not.toBeNull();
    expect(media.bitrate).not.toBeNull();

    // And nothing the pipeline would have added.
    expect(media.confidence).toBeNull();
    expect(media.studio).toBeNull();
    expect(media.title).toBe(scanned.title);
    expect(
      await databaseScalar(
        `select coalesce(oshash, 'none') from media_files where path = '${quote(media.path)}'`,
      ),
    ).toBe("none");
  });

  test("the live database, not the application, refuses a second active file", async () => {
    test.skip(!canReadStackDatabase(), NO_DATABASE);
    // ADR 0032:24 says the one-active-row rule "is enforced in the database";
    // ADR 0007 says PostgreSQL is the only supported database. A model-level
    // test on in-memory SQLite proves the declaration, not the deployment, so
    // this asks the deployed PostgreSQL to break it and reads the refusal.
    const mediaId =
      spine?.id ??
      databaseScalar(`select media_id from media_files where is_active
       and path like '%/${quote(STUDIO)}/%' limit 1`);
    expect(mediaId, "No imported media to test the constraint against.").toMatch(/^[0-9a-f-]{36}$/);

    const index = databaseScalar(
      `select indexdef from pg_indexes
       where tablename = 'media_files' and indexname = 'uq_media_files_active_media'`,
    );
    // The migrations really deployed a *partial* unique index: unique on the
    // media, and only over the active rows, which is what lets history pile up.
    expect(index ?? "").toContain("UNIQUE INDEX");
    expect(index ?? "").toContain("(media_id)");
    expect(index ?? "").toContain("WHERE is_active");

    const refusal = databaseAttempt(
      `insert into media_files (id, media_id, path, size, modified_at_ns, is_active)
       values (gen_random_uuid(), '${mediaId}', '/data/gauntlet-second-active-${RUN}', 1, 1, true)`,
    );
    expect(refusal ?? "").toContain("duplicate key value violates unique constraint");
    expect(refusal ?? "").toContain("uq_media_files_active_media");
    // The rejected statement took its own transaction down with it, so the row
    // it tried to write is not there and the media still has exactly one.
    expect(
      await databaseScalar(
        `select count(*) from media_files where path = '/data/gauntlet-second-active-${RUN}'`,
      ),
    ).toBe("0");
    expect(
      await databaseScalar(
        `select count(*) from media_files where media_id = '${mediaId}' and is_active`,
      ),
    ).toBe("1");
  });

  test("import produces a hover sprite and its VTT index", async ({ page }) => {
    // Not a poll for a fixture another test makes: a 150-second `importedMedia`
    // timeout is itself a failure. It uses the record the spine test published,
    // and says so when that test did not run.
    test.skip(
      spine === null,
      "The spine import did not run, so there is no imported item whose artwork could be missing a sprite.",
    );
    test.setTimeout(60_000);
    // Step 10 names four artefacts. The import used to enqueue only
    // `generate_artwork_job`, which makes the poster and the preview frames;
    // nothing enqueued `generate_preview_sprite_job`, so no import ever produced
    // a sprite or its VTT index and the library grid's hover preview had nothing
    // to show for anything this instance had imported.
    const id = spine?.id ?? "";
    await expect
      .poll(async () => (await page.request.get(`/api/media/${id}/sprite`)).status(), {
        timeout: 45_000,
        intervals: [2_000],
        message:
          "The import never produced a hover sprite. See docs/pipelines/import.md step 10 " +
          "and `generate_preview_sprite_job`.",
      })
      .toBe(200);
    expect(existsSync(hostPathFor(`/data/thumbnails/${id}/sprite.vtt`) ?? "")).toBe(true);
  });

  test("a fuzzy match on title, studio, date and duration is an upgrade candidate", async ({
    page,
  }) => {
    test.skip(!canPlaceCompletedDownloads(), NO_DOWNLOAD_VOLUME);
    test.setTimeout(IMPORT_TIMEOUT + 240_000);
    // Step 3's second half. `detect_duplicate` in pornarr_core/dedup.py
    // implements every threshold the document states; the import used to check
    // the exact oshash and nothing else, so two near-identical releases of one
    // scene became two unrelated library items.
    // One after the other, not both at once. `_fuzzy_duplicate` compares the
    // arriving release against what is already in the library, so the first
    // has to be in it before the second is staged. Written together, the two
    // imports run concurrently on the import worker and each finds a library
    // the other is not in yet - two items, correctly, for a question neither
    // was asked. An upgrade arriving after the release it upgrades is also
    // the only order this ever happens in outside a test.
    const near = `Fuzzy Twin ${RUN}`;
    writeCompletedDownload(staging(), `${STUDIO} - ${near} (2026-03-04) 1080p`, {
      seconds: 14,
      seed: 6,
    });
    const first = await importedMedia(page, near);

    writeCompletedDownload(staging(), `${STUDIO} - ${near}s (2026-03-05) 2160p`, {
      seconds: 14,
      seed: 6,
      qp: 1,
    });
    const second = await importedMedia(page, `${near}s`);
    expect(second.id).toBe(first.id);
  });

  test("every firing filter rule is written to the audit log", async ({ page }) => {
    test.skip(!canReadStackDatabase(), NO_DATABASE);
    test.skip(!canPlaceCompletedDownloads(), NO_DOWNLOAD_VOLUME);
    test.setTimeout(IMPORT_TIMEOUT + 60_000);
    // import.md L31. The rule is turned on through the operator's own route, a
    // download whose title the rule matches is staged, and the record the
    // pipeline leaves is read out of the database.
    //
    // `action = 'filter.matched'` exactly, never `like 'filter%'`: the profile
    // route writes `filter_profile.updated` when an administrator edits the
    // profile, and a prefix match would be satisfied by that -- a record that
    // somebody changed the configuration, standing in for a record that a rule
    // fired. Those are different claims and this is the second one.
    const term = `gauntlet-audit-${RUN}`;
    const before = Number(
      (await databaseScalar("select count(*) from audit_log where action = 'filter.matched'")) ??
        "0",
    );
    const profile = await apiGet<FilterProfile>(page, "/api/admin/filters/profile");
    const off = {
      rules: profile.rules.map((rule) => ({
        kind: rule.kind,
        pattern: "",
        action: "reject",
        enabled: false,
      })),
    };
    await apiPut<FilterProfile>(page, "/api/admin/filters/profile", {
      rules: profile.rules.map((rule) =>
        rule.kind === "term"
          ? { kind: rule.kind, pattern: term, action: "quarantine", enabled: true }
          : { kind: rule.kind, pattern: "", action: "reject", enabled: false },
      ),
    });
    const ruleId = (await apiGet<FilterProfile>(page, "/api/admin/filters/profile")).rules.find(
      (rule) => rule.kind === "term",
    )?.id;
    expect(ruleId, "The term rule is not in the profile that was just written.").toBeDefined();
    // The rule keeps its id when its pattern is rewritten, so records this rule
    // left on an earlier run carry the same target. Counted from here, or a
    // stale row would answer for one this run never produced.
    const beforeForRule = Number(
      (await databaseScalar(
        `select count(*) from audit_log where action = 'filter.matched' and target = '${quote(ruleId as string)}'`,
      )) ?? "0",
    );

    try {
      writeCompletedDownload(staging(), `${STUDIO} - ${term} (2026-05-05) 1080p`);
      await expect
        .poll(
          async () =>
            Number(
              (await databaseScalar(
                `select count(*) from audit_log where action = 'filter.matched' and target = '${quote(ruleId as string)}'`,
              )) ?? "0",
            ),
          {
            timeout: IMPORT_TIMEOUT,
            intervals: [2_000],
            message:
              "A content filter quarantined a download and left no audit record. " +
              "See docs/pipelines/import.md step 8.",
          },
        )
        .toBeGreaterThan(beforeForRule);

      // And it is the pipeline's own record, not a second copy of the
      // configuration change: no actor, and the automation source.
      expect(
        await databaseScalar(
          `select count(*) from audit_log where action = 'filter.matched' and target = '${quote(ruleId as string)}' and actor_id is null and source = 'automation'`,
        ),
      ).not.toBe("0");
      expect(
        Number(
          (await databaseScalar(
            "select count(*) from audit_log where action = 'filter.matched'",
          )) ?? "0",
        ),
      ).toBeGreaterThan(before);
    } finally {
      await apiPut<FilterProfile>(page, "/api/admin/filters/profile", off);
    }
  });
});
