/**
 * Acquisition: grabbing a release, and everything the queue then says about it.
 *
 * The bar here is deliberately higher than "the queue has a row". A release is
 * revalidated, handed to the qBittorrent the testing compose file runs, and
 * every claim about what happens next is checked twice - once against Pornarr's
 * own view (`/api/queue`, `/api/requests`) and once against the download
 * client's Web UI, which is the only thing that can tell a real pause from a
 * database write that says "paused".
 *
 * Nothing sleeps. Where a state change has to travel through the five-second
 * `download_poll` cron, the test polls the resource to convergence with an
 * explicit timeout and fails when it does not arrive, which is also what proves
 * the cron is running at all.
 *
 * Two indexers are used and the difference matters. `fake_indexer.py` publishes
 * one release and then plays the seeder for it, so it is the only way to watch
 * a real transfer - and the only copy of it, which is why nothing here cancels
 * it. `control_indexer.py` publishes a fresh, unfinishable release per search
 * term, which is what makes pause, resume, re-prioritise and cancel safe to
 * drive against the real client.
 */
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { type Page, expect, test } from "@playwright/test";
import {
  type ClientTorrent,
  type ExternalRelease,
  type QueueJob,
  TESTING_RELEASE_TITLE,
  apiDelete,
  apiGet,
  apiPatch,
  apiPost,
  apiPostRaw,
  apiPut,
  canReadStackDatabase,
  clientTorrent,
  collectEvents,
  collectedEvents,
  controlIndexer,
  controlInfoHash,
  controlReleaseTitle,
  databaseScalar,
  expectNoAccessibilityViolations,
  hostPathFor,
  indexerSearch,
  loginAsAdmin,
  queueJob,
  queueJobs,
  queueSummary,
  stopCollectingEvents,
  testingDownloadClient,
  testingIndexer,
} from "./helpers";

type RequestRecord = {
  readonly id: string;
  readonly query: string;
  readonly selected_release_guid: string | null;
  readonly status: string;
  readonly priority: number;
  readonly history: { readonly status: string }[];
};

type GrabResponse = { readonly request_id: string; readonly download_job_id: string };
type ErrorBody = { readonly code: string; readonly status: number };
type DownloadClientRecord = {
  readonly id: string;
  readonly name: string;
  readonly implementation: string;
  readonly protocol: string;
  readonly health: string;
  readonly last_error: string | null;
};

/** `_ACTIVE_STATUSES` in apps/api/pornarr_api/routers/queue.py. */
const ACTIVE_STATUSES = new Set(["checking", "downloading", "importing", "moving", "repairing"]);
/** `_WAITING_STATUSES` there: everything that is still owed a turn. */
const WAITING_STATUSES = new Set([
  "checking",
  "downloading",
  "importing",
  "metadata",
  "moving",
  "queued",
  "repairing",
  "stalled",
]);
const CONFIDENCES = new Set(["high", "medium", "low"]);
/** `_search` in apps/worker/pornarr_worker/search.py answers with one of these. */
const SEARCHED = new Set(["completed", "cached"]);

async function requestById(page: Page, id: string): Promise<RequestRecord> {
  const found = (await apiGet<RequestRecord[]>(page, "/api/requests")).find(
    (item) => item.id === id,
  );
  if (found === undefined) throw new Error(`Request ${id} is missing from /api/requests.`);
  return found;
}

/**
 * Every request this file creates carries this prefix, so its own leftovers can
 * be told apart from another spec's and cleaned up without touching them.
 */
const OWN_PREFIX = "E2E acquisition ";
/**
 * The one request that grabs the compose fixture. It is found rather than
 * created, because it can never be cancelled - cancelling deletes the client's
 * files, and those are the only copy of the fixture every other suite imports
 * from - and because a request that is grabbed never reaches a terminal state
 * (see the executed expected failure at the bottom of this file), so creating a
 * new one per run permanently consumes one of the ten active-request slots
 * `request_max_active_per_user` allows.
 */
const SPINE_QUERY = `${OWN_PREFIX}spine`;
const TERMINAL_REQUEST_STATUSES = new Set(["available", "cancelled", "failed", "not_found"]);

/**
 * A request that names nothing, so the once-a-minute automatic search cannot
 * match it against a cached release and grab on the test's behalf. Every grab
 * in this file is one the test asked for by release id.
 */
async function unmatchableRequest(page: Page, label: string): Promise<RequestRecord> {
  return createRequest(page, `${OWN_PREFIX}${label}`);
}

/** Create a request and confirm the server really has it before using its id. */
async function createRequest(page: Page, query: string): Promise<RequestRecord> {
  const created = await apiPost<RequestRecord>(page, "/api/requests", { query });
  // Read it back before using it. Once, on 2026-08-19 07:34:11, a grab issued
  // 14ms after a 201 was answered 404 NOT_FOUND for a row the database shows
  // was created at that moment - see BUILD.md, "Observed once". This is the
  // rule the brief asks for anyway: never trust the mutation's own response.
  await expect
    .poll(
      async () =>
        (await apiGet<RequestRecord[]>(page, "/api/requests")).some(
          (item) => item.id === created.id,
        ),
      {
        timeout: 10_000,
        intervals: [100],
        message: `The request ${created.id} that POST /api/requests reported as created is not in the list.`,
      },
    )
    .toBe(true);
  return created;
}

/** Find this file's fixture request, or make it once. */
async function spineRequest(page: Page): Promise<RequestRecord> {
  const existing = (await apiGet<RequestRecord[]>(page, "/api/requests")).find(
    (item) => item.query === SPINE_QUERY && !TERMINAL_REQUEST_STATUSES.has(item.status),
  );
  return existing ?? createRequest(page, SPINE_QUERY);
}

/**
 * Cancel what an interrupted earlier run of this file left in flight.
 *
 * Only this file's own requests, never the fixture's, and never one holding a
 * release from the seeding indexer: cancelling those deletes the client's copy
 * of the file the rest of the suite imports from.
 */
async function releaseOwnLeftovers(page: Page): Promise<void> {
  for (const request of await apiGet<RequestRecord[]>(page, "/api/requests")) {
    if (!request.query.startsWith(OWN_PREFIX) || request.query === SPINE_QUERY) continue;
    if (TERMINAL_REQUEST_STATUSES.has(request.status)) continue;
    if (request.selected_release_guid?.startsWith("fake-indexer-")) continue;
    await apiPostRaw(page, `/api/requests/${request.id}/cancel`);
  }
}

async function grab(page: Page, requestId: string, releaseId: string) {
  return apiPostRaw(page, `/api/requests/${requestId}/grab`, { release_id: releaseId });
}

/** Read one release out of a completed fan-out, and say which indexer owes it. */
function releaseFrom(search: { items: ExternalRelease[] }, indexerId: string): ExternalRelease {
  const release = search.items.find((item) => item.indexer_id === indexerId);
  if (release === undefined) {
    throw new Error(
      `No release from indexer ${indexerId} in ${JSON.stringify(search.items.map((item) => item.guid))}.`,
    );
  }
  return release;
}

