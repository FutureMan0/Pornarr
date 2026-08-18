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
import { constants, accessSync, existsSync, mkdirSync, rmSync, statSync } from "node:fs";
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

export async function apiPatch<T>(page: Page, path: string, data: unknown): Promise<T> {
  const response = await page.request.patch(path, {
    data,
    headers: { [CSRF_HEADER]: await csrfToken(page) },
  });
  if (!response.ok()) {
    throw new Error(`PATCH ${path} returned ${response.status()}: ${await response.text()}`);
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

export async function libraryItems(page: Page): Promise<LibraryItem[]> {
  return (await apiGet<{ items: LibraryItem[] }>(page, "/api/library?limit=48&offset=0")).items;
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

export type QueueJob = {
  readonly id: string;
  readonly release_guid: string;
  readonly status: string;
};

export async function queueJobs(page: Page): Promise<QueueJob[]> {
  return (await apiGet<{ items: QueueJob[] }>(page, "/api/queue")).items;
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
  mkdirSync(directory, { recursive: true });
  const target = join(directory, `${name}.mp4`);
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
      "testsrc2=size=1920x1080:rate=30:duration=20",
      "-f",
      "lavfi",
      "-i",
      "sine=frequency=440:duration=20",
      "-c:v",
      "libx264",
      "-preset",
      "ultrafast",
      "-qp",
      "0",
      "-pix_fmt",
      "yuv420p",
      "-c:a",
      "aac",
      "-shortest",
      target,
    ],
    { stdio: "ignore" },
  );
  if (!existsSync(target) || statSync(target).size < MINIMUM_IMPORT_BYTES) {
    throw new Error(`The generated fixture at ${target} is below the minimum import size.`);
  }
  return target;
}
