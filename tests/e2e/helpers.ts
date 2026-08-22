/**
 * Shared browser and seeding helpers for the end-to-end suite.
 *
 * Two rules hold everywhere in here. Every screen the suite visits is checked
 * with axe, and nothing is written behind the application's back: data a flow
 * needs is created through the same API the browser uses, with the same session
 * and the same CSRF token, so a seeded fixture cannot pass a test the product
 * would fail.
 */
import { execFileSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import {
  constants,
  accessSync,
  existsSync,
  mkdirSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { type APIResponse, type Page, expect, test } from "@playwright/test";

/**
 * The administrator the suite signs in as. It is overridable because the suite
 * runs both against a throwaway stack it configures itself and against a
 * long-lived one that was configured before the suite ever ran.
 */
export const ADMIN_USERNAME = process.env.E2E_ADMIN_USERNAME ?? "gauntlet";
export const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "Gauntlet-Test-2026!";

/** Mirrors `CSRF_COOKIE` in `apps/api/pornarr_api/auth.py`. */
const CSRF_COOKIE = "pornarr_csrf";
/** Mirrors `CSRF_HEADER` in `apps/api/pornarr_api/auth.py`. */
const CSRF_HEADER = "X-CSRF-Token";

export async function expectNoAccessibilityViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
}

export async function waitForWebServer(page: Page): Promise<void> {
  await expect
    .poll(
      async () => {
        try {
          return (await page.request.get("/")).ok();
        } catch {
          return false;
        }
      },
      { timeout: 30_000 },
    )
    .toBe(true);
}

export async function setUpAdministrator(page: Page): Promise<void> {
  await page.goto("/setup");

  await expect(page.getByRole("heading", { name: "Set up Pornarr" })).toBeVisible();
  await page.getByLabel("Username").fill(ADMIN_USERNAME);
  await page.getByLabel("Password").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Continue" }).click();

  await expect(page.getByRole("heading", { name: "Choose the library path" })).toBeVisible();
  await page.getByRole("textbox", { name: "Library path" }).fill("/data");
  await page.getByRole("button", { name: "Continue" }).click();

  // Steps 3 and 4. Both are optional by design -- the wizard says an indexer and
  // a client can be added later under Settings -- and a helper whose job is
  // "get me a signed-in administrator" takes that offer. A spec that wants
  // either one configured drives the step itself; `setup.spec.ts` does.
  await expect(page.getByRole("heading", { name: "Add a search indexer" })).toBeVisible();
  await page.getByRole("button", { name: "Skip for now" }).click();

  await expect(page.getByRole("heading", { name: "Add a download client" })).toBeVisible();
  await page.getByRole("button", { name: "Skip for now" }).click();

  const filtersHeading = page.getByRole("heading", { name: "Review content filters" });
  const copyImports = page.getByRole("button", { name: "Continue with copy imports" });
  await expect(filtersHeading.or(copyImports)).toBeVisible();
  if (await copyImports.isVisible()) {
    await copyImports.click();
    await expect(filtersHeading).toBeVisible();
  }
  await page.getByRole("button", { name: "Continue" }).click();

  await expect(page.getByRole("heading", { name: "Metadata providers" })).toBeVisible();
  await page.getByRole("button", { name: "Skip for now" }).click();

  await expect(page.getByRole("heading", { name: "Review setup" })).toBeVisible();
  await page.getByRole("button", { name: "Complete setup" }).click();

  await expect(page.getByRole("heading", { name: "Setup complete" })).toBeVisible();
  await page.getByRole("link", { name: "Sign in" }).click();
}

export async function loginAsAdmin(page: Page): Promise<void> {
  await waitForWebServer(page);
  const setupStatus = await page.request.get("/api/setup/status");
  expect(setupStatus.ok()).toBe(true);
  const { configured } = (await setupStatus.json()) as { configured: boolean };
  if (!configured) {
    await setUpAdministrator(page);
  } else {
    await page.goto("/login");
  }

  if (page.url().includes("/login")) {
    await page.getByLabel("Username").fill(ADMIN_USERNAME);
    await page.getByLabel("Password").fill(ADMIN_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    // The Dashboard, not the library: signing in as an administrator lands on
    // maintenance, because that is what the account is for. See
    // `routes/home-redirect.tsx`.
    await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();
  }
}

/**
 * The signed-in browser context is the only session the suite has, so every
 * seeding call goes through it: same cookie, same CSRF double-submit, same
 * authorisation checks the UI is subject to.
 */
async function csrfToken(page: Page): Promise<string> {
  const cookies = await page.context().cookies();
  const token = cookies.find((cookie) => cookie.name === CSRF_COOKIE)?.value;
  if (token === undefined) throw new Error("The signed-in session carries no CSRF cookie.");
  return token;
}

export async function apiGet<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(path);
  if (!response.ok()) {
    throw new Error(`GET ${path} returned ${response.status()}: ${await response.text()}`);
  }
  return (await response.json()) as T;
}

/** POST without asserting success, for flows whose subject is the status code. */
export async function apiPostRaw(
  page: Page,
  path: string,
  data: unknown = {},
): Promise<APIResponse> {
  return page.request.post(path, { data, headers: { [CSRF_HEADER]: await csrfToken(page) } });
}

export async function apiPost<T>(page: Page, path: string, data: unknown = {}): Promise<T> {
  const response = await apiPostRaw(page, path, data);
  if (!response.ok()) {
    throw new Error(`POST ${path} returned ${response.status()}: ${await response.text()}`);
  }
  return (await response.json()) as T;
}

/** PATCH without asserting success, for flows whose subject is the status code. */
export async function apiPatchRaw(page: Page, path: string, data: unknown): Promise<APIResponse> {
  return page.request.patch(path, { data, headers: { [CSRF_HEADER]: await csrfToken(page) } });
}

export async function apiPatch<T>(page: Page, path: string, data: unknown): Promise<T> {
  const response = await apiPatchRaw(page, path, data);
  if (!response.ok()) {
    throw new Error(`PATCH ${path} returned ${response.status()}: ${await response.text()}`);
  }
  return (await response.json()) as T;
}

export async function apiPut<T>(page: Page, path: string, data: unknown): Promise<T> {
  const response = await page.request.put(path, {
    data,
    headers: { [CSRF_HEADER]: await csrfToken(page) },
  });
  if (!response.ok()) {
    throw new Error(`PUT ${path} returned ${response.status()}: ${await response.text()}`);
  }
  return (await response.json()) as T;
}

export async function apiDelete(page: Page, path: string): Promise<APIResponse> {
  return page.request.delete(path, { headers: { [CSRF_HEADER]: await csrfToken(page) } });
}

export type SeededRequest = {
  readonly id: string;
  readonly query: string;
  readonly status: string;
};

/** Create a request the way the requests screen does, so its status can be followed. */
export async function seedRequest(page: Page, query: string): Promise<SeededRequest> {
  return apiPost<SeededRequest>(page, "/api/requests", { query });
}

export type LibraryItem = { readonly id: string; readonly title: string };

type LibraryPage = {
  readonly items: LibraryItem[];
  readonly total: number;
  readonly next_offset: number | null;
};

/**
 * Every item in the library, not the first page of it. The stack is shared, so
 * the library grows past any single page as other specs seed into it, and a
 * helper that read `limit=48&offset=0` silently stopped finding fixtures once
 * the library passed 48 rows. `MAX_PAGE_ITEMS` in routers/peers.py caps `limit`
 * at 100; the page count is bounded so a server that never advances
 * `next_offset` fails loudly instead of looping.
 */
export async function libraryItems(page: Page): Promise<LibraryItem[]> {
  const items: LibraryItem[] = [];
  let offset: number | null = 0;
  for (let pages = 0; offset !== null; pages += 1) {
    expect(
      pages,
      "The library never stopped paginating; next_offset is not advancing.",
    ).toBeLessThan(50);
    const body: LibraryPage = await apiGet<LibraryPage>(
      page,
      `/api/library?limit=100&offset=${offset}`,
    );
    items.push(...body.items);
    offset = body.items.length === 0 ? null : body.next_offset;
  }
  return items;
}

export async function libraryItemByTitle(page: Page, title: string): Promise<LibraryItem | null> {
  return (await libraryItems(page)).find((item) => item.title === title) ?? null;
}

export type QuarantineItem = { readonly id: string; readonly original_path: string };

export async function firstQuarantineItem(page: Page): Promise<QuarantineItem | null> {
  const items = await apiGet<QuarantineItem[]>(page, "/api/admin/quarantine");
  return items[0] ?? null;
}

export type Recommendation = { readonly media_id: string; readonly title: string };

export async function firstRecommendation(page: Page): Promise<Recommendation | null> {
  const items = await apiGet<Recommendation[]>(page, "/api/recommendations");
  return items[0] ?? null;
}

export async function configuredIndexerCount(page: Page): Promise<number> {
  return (await apiGet<unknown[]>(page, "/api/admin/indexers")).length;
}

/**
 * The indexer and the download client that `docker-compose.testing.yml` brings
 * up, configured through the same routes an operator would use.
 *
 * Both return null when that compose file is not running, and the tests that
 * need them say so in their skip rather than pretending the grab path was
 * covered.
 */
const TESTING_INDEXER_NAME = "E2E fake indexer";
const TESTING_CLIENT_NAME = "E2E qBittorrent";
export const TESTING_RELEASE_TITLE =
  process.env.E2E_FAKE_RELEASE_TITLE ?? "Fake Studio - Compose Test Scene (2026) 1080p";

type NamedResource = { readonly id: string; readonly name: string; readonly health: string };

async function healthyResource(
  page: Page,
  collection: string,
  name: string,
  body: Record<string, unknown>,
): Promise<NamedResource | null> {
  const existing = (await apiGet<NamedResource[]>(page, collection)).find(
    (item) => item.name === name,
  );
  const resource =
    existing ??
    (await apiPostRaw(page, collection, { ...body, name }).then(async (response) =>
      response.status() === 201 ? ((await response.json()) as NamedResource) : null,
    ));
  if (resource === null) return null;
  const tested = await apiPostRaw(page, `${collection}/${resource.id}/test`, {});
  if (!tested.ok()) return null;
  return (await tested.json()) as NamedResource;
}

export async function testingIndexer(page: Page): Promise<NamedResource | null> {
  return healthyResource(page, "/api/admin/indexers", TESTING_INDEXER_NAME, {
    protocol: "torrent",
    implementation: "torznab",
    base_url: process.env.E2E_FAKE_INDEXER_URL ?? "http://fake-indexer:9117/api",
    api_key: "fake",
    priority: 1,
    enabled: true,
  });
}

export async function testingDownloadClient(page: Page): Promise<NamedResource | null> {
  return healthyResource(page, "/api/admin/download-clients", TESTING_CLIENT_NAME, {
    protocol: "torrent",
    implementation: "qbittorrent",
    host: process.env.E2E_QBITTORRENT_HOST ?? "qbittorrent",
    port: Number(process.env.E2E_QBITTORRENT_PORT ?? 8080),
    credentials: JSON.stringify({ username: "admin", password: "adminadmin" }),
    category: "pornarr",
    enabled: true,
  });
}

/**
 * One row of `/api/queue`, in full.
 *
 * The whole record, not an identifier and a status: a queue that is only ever
 * asserted by its length is a queue nobody has actually tested.
 */
export type QueueJob = {
  readonly id: string;
  readonly request_id: string | null;
  readonly title: string | null;
  readonly client_name: string;
  readonly protocol: string;
  readonly release_guid: string;
  readonly status: string;
  readonly priority: number;
  readonly remaining_bytes: number | null;
  readonly size_bytes: number | null;
  readonly download_speed_bytes: number | null;
  readonly error: string | null;
  readonly estimated_seconds: number | null;
  readonly created_at: string;
  readonly queue_estimate: {
    readonly low_seconds: number | null;
    readonly high_seconds: number | null;
    readonly confidence: string;
  };
};

export async function queueJobs(page: Page, query = ""): Promise<QueueJob[]> {
  return (await apiGet<{ items: QueueJob[] }>(page, `/api/queue${query}`)).items;
}

export async function queueJob(page: Page, jobId: string): Promise<QueueJob | null> {
  return (await queueJobs(page, "?limit=100")).find((job) => job.id === jobId) ?? null;
}

export type QueueSummary = {
  readonly active: number;
  readonly queued: number;
  readonly failed: number;
  readonly completed: number;
  readonly speed_bytes: number;
};

export async function queueSummary(page: Page): Promise<QueueSummary> {
  return apiGet<QueueSummary>(page, "/api/queue/summary");
}

/**
 * qBittorrent's own Web UI, as the suite sees it from the host.
 *
 * The point of reaching past Pornarr is that a lifecycle claim ("pause pauses
 * it") is about the download client, not about Pornarr's opinion of the
 * download client. Asserting only Pornarr's copy of the state would pass
 * against a product that never called the client at all.
 */
export const QBITTORRENT_URL = process.env.E2E_QBITTORRENT_WEBUI ?? "http://localhost:8181";

export type ClientTorrent = {
  readonly hash: string;
  readonly name: string;
  readonly state: string;
  readonly category: string;
  readonly size: number;
  readonly progress: number;
  /** The client's own estimate, in seconds. 8_640_000 is its "no idea" value. */
  readonly eta: number;
  readonly amount_left: number;
  readonly dlspeed: number;
  readonly content_path: string;
};

/** What the download client itself says about one torrent, or null if it has none. */
export async function clientTorrent(page: Page, infoHash: string): Promise<ClientTorrent | null> {
  const response = await page.request.get(
    `${QBITTORRENT_URL}/api/v2/torrents/info?hashes=${infoHash}`,
  );
  if (!response.ok()) {
    throw new Error(`qBittorrent answered ${response.status()} for ${infoHash}.`);
  }
  return ((await response.json()) as ClientTorrent[])[0] ?? null;
}

/**
 * The second testing indexer: releases that are real enough to grab and can
 * never finish. See `infrastructure/testing/control_indexer.py` for why the
 * seeded fixture indexer cannot serve the lifecycle tests.
 *
 * It is started on demand inside the `fake-indexer` container rather than
 * declared as a compose service, so a stack that is already up does not have to
 * be recreated to gain it.
 */
const CONTROL_INDEXER_NAME = "E2E control indexer";
const CONTROL_INDEXER_SCRIPT = "/app/infrastructure/testing/control_indexer.py";
/** Answering a search for this term with nothing is the whole point of it. */
export const CONTROL_INDEXER_EMPTY_TERM = "gauntlet-control-nothing";

function controlIndexerContainer(): string | null {
  try {
    const project = process.env.E2E_COMPOSE_PROJECT ?? "pornarr_gauntlet";
    const found = execFileSync("docker", [
      "ps",
      "--filter",
      `label=com.docker.compose.project=${project}`,
      "--filter",
      "label=com.docker.compose.service=fake-indexer",
      "--format",
      "{{.Names}}",
    ])
      .toString()
      .trim();
    return found === "" ? null : found.split("\n")[0];
  } catch {
    return null;
  }
}

/** The info hash `control_indexer.py` publishes for a search term. */
export function controlInfoHash(term: string): string {
  return createHash("sha1").update(term).digest("hex");
}

/** `title_for` in `infrastructure/testing/control_indexer.py`. */
export function controlReleaseTitle(term: string): string {
  return `Gauntlet Control ${term} (2026) 2160p`;
}

export async function controlIndexer(page: Page): Promise<NamedResource | null> {
  const container = controlIndexerContainer();
  if (container === null) return null;
  try {
    // Starting a second copy is harmless: the port is taken, so it exits and
    // the one already serving carries on.
    execFileSync("docker", ["exec", "-d", container, "python", CONTROL_INDEXER_SCRIPT], {
      stdio: "ignore",
    });
  } catch {
    return null;
  }
  const existing = (await apiGet<NamedResource[]>(page, "/api/admin/indexers")).find(
    (item) => item.name === CONTROL_INDEXER_NAME,
  );
  const resource =
    existing ??
    (await apiPostRaw(page, "/api/admin/indexers", {
      name: CONTROL_INDEXER_NAME,
      protocol: "torrent",
      implementation: "torznab",
      base_url: process.env.E2E_CONTROL_INDEXER_URL ?? "http://fake-indexer:9118/api",
      api_key: "control",
      priority: 2,
      enabled: true,
    }).then(async (response) =>
      response.status() === 201 ? ((await response.json()) as NamedResource) : null,
    ));
  if (resource === null) return null;
  // The server was only just started, so the first capability check can land
  // before it is listening.
  let tested: APIResponse | null = null;
  await expect
    .poll(
      async () => {
        tested = await apiPostRaw(page, `/api/admin/indexers/${resource.id}/test`, {});
        return tested.ok();
      },
      { timeout: 20_000, intervals: [500] },
    )
    .toBe(true);
  return tested === null ? null : ((await (tested as APIResponse).json()) as NamedResource);
}

export type ExternalRelease = {
  readonly id: string;
  readonly guid: string;
  readonly title: string;
  readonly indexer_id: string;
  readonly indexer_name: string;
  readonly protocol: string;
  readonly size: number | null;
  readonly seeders: number | null;
  readonly estimate: {
    readonly low_seconds: number | null;
    readonly high_seconds: number | null;
    readonly confidence: string;
  };
};

export type IndexerSearch = {
  readonly id: string;
  readonly statuses: Record<string, string>;
  readonly items: ExternalRelease[];
};

/**
 * Run one indexer fan-out to completion and return it.
 *
 * Completion is every configured indexer having left `pending`, which is the
 * product's own signal; polling until a result happens to appear would pass on
 * a stale cache and hide an indexer that failed.
 */
export async function indexerSearch(page: Page, query: string): Promise<IndexerSearch> {
  const started = await apiPost<{ id: string }>(page, "/api/search/indexers", { q: query });
  let latest: IndexerSearch | null = null;
  await expect
    .poll(
      async () => {
        latest = await apiGet<IndexerSearch>(page, `/api/search/indexers/${started.id}`);
        const statuses = Object.values(latest.statuses);
        return statuses.length > 0 && statuses.every((status) => status !== "pending");
      },
      {
        timeout: 60_000,
        intervals: [500],
        message: `The indexer search for ${query} never finished. See apps/worker/pornarr_worker/search.py.`,
      },
    )
    .toBe(true);
  return latest as unknown as IndexerSearch;
}

export type PlaybackInfo = { readonly direct_play: boolean };

export async function playbackInfo(page: Page, mediaId: string): Promise<PlaybackInfo> {
  return apiGet<PlaybackInfo>(page, `/api/media/${mediaId}/playback-info`);
}

export type TranscodeSession = { readonly id: string; readonly media_id: string };

export async function transcodeSessions(page: Page): Promise<TranscodeSession[]> {
  return apiGet<TranscodeSession[]>(page, "/api/admin/transcode/sessions");
}

/**
 * Close every open transcode session. The software session cap on a host
 * without a usable encoder is one, so a session left behind by an earlier test
 * would make the next one fail for a reason that is not its own.
 */
export async function releaseTranscodeSessions(page: Page): Promise<void> {
  for (const session of await transcodeSessions(page)) {
    await apiDelete(page, `/api/admin/transcode/sessions/${session.id}`);
  }
}

/**
 * The repository root. Derived from the config Playwright was started with,
 * which is the only path the suite can be sure of: `rootDir` is the common
 * parent of the spec files, not the checkout.
 */
function projectRoot(): string {
  const configFile = test.info().config.configFile;
  return configFile === undefined ? process.cwd() : dirname(configFile);
}

/**
 * The containers' `/data` tree as the suite sees it on the host. Everything the
 * stack reads from disk - library roots, completed downloads - lives under it
 * through one bind mount; a run against a stack whose volume is not reachable
 * overrides this or skips.
 */
export function hostDataRoot(): string {
  return process.env.E2E_DATA_PATH ?? join(projectRoot(), "data");
}

/** Translate a path the API reports (container side) to one the suite can write. */
export function hostPathFor(containerPath: string): string | null {
  if (containerPath !== "/data" && !containerPath.startsWith("/data/")) return null;
  return join(hostDataRoot(), containerPath.slice("/data".length));
}

export function hasFfmpeg(): boolean {
  try {
    execFileSync("ffmpeg", ["-version"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

function isWritableDirectory(path: string): boolean {
  try {
    if (!statSync(path).isDirectory()) return false;
    accessSync(path, constants.W_OK);
    return true;
  } catch {
    return false;
  }
}

/**
 * Write a real, probeable H.264/AAC file. Small and short: the scan importer
 * accepts any regular media file, and a short clip keeps the transcode the
 * player asks for within a test's patience.
 */
function writeFixtureMedia(target: string, seconds: number): void {
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
      `testsrc2=size=640x360:rate=15:duration=${seconds}`,
      "-f",
      "lavfi",
      "-i",
      `sine=frequency=440:duration=${seconds}`,
      "-c:v",
      "libx264",
      "-preset",
      "ultrafast",
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

export const LIBRARY_FIXTURE_TITLE = "Pornarr E2E Fixture";
/** Long enough that a player can be seeked past the ten-second progress threshold. */
const LIBRARY_FIXTURE_SECONDS = 30;
/** Two scan windows plus the scan itself, so a collapsed request is not the end of it. */
const SEED_TIMEOUT_MILLISECONDS = 150_000;
const SCAN_RETRY_MILLISECONDS = 20_000;

export type RootFolder = { readonly id: string; readonly path: string; readonly enabled: boolean };

export async function enabledRootFolder(page: Page): Promise<RootFolder | null> {
  const folders = await apiGet<RootFolder[]>(page, "/api/admin/library/root-folders");
  return folders.find((folder) => folder.enabled) ?? null;
}

/**
 * Put one playable file in the library and return it.
 *
 * A file placed in a configured root folder and picked up by
 * `POST /api/admin/library/root-folders/{id}/scan` is the product's own way of
 * adopting media that is already on disk, so the library ends up holding
 * something the application itself decided to hold. The fixture keeps a stable
 * name, which makes a second run a no-op instead of another library entry.
 *
 * Returns null only when the environment cannot provide the file at all -
 * no ffmpeg, or a data volume the suite cannot write. A scan that runs and
 * still leaves the library empty is a failure, not a skip.
 */
export async function seedLibraryMedia(
  page: Page,
  title: string = LIBRARY_FIXTURE_TITLE,
): Promise<LibraryItem | null> {
  const existing = await libraryItemByTitle(page, title);
  const folder = await enabledRootFolder(page);
  if (folder === null) return null;
  const hostFolder = hostPathFor(folder.path);
  if (hostFolder === null || !isWritableDirectory(hostFolder) || !hasFfmpeg()) return null;

  const target = join(hostFolder, `${title}.mp4`);
  if (existing !== null && existsSync(target)) return existing;

  // Seeding is a once-per-run cost that the first test to ask for media pays,
  // and it outlasts the default per-test budget.
  test.setTimeout(Math.max(test.info().timeout, SEED_TIMEOUT_MILLISECONDS + 60_000));
  writeFixtureMedia(target, LIBRARY_FIXTURE_SECONDS);

  // A scan request is collapsed with any other made in the same wall-clock
  // minute, so one asked for just after a scan ran is answered 202 and does
  // nothing. Asking again on a slower cadence is what gets the new file seen.
  let lastScan = 0;
  await expect
    .poll(
      async () => {
        if (Date.now() - lastScan > SCAN_RETRY_MILLISECONDS) {
          lastScan = Date.now();
          const scan = await apiPostRaw(page, `/api/admin/library/root-folders/${folder.id}/scan`);
          expect([202, 409]).toContain(scan.status());
        }
        return (await libraryItemByTitle(page, title)) !== null;
      },
      {
        timeout: SEED_TIMEOUT_MILLISECONDS,
        intervals: [1_000],
        message: `A library scan of ${folder.path} did not adopt ${target}. See apps/worker/pornarr_worker/jobs/scan.py.`,
      },
    )
    .toBe(true);
  return libraryItemByTitle(page, title);
}

/**
 * The completed-download tree the import worker watches, on the host side of
 * the same bind mount.
 */
export function downloadsRoot(): string {
  return process.env.E2E_DOWNLOADS_PATH ?? join(hostDataRoot(), "torrents");
}

/**
 * The usenet completed-download tree. `Settings.usenet_path` in
 * `packages/shared/pornarr_shared/config.py`; the importer decides between a
 * hardlink and a move by which of the two roots a source sits under.
 */
export function usenetRoot(): string {
  return process.env.E2E_USENET_PATH ?? join(hostDataRoot(), "usenet");
}

export function canPlaceCompletedDownloads(): boolean {
  return hasFfmpeg() && isWritableDirectory(downloadsRoot());
}

/**
 * `MINIMUM_SIZE_BYTES` in `apps/worker/pornarr_worker/jobs/import_intake.py`.
 * Anything smaller is rejected as `too_small` before the pipeline starts, so a
 * fixture has to clear it to exercise anything at all.
 */
const MINIMUM_IMPORT_BYTES = 50 * 1024 ** 2;

/**
 * Generate a real, probeable H.264/AAC file and drop it where a finished
 * torrent would land. Lossless encoding of a synthetic pattern is what makes it
 * large enough to pass intake in a couple of seconds; the file is written under
 * the gitignored data volume and never committed.
 *
 * The import trigger table is keyed by source path, so each call must use a
 * name that has not been staged before or the pipeline ignores it.
 */
export function placeCompletedDownload(name: string): string {
  const directory = join(downloadsRoot(), "e2e");
  rmSync(directory, { recursive: true, force: true });
  return writeCompletedDownload(directory, name);
}

/**
 * Build a fixture out of sight and move it into place in one step.
 *
 * The import worker watches the download roots with `watchfiles` and also
 * sweeps them every thirty seconds, so a file being written *inside* one of
 * them is offered to intake half-finished. `validate_import_file` reads the
 * size before it waits for the file to settle, so a large fixture caught
 * mid-write is rejected as `too_small` and never looked at again - a real
 * defect, pinned in `tests/worker/test_import_intake.py`, and one a test must
 * not depend on. Building beside the watched trees and renaming makes the file
 * appear complete or not at all. The staging extension is one no scanner and no
 * importer collects.
 */
function placeAtomically(
  directory: string,
  filename: string,
  write: (path: string) => void,
): string {
  const workshop = join(hostDataRoot(), "gauntlet-fixtures");
  mkdirSync(workshop, { recursive: true });
  mkdirSync(directory, { recursive: true });
  const temporary = join(workshop, `${randomBytes(8).toString("hex")}.tmpfixture`);
  const target = join(directory, filename);
  try {
    write(temporary);
    renameSync(temporary, target);
  } finally {
    rmSync(temporary, { force: true });
  }
  return target;
}

/**
 * Write a completed-download fixture under a caller-chosen directory.
 *
 * `placeCompletedDownload` clears one shared directory before writing, which is
 * right for a single throwaway file and wrong for a test that needs several of
 * them to exist at once, or that needs one to survive while the pipeline is
 * watched for a second and a third sweep. This variant creates the directory it
 * is given and leaves everything else alone.
 *
 * `seed` changes the encoded picture, so two fixtures with the same seed are
 * visually identical and two with different seeds are not - which is what the
 * perceptual-hash comparison is about. `qp` decides how compressible the result
 * is: near-lossless output clears the fifty-megabyte intake floor in a couple of
 * seconds, and changing it produces a different byte stream, and therefore a
 * different oshash, for the very same picture.
 *
 * One below lossless, not lossless. x264 encodes `-qp 0` in High 4:4:4
 * Predictive whatever pixel format it is given, and no browser decodes 4:4:4 -
 * so every fixture the pipeline imported was a file the product was right to
 * refuse direct play for, and a suite asserting playback against them was
 * asserting it against something no viewer would ever have. `-qp 1` is
 * Constrained Baseline, is larger still, and is what an ordinary release looks
 * like.
 */
export function writeCompletedDownload(
  directory: string,
  name: string,
  { seconds = 20, seed = 0, qp = 1 }: { seconds?: number; seed?: number; qp?: number } = {},
): string {
  const target = placeAtomically(directory, `${name}.mp4`, (fixture) => {
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
        `testsrc2=size=1920x1080:rate=30:duration=${seconds},hue=h=${seed * 37}`,
        "-f",
        "lavfi",
        "-i",
        `sine=frequency=${440 + seed}:duration=${seconds}`,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-qp",
        String(qp),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        // The fixture's own name, written into the container. Two runs of the
        // same spec would otherwise encode the same picture with the same
        // settings and produce the same bytes, and the second run's file would be
        // turned away as a duplicate of the first run's - which is the pipeline
        // being right and the test being wrong.
        "-metadata",
        `title=${name}`,
        "-shortest",
        // The staging name carries an extension ffmpeg does not know, so the
        // container has to be named outright.
        "-f",
        "mp4",
        fixture,
      ],
      { stdio: "ignore" },
    );
  });
  if (!existsSync(target) || statSync(target).size < MINIMUM_IMPORT_BYTES) {
    throw new Error(
      `The generated fixture at ${target} is below the minimum import size; intake would reject it as too_small. Ask for a longer clip.`,
    );
  }
  return target;
}

/** A file that clears intake on extension and size but that ffprobe cannot read. */
export function writeUndecodableDownload(directory: string, name: string): string {
  return placeAtomically(directory, `${name}.mp4`, (fixture) => {
    writeFileSync(fixture, randomBytes(MINIMUM_IMPORT_BYTES + 1024 ** 2));
  });
}

/** A file of an exact size and name, for the intake decisions that are about those. */
export function writeSizedFile(directory: string, name: string, bytes: number): string {
  return placeAtomically(directory, name, (fixture) => {
    writeFileSync(fixture, Buffer.alloc(bytes, 0x21));
  });
}

const COMPOSE_PROJECT = process.env.E2E_COMPOSE_PROJECT ?? "pornarr_gauntlet";
/** psql field separator: three characters no library path will ever contain. */
const FIELD_SEPARATOR = "~|~";

function composeExec(service: string, command: readonly string[]): string | null {
  try {
    return execFileSync(
      "docker",
      ["compose", "-p", COMPOSE_PROJECT, "exec", "-T", service, ...command],
      { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
    );
  } catch {
    return null;
  }
}

function psql(query: string, separator?: string): string | null {
  return composeExec("postgres", [
    "psql",
    "-U",
    "pornarr",
    "-d",
    "pornarr",
    "-t",
    "-A",
    ...(separator === undefined ? [] : ["-F", separator]),
    "-c",
    query,
  ]);
}

/** One scalar from the application's own database, for facts no route exposes. */
export function databaseScalar(query: string): string | null {
  const output = psql(query);
  return output === null ? null : output.trim();
}

export function canReadStackDatabase(): boolean {
  return databaseScalar("select 1") === "1";
}

/**
 * Run one statement against the live database and return what it said, whether
 * it succeeded or was refused.
 *
 * `databaseScalar` swallows a failure and answers null, which is right for
 * reading a fact and useless for the one claim that is *about* a refusal: ADR
 * 0032 says the single-active-file rule is enforced in the database rather than
 * in application code, and the only way to see that is to ask the database to
 * break it and read the error it raises. A rejected statement aborts its own
 * implicit transaction, so nothing it wrote survives.
 */
export function databaseAttempt(query: string): string | null {
  const command = [
    "compose",
    "-p",
    COMPOSE_PROJECT,
    "exec",
    "-T",
    "postgres",
    "psql",
    "-U",
    "pornarr",
    "-d",
    "pornarr",
    "-t",
    "-A",
    "-c",
    query,
  ];
  try {
    return execFileSync("docker", command, {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    }).trim();
  } catch (error) {
    const failure = error as { stdout?: string; stderr?: string; code?: string };
    if (failure.stdout === undefined && failure.stderr === undefined) return null;
    return `${failure.stdout ?? ""}${failure.stderr ?? ""}`.trim();
  }
}

export type ImportTriggerRow = {
  readonly status: string;
  readonly errorCode: string | null;
  readonly errorDetail: string | null;
};

/**
 * What the import pipeline decided about one source file, and why.
 *
 * Read straight from Postgres because no route exposes it, and the reason is
 * the point: a test that only watched the library could not tell "rejected as
 * an archive" from "rejected as too small" from "still working". This is an
 * observation channel and never a seeding one - nothing in the suite writes
 * here, everything it creates it creates through the API or through the
 * watched download tree.
 *
 * Returns null when the compose project is not reachable from the test runner,
 * and the tests that need it say so in their skip.
 */
export function importTriggerRow(containerPath: string): ImportTriggerRow | null {
  const escaped = containerPath.replaceAll("'", "''");
  const output = psql(
    `select status, coalesce(error_code, ''), coalesce(error_detail, '') from import_triggers where source_path = '${escaped}'`,
    FIELD_SEPARATOR,
  );
  if (output === null) return null;
  const line = output.split("\n").find((row) => row.includes(FIELD_SEPARATOR));
  if (line === undefined) return null;
  const [status, code, detail] = line.split(FIELD_SEPARATOR);
  return {
    status,
    errorCode: code === "" ? null : code,
    errorDetail: detail === "" ? null : detail,
  };
}

/**
 * Put one of the product's own registered jobs on its own queue.
 *
 * Two documented behaviours - the nightly perceptual-hash sweep and the upgrade
 * replacement - have no route and no reachable trigger, so the only way to
 * execute them against the live stack is to enqueue the job the worker already
 * registers, through the product's own `enqueue_once`. The work is entirely the
 * product's: the real function, on the real queue, run by the real worker,
 * against the real database and the real files. What this skips is the clock,
 * and in the upgrade's case the caller the product does not have. Both are
 * named in `.gauntlet/pieces/05-import/HOLES.md`.
 */
export function enqueueStackJob(
  functionName: string,
  args: readonly string[],
  queue: string,
  options: { readonly once?: boolean } = {},
): boolean {
  // `once` is the product's own idempotent enqueue, which is right for work
  // keyed by its arguments. A job that takes no arguments always produces the
  // same id, and ARQ keeps that id for an hour after the job finishes, so a
  // second run in the same hour would be dropped and the test would be asserting
  // the first run's outcome. Those callers ask for a fresh enqueue instead.
  const once = options.once ?? true;
  const request = JSON.stringify({ function: functionName, args, queue, once });
  const script = [
    "import asyncio, json, sys",
    "from arq.connections import RedisSettings, create_pool",
    "from pornarr_shared.config import get_settings",
    "from pornarr_shared.jobs import enqueue_once",
    "async def main():",
    "    asked = json.loads(sys.argv[1])",
    "    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))",
    "    if asked['once']:",
    "        await enqueue_once(pool, asked['function'], *asked['args'], queue=asked['queue'])",
    "    else:",
    "        await pool.enqueue_job(",
    "            asked['function'], *asked['args'], _queue_name=asked['queue']",
    "        )",
    "    await pool.aclose()",
    "asyncio.run(main())",
  ].join("\n");
  return composeExec("worker", ["python", "-c", script, request]) !== null;
}

export function canDriveStackJobs(): boolean {
  return composeExec("worker", ["python", "-c", "print(1)"]) !== null;
}

/**
 * Run one Redis command against the stack's own Redis.
 *
 * Used to force the clock rather than wait it out: a transcode session's
 * heartbeat key carries the sixty-second TTL, so shortening that key is the
 * only way to observe the silence teardown inside a test's patience. It is an
 * observation-and-clock channel, never a seeding one - every session the suite
 * expires it first created through the API.
 */
export function stackRedis(command: readonly string[]): string | null {
  const output = composeExec("redis", ["redis-cli", ...command]);
  return output === null ? null : output.trim();
}

export function canDriveStackRedis(): boolean {
  return stackRedis(["ping"]) === "PONG";
}

/**
 * Whether an FFmpeg process is still writing into one transcode session's
 * directory, seen from the host.
 *
 * The container shares the host kernel and FFmpeg is started with the
 * container's own paths in its argument vector, so the session directory is a
 * unique marker for exactly one process. This is what makes "sixty seconds
 * without a heartbeat kills FFmpeg" observable as something other than a
 * directory disappearing.
 */
export function transcodeProcessCount(sessionId: string): number {
  try {
    const output = execFileSync("pgrep", ["-cf", `/data/transcodes/${sessionId}`], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    });
    return Number.parseInt(output.trim(), 10);
  } catch {
    // pgrep exits 1 when nothing matches, which is the answer rather than a fault.
    return 0;
  }
}

/** The FFmpeg command line for one session, as the operating system has it. */
export function transcodeCommandLine(sessionId: string): string | null {
  try {
    return execFileSync("pgrep", ["-af", `/data/transcodes/${sessionId}`], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return null;
  }
}

export type CollectedEvent = { readonly type: string; readonly data: string };

/**
 * Start collecting server-sent events in the page that is already signed in.
 *
 * `GET /api/events` is a stream, so it cannot be read with a request that waits
 * for a body; an `EventSource` opened in the page uses the same session cookie
 * the browser already holds, and is how the application itself consumes it.
 */
export async function collectEvents(page: Page, types: readonly string[]): Promise<void> {
  await page.evaluate((eventTypes) => {
    const scope = window as unknown as {
      __pornarrEvents?: { type: string; data: string }[];
      __pornarrEventSource?: EventSource;
    };
    scope.__pornarrEventSource?.close();
    const collected: { type: string; data: string }[] = [];
    scope.__pornarrEvents = collected;
    const source = new EventSource("/api/events");
    scope.__pornarrEventSource = source;
    for (const type of eventTypes) {
      source.addEventListener(type, (event) => {
        collected.push({ type, data: (event as MessageEvent<string>).data });
      });
    }
  }, types);
}

export async function collectedEvents(page: Page): Promise<CollectedEvent[]> {
  return page.evaluate(() => {
    const scope = window as unknown as { __pornarrEvents?: { type: string; data: string }[] };
    return scope.__pornarrEvents ?? [];
  });
}

export async function stopCollectingEvents(page: Page): Promise<void> {
  await page.evaluate(() => {
    const scope = window as unknown as { __pornarrEventSource?: EventSource };
    scope.__pornarrEventSource?.close();
    scope.__pornarrEventSource = undefined;
  });
}