/**
 * A search-time estimate is a range with a confidence, or it is honestly
 * unknown - never a bare figure (ADR 0031).
 *
 * On this stack it is always unknown, and correctly so: the line speed comes
 * from recorded download-speed measurements (`_download_speeds` in
 * apps/api/pornarr_api/routers/search.py), and the only transfer here is a
 * local verification that moves no bytes over a link. See HOLES.md.
 */
function expectSearchEstimate(release: ExternalRelease): void {
  const { low_seconds, high_seconds, confidence } = release.estimate;
  if (confidence === "unknown") {
    expect(low_seconds).toBeNull();
    expect(high_seconds).toBeNull();
    return;
  }
  expect(CONFIDENCES).toContain(confidence);
  expect(low_seconds).not.toBeNull();
  expect(high_seconds as number).toBeGreaterThanOrEqual(low_seconds as number);
}

/** Every queue estimate is a range with a confidence, or it is honestly unknown. */
function expectRangedEstimate(job: QueueJob): void {
  const { low_seconds, high_seconds, confidence } = job.queue_estimate;
  if (WAITING_STATUSES.has(job.status)) {
    expect(low_seconds, `queue estimate for a ${job.status} job`).not.toBeNull();
    expect(high_seconds).not.toBeNull();
    expect(high_seconds as number).toBeGreaterThanOrEqual(low_seconds as number);
    expect(CONFIDENCES).toContain(confidence);
  } else {
    // ADR 0031: no estimate is better than one the system cannot stand behind.
    expect(low_seconds).toBeNull();
    expect(high_seconds).toBeNull();
    expect(confidence).toBe("unknown");
  }
}

/**
 * The import trigger the completion produced, read from the product's own
 * database: it is the record of the handover from the download client to the
 * import pipeline, and no route exposes it.
 */
type ImportTriggerForJob = {
  readonly status: string;
  readonly errorCode: string | null;
  readonly sourcePath: string;
  readonly reportedPath: string | null;
};

const TRIGGER_SEPARATOR = "~|~";

function importTriggerForJob(jobId: string): ImportTriggerForJob | null {
  const columns = [
    "status",
    "coalesce(error_code, '')",
    "source_path",
    "coalesce(reported_path, '')",
  ].join(` || '${TRIGGER_SEPARATOR}' || `);
  const output = databaseScalar(
    `select ${columns} from import_triggers where download_job_id = '${jobId}'`,
  );
  const line = output?.split("\n").find((row) => row.includes(TRIGGER_SEPARATOR));
  if (line === undefined || line === null) return null;
  const [status, errorCode, sourcePath, reportedPath] = line.split(TRIGGER_SEPARATOR);
  return {
    status,
    errorCode: errorCode === "" ? null : errorCode,
    sourcePath,
    reportedPath: reportedPath === "" ? null : reportedPath,
  };
}

/** Every file the library currently considers live, by container path. */
function activeLibraryPaths(): string[] {
  const output = databaseScalar("select path from media_files where is_active");
  if (output === null) return [];
  return output
    .split("\n")
    .map((row) => row.trim())
    .filter((row) => row.startsWith("/"));
}

/** `eta_seconds` in packages/integrations/pornarr_integrations/qbittorrent.py. */
function clientEta(torrent: ClientTorrent): number | null {
  return torrent.eta < 0 || torrent.eta >= 8_640_000 ? null : torrent.eta;
}

function sameFigures(job: QueueJob, torrent: ClientTorrent): boolean {
  return (
    job.size_bytes === torrent.size &&
    job.remaining_bytes === torrent.amount_left &&
    job.download_speed_bytes === torrent.dlspeed &&
    job.estimated_seconds === clientEta(torrent)
  );
}

/** Every queue row, following the cursor - the estimate is summed over all of them. */
async function wholeQueue(page: Page): Promise<QueueJob[]> {
  const collected: QueueJob[] = [];
  let query = "?limit=100";
  for (let pages = 0; pages < 20; pages += 1) {
    const response = await apiGet<{ items: QueueJob[]; next_cursor: string | null }>(
      page,
      `/api/queue${query}`,
    );
    collected.push(...response.items);
    if (response.next_cursor === null) break;
    query = `?limit=100&cursor=${encodeURIComponent(response.next_cursor)}`;
  }
  return collected;
}

/**
 * `list_queue` in apps/api/pornarr_api/routers/queue.py builds the waiting list
 * from every job that is neither terminal nor paused and carries an estimate.
 */
function queueSeconds(priority: number, rows: QueueJob[]): number {
  return rows
    .filter(
      (row) =>
        !["completed", "failed", "removed", "paused"].includes(row.status) &&
        row.estimated_seconds !== null &&
        row.priority >= priority,
    )
    .reduce((total, row) => total + (row.estimated_seconds as number), 0);
}

function matchesSum(job: QueueJob, rows: QueueJob[]): boolean {
  const seconds = queueSeconds(job.priority, rows);
  return (
    job.queue_estimate.low_seconds === Math.trunc(seconds * 0.8) &&
    job.queue_estimate.high_seconds === Math.trunc(seconds * 1.2)
  );
}

test.describe("acquisition", () => {
  // Real transfers, a five-second poll cron and a client that has to be asked
  // rather than assumed: every wait here is a poll with a deadline, but the
  // deadlines add up past the default per-test budget.
  test.describe.configure({ timeout: 240_000 });

  let controlIndexerId: string | null = null;

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await releaseOwnLeftovers(page);
  });

  test("a release is grabbed once, and the queue says what it is", async ({ page }) => {
    const indexer = await testingIndexer(page);
    const client = await testingDownloadClient(page);
    test.skip(
      indexer === null || client === null,
      "docker-compose.testing.yml is not running, so there is no indexer and no download client to grab through.",
    );

    // ADR 0003: the client is a row, not an environment variable, and it is the
    // qBittorrent adapter that shipped.
    const clients = await apiGet<DownloadClientRecord[]>(page, "/api/admin/download-clients");
    expect(clients.map((item) => item.name)).toContain("E2E qBittorrent");
    expect(clients.find((item) => item.name === "E2E qBittorrent")).toMatchObject({
      implementation: "qbittorrent",
      protocol: "torrent",
      health: "healthy",
    });

    const search = await indexerSearch(page, TESTING_RELEASE_TITLE);
    expect(SEARCHED).toContain(search.statuses[(indexer as { id: string }).id]);
    const release = releaseFrom(search, (indexer as { id: string }).id);
    expect(release.title).toBe(TESTING_RELEASE_TITLE);
    expect(release.protocol).toBe("torrent");
    expectSearchEstimate(release);

    const infoHash = release.guid.replace("fake-indexer-", "");
    const request = await spineRequest(page);
    const response = await grab(page, request.id, release.id);
    // 201 the first time it is handed to the client, 200 when the client is
    // already holding it - the compose fixture is shared and only ever grabbed
    // once, so both are correct answers to the same call.
    expect([200, 201], await response.text()).toContain(response.status());
    const { download_job_id } = (await response.json()) as GrabResponse;

    // The client itself has it, under the category the row configured.
    await expect
      .poll(async () => (await clientTorrent(page, infoHash))?.hash ?? null, {
        timeout: 20_000,
        intervals: [500],
        message: `qBittorrent never received ${infoHash}. See packages/integrations/pornarr_integrations/qbittorrent.py.`,
      })
      .toBe(infoHash);
    expect((await clientTorrent(page, infoHash))?.category).toBe("pornarr");

    // The request moved through its lifecycle rather than jumping to the end.
    const stored = await requestById(page, request.id);
    expect(stored.selected_release_guid).toBe(release.guid);
    expect(["queued", "downloading", "processing", "available"]).toContain(stored.status);
    // Three states, exactly - it did not jump straight to queued, and grabbing
    // the same release again added nothing. Compared sorted rather than in
    // order: `list_requests` loads the history with `selectinload` and no
    // `order_by` (routers/requests.py:312), and `RequestHistory.created_at`
    // defaults to `func.now()`, which in Postgres is the *transaction* clock -
    // so the two rows the grab writes carry the identical timestamp and no
    // reader can recover which came first. See BUILD.md defect 9.
    expect([...stored.history.map((entry) => entry.status)].sort()).toEqual([
      "queued",
      "results_found",
      "searching",
    ]);

    // The queue record, in full: what it is, who has it, how big, how far.
    // `remaining_bytes` is the field only the poll can write - the grab records
    // a size from the indexer, so waiting on that would prove nothing. Reaching
    // it without anyone asking is the download-poll cron doing its job.
    await expect
      .poll(async () => (await queueJob(page, download_job_id))?.remaining_bytes ?? null, {
        timeout: 60_000,
        intervals: [1_000],
        message:
          "download_poll never reported progress for the grabbed job. See apps/worker/pornarr_worker/jobs/download_poll.py and the beat schedule in apps/worker/pornarr_worker/settings.py.",
      })
      .not.toBeNull();
    const job = (await queueJob(page, download_job_id)) as QueueJob;
    expect(job).toMatchObject({
      client_name: "E2E qBittorrent",
      protocol: "torrent",
      release_guid: release.guid,
      priority: 80,
      error: null,
    });
    // A queue that lists release GUIDs is a queue nobody can read: the row
    // carries a request's own wording. Which request, when several people asked
    // for the same release, is not decided anywhere - `list_queue` builds a
    // dict keyed by download job from an unordered query
    // (routers/queue.py:167-181), so the last row wins. So the assertion is
    // that the pair is coherent: the id names a real request that grabbed this
    // release, and the title is that request's query, not some other one's.
    // See BUILD.md defect 10.
    const askers = (await apiGet<RequestRecord[]>(page, "/api/requests")).filter(
      (item) => item.selected_release_guid === release.guid,
    );
    expect(askers.map((item) => item.id)).toContain(request.id);
    expect(askers.map((item) => item.id)).toContain(job.request_id);
    expect(job.title).toBe(askers.find((item) => item.id === job.request_id)?.query);
    expect(job.remaining_bytes as number).toBeLessThanOrEqual(job.size_bytes as number);
    expect(Number.isNaN(Date.parse(job.created_at))).toBe(false);
    expectRangedEstimate(job);

    // The download client's own figure, not a second opinion invented here.
    //
    // Polled rather than read once. A torrent's size is not known to the client
    // until it has its metadata, so a single sample taken moments after the grab
    // can legitimately be 0 on both sides or 0 on one of them, and reading it
    // once turned that race into "the poll does not carry the size across" -
    // which `_apply_poll` in download_poll.py has always done, in the same
    // statement as `remaining_bytes`. The sibling case below polls for the same
    // reason.
    await expect
      .poll(
        async () => {
          const size = (await clientTorrent(page, infoHash))?.size ?? 0;
          const recorded = (await queueJob(page, download_job_id))?.size_bytes ?? 0;
          return size > 0 && recorded === size;
        },
        {
          timeout: 30_000,
          intervals: [1_000],
          message:
            "The queue row never took the download client's own size. See `_apply_poll` in " +
            "apps/worker/pornarr_worker/jobs/download_poll.py.",
        },
      )
      .toBe(true);
  });

  test("grabbing something the client already has is the same job, not an error", async ({
    page,
  }) => {
    const client = await testingDownloadClient(page);
    const control = await controlIndexer(page);
    test.skip(
      client === null || control === null,
      "docker-compose.testing.yml is not running, so there is no download client and no control indexer.",
    );
    controlIndexerId = (control as { id: string }).id;

    const term = `idempotent-${Date.now()}`;
    const release = releaseFrom(await indexerSearch(page, term), controlIndexerId);
    expect(release.guid).toBe(`gauntlet-control-${controlInfoHash(term)}`);

    const first = await unmatchableRequest(page, term);
    const firstGrab = await grab(page, first.id, release.id);
    expect(firstGrab.status(), await firstGrab.text()).toBe(201);
    const jobId = ((await firstGrab.json()) as GrabResponse).download_job_id;

    // The same reader clicking twice.
    const again = await grab(page, first.id, release.id);
    expect(again.status()).toBe(200);
    expect(((await again.json()) as GrabResponse).download_job_id).toBe(jobId);

    // And a different reader asking for the same release.
    const second = await unmatchableRequest(page, `${term} second`);
    const secondGrab = await grab(page, second.id, release.id);
    expect(secondGrab.status()).toBe(200);
    expect(((await secondGrab.json()) as GrabResponse).download_job_id).toBe(jobId);
    expect((await requestById(page, second.id)).selected_release_guid).toBe(release.guid);

    // One release, one job. `release_guid` is the key that makes it so.
    const jobs = (await queueJobs(page, "?limit=100")).filter(
      (item) => item.release_guid === release.guid,
    );
    expect(jobs).toHaveLength(1);
    expect(jobs[0].id).toBe(jobId);

    for (const requestId of [second.id, first.id]) {
      await apiPostRaw(page, `/api/requests/${requestId}/cancel`);
    }
    await expect
      .poll(async () => (await clientTorrent(page, controlInfoHash(term))) === null, {
        timeout: 20_000,
        intervals: [500],
        message: "The cancelled control torrent is still in qBittorrent.",
      })
      .toBe(true);
  });

  test("pause, resume, re-prioritise and cancel reach the download client", async ({ page }) => {
    const client = await testingDownloadClient(page);
    const control = await controlIndexer(page);
    test.skip(
      client === null || control === null,
      "docker-compose.testing.yml is not running, so there is no download client and no control indexer.",
    );
    controlIndexerId = (control as { id: string }).id;

    const term = `lifecycle-${Date.now()}`;
    const infoHash = controlInfoHash(term);
    const search = await indexerSearch(page, term);
    expect(SEARCHED).toContain(search.statuses[controlIndexerId]);
    const release = releaseFrom(search, controlIndexerId);
    expect(release.title).toBe(controlReleaseTitle(term));
    expectSearchEstimate(release);

    const request = await unmatchableRequest(page, term);
    const grabbed = await grab(page, request.id, release.id);
    expect(grabbed.status(), await grabbed.text()).toBe(201);
    const jobId = ((await grabbed.json()) as GrabResponse).download_job_id;

    await expect
      .poll(async () => (await clientTorrent(page, infoHash))?.hash ?? null, {
        timeout: 20_000,
        intervals: [500],
        message: `qBittorrent never received ${infoHash}.`,
      })
      .toBe(infoHash);

    // PAUSE. The claim is about the download client, so the client is asked.
    const paused = await apiPostRaw(page, `/api/requests/${request.id}/pause`);
    expect(paused.status(), await paused.text()).toBe(200);
    await expect
      .poll(async () => (await clientTorrent(page, infoHash))?.state ?? null, {
        timeout: 20_000,
        intervals: [500],
        message: "qBittorrent never stopped the paused torrent.",
      })
      .toMatch(/^(stopped|paused)/);
    expect((await queueJob(page, jobId))?.status).toBe("paused");

    // A paused torrent must not take the client out of service. The poll reads
    // every torrent the client holds in one call, so one state it cannot map
    // fails the whole batch - and a client whose health goes bad is a client
    // `route_download_client` will not route to.
    //
    // The barrier is a second grab made while the first is paused: its job is
    // written as `queued` by the grab and can only become `metadata` if a poll
    // succeeded with the paused torrent in view. Fail on timeout, do not sleep.
    const secondTerm = `${term}-while-paused`;
    const secondRelease = releaseFrom(await indexerSearch(page, secondTerm), controlIndexerId);
    const secondRequest = await unmatchableRequest(page, secondTerm);
    const secondGrab = await grab(page, secondRequest.id, secondRelease.id);
    expect(secondGrab.status(), await secondGrab.text()).toBe(201);
    const secondJobId = ((await secondGrab.json()) as GrabResponse).download_job_id;
    await expect
      .poll(async () => (await queueJob(page, secondJobId))?.status ?? null, {
        timeout: 60_000,
        intervals: [1_000],
        message:
          "download_poll never synchronised a second download while another was paused. See QBITTORRENT_STATE_MAP in packages/integrations/pornarr_integrations/qbittorrent.py.",
      })
      .toBe("metadata");
    const health = (await apiGet<DownloadClientRecord[]>(page, "/api/admin/download-clients")).find(
      (item) => item.name === "E2E qBittorrent",
    ) as DownloadClientRecord;
    expect(health.last_error).toBeNull();
    expect(health.health).toBe("healthy");

    // RESUME. The client starts it again, and Pornarr's copy of the state comes
    // back from the client through the five-second poll rather than from the
    // endpoint that asked - which is what proves the cron runs (ADR 0021).
    const resumed = await apiPostRaw(page, `/api/requests/${request.id}/resume`);
    expect(resumed.status(), await resumed.text()).toBe(200);
    await expect
      .poll(async () => (await clientTorrent(page, infoHash))?.state ?? null, {
        timeout: 20_000,
        intervals: [500],
        message: "qBittorrent never restarted the resumed torrent.",
      })
      .not.toMatch(/^(stopped|paused)/);
    await expect
      .poll(async () => (await queueJob(page, jobId))?.status ?? null, {
        timeout: 45_000,
        intervals: [1_000],
        message:
          "download_poll never resynchronised the resumed job. See apps/worker/pornarr_worker/jobs/download_poll.py.",
      })
      .toBe("metadata");

    // RE-PRIORITISE. The number moves on the request and on its queue row, and
    // the queue estimate is recomputed from the new priority on the next read.
    const before = (await queueJob(page, jobId)) as QueueJob;
    expect(before.priority).toBe(80);
    const raised = await apiPatch<RequestRecord>(page, `/api/requests/${request.id}/priority`, {
      priority: 100,
    });
    expect(raised.priority).toBe(100);
    expect((await requestById(page, request.id)).priority).toBe(100);
    const after = (await queueJob(page, jobId)) as QueueJob;
    expect(after.priority).toBe(100);
    expectRangedEstimate(after);
    // Only jobs of equal or higher priority are waited for, so raising this one
    // can never lengthen its own wait (ADR 0031).
    expect(after.queue_estimate.high_seconds as number).toBeLessThanOrEqual(
      before.queue_estimate.high_seconds as number,
    );

    // CANCEL. Terminal for the request, and the torrent leaves the client.
    const cancelled = await apiPostRaw(page, `/api/requests/${request.id}/cancel`);
    expect(cancelled.status(), await cancelled.text()).toBe(200);
    expect((await requestById(page, request.id)).status).toBe("cancelled");
    await expect
      .poll(async () => (await clientTorrent(page, infoHash)) === null, {
        timeout: 20_000,
        intervals: [500],
        message: "The cancelled torrent is still in qBittorrent.",
      })
      .toBe(true);
    // And the poll notices the job is gone rather than leaving it in flight.
    await expect
      .poll(async () => (await queueJob(page, jobId))?.status ?? null, {
        timeout: 45_000,
        intervals: [1_000],
        message: "download_poll never marked the cancelled job removed.",
      })
      .toBe("removed");
    expect((await queueJob(page, jobId))?.error).toBe(
      "Job no longer exists in the download client.",
    );

    await apiPostRaw(page, `/api/requests/${secondRequest.id}/cancel`);
    await expect
      .poll(async () => (await clientTorrent(page, controlInfoHash(secondTerm))) === null, {
        timeout: 20_000,
        intervals: [500],
        message: "The second control torrent was not removed from qBittorrent.",
      })
      .toBe(true);
  });

  test("a grab that cannot happen says why", async ({ page }) => {
    const client = await testingDownloadClient(page);
    const control = await controlIndexer(page);
    test.skip(
      client === null || control === null,
      "docker-compose.testing.yml is not running, so there is no download client and no control indexer.",
    );
    controlIndexerId = (control as { id: string }).id;

    const request = await unmatchableRequest(page, `refusals ${Date.now()}`);

    // A release that is not in the cache at all - the same answer a release
    // that has since been evicted gets, because the lookup is the same one.
    const missing = await grab(page, request.id, "00000000-0000-4000-8000-000000000000");
    expect(missing.status()).toBe(404);
    expect(((await missing.json()) as ErrorBody).code).toBe("RELEASE_NOT_FOUND");

    // An indexer that answers with nothing leaves nothing to grab, and says so
    // by reporting a healthy search with no results of its own.
    const empty = await indexerSearch(page, "gauntlet-control-nothing");
    expect(SEARCHED).toContain(empty.statuses[controlIndexerId]);
    expect(empty.items.filter((item) => item.indexer_id === controlIndexerId)).toEqual([]);

    // A cancelled request cannot accept a release.
    const term = `refused-${Date.now()}`;
    const release = releaseFrom(await indexerSearch(page, term), controlIndexerId);
    await apiPost(page, `/api/requests/${request.id}/cancel`);
    const late = await grab(page, request.id, release.id);
    expect(late.status()).toBe(409);
    expect(((await late.json()) as ErrorBody).code).toBe("REQUEST_NOT_GRABBABLE");

    // Nothing was handed to the client on the way to any of those refusals.
    expect(await clientTorrent(page, controlInfoHash(term))).toBeNull();
  });

  test("a download client that cannot be reached is a named refusal, not a stack trace", async ({
    page,
  }) => {
    // SABnzbd is the other adapter ADR 0003 says shipped, and there is no
    // SABnzbd on this stack: the point of the row is that the attempt fails as
    // a connection rather than as an unhandled httpx exception.
    const unreachable = await apiPost<DownloadClientRecord>(page, "/api/admin/download-clients", {
      name: `E2E unreachable SABnzbd ${Date.now()}`,
      protocol: "usenet",
      implementation: "sabnzbd",
      host: "127.0.0.1",
      port: 1,
      credentials: "e2e-secret-api-key",
      enabled: false,
    });
    const absent = await apiPost<DownloadClientRecord>(page, "/api/admin/download-clients", {
      name: `E2E unimplemented NZBGet ${Date.now()}`,
      protocol: "usenet",
      implementation: "nzbget",
      host: "127.0.0.1",
      port: 1,
      credentials: "e2e-secret-api-key",
      enabled: false,
    });
    try {
      const tested = await apiPostRaw(page, `/api/admin/download-clients/${unreachable.id}/test`);
      expect(tested.status()).toBe(422);
      // The contract shape, whole: a code the client can translate, the status
      // repeated inside the body, and a context object - no prose, no traceback.
      expect(await tested.json()).toEqual({
        code: "DOWNLOAD_CLIENT_CONNECTION_FAILED",
        status: 422,
        context: {},
      });
      expect(await tested.text()).not.toContain("e2e-secret-api-key");

      // ADR 0003 lists NZBGet as an adapter for later. Asking for one is
      // refused rather than attempted - with, as things stand, exactly the
      // answer an unreachable client gets. See BUILD.md.
      const unsupported = await apiPostRaw(page, `/api/admin/download-clients/${absent.id}/test`);
      expect(unsupported.status()).toBe(422);
      expect(((await unsupported.json()) as ErrorBody).code).toBe(
        "DOWNLOAD_CLIENT_CONNECTION_FAILED",
      );
    } finally {
      await apiDelete(page, `/api/admin/download-clients/${unreachable.id}`);
      await apiDelete(page, `/api/admin/download-clients/${absent.id}`);
    }
  });

  // `test_download_client` writes `health`, `last_error` and `last_tested_at`
  // and then raises, and `database_session` rolls the request back on the way
  // out - so the failure it had just diagnosed was discarded and the row still
  // read "unknown". The diagnosis is committed before the refusal is raised now,
  // which is what `test_indexer` had always done.
  test("a failed connection test is remembered on the client row", async ({ page }) => {
    const client = await apiPost<DownloadClientRecord>(page, "/api/admin/download-clients", {
      name: `E2E unreachable health ${Date.now()}`,
      protocol: "usenet",
      implementation: "sabnzbd",
      host: "127.0.0.1",
      port: 1,
      credentials: "e2e-secret-api-key",
      enabled: false,
    });
    try {
      expect(
        (await apiPostRaw(page, `/api/admin/download-clients/${client.id}/test`)).status(),
      ).toBe(422);
      const stored = (
        await apiGet<DownloadClientRecord[]>(page, "/api/admin/download-clients")
      ).find((item) => item.id === client.id) as DownloadClientRecord;
      expect(stored.health).toBe("unhealthy");
      expect(stored.last_error).not.toBeNull();
      expect(stored.last_error).not.toContain("e2e-secret-api-key");
    } finally {
      await apiDelete(page, `/api/admin/download-clients/${client.id}`);
    }
  });

  test("the summary counts the whole queue, not the page that happened to load", async ({
    page,
  }) => {
    let summary = await queueSummary(page);
    let jobs = await queueJobs(page, "?limit=100");
    const counted = (rows: QueueJob[], status: string) =>
      rows.filter((row) => row.status === status).length;
    const active = (rows: QueueJob[]) => rows.filter((row) => ACTIVE_STATUSES.has(row.status));
    const consistent = () =>
      summary.queued === counted(jobs, "queued") &&
      summary.failed === counted(jobs, "failed") &&
      summary.completed === counted(jobs, "completed") &&
      summary.active === active(jobs).length;
    // The two reads are seconds apart and the poll job moves jobs between them,
    // so a disagreement is retried before it is believed.
    for (let attempt = 0; attempt < 8 && !consistent(); attempt += 1) {
      summary = await queueSummary(page);
      jobs = await queueJobs(page, "?limit=100");
    }

    expect(summary.queued).toBe(counted(jobs, "queued"));
    expect(summary.failed).toBe(counted(jobs, "failed"));
    expect(summary.completed).toBe(counted(jobs, "completed"));
    expect(summary.active).toBe(active(jobs).length);
    expect(summary.speed_bytes).toBe(
      active(jobs).reduce((total, row) => total + (row.download_speed_bytes ?? 0), 0),
    );

    // One row on the page, the same figures above it.
    const firstPage = await queueJobs(page, "?limit=1");
    expect(firstPage.length).toBeLessThanOrEqual(1);
    const narrowed = await queueSummary(page);
    expect(narrowed.completed).toBe(summary.completed);

    // Every row's estimate obeys ADR 0031, whatever state it is in.
    for (const job of jobs) expectRangedEstimate(job);
  });

  test("the downloads screen renders the queue record it was given", async ({ page }) => {
    const jobs = await queueJobs(page, "?limit=100");
    const row = jobs.find((job) => !["completed", "removed"].includes(job.status));
    test.skip(
      row === undefined,
      "The queue holds nothing in flight, so there is no row for the screen to render.",
    );
    const job = row as QueueJob;

    await page.goto("/downloads");
    // The shell renders the screen's only h1 in the top bar, outside `main`;
    // see apps/web/src/shell/page-title.tsx for why.
    await expect(page.getByRole("heading", { name: "Downloads", level: 1 })).toBeVisible();
    const downloads = page.locator("main");

    const entry = downloads
      .getByRole("row")
      .filter({ hasText: job.title ?? job.release_guid })
      .first();
    await expect(entry).toBeVisible();
    // The five facts C8 says a queue row carries: what it is, whose client has
    // it, over which protocol, what stage it is at, and how long it has left.
    await expect(entry).toContainText(job.client_name);
    await expect(entry).toContainText(job.protocol);
    await expect(entry).toContainText(job.status);
    if (job.size_bytes !== null && job.remaining_bytes !== null && job.size_bytes > 0) {
      const percent = Math.round(((job.size_bytes - job.remaining_bytes) / job.size_bytes) * 100);
      await expect(entry).toContainText(`${percent}%`);
    } else {
      await expect(entry).toContainText("Size not reported");
    }
    // Never a bare figure: a range with a tilde, or the honest "Unknown".
    await expect(entry).toContainText(job.queue_estimate.low_seconds === null ? "Unknown" : "~");

    // The cards above it count the queue rather than the rows on screen.
    const summary = await queueSummary(page);
    await expect(downloads.getByRole("listitem").filter({ hasText: "Queued" })).toContainText(
      String(summary.queued),
    );
    await expectNoAccessibilityViolations(page);
  });

  test("two clients on one protocol are routed by priority, then by health", async ({ page }) => {
    const client = await testingDownloadClient(page);
    const control = await controlIndexer(page);
    test.skip(
      client === null || control === null,
      "docker-compose.testing.yml is not running, so there is no download client and no control indexer.",
    );
    controlIndexerId = (control as { id: string }).id;

    // A second instance of the same protocol, pointing at the same qBittorrent
    // so that it is genuinely healthy rather than merely present, and preferred
    // because its priority is lower. ADR 0003: "Multiple instances per protocol
    // are supported, and a grab is routed by protocol, then priority, then
    // health." Each of the three is made to decide the outcome in turn.
    const preferredName = `E2E preferred qBittorrent ${Date.now()}`;
    const definition = {
      protocol: "torrent",
      implementation: "qbittorrent",
      host: process.env.E2E_QBITTORRENT_HOST ?? "qbittorrent",
      port: Number(process.env.E2E_QBITTORRENT_PORT ?? 8080),
      credentials: JSON.stringify({ username: "admin", password: "adminadmin" }),
      category: "pornarr",
      priority: -1,
      remove_completed: false,
      enabled: true,
    };
    const preferred = await apiPost<DownloadClientRecord>(page, "/api/admin/download-clients", {
      ...definition,
      name: preferredName,
    });
    const requests: string[] = [];
    try {
      const tested = await apiPostRaw(page, `/api/admin/download-clients/${preferred.id}/test`);
      expect(tested.status(), await tested.text()).toBe(200);
      expect(((await tested.json()) as DownloadClientRecord).health).toBe("healthy");

      // PRIORITY. Two healthy torrent clients, and the grab goes to the one
      // configured first - which is the new row, not the one every other test
      // in this file uses.
      const firstTerm = `routing-preferred-${Date.now()}`;
      const firstRelease = releaseFrom(await indexerSearch(page, firstTerm), controlIndexerId);
      const firstRequest = await unmatchableRequest(page, firstTerm);
      requests.push(firstRequest.id);
      const firstGrab = await grab(page, firstRequest.id, firstRelease.id);
      expect(firstGrab.status(), await firstGrab.text()).toBe(201);
      const firstJobId = ((await firstGrab.json()) as GrabResponse).download_job_id;
      expect((await queueJob(page, firstJobId))?.client_name).toBe(preferredName);
      // And the client it was routed to is the one holding the torrent.
      await expect
        .poll(async () => (await clientTorrent(page, controlInfoHash(firstTerm)))?.hash ?? null, {
          timeout: 20_000,
          intervals: [500],
          message: "The preferred client never received the routed torrent.",
        })
        .toBe(controlInfoHash(firstTerm));

      await apiPostRaw(page, `/api/requests/${firstRequest.id}/cancel`);
      await expect
        .poll(async () => (await clientTorrent(page, controlInfoHash(firstTerm))) === null, {
          timeout: 20_000,
          intervals: [500],
          message: "The routed torrent was not removed from the preferred client.",
        })
        .toBe(true);
      // Let the poll retire the job before the client it belongs to is taken
      // away, so this test leaves nothing permanently in flight behind it.
      await expect
        .poll(async () => (await queueJob(page, firstJobId))?.status ?? null, {
          timeout: 45_000,
          intervals: [1_000],
          message: "download_poll never retired the job routed to the preferred client.",
        })
        .toBe("removed");

      // HEALTH. Point the preferred row somewhere unreachable and let the
      // product notice by itself. Waiting for `download_poll` rather than
      // writing `health` directly is what makes this a test of the routing
      // rule and of ADR 0021's cron at the same time.
      await apiPut<DownloadClientRecord>(page, `/api/admin/download-clients/${preferred.id}`, {
        ...definition,
        name: preferredName,
        host: "127.0.0.1",
        port: 1,
      });
      await expect
        .poll(
          async () =>
            (await apiGet<DownloadClientRecord[]>(page, "/api/admin/download-clients")).find(
              (item) => item.id === preferred.id,
            )?.health ?? null,
          {
            timeout: 60_000,
            intervals: [1_000],
            message:
              "download_poll never marked the unreachable client unhealthy. See apps/worker/pornarr_worker/jobs/download_poll.py.",
          },
        )
        .toBe("unhealthy");
      const clients = await apiGet<DownloadClientRecord[]>(page, "/api/admin/download-clients");
      const broken = clients.find((item) => item.id === preferred.id) as DownloadClientRecord;
      // The reason, not just the verdict - and without the credential in it.
      expect(broken.last_error).not.toBeNull();
      expect(broken.last_error).not.toContain("adminadmin");
      // One client failing must not take the other one down with it: they are
      // polled concurrently and their health is recorded separately.
      expect(clients.find((item) => item.name === "E2E qBittorrent")?.health).toBe("healthy");

      const secondTerm = `routing-fallback-${Date.now()}`;
      const secondRelease = releaseFrom(await indexerSearch(page, secondTerm), controlIndexerId);
      const secondRequest = await unmatchableRequest(page, secondTerm);
      requests.push(secondRequest.id);
      const secondGrab = await grab(page, secondRequest.id, secondRelease.id);
      expect(secondGrab.status(), await secondGrab.text()).toBe(201);
      const secondJobId = ((await secondGrab.json()) as GrabResponse).download_job_id;
      // Same protocol, better priority, but no longer healthy: skipped.
      expect((await queueJob(page, secondJobId))?.client_name).toBe("E2E qBittorrent");

      await apiPostRaw(page, `/api/requests/${secondRequest.id}/cancel`);
      await expect
        .poll(async () => (await clientTorrent(page, controlInfoHash(secondTerm))) === null, {
          timeout: 20_000,
          intervals: [500],
          message: "The fallback client's torrent was not removed.",
        })
        .toBe(true);
    } finally {
      for (const requestId of requests) {
        await apiPostRaw(page, `/api/requests/${requestId}/cancel`);
      }
      await apiDelete(page, `/api/admin/download-clients/${preferred.id}`);
    }
  });

  test("a completed torrent is imported and left seeding", async ({ page }) => {
    const indexer = await testingIndexer(page);
    const client = await testingDownloadClient(page);
    test.skip(
      indexer === null || client === null,
      "docker-compose.testing.yml is not running, so there is no indexer to grab a finishable release from.",
    );
    test.skip(
      !canReadStackDatabase(),
      "The import trigger is the product's own record of the handover and no route exposes it, so it is read from the stack's database; this stack's postgres container is not reachable.",
    );

    const search = await indexerSearch(page, TESTING_RELEASE_TITLE);
    const release = releaseFrom(search, (indexer as { id: string }).id);
    const infoHash = release.guid.replace("fake-indexer-", "");
    const request = await spineRequest(page);
    const response = await grab(page, request.id, release.id);
    // 201 the first time, 200 for every run after: the compose fixture is one
    // release and one job, and grabbing it again attaches rather than duplicates.
    expect([200, 201], await response.text()).toContain(response.status());
    const jobId = ((await response.json()) as GrabResponse).download_job_id;

    // The client finishes the transfer. A torrent told to keep seeding reports
    // itself as seeding, never as completed, which is why the import cannot
    // wait for "completed" (download_poll.py:28-33).
    await expect
      .poll(async () => (await queueJob(page, jobId))?.status ?? null, {
        timeout: 180_000,
        intervals: [2_000],
        message:
          "The grabbed release never finished in the download client. See infrastructure/testing/fake_indexer.py, which plays the seeder.",
      })
      .toMatch(/^(seeding|completed)$/);

    // import.md L3: "Triggered when a download completes." The trigger is the
    // handover record, keyed by the download job, and it names the file.
    await expect
      .poll(() => importTriggerForJob(jobId)?.status ?? null, {
        timeout: 180_000,
        intervals: [2_000],
        message:
          "No import trigger reached 'imported' for the completed download. See apps/worker/pornarr_worker/jobs/import_trigger.py.",
      })
      .toBe("imported");
    const trigger = importTriggerForJob(jobId) as ImportTriggerForJob;
    expect(trigger.errorCode).toBeNull();

    // The path imported is the path the client itself reported, not one Pornarr
    // guessed: this is the failure docs/operations/troubleshooting.md L41-45 is
    // about, and here the two agree.
    const torrent = (await clientTorrent(page, infoHash)) as ClientTorrent;
    expect(trigger.sourcePath).toBe(torrent.content_path);
    // `reported_path` is only written when the completion is what created the
    // trigger. When the filesystem watcher got to the same file first, the
    // completion adopts that row by download job alone and the client's own
    // wording of the path is not kept (import_trigger.py:105-111) - so this is
    // "null or exactly the client's path", never anything else. See BUILD.md.
    expect([null, torrent.content_path]).toContain(trigger.reportedPath);

    // ADR 0004 and deployment.md L27-28: a hardlink, so the torrent keeps
    // seeding. The download file is still there, and it has a twin.
    const source = hostPathFor(trigger.sourcePath);
    expect(source, `${trigger.sourcePath} is not inside the stack's data volume.`).not.toBeNull();
    expect(existsSync(source as string)).toBe(true);
    const sourceStat = statSync(source as string);
    expect(sourceStat.nlink).toBeGreaterThanOrEqual(2);

    // The twin is in the library, and it is the same inode - a copy would have
    // its own. Download and quarantine trees are excluded because the library
    // scanner adopts them as media in their own right; see piece 05's BUILD.md.
    const twins = activeLibraryPaths().filter(
      (path) =>
        !path.startsWith("/data/torrents/") &&
        !path.startsWith("/data/usenet/") &&
        !path.startsWith("/data/qbt-incomplete/"),
    );
    const linked = twins.filter((path) => {
      const host = hostPathFor(path);
      return host !== null && existsSync(host) && statSync(host).ino === sourceStat.ino;
    });
    expect(linked, "No library file shares an inode with the completed download.").toHaveLength(1);
    expect(statSync(hostPathFor(linked[0]) as string).size).toBe(sourceStat.size);

    // And the client still has it, complete, seeding: import did not move the
    // file out from under the torrent.
    expect(torrent.progress).toBe(1);
    expect(torrent.state).toMatch(/UP$/);
    expect(torrent.amount_left).toBe(0);
  });

  test("the queue reports the download client's own figures", async ({ page }) => {
    const client = await testingDownloadClient(page);
    test.skip(
      client === null,
      "docker-compose.testing.yml is not running, so there is no download client to compare against.",
    );
    const jobs = (await queueJobs(page, "?limit=100")).filter(
      (job) => job.status !== "removed" && job.client_name === "E2E qBittorrent",
    );
    test.skip(
      jobs.length === 0,
      "The download client is holding nothing, so there is no job whose figures can be compared with it.",
    );

    // ADR 0031: "For running jobs the download client's own estimate wins."
    // Every number on the row is the client's, read back out of the client.
    // The poll runs every five seconds, so a disagreement is re-read before it
    // is believed rather than being called a defect on the first sample.
    let compared = 0;
    for (const job of jobs) {
      const clientJobId = job.release_guid.replace(/^(fake-indexer-|gauntlet-control-)/, "");
      let torrent = await clientTorrent(page, clientJobId);
      if (torrent === null) continue;
      let row = (await queueJob(page, job.id)) as QueueJob;
      for (let attempt = 0; attempt < 8 && !sameFigures(row, torrent); attempt += 1) {
        torrent = await clientTorrent(page, clientJobId);
        row = (await queueJob(page, job.id)) as QueueJob;
        if (torrent === null) break;
      }
      if (torrent === null) continue;
      expect(row.size_bytes, `size for ${job.id}`).toBe(torrent.size);
      expect(row.remaining_bytes, `remaining for ${job.id}`).toBe(torrent.amount_left);
      expect(row.download_speed_bytes, `speed for ${job.id}`).toBe(torrent.dlspeed);
      // qbittorrent.py:143 - the client's sentinel for "no idea" becomes an
      // honest null rather than a hundred-day estimate.
      expect(row.estimated_seconds, `estimate for ${job.id}`).toBe(clientEta(torrent));
      compared += 1;
    }
    expect(compared, "No queue row could be matched to a torrent in the client.").toBeGreaterThan(
      0,
    );
  });

  test("a queue estimate is the sum of the jobs that run before it", async ({ page }) => {
    // ADR 0031: "Queue time sums the remaining time of equal or higher priority
    // jobs". Recomputed here from the queue's own rows and compared with what
    // the endpoint returned, which executes the formula rather than trusting it.
    let rows = await wholeQueue(page);
    const agrees = (queue: QueueJob[]) =>
      queue.every((job) => !WAITING_STATUSES.has(job.status) || matchesSum(job, queue));
    for (let attempt = 0; attempt < 6 && !agrees(rows); attempt += 1) {
      rows = await wholeQueue(page);
    }
    const waiting = rows.filter((job) => WAITING_STATUSES.has(job.status));
    expect(
      waiting.length,
      "The queue holds nothing waiting, so no estimate was checked.",
    ).toBeGreaterThan(0);
    for (const job of waiting) {
      const seconds = queueSeconds(job.priority, rows);
      expect(job.queue_estimate.low_seconds, `low for ${job.id}`).toBe(Math.trunc(seconds * 0.8));
      expect(job.queue_estimate.high_seconds, `high for ${job.id}`).toBe(Math.trunc(seconds * 1.2));
      expect(job.queue_estimate.confidence).toBe("medium");
    }
    // A job that is not waiting has no queue position, and says so.
    for (const job of rows.filter((item) => !WAITING_STATUSES.has(item.status))) {
      expect(job.queue_estimate).toEqual({
        low_seconds: null,
        high_seconds: null,
        confidence: "unknown",
      });
    }
  });

  test("the stream carries the download's state changes and its progress", async ({ page }) => {
    const client = await testingDownloadClient(page);
    const control = await controlIndexer(page);
    test.skip(
      client === null || control === null,
      "docker-compose.testing.yml is not running, so there is no download client and no control indexer.",
    );
    controlIndexerId = (control as { id: string }).id;

    // Opened before the grab, so nothing that follows can be missed.
    await collectEvents(page, ["download.status", "download.progress"]);
    const term = `events-${Date.now()}`;
    const release = releaseFrom(await indexerSearch(page, term), controlIndexerId);
    const request = await unmatchableRequest(page, term);
    const grabbed = await grab(page, request.id, release.id);
    expect(grabbed.status(), await grabbed.text()).toBe(201);
    const jobId = ((await grabbed.json()) as GrabResponse).download_job_id;

    try {
      const ours = async () =>
        (await collectedEvents(page)).filter(
          (event) => (JSON.parse(event.data) as { job_id?: string }).job_id === jobId,
        );
      await expect
        .poll(async () => (await ours()).some((event) => event.type === "download.status"), {
          timeout: 60_000,
          intervals: [1_000],
          message:
            "No download.status arrived for the grabbed job. See _transition in apps/worker/pornarr_worker/jobs/download_poll.py.",
        })
        .toBe(true);
      const events = await ours();
      const statuses = events
        .filter((event) => event.type === "download.status")
        .map((event) => (JSON.parse(event.data) as { status: string }).status);
      // The status carried is the lifecycle vocabulary DESIGN.md L225-227 uses,
      // not the client's own spelling of it.
      expect(statuses.every((status) => WAITING_STATUSES.has(status) || status === "paused")).toBe(
        true,
      );
      expect(statuses).toContain("metadata");
      for (const event of events.filter((item) => item.type === "download.progress")) {
        const payload = JSON.parse(event.data) as Record<string, unknown>;
        expect(Object.keys(payload).sort()).toEqual([
          "download_speed_bytes",
          "estimated_seconds",
          "job_id",
          "remaining_bytes",
          "size_bytes",
        ]);
      }
    } finally {
      await stopCollectingEvents(page);
      await apiPostRaw(page, `/api/requests/${request.id}/cancel`);
      await expect
        .poll(async () => (await clientTorrent(page, controlInfoHash(term))) === null, {
          timeout: 20_000,
          intervals: [500],
          message: "The event test's control torrent is still in qBittorrent.",
        })
        .toBe(true);
    }
  });

  test("a grab emits the download lifecycle events the contract names", async ({ page }) => {
    const client = await testingDownloadClient(page);
    const control = await controlIndexer(page);
    test.skip(
      client === null || control === null,
      "docker-compose.testing.yml is not running, so there is no download client and no control indexer.",
    );
    controlIndexerId = (control as { id: string }).id;

    // docs/api-contract.md L46-47 lists request.created, download.queued and
    // download.started among the event types. Nothing publishes any of them.
    await collectEvents(page, ["request.created", "download.queued", "download.started"]);
    const term = `documented-events-${Date.now()}`;
    const release = releaseFrom(await indexerSearch(page, term), controlIndexerId);
    const request = await unmatchableRequest(page, term);
    try {
      expect((await grab(page, request.id, release.id)).status()).toBe(201);
      await expect
        .poll(async () => (await collectedEvents(page)).map((event) => event.type).sort(), {
          timeout: 30_000,
          intervals: [1_000],
        })
        .toEqual(["download.queued", "download.started", "request.created"]);
    } finally {
      await stopCollectingEvents(page);
      await apiPostRaw(page, `/api/requests/${request.id}/cancel`);
    }
  });

  test("a request whose download finished and imported reaches available", async ({ page }) => {
    const indexer = await testingIndexer(page);
    const client = await testingDownloadClient(page);
    test.skip(
      indexer === null || client === null,
      "docker-compose.testing.yml is not running, so there is no release that can finish.",
    );

    // DESIGN.md L225-227 states one lifecycle vocabulary for requests and
    // downloads: "searching, queued, downloading, importing, available,
    // quarantined, failed, cancelled". Three of those states are declared as
    // legal transitions in packages/db/pornarr_db/requests.py:29-34 and are
    // reached by nothing: no caller anywhere moves a request to DOWNLOADING,
    // PROCESSING or AVAILABLE. So a request whose release has been
    // downloaded, hardlinked into the library and published as
    // `media.available` still says "queued", for ever - which also makes
    // `request_max_active_per_user` a lifetime cap rather than a
    // concurrency one.
    const search = await indexerSearch(page, TESTING_RELEASE_TITLE);
    const release = releaseFrom(search, (indexer as { id: string }).id);
    const request = await spineRequest(page);
    const grabbed = await grab(page, request.id, release.id);
    expect([200, 201]).toContain(grabbed.status());
    const jobId = ((await grabbed.json()) as GrabResponse).download_job_id;
    await expect
      .poll(async () => (await queueJob(page, jobId))?.status ?? null, {
        timeout: 180_000,
        intervals: [2_000],
      })
      .toMatch(/^(seeding|completed)$/);

    await expect
      .poll(async () => (await requestById(page, request.id)).status, {
        timeout: 30_000,
        intervals: [2_000],
      })
      .toBe("available");
  });

  test("the code an unreachable download client answers with is the one the contract names", async ({
    page,
  }) => {
    // api-contract.md L28-30: the code is stable and machine-readable, and the
    // frontend maps it to a translated message. That only holds while the
    // document and the application agree on the spelling, and they had drifted:
    // the document named `DOWNLOAD_CLIENT_UNREACHABLE` and the application has
    // always raised `DOWNLOAD_CLIENT_CONNECTION_FAILED`, so a client generated
    // from the document carried a branch that could never be taken.
    //
    // Read out of the document rather than typed in here, so the next drift
    // fails this rather than being noticed by somebody a year later.
    const contract = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "..", "..", "docs", "api-contract.md"),
      "utf8",
    );
    const documented = (contract.match(/`(DOWNLOAD_CLIENT_[A-Z_]+)`/) ?? [])[1];
    expect(documented, "api-contract.md names no download-client error code at all.").toBeDefined();

    const unreachable = await apiPost<DownloadClientRecord>(page, "/api/admin/download-clients", {
      name: `E2E contract code ${Date.now()}`,
      protocol: "usenet",
      implementation: "sabnzbd",
      host: "127.0.0.1",
      port: 1,
      credentials: "e2e-secret-api-key",
      enabled: false,
    });
    try {
      const tested = await apiPostRaw(page, `/api/admin/download-clients/${unreachable.id}/test`);
      expect(tested.status()).toBe(422);
      const code = ((await tested.json()) as ErrorBody).code;
      expect(code).toBe(documented);
      // And the code really is mapped to a sentence, which is the half of the
      // promise a matching spelling does not prove on its own.
      for (const locale of ["en", "de"]) {
        const messages = JSON.parse(
          readFileSync(
            join(
              dirname(fileURLToPath(import.meta.url)),
              "..",
              "..",
              "apps",
              "web",
              "src",
              "i18n",
              `${locale}.json`,
            ),
            "utf8",
          ),
        ) as { errors: Record<string, string>; errorSteps: Record<string, string> };
        expect(messages.errors[code], `${locale}.json has no sentence for ${code}.`).toBeTruthy();
        expect(
          messages.errorSteps[code],
          `${locale}.json tells the reader no next step for ${code}.`,
        ).toBeTruthy();
      }
    } finally {
      await apiDelete(page, `/api/admin/download-clients/${unreachable.id}`);
    }
  });

  test.afterAll(async ({ browser }) => {
    // The control indexer is this file's fixture and nobody else's: leaving it
    // enabled would put an extra release into every other suite's search.
    if (controlIndexerId === null) return;
    const page = await browser.newPage();
    try {
      await loginAsAdmin(page);
      await apiDelete(page, `/api/admin/indexers/${controlIndexerId}`);
    } finally {
      await page.close();
    }
  });
});
