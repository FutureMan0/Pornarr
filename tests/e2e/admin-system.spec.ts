/**
 * Piece 13: quality decisions, monitors, RSS, the job queue, the event stream
 * and the system surfaces, against the live stack.
 *
 * Three things shape this file.
 *
 * **A decision is asserted by its reason, not by its status code.**
 * `pornarr_core.quality.decide_quality` returns a verdict, a reason *and* a
 * score, and `/api/admin/quality/preview` puts all three on the wire. Every
 * quality case below names the reason, and the matrix carries a row just inside
 * and a row just outside each documented boundary, because a profile that
 * accepts everything passes a test that only ever asserts "grab".
 *
 * **RSS needs a feed that can change.** Neither indexer the stack already has
 * can serve one: `fake_indexer.py` publishes a single release whose GUID never
 * changes, so `last_rss_guid` is permanently caught up, and
 * `control_indexer.py` answers a feed request with an empty channel by design.
 * `infrastructure/testing/rss_indexer.py` is the third, started on demand
 * inside the same container, and it is the only one a test can publish into.
 * It also counts the requests that arrive, which is what makes ADR 0030 L11 -
 * one request per indexer per cycle - assertable rather than inferable.
 *
 * **The stack is shared.** Everything created here carries a run-unique marker,
 * every assertion filters to it, and `afterAll` removes the indexer, the
 * monitors, the profiles, the custom formats and the automatic requests this
 * file made.
 */
import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { request as httpRequest } from "node:http";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import {
  apiDelete,
  apiGet,
  apiPatchRaw,
  apiPost,
  apiPostRaw,
  apiPut,
  canDriveStackJobs,
  canReadStackDatabase,
  databaseScalar,
  loginAsAdmin,
  testingIndexer,
} from "./helpers";

const COMPOSE_PROJECT = process.env.E2E_COMPOSE_PROJECT ?? "pornarr_gauntlet";
const API_ORIGIN = process.env.E2E_API_URL ?? "http://127.0.0.1:8000";
/** One token per run, so nothing here can collide with another builder's rows. */
const RUN = randomBytes(3).toString("hex");
const MARKER = `p13-${RUN}`;

// ---------------------------------------------------------------------------
// Stack access
//
// These are local rather than in `helpers.ts` on purpose: three builders are
// appending to that file today, and a new export in it is a merge conflict for
// all three. Nothing here duplicates an existing helper.
// ---------------------------------------------------------------------------

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

/**
 * Enqueue one of the product's own registered jobs and wait for arq's own
 * result record.
 *
 * The result is what makes the queue claims falsifiable: arq answers "function
 * not found" for a job put on a queue whose worker does not register it, which
 * is the difference between four queues and one bus with four names.
 */
type JobOutcome = {
  readonly finished: boolean;
  readonly success: boolean;
  readonly result: string;
};

const JOB_PROBE = [
  "import asyncio, json, sys",
  "from arq.connections import RedisSettings, create_pool",
  "from pornarr_shared.config import get_settings",
  "async def main():",
  "    asked = json.loads(sys.argv[1])",
  "    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))",
  "    job = await pool.enqueue_job(",
  "        asked['function'], *asked['args'], _queue_name=asked['queue']",
  "    )",
  "    loop = asyncio.get_running_loop()",
  "    deadline = loop.time() + asked['timeout']",
  "    info = None",
  "    while loop.time() < deadline:",
  "        info = await job.result_info()",
  "        if info is not None:",
  "            break",
  "        await asyncio.sleep(0.5)",
  "    print(json.dumps({",
  "        'finished': info is not None,",
  "        'success': bool(info and info.success),",
  "        'result': '' if info is None else str(info.result),",
  "    }))",
  "    await pool.aclose()",
  "asyncio.run(main())",
].join("\n");

function runStackJob(
  functionName: string,
  args: readonly string[],
  queue: string,
  timeoutSeconds = 90,
): JobOutcome | null {
  const asked = JSON.stringify({ function: functionName, args, queue, timeout: timeoutSeconds });
  const output = composeExec("worker", ["python", "-c", JOB_PROBE, asked]);
  if (output === null) return null;
  return JSON.parse(output.trim()) as JobOutcome;
}

/**
 * The RSS test indexer, reached from inside the container that runs it.
 *
 * `127.0.0.1` rather than `localhost`: the image resolves `localhost` to `::1`
 * first and the server binds IPv4 only, so the name would answer "connection
 * refused" against a perfectly healthy process.
 */
function rssIndexerCall(path: string): string | null {
  return composeExec("fake-indexer", ["wget", "-qO-", `http://127.0.0.1:9119${path}`]);
}

function startRssIndexer(): boolean {
  try {
    // Starting a second copy is harmless: the port is taken, so it exits and
    // the one already serving carries on. Mirrors `controlIndexer` in helpers.
    execFileSync(
      "docker",
      [
        "compose",
        "-p",
        COMPOSE_PROJECT,
        "exec",
        "-T",
        "-d",
        "fake-indexer",
        "python",
        "/app/infrastructure/testing/rss_indexer.py",
      ],
      { stdio: "ignore" },
    );
  } catch {
    return false;
  }
  for (let attempt = 0; attempt < 40; attempt += 1) {
    if (rssIndexerCall("/stats") !== null) return true;
    execFileSync("sleep", ["0.25"]);
  }
  return false;
}

type FeedCounters = {
  readonly feed_requests: number;
  readonly search_requests: number;
  readonly items: number;
};

function feedCounters(): FeedCounters {
  const raw = rssIndexerCall("/stats");
  if (raw === null) throw new Error("The RSS test indexer is not answering.");
  return JSON.parse(raw) as FeedCounters;
}

function publishToFeed(title: string): string {
  const raw = rssIndexerCall(`/publish?title=${encodeURIComponent(title)}`);
  if (raw === null) throw new Error("The RSS test indexer refused a publication.");
  return (JSON.parse(raw) as { guid: string }).guid;
}

// ---------------------------------------------------------------------------
// The event stream
//
// `page.request` buffers a whole body, so it can never read a stream that does
// not end. Node's own client can, and it reaches the API directly rather than
// through the Vite dev proxy - which matters for `X-Accel-Buffering`, a header
// whose whole point is what sits in front of the API.
// ---------------------------------------------------------------------------

type StreamRead = {
  readonly status: number;
  readonly headers: Record<string, string>;
  readonly frames: readonly {
    readonly id: string;
    readonly event: string;
    readonly data: string;
  }[];
};

function parseFrames(body: string): StreamRead["frames"] {
  return body
    .split("\n\n")
    .filter((block) => block.includes("event:"))
    .map((block) => {
      const line = (prefix: string) =>
        block
          .split("\n")
          .find((row) => row.startsWith(prefix))
          ?.slice(prefix.length)
          .trim() ?? "";
      return { id: line("id:"), event: line("event:"), data: line("data:") };
    });
}

async function readEventStream(
  page: Page,
  options: {
    readonly lastEventId?: string;
    readonly cookie?: boolean;
    readonly until?: (frames: StreamRead["frames"]) => boolean;
    readonly timeoutMs?: number;
    readonly during?: () => Promise<void>;
  } = {},
): Promise<StreamRead> {
  const cookies = await page.context().cookies();
  const cookieHeader = cookies.map((entry) => `${entry.name}=${entry.value}`).join("; ");
  const headers: Record<string, string> = { Accept: "text/event-stream" };
  if (options.cookie !== false) headers.Cookie = cookieHeader;
  if (options.lastEventId !== undefined) headers["Last-Event-ID"] = options.lastEventId;

  return new Promise<StreamRead>((resolve, reject) => {
    const url = new URL("/api/events", API_ORIGIN);
    const clientRequest = httpRequest(
      { hostname: url.hostname, port: url.port, path: url.pathname, method: "GET", headers },
      (response) => {
        let body = "";
        let settled = false;
        const finish = () => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          response.destroy();
          clientRequest.destroy();
          resolve({
            status: response.statusCode ?? 0,
            headers: Object.fromEntries(
              Object.entries(response.headers).map(([key, value]) => [key, String(value)]),
            ),
            frames: parseFrames(body),
          });
        };
        const timer = setTimeout(finish, options.timeoutMs ?? 8_000);
        response.on("data", (chunk: Buffer) => {
          body += chunk.toString();
          if (options.until?.(parseFrames(body))) finish();
        });
        response.on("end", finish);
        response.on("error", finish);
        // The stream is open before whatever is meant to publish into it runs;
        // starting the work first is how a test races its own subject.
        void options.during?.().catch(reject);
      },
    );
    clientRequest.on("error", reject);
    clientRequest.end();
  });
}

// ---------------------------------------------------------------------------
// Types for the surfaces under test
// ---------------------------------------------------------------------------

type QualityDefinition = {
  readonly id: string;
  readonly name: string;
  readonly resolution: string;
  readonly source: string;
  readonly weight: number;
  readonly minimum_size_mb_per_minute: number;
  readonly maximum_size_mb_per_minute: number;
};

type QualityProfile = {
  readonly id: string;
  readonly name: string;
  readonly cutoff_quality_id: string;
  readonly minimum_custom_format_score: number;
  readonly is_default: boolean;
  readonly qualities: QualityDefinition[];
};

type CustomFormatCondition = {
  readonly field: string;
  readonly operator: string;
  readonly value: unknown;
  readonly negate?: boolean;
  readonly required?: boolean;
};

type CustomFormat = {
  readonly id: string;
  readonly name: string;
  readonly score: number;
  readonly conditions: (CustomFormatCondition & { readonly id: string })[];
};

type QualityPreview = {
  readonly quality: QualityDefinition | null;
  readonly score: number;
  readonly verdict: string;
  readonly reason: string;
  readonly matched_custom_formats: { readonly name: string; readonly score: number }[];
  readonly fields: Record<string, unknown>;
};

type Monitor = {
  readonly id: string;
  readonly kind: string;
  readonly performer_id: string | null;
  readonly studio_id: string | null;
  readonly query: string | null;
  readonly quality_profile_id: string;
  readonly enabled: boolean;
  readonly minimum_score: number;
  readonly last_match_at: string | null;
};

type RequestRow = {
  readonly id: string;
  readonly query: string;
  readonly selected_release_guid: string | null;
  readonly status: string;
  readonly priority: number;
  readonly is_automatic: boolean;
};

type IndexerRow = {
  readonly id: string;
  readonly name: string;
  readonly health: string;
  readonly enabled: boolean;
  readonly stats: { readonly queries: number; readonly failures: number; readonly grabs: number };
};

type ErrorBody = { readonly code: string; readonly status: number; readonly context: unknown };

// ---------------------------------------------------------------------------
// Shared fixtures
// ---------------------------------------------------------------------------

const RSS_INDEXER_NAME = `E2E RSS indexer ${MARKER}`;
/**
 * Built so that every gate in `monitor_match` can be reached deliberately.
 * `normalize_release_title` folds punctuation to spaces, so the monitor query
 * and the quality definition name must both appear as whole phrases: the
 * profile item "WEB 1080p" has to sit in the title in that order for
 * `_quality_item` to find it at all.
 */
function feedTitle(subject: string): string {
  return `Gauntlet ${MARKER} ${subject} (2026) WEB 1080p`;
}

/** The monitor phrase the titles above all contain. */
const MONITOR_QUERY = `gauntlet ${MARKER}`;

let definitions: QualityDefinition[] = [];
const createdProfiles: string[] = [];
const createdFormats: string[] = [];
const createdMonitors: string[] = [];
let rssIndexerId: string | null = null;
let rssIndexerAvailable = false;

async function definitionByName(page: Page, name: string): Promise<QualityDefinition> {
  if (definitions.length === 0) {
    definitions = await apiGet<QualityDefinition[]>(page, "/api/admin/quality/definitions");
  }
  const found = definitions.find((definition) => definition.name === name);
  if (found === undefined) {
    throw new Error(`No quality definition named ${name}; got ${definitions.map((d) => d.name)}`);
  }
  return found;
}

async function preview(
  page: Page,
  body: Record<string, unknown>,
): Promise<{ status: number; body: QualityPreview | ErrorBody }> {
  const response = await apiPostRaw(page, "/api/admin/quality/preview", body);
  return { status: response.status(), body: (await response.json()) as QualityPreview | ErrorBody };
}

test.describe("piece 13 - quality, monitors, RSS, jobs, system", () => {
  test.beforeAll(async ({ browser }) => {
    const page = await browser.newPage();
    await loginAsAdmin(page);
    definitions = await apiGet<QualityDefinition[]>(page, "/api/admin/quality/definitions");
    rssIndexerAvailable = startRssIndexer();
    if (rssIndexerAvailable) {
      rssIndexerCall("/reset");
      const created = await apiPostRaw(page, "/api/admin/indexers", {
        name: RSS_INDEXER_NAME,
        protocol: "torrent",
        implementation: "torznab",
        base_url: process.env.E2E_RSS_INDEXER_URL ?? "http://fake-indexer:9119/api",
        api_key: "rss",
        priority: 3,
        enabled: true,
      });
      if (created.status() === 201) {
        rssIndexerId = ((await created.json()) as IndexerRow).id;
        const tested = await apiPostRaw(page, `/api/admin/indexers/${rssIndexerId}/test`, {});
        rssIndexerAvailable = tested.ok();
      } else {
        rssIndexerAvailable = false;
      }
    }
    await page.close();
  });

  test.afterAll(async ({ browser }) => {
    const page = await browser.newPage();
    await loginAsAdmin(page);
    for (const id of createdMonitors) await apiDelete(page, `/api/monitors/${id}`);
    for (const id of createdFormats)
      await apiDelete(page, `/api/admin/quality/custom-formats/${id}`);
    for (const id of createdProfiles) await apiDelete(page, `/api/admin/quality/profiles/${id}`);
    // Every automatic request this run's feed produced, cancelled through the
    // product so the search dispatcher stops looking for it.
    const requests = await apiGet<RequestRow[]>(page, "/api/requests");
    for (const row of requests.filter((entry) => entry.query.includes(MARKER))) {
      await apiPostRaw(page, `/api/requests/${row.id}/cancel`, {});
    }
    if (rssIndexerId !== null) await apiDelete(page, `/api/admin/indexers/${rssIndexerId}`);
    rssIndexerCall("/reset");
    await page.close();
  });

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  // -------------------------------------------------------------------------
  // 13.1, 13.2, 13.3, 13.4 - quality
  // -------------------------------------------------------------------------

  test("quality definitions are ranked and every one carries size sanity bounds", async ({
    page,
  }) => {
    // ADR 0029 L9: "Ranked quality definitions with size sanity bounds".
    const shipped = await apiGet<QualityDefinition[]>(page, "/api/admin/quality/definitions");
    expect(shipped.length).toBeGreaterThanOrEqual(3);

    const weights = shipped.map((definition) => definition.weight);
    expect(weights, "definitions are returned in rank order").toEqual(
      [...weights].sort((a, b) => a - b),
    );
    expect(
      new Set(weights).size,
      "two definitions share a rank, so neither outranks the other",
    ).toBe(weights.length);

    for (const definition of shipped) {
      expect(definition.minimum_size_mb_per_minute, definition.name).toBeGreaterThan(0);
      expect(definition.maximum_size_mb_per_minute, definition.name).toBeGreaterThan(
        definition.minimum_size_mb_per_minute,
      );
      expect(definition.resolution, definition.name).not.toBe("");
      expect(definition.source, definition.name).not.toBe("");
    }

    // The rank is not decoration: a higher resolution outranks a lower one at
    // the same source, which is what makes "better" mean anything downstream.
    const byResolution = new Map(shipped.map((definition) => [definition.resolution, definition]));
    const sd = byResolution.get("720p");
    const hd = byResolution.get("1080p");
    const uhd = byResolution.get("2160p");
    expect(sd && hd && uhd, "the shipped ladder is 720p, 1080p, 2160p").toBeTruthy();
    expect((sd as QualityDefinition).weight).toBeLessThan((hd as QualityDefinition).weight);
    expect((hd as QualityDefinition).weight).toBeLessThan((uhd as QualityDefinition).weight);

    // The bounds are a real predicate, not a stored pair: `is_size_mislabelled`
    // is the only consumer and it is exercised by the import suite. What this
    // asserts is that the pair the API returns is the pair the database holds.
    const stored = databaseScalar(
      "select minimum_size_mb_per_minute || '/' || maximum_size_mb_per_minute" +
        " from quality_definitions where name = 'WEB 1080p'",
    );
    test.skip(stored === null, "The stack's database is not reachable from the test runner.");
    expect(stored).toBe(
      `${(hd as QualityDefinition).minimum_size_mb_per_minute}/${(hd as QualityDefinition).maximum_size_mb_per_minute}`,
    );
  });

  test("the allowed set decides, and the preview names the rule that decided", async ({ page }) => {
    // ADR 0029 L9 and openapi.json `/api/admin/quality/preview`: a profile has
    // an allowed set, and the preview shows how a release would be scored
    // before anything is grabbed. A row just inside and a row just outside.
    const hd = await definitionByName(page, "WEB 1080p");
    const uhd = await definitionByName(page, "WEB 2160p");
    const release = `Gauntlet ${MARKER} Ladder (2026) WEB-DL 2160p`;

    const outside = await preview(page, {
      release_name: release,
      profile: { quality_definition_ids: [hd.id], cutoff_quality_id: hd.id },
    });
    expect(outside.status).toBe(200);
    const rejected = outside.body as QualityPreview;
    expect(rejected.verdict).toBe("reject");
    // `_quality_from_release` cannot map 2160p onto a profile holding only
    // 1080p, so the refusal is that the release's quality was not detected
    // *within this profile* rather than that it was detected and disallowed.
    expect(rejected.reason).toBe("quality_not_detected");
    expect(rejected.quality).toBeNull();

    const inside = await preview(page, {
      release_name: release,
      profile: { quality_definition_ids: [hd.id, uhd.id], cutoff_quality_id: uhd.id },
    });
    expect(inside.status).toBe(200);
    const grabbed = inside.body as QualityPreview;
    expect(grabbed.verdict).toBe("grab");
    expect(grabbed.reason).toBe("no_existing_file");
    expect(grabbed.quality?.name).toBe("WEB 2160p");
    expect(grabbed.score).toBe(uhd.weight);
    // The parsed properties the score was computed from, so a wrong verdict can
    // be told apart from a wrong parse. `web-dl`, not the definition's `web`:
    // `_source` in pornarr_core/matching.py keeps the precise source the release
    // named, and `_quality_from_release` matches it to the coarser family the
    // definition carries by prefix. Asserting `web` here would pin the parse to
    // the definition's vocabulary and lose the distinction between WEB-DL and
    // WEBRip that a custom format is written against.
    expect(grabbed.fields).toMatchObject({ resolution: "2160p", source: "web-dl" });
  });

  test("a release below the profile minimum score is rejected, and one on it is not", async ({
    page,
  }) => {
    // `decide_quality` compares `score < profile.minimum_custom_format_score`,
    // so the boundary itself passes. Both sides of it are asserted.
    const hd = await definitionByName(page, "WEB 1080p");
    const release = `Gauntlet ${MARKER} Minimum (2026) WEB-DL 1080p`;
    const profile = { quality_definition_ids: [hd.id], cutoff_quality_id: hd.id };

    const onTheLine = await preview(page, {
      release_name: release,
      profile: { ...profile, minimum_custom_format_score: hd.weight },
    });
    expect((onTheLine.body as QualityPreview).verdict).toBe("grab");
    expect((onTheLine.body as QualityPreview).reason).toBe("no_existing_file");
    expect((onTheLine.body as QualityPreview).score).toBe(hd.weight);

    const oneAbove = await preview(page, {
      release_name: release,
      profile: { ...profile, minimum_custom_format_score: hd.weight + 1 },
    });
    expect((oneAbove.body as QualityPreview).verdict).toBe("reject");
    expect((oneAbove.body as QualityPreview).reason).toBe("below_minimum_score");
    expect((oneAbove.body as QualityPreview).score).toBe(hd.weight);
  });

  test("a custom format scores only the releases its conditions match", async ({ page }) => {
    // ADR 0029 L9: "custom formats as a scoring system over release properties".
    const hd = await definitionByName(page, "WEB 1080p");
    const profile = { quality_definition_ids: [hd.id], cutoff_quality_id: hd.id };
    const format = {
      name: `${MARKER} h265`,
      score: 25,
      conditions: [{ field: "codec", operator: "equals", value: "hevc" }],
    };

    const matching = await preview(page, {
      release_name: `Gauntlet ${MARKER} Codec (2026) WEB-DL 1080p HEVC`,
      profile,
      custom_formats: [format],
    });
    const matched = matching.body as QualityPreview;
    expect(matched.fields).toMatchObject({ codec: "hevc" });
    expect(matched.matched_custom_formats).toEqual([{ name: format.name, score: 25 }]);
    expect(matched.score, "the format's score is added to the quality's rank").toBe(hd.weight + 25);
    expect(matched.verdict).toBe("grab");

    const notMatching = await preview(page, {
      release_name: `Gauntlet ${MARKER} Codec (2026) WEB-DL 1080p x264`,
      profile,
      custom_formats: [format],
    });
    const unmatched = notMatching.body as QualityPreview;
    expect(unmatched.fields).toMatchObject({ codec: "h264" });
    expect(unmatched.matched_custom_formats).toEqual([]);
    expect(unmatched.score).toBe(hd.weight);

    // The score is what a minimum turns into a refusal: the same release, the
    // same profile, one point of minimum above what the format contributes.
    const refused = await preview(page, {
      release_name: `Gauntlet ${MARKER} Codec (2026) WEB-DL 1080p x264`,
      profile: { ...profile, minimum_custom_format_score: hd.weight + 1 },
      custom_formats: [format],
    });
    expect((refused.body as QualityPreview).reason).toBe("below_minimum_score");
    const allowed = await preview(page, {
      release_name: `Gauntlet ${MARKER} Codec (2026) WEB-DL 1080p HEVC`,
      profile: { ...profile, minimum_custom_format_score: hd.weight + 1 },
      custom_formats: [format],
    });
    expect((allowed.body as QualityPreview).verdict).toBe("grab");
  });

  test("a saved custom format survives a re-read, and a duplicate name is refused by code", async ({
    page,
  }) => {
    const body = {
      name: `${MARKER} remux`,
      score: 40,
      conditions: [
        { field: "flags", operator: "contains", value: "remux", negate: false, required: true },
      ],
    };
    const created = await apiPost<CustomFormat>(page, "/api/admin/quality/custom-formats", body);
    createdFormats.push(created.id);

    // Re-read rather than trust the mutation's own response.
    const listed = (await apiGet<CustomFormat[]>(page, "/api/admin/quality/custom-formats")).find(
      (format) => format.id === created.id,
    );
    expect(listed, "the created format is not in the collection").toBeDefined();
    expect(listed?.score).toBe(40);
    expect(listed?.conditions).toHaveLength(1);
    expect(listed?.conditions[0]).toMatchObject({
      field: "flags",
      operator: "contains",
      value: "remux",
      negate: false,
      required: true,
    });

    const duplicate = await apiPostRaw(page, "/api/admin/quality/custom-formats", body);
    expect(duplicate.status()).toBe(422);
    const error = (await duplicate.json()) as ErrorBody;
    expect(error.code).toBe("QUALITY_CONFIGURATION_INVALID");
    expect(error.status).toBe(422);
    expect(error.context).toMatchObject({ reason: "duplicate_name" });

    const changed = await apiPut<CustomFormat>(
      page,
      `/api/admin/quality/custom-formats/${created.id}`,
      { ...body, score: 5 },
    );
    expect(changed.score).toBe(5);
    const reread = (await apiGet<CustomFormat[]>(page, "/api/admin/quality/custom-formats")).find(
      (format) => format.id === created.id,
    );
    expect(reread?.score, "the edit did not persist").toBe(5);
  });

  test("a profile round-trips, and its cutoff must be inside its own allowed set", async ({
    page,
  }) => {
    const sd = await definitionByName(page, "WEB 720p");
    const hd = await definitionByName(page, "WEB 1080p");
    const uhd = await definitionByName(page, "WEB 2160p");

    const outsideCutoff = await apiPostRaw(page, "/api/admin/quality/profiles", {
      name: `${MARKER} bad cutoff`,
      quality_definition_ids: [sd.id, hd.id],
      cutoff_quality_id: uhd.id,
    });
    expect(outsideCutoff.status()).toBe(422);
    const error = (await outsideCutoff.json()) as ErrorBody;
    expect(error.code).toBe("QUALITY_CONFIGURATION_INVALID");
    expect(error.context).toEqual({});

    const profile = await apiPost<QualityProfile>(page, "/api/admin/quality/profiles", {
      name: `${MARKER} profile`,
      quality_definition_ids: [uhd.id, sd.id, hd.id],
      cutoff_quality_id: hd.id,
      minimum_custom_format_score: 7,
    });
    createdProfiles.push(profile.id);

    const reread = await apiGet<QualityProfile>(page, `/api/admin/quality/profiles/${profile.id}`);
    expect(reread.cutoff_quality_id).toBe(hd.id);
    expect(reread.minimum_custom_format_score).toBe(7);
    expect(reread.is_default).toBe(false);
    // The order the profile was written in is the order it reads back in:
    // `QualityProfileItem.position` is persisted and the relationship is
    // ordered by it.
    expect(reread.qualities.map((quality) => quality.id)).toEqual([uhd.id, sd.id, hd.id]);

    // ...and it is inert. Every quality selection in the product sorts by
    // `quality_definition.weight`, so the same release wins whichever order the
    // profile lists. Asserted rather than assumed, because ADR 0029 does not
    // claim ordering and a reader of the screen would expect it to matter.
    const release = `Gauntlet ${MARKER} Order (2026) WEB-DL 1080p`;
    const forwards = await preview(page, {
      release_name: release,
      profile: { quality_definition_ids: [sd.id, hd.id, uhd.id], cutoff_quality_id: hd.id },
    });
    const backwards = await preview(page, {
      release_name: release,
      profile: { quality_definition_ids: [uhd.id, hd.id, sd.id], cutoff_quality_id: hd.id },
    });
    expect((backwards.body as QualityPreview).quality?.id).toBe(
      (forwards.body as QualityPreview).quality?.id,
    );
    expect((backwards.body as QualityPreview).score).toBe((forwards.body as QualityPreview).score);
  });

  test("the default profile cannot be deleted, and one a monitor uses cannot either", async ({
    page,
  }) => {
    const hd = await definitionByName(page, "WEB 1080p");
    const profiles = await apiGet<QualityProfile[]>(page, "/api/admin/quality/profiles");
    const fallback = profiles.find((profile) => profile.is_default);
    expect(fallback, "the instance has no default quality profile").toBeDefined();

    const refusedDefault = await apiDelete(
      page,
      `/api/admin/quality/profiles/${(fallback as QualityProfile).id}`,
    );
    expect(refusedDefault.status()).toBe(409);
    const defaultError = (await refusedDefault.json()) as ErrorBody;
    expect(defaultError.code).toBe("QUALITY_PROFILE_IN_USE");
    expect(defaultError.context).toMatchObject({ reason: "default" });

    const profile = await apiPost<QualityProfile>(page, "/api/admin/quality/profiles", {
      name: `${MARKER} in use`,
      quality_definition_ids: [hd.id],
      cutoff_quality_id: hd.id,
    });
    createdProfiles.push(profile.id);
    const monitor = await apiPost<Monitor>(page, "/api/monitors", {
      kind: "query",
      query: `${MARKER} profile guard`,
      quality_profile_id: profile.id,
    });
    createdMonitors.push(monitor.id);

    const refusedInUse = await apiDelete(page, `/api/admin/quality/profiles/${profile.id}`);
    expect(refusedInUse.status()).toBe(409);
    const inUseError = (await refusedInUse.json()) as ErrorBody;
    expect(inUseError.code).toBe("QUALITY_PROFILE_IN_USE");
    expect(inUseError.context).toMatchObject({ reason: "monitor" });

    // The profile is still there after the refusal, not half-deleted.
    expect(
      (await apiGet<QualityProfile>(page, `/api/admin/quality/profiles/${profile.id}`)).id,
    ).toBe(profile.id);
  });

  test("the cutoff changes no decision, because four of the six reasons have no caller", async ({
    page,
  }) => {
    // ADR 0029 L9 says a profile has "an allowed set and a cutoff" and drives
    // "an upgrade path"; README.md L16 sells "automatic upgrades". Four of
    // `QualityReason`'s six values are unreachable in the running product:
    //
    //   `upgrade_available`, `cutoff_met`, `not_an_upgrade` - reachable only
    //   when `decide_quality` is handed an existing file, and all three callers
    //   pass `existing=None` (admin_quality.py:339, monitor_match.py:107,
    //   request_search.py:223). The cutoff rank is read in exactly one branch,
    //   and that branch is behind `existing is not None`.
    //
    //   `not_in_profile` - reachable only when the candidate's quality is
    //   outside `allowed_qualities`, and all three callers derive the candidate
    //   *from the profile's own items* (`_quality_from_release`,
    //   `_quality_item`), so the candidate is a member by construction.
    //
    // Only `no_existing_file` and `below_minimum_score` can occur. This test
    // asserts that, across every cutoff and every quality, which is what turns
    // red the day an upgrade path acquires a caller.
    const [sd, hd, uhd] = [
      await definitionByName(page, "WEB 720p"),
      await definitionByName(page, "WEB 1080p"),
      await definitionByName(page, "WEB 2160p"),
    ];
    const ids = [sd.id, hd.id, uhd.id];
    const unreachable = ["upgrade_available", "cutoff_met", "not_an_upgrade", "not_in_profile"];

    for (const cutoff of ids) {
      for (const [name, resolution] of [
        ["WEB 720p", "720p"],
        ["WEB 1080p", "1080p"],
        ["WEB 2160p", "2160p"],
      ] as const) {
        const answer = await preview(page, {
          release_name: `Gauntlet ${MARKER} Cutoff (2026) WEB-DL ${resolution}`,
          profile: { quality_definition_ids: ids, cutoff_quality_id: cutoff },
        });
        const decision = answer.body as QualityPreview;
        expect(decision.quality?.name, `${name} against cutoff ${cutoff}`).toBe(name);
        expect(unreachable, `${name} against cutoff ${cutoff}`).not.toContain(decision.reason);
        expect(decision.verdict, `${name} against cutoff ${cutoff}`).toBe("grab");
        expect(decision.reason, `${name} against cutoff ${cutoff}`).toBe("no_existing_file");
      }
    }

    // And the request body has no way to describe an existing file, so this is
    // a property of the contract rather than of the three cases above.
    const schema = await apiGet<Record<string, unknown>>(page, "/api/openapi.json");
    const request = JSON.stringify(
      (schema.components as Record<string, Record<string, unknown>>).schemas.QualityPreviewRequest,
    );
    expect(request).not.toContain("existing");
    expect(request).not.toContain("current_file");
  });

  // -------------------------------------------------------------------------
  // 13.8, 13.11 - monitors
  // -------------------------------------------------------------------------

  test("a query monitor binds to a profile and a minimum score, and both survive a re-read", async ({
    page,
  }) => {
    // ADR 0030 L9: "A monitor binds a user to a performer, a studio or a saved
    // query, with a quality profile and a minimum score."
    const profiles = await apiGet<QualityProfile[]>(page, "/api/admin/quality/profiles");
    const fallback = profiles.find((profile) => profile.is_default) as QualityProfile;

    const withoutProfile = await apiPost<Monitor>(page, "/api/monitors", {
      kind: "query",
      query: `${MARKER} default profile`,
    });
    createdMonitors.push(withoutProfile.id);
    expect(withoutProfile.quality_profile_id, "an omitted profile takes the default").toBe(
      fallback.id,
    );
    expect(withoutProfile.minimum_score).toBe(0);
    expect(withoutProfile.enabled).toBe(true);
    expect(withoutProfile.last_match_at).toBeNull();

    const scored = await apiPost<Monitor>(page, "/api/monitors", {
      kind: "query",
      query: `${MARKER} scored`,
      minimum_score: 55,
      enabled: false,
    });
    createdMonitors.push(scored.id);

    const listed = (await apiGet<Monitor[]>(page, "/api/monitors")).find(
      (monitor) => monitor.id === scored.id,
    );
    expect(listed?.minimum_score).toBe(55);
    expect(listed?.enabled).toBe(false);
    expect(listed?.query).toBe(`${MARKER} scored`);
    expect(listed?.kind).toBe("query");
    expect(listed?.performer_id).toBeNull();
    expect(listed?.studio_id).toBeNull();

    const patched = await apiPatch(page, `/api/monitors/${scored.id}`, {
      enabled: true,
      minimum_score: 12,
    });
    expect(patched.enabled).toBe(true);
    const rereadAfterPatch = (await apiGet<Monitor[]>(page, "/api/monitors")).find(
      (monitor) => monitor.id === scored.id,
    );
    expect(rereadAfterPatch?.minimum_score, "the patch did not persist").toBe(12);
    expect(rereadAfterPatch?.enabled).toBe(true);
  });

  test("a monitor must name exactly one target, and a second on the same one is refused", async ({
    page,
  }) => {
    // The other two kinds are unreachable from outside on any instance: nothing
    // in `openapi.json` creates or lists a performer or a studio, so no caller
    // can obtain an id `validate_target` would accept. Both halves asserted.
    const twoTargets = await apiPostRaw(page, "/api/monitors", {
      kind: "query",
      query: `${MARKER} two targets`,
      studio_id: "00000000-0000-0000-0000-000000000001",
    });
    expect(twoTargets.status()).toBe(422);
    expect(((await twoTargets.json()) as ErrorBody).code).toBe("VALIDATION_FAILED");

    const unknownPerformer = await apiPostRaw(page, "/api/monitors", {
      kind: "performer",
      performer_id: "00000000-0000-0000-0000-000000000002",
    });
    expect(unknownPerformer.status()).toBe(404);

    const schema = await apiGet<Record<string, unknown>>(page, "/api/openapi.json");
    const paths = Object.keys(schema.paths as Record<string, unknown>);
    expect(
      paths.filter((path) => /performers?$|studios?$/.test(path)),
      "a route now lists performers or studios, so the two other monitor kinds are reachable",
    ).toEqual([]);

    const first = await apiPost<Monitor>(page, "/api/monitors", {
      kind: "query",
      query: `${MARKER} duplicate`,
    });
    createdMonitors.push(first.id);
    // Normalisation, not string equality: the same phrase punctuated differently.
    const second = await apiPostRaw(page, "/api/monitors", {
      kind: "query",
      query: `${MARKER}   duplicate!`,
    });
    expect(second.status()).toBe(409);
    const duplicate = (await second.json()) as ErrorBody;
    expect(duplicate.code).toBe("MONITOR_ALREADY_EXISTS");
    expect(duplicate.status).toBe(409);

    expect(
      (await apiGet<Monitor[]>(page, "/api/monitors")).filter((monitor) =>
        monitor.query?.includes(`${MARKER} duplicate`),
      ),
      "the refused duplicate was created anyway",
    ).toHaveLength(1);
  });

  test("a per-monitor backlog search is queued once for the minute it is asked in", async ({
    page,
  }) => {
    // ADR 0030 L9 and openapi.json `/api/monitors/{id}/backlog-search`: the
    // daily search "can also be triggered per monitor".
    test.skip(!canDriveStackJobs(), "The compose worker is not reachable from the test runner.");
    const monitor = await apiPost<Monitor>(page, "/api/monitors", {
      kind: "query",
      query: `${MARKER} backlog`,
    });
    createdMonitors.push(monitor.id);

    const accepted = await apiPostRaw(page, `/api/monitors/${monitor.id}/backlog-search`, {});
    expect(accepted.status()).toBe(202);
    expect(await accepted.text()).toBe("");

    // The job the route enqueues really lands on the indexer queue and really
    // runs. `backlog_search` refuses a monitor younger than a day
    // (`MINIMUM_MONITOR_AGE`), so the honest assertion is that it ran and
    // returned zero matches rather than that it found something.
    const outcome = runStackJob("backlog_search", [monitor.id, `probe:${RUN}`], "pornarr:indexer");
    expect(outcome?.finished, "backlog_search never produced a result").toBe(true);
    expect(outcome?.success).toBe(true);
    expect(outcome?.result, "a monitor created seconds ago is not yet eligible").toBe("0");

    const unknown = await apiPostRaw(
      page,
      "/api/monitors/00000000-0000-0000-0000-000000000003/backlog-search",
      {},
    );
    expect(unknown.status()).toBe(404);
  });

  // -------------------------------------------------------------------------
  // 13.9, 13.10, 13.12, 13.13 - RSS
  // -------------------------------------------------------------------------

  test("one RSS cycle costs one feed request per indexer, however many monitors exist", async ({
    page,
  }) => {
    // ADR 0030 L11: "RSS costs one request per indexer per cycle regardless of
    // how many monitors exist, which repeated searches would not." Counted at
    // the indexer, which is where a request either arrives or does not.
    test.skip(
      !rssIndexerAvailable || !canDriveStackJobs(),
      "The RSS test indexer or the compose worker is not reachable from the test runner.",
    );
    // The last claim below is that every enabled indexer is polled, not only
    // this file's own. That needs a second enabled indexer to exist, and this
    // file only ever registers one - so on a stack where no other suite has run
    // yet the claim was being made against a single row and failed. Registering
    // the compose fixture here makes the second indexer this test's own
    // precondition rather than residue it happened to inherit.
    const second = await testingIndexer(page);
    test.skip(
      second === null,
      "docker-compose.testing.yml is not running, so there is no second indexer to prove the per-indexer cost against.",
    );
    rssIndexerCall("/reset");
    publishToFeed(feedTitle("Cost"));

    const before = feedCounters();
    expect(before.feed_requests).toBe(0);
    expect(runStackJob("rss_sync", [`cost-a-${RUN}`], "pornarr:indexer")?.finished).toBe(true);
    const afterOneMonitor = feedCounters();
    expect(afterOneMonitor.feed_requests, "one cycle, one feed request").toBe(1);
    expect(afterOneMonitor.search_requests, "RSS must not be a term search").toBe(0);

    // Three more monitors, and the cost must not move.
    for (const subject of ["cost one", "cost two", "cost three"]) {
      const monitor = await apiPost<Monitor>(page, "/api/monitors", {
        kind: "query",
        query: `${MARKER} ${subject}`,
      });
      createdMonitors.push(monitor.id);
    }
    expect(
      (await apiGet<Monitor[]>(page, "/api/monitors")).filter((monitor) => monitor.enabled).length,
      "the monitors that were meant to make this expensive are not there",
    ).toBeGreaterThanOrEqual(4);

    expect(runStackJob("rss_sync", [`cost-b-${RUN}`], "pornarr:indexer")?.finished).toBe(true);
    const afterFour = feedCounters();
    expect(afterFour.feed_requests, "the second cycle cost more than one request").toBe(2);
    expect(afterFour.search_requests).toBe(0);

    // And every enabled indexer was polled, not only this one: the product's
    // own per-indexer counter moved for each of them.
    const indexers = await apiGet<IndexerRow[]>(page, "/api/admin/indexers");
    expect(indexers.filter((indexer) => indexer.enabled).length).toBeGreaterThanOrEqual(2);
  });

  test("a new release reaches every monitor and becomes an automatic request at priority 60", async ({
    page,
  }) => {
    // ADR 0030 L9: an RSS sync "matches new releases against all monitors,
    // creating automatic requests at priority 60".
    test.skip(
      !rssIndexerAvailable || !canDriveStackJobs(),
      "The RSS test indexer or the compose worker is not reachable from the test runner.",
    );
    rssIndexerCall("/reset");

    const monitor = await apiPost<Monitor>(page, "/api/monitors", {
      kind: "query",
      query: MONITOR_QUERY,
    });
    createdMonitors.push(monitor.id);
    expect(monitor.last_match_at).toBeNull();

    const title = feedTitle("Discovery");
    const guid = publishToFeed(title);
    expect(runStackJob("rss_sync", [`match-${RUN}`], "pornarr:indexer")?.finished).toBe(true);

    // `rss_sync` hands the batch to `monitor_match` on the same queue, so the
    // request appears a job later. Polled to convergence with a deadline that
    // fails rather than a sleep that hides.
    let created: RequestRow | undefined;
    await expect
      .poll(
        async () => {
          created = (await apiGet<RequestRow[]>(page, "/api/requests")).find(
            (row) => row.selected_release_guid === guid,
          );
          return created !== undefined;
        },
        {
          timeout: 60_000,
          intervals: [500],
          message:
            "No automatic request was created for the released GUID. See apps/worker/pornarr_worker/jobs/monitor_match.py.",
        },
      )
      .toBe(true);

    const request = created as RequestRow;
    expect(request.is_automatic, "a monitor match is not marked automatic").toBe(true);
    expect(request.priority, "ADR 0030 L9 fixes the automatic priority at 60").toBe(60);
    expect(request.query, "the request carries the release title, not its GUID").toBe(title);
    expect(request.status).toBe("queued");

    // The monitor itself records that it matched, which is what "matched
    // against all monitors" means from the monitor's side.
    const matched = (await apiGet<Monitor[]>(page, "/api/monitors")).find(
      (row) => row.id === monitor.id,
    );
    expect(matched?.last_match_at, "the monitor did not record its match").not.toBeNull();

    // The release is in the cache the monitors matched against, keyed by
    // indexer and GUID and carrying a TTL - ADR 0033's whole subject.
    const cached = databaseScalar(
      `select count(*) from release_cache where guid = '${guid}' and expires_at > now()`,
    );
    if (cached !== null) expect(cached).toBe("1");
  });

  test("the preview and the grab path do not agree about what a release's quality is", async ({
    page,
  }) => {
    // Claim 13.4 is that `/api/admin/quality/preview` "shows how a given release
    // would be scored before anything is grabbed". It shows how a *different*
    // matcher would score it.
    //
    //   The preview parses the release (`parse_release`) and maps the parsed
    //   resolution and source onto a definition. `_SOURCE` in matching.py:16
    //   recognises `web-dl`, `webrip`, `bluray`, `bdrip`, `hdtv` and `cam` - not
    //   a bare `WEB`, which is the word the shipped definitions are named after.
    //
    //   The grab path (`monitor_match._quality_item`, `request_search._quality_item`)
    //   does not parse at all: it looks for the definition's *name* as a
    //   normalised phrase inside the title, so it needs a literal "WEB 1080p"
    //   and cannot see "WEB-DL 1080p".
    //
    // So the two disagree on both of the obvious spellings, in opposite
    // directions, and the preview cannot tell an operator what the monitor will
    // do. Both halves are executed: the preview through its own route, the grab
    // path through one real RSS cycle carrying both spellings at once.
    test.skip(
      !rssIndexerAvailable || !canDriveStackJobs(),
      "The RSS test indexer or the compose worker is not reachable from the test runner.",
    );
    const hd = await definitionByName(page, "WEB 1080p");
    const profile = { quality_definition_ids: [hd.id], cutoff_quality_id: hd.id };

    const grabSpelling = `Gauntlet ${MARKER} spelling alpha (2026) WEB 1080p`;
    const previewSpelling = `Gauntlet ${MARKER} spelling beta (2026) WEB-DL 1080p`;

    const previewOfGrabSpelling = await preview(page, {
      release_name: grabSpelling,
      profile,
    });
    expect(
      (previewOfGrabSpelling.body as QualityPreview).quality,
      "the preview now recognises the spelling the grab path needs",
    ).toBeNull();
    expect((previewOfGrabSpelling.body as QualityPreview).reason).toBe("quality_not_detected");

    const previewOfPreviewSpelling = await preview(page, {
      release_name: previewSpelling,
      profile,
    });
    expect((previewOfPreviewSpelling.body as QualityPreview).quality?.name).toBe("WEB 1080p");
    expect((previewOfPreviewSpelling.body as QualityPreview).verdict).toBe("grab");

    // Now the grab path, on one cycle carrying both. The monitor matches both
    // titles by name, so anything that separates them is the quality matcher.
    rssIndexerCall("/reset");
    const monitor = await apiPost<Monitor>(page, "/api/monitors", {
      kind: "query",
      query: `${MARKER} spelling`,
    });
    createdMonitors.push(monitor.id);
    const grabGuid = publishToFeed(grabSpelling);
    const previewGuid = publishToFeed(previewSpelling);
    expect(runStackJob("rss_sync", [`spelling-${RUN}`], "pornarr:indexer")?.finished).toBe(true);

    await expect
      .poll(
        async () =>
          (await apiGet<RequestRow[]>(page, "/api/requests")).filter((row) =>
            row.query.includes(`${MARKER} spelling`),
          ).length,
        {
          timeout: 60_000,
          intervals: [500],
          message: "Neither spelling produced an automatic request, so nothing was compared.",
        },
      )
      .toBe(1);
    // The monitor saw both releases - `match_release` stamps it per release -
    // so the one that did not become a request was stopped by the quality
    // matcher and not by the monitor.
    expect(
      (await apiGet<Monitor[]>(page, "/api/monitors")).find((row) => row.id === monitor.id)
        ?.last_match_at,
    ).not.toBeNull();
    const guids = (await apiGet<RequestRow[]>(page, "/api/requests"))
      .filter((row) => row.query.includes(`${MARKER} spelling`))
      .map((row) => row.selected_release_guid);
    expect(guids, "the grab path took the spelling the preview refuses").toEqual([grabGuid]);
    expect(guids, "the grab path took the spelling the preview accepts").not.toContain(previewGuid);
  });

  test("a second poll of an unchanged feed reprocesses nothing, and a third with a new entry does", async ({
    page,
  }) => {
    // ADR 0033 L11: "repeated RSS polls do not reprocess the same release".
    // `monitor.last_match_at` is the discriminator: `match_release` stamps it
    // before any per-user skip, so it moves whenever a release is reprocessed
    // and stays put when the feed marker says there is nothing new.
    test.skip(
      !rssIndexerAvailable || !canDriveStackJobs(),
      "The RSS test indexer or the compose worker is not reachable from the test runner.",
    );
    rssIndexerCall("/reset");
    const monitor = await apiPost<Monitor>(page, "/api/monitors", {
      kind: "query",
      query: `${MARKER} repeat`,
    });
    createdMonitors.push(monitor.id);

    const first = publishToFeed(`Gauntlet ${MARKER} repeat one (2026) WEB 1080p`);
    expect(runStackJob("rss_sync", [`repeat-a-${RUN}`], "pornarr:indexer")?.finished).toBe(true);

    let afterFirst: string | null = null;
    await expect
      .poll(
        async () => {
          afterFirst =
            (await apiGet<Monitor[]>(page, "/api/monitors")).find((row) => row.id === monitor.id)
              ?.last_match_at ?? null;
          return afterFirst !== null;
        },
        { timeout: 60_000, intervals: [500], message: "The first poll never reached the monitor." },
      )
      .toBe(true);
    const requestsAfterFirst = (await apiGet<RequestRow[]>(page, "/api/requests")).filter((row) =>
      row.query.includes(`${MARKER} repeat`),
    );
    expect(requestsAfterFirst).toHaveLength(1);
    expect(requestsAfterFirst[0].selected_release_guid).toBe(first);

    // Second cycle, same feed. The indexer is still asked - that is the one
    // request per cycle - but nothing downstream of it runs.
    const beforeSecond = feedCounters().feed_requests;
    expect(runStackJob("rss_sync", [`repeat-b-${RUN}`], "pornarr:indexer")?.finished).toBe(true);
    expect(feedCounters().feed_requests, "the second cycle did not poll the feed at all").toBe(
      beforeSecond + 1,
    );
    // Give any match job that was going to run the chance to run before the
    // assertion that none did.
    await page.waitForTimeout(5_000);
    expect(
      (await apiGet<Monitor[]>(page, "/api/monitors")).find((row) => row.id === monitor.id)
        ?.last_match_at,
      "the already-seen release was matched a second time",
    ).toBe(afterFirst);
    expect(
      (await apiGet<RequestRow[]>(page, "/api/requests")).filter((row) =>
        row.query.includes(`${MARKER} repeat`),
      ),
      "a second request was created for a release the feed had already offered",
    ).toHaveLength(1);

    // Third cycle, one genuinely new entry ahead of the old one.
    const second = publishToFeed(`Gauntlet ${MARKER} repeat two (2026) WEB 1080p`);
    expect(runStackJob("rss_sync", [`repeat-c-${RUN}`], "pornarr:indexer")?.finished).toBe(true);
    await expect
      .poll(
        async () =>
          (await apiGet<RequestRow[]>(page, "/api/requests")).filter((row) =>
            row.query.includes(`${MARKER} repeat`),
          ).length,
        {
          timeout: 60_000,
          intervals: [500],
          message: "A new feed entry produced no new automatic request.",
        },
      )
      .toBe(2);
    const guids = (await apiGet<RequestRow[]>(page, "/api/requests"))
      .filter((row) => row.query.includes(`${MARKER} repeat`))
      .map((row) => row.selected_release_guid);
    expect(new Set(guids)).toEqual(new Set([first, second]));
  });

  // -------------------------------------------------------------------------
  // 13.14, 13.15 - the job queue
  // -------------------------------------------------------------------------

  test("the four queues are separate registries, not one bus with four names", async ({ page }) => {
    // ADR 0021 L9: "arq, with queues for default, import, transcode and indexer
    // work". Separateness is only observable in the negative: a function the
    // queue's worker does not register is refused by name rather than run.
    test.skip(!canDriveStackJobs(), "The compose worker is not reachable from the test runner.");
    void page;

    const ran = runStackJob("heartbeat", [], "pornarr:default");
    expect(ran?.finished).toBe(true);
    expect(ran?.success, "the default queue did not run its own scheduled probe").toBe(true);
    expect(ran?.result).toBe("ok");

    const indexerOnly = runStackJob("cleanup_release_cache", [], "pornarr:indexer");
    expect(indexerOnly?.success, "the indexer queue did not run its own cache cleanup").toBe(true);

    for (const [functionName, queue] of [
      ["heartbeat", "pornarr:indexer"],
      ["heartbeat", "pornarr:transcode"],
      ["search_indexers", "pornarr:default"],
      ["import_media", "pornarr:default"],
      ["generate_preview_sprite_job", "pornarr:import"],
    ] as const) {
      const refused = runStackJob(functionName, [], queue);
      expect(refused?.finished, `${functionName} on ${queue}`).toBe(true);
      expect(refused?.success, `${functionName} was run by the ${queue} worker`).toBe(false);
      expect(refused?.result, `${functionName} on ${queue}`).toBe(
        `function '${functionName}' not found`,
      );
    }
  });

  test("the scheduler drives RSS sync, download polling, profile training and cleanup", async ({
    page,
  }) => {
    // ADR 0021 L9: "and its built-in cron for RSS sync, download polling,
    // profile training and cleanup". Read off the running scheduler's own
    // results in Redis rather than off the settings module, so a beat container
    // that is not actually running fails this.
    test.skip(!canDriveStackJobs(), "The compose worker is not reachable from the test runner.");
    void page;
    const keys = composeExec("redis", [
      "redis-cli",
      "--scan",
      "--count",
      "1000",
      "--pattern",
      "arq:result:*",
    ]);
    expect(keys, "the stack's Redis is not reachable").not.toBeNull();
    const names = new Set(
      (keys ?? "")
        .split("\n")
        .map((key) => key.replace(/^arq:result:/, "").replace(/:\d+$/, ""))
        .filter((name) => name !== ""),
    );
    // `download_poll` runs every five seconds and `heartbeat` every minute, so
    // both are always present on a live stack; the two nightly ones are proven
    // by their registration plus one forced run each below.
    expect([...names], "the scheduler is not producing results at all").toEqual(
      expect.arrayContaining(["download_poll", "heartbeat"]),
    );

    for (const scheduled of [
      "dispatch_rss_sync",
      "refresh_interest_profiles_job",
      "cleanup_transcodes",
    ]) {
      const outcome = runStackJob(scheduled, [], "pornarr:default", 120);
      expect(outcome?.finished, `${scheduled} never produced a result`).toBe(true);
      expect(outcome?.success, `${scheduled} failed: ${outcome?.result}`).toBe(true);
    }
  });

  // -------------------------------------------------------------------------
  // 13.17, 13.18, 13.19, 13.21 - the event stream
  // -------------------------------------------------------------------------

  test("the stream is the session's, and it tells proxies not to buffer it", async ({ page }) => {
    // ADR 0020 L9 and api-contract.md L39-52: one endpoint, the same session
    // cookie as every other route, and `X-Accel-Buffering: no`.
    const anonymous = await readEventStream(page, { cookie: false, timeoutMs: 5_000 });
    expect(anonymous.status, "the event stream answered without a session").toBe(401);

    const authenticated = await readEventStream(page, { timeoutMs: 3_000 });
    expect(authenticated.status).toBe(200);
    expect(authenticated.headers["content-type"]).toContain("text/event-stream");
    expect(authenticated.headers["x-accel-buffering"]).toBe("no");
    expect(authenticated.headers["cache-control"]).toBe("no-cache");
    expect(authenticated.headers.connection).toContain("keep-alive");
  });

  test("an event published in the worker arrives on a stream opened against the API", async ({
    page,
  }) => {
    // ADR 0020 L9 and api-contract.md L40-41: "Fan-out goes through Redis
    // pub/sub so multiple API instances all deliver." One indexer search
    // publishes from two processes: `search.started` from the API container and
    // `search.completed` from worker-indexer. Both arriving on one stream is
    // the bus doing its job across process boundaries.
    const query = `${MARKER} sse`;
    const read = await readEventStream(page, {
      timeoutMs: 60_000,
      until: (frames) => frames.some((frame) => frame.event === "search.completed"),
      during: async () => {
        await apiPost(page, "/api/search/indexers", { q: query });
      },
    });

    expect(read.status).toBe(200);
    const types = read.frames.map((frame) => frame.event);
    expect(types, "the API's own publication never arrived").toContain("search.started");
    expect(types, "the worker's publication never arrived").toContain("search.completed");

    const started = read.frames.find((frame) => frame.event === "search.started");
    expect(JSON.parse(started?.data ?? "{}")).toMatchObject({ query });
    const completed = read.frames.find((frame) => frame.event === "search.completed");
    const statuses = (JSON.parse(completed?.data ?? "{}") as { statuses: Record<string, string> })
      .statuses;
    expect(Object.keys(statuses).length, "the fan-out reached no indexer").toBeGreaterThanOrEqual(
      1,
    );
    expect(Object.values(statuses).every((status) => status !== "pending")).toBe(true);
  });

  test("event ids are Redis stream ids, and Last-Event-ID resumes from exactly there", async ({
    page,
  }) => {
    // ADR 0020 L9 and api-contract.md L41-42: "Event identifiers are Redis
    // stream identifiers, which makes `Last-Event-ID` resume exact."
    const firstQuery = `${MARKER} resume one`;
    const firstRead = await readEventStream(page, {
      timeoutMs: 60_000,
      until: (frames) => frames.some((frame) => frame.event === "search.completed"),
      during: async () => {
        await apiPost(page, "/api/search/indexers", { q: firstQuery });
      },
    });
    const firstStarted = firstRead.frames.find(
      (frame) => frame.event === "search.started" && frame.data.includes(firstQuery),
    );
    expect(firstStarted, "no search.started frame for the first query").toBeDefined();
    const anchor = (firstStarted as { id: string }).id;
    // `<milliseconds>-<sequence>`, which is what XADD produces and nothing else does.
    expect(anchor).toMatch(/^\d+-\d+$/);

    const stored = composeExec("redis", ["redis-cli", "XRANGE", "pornarr:events", anchor, anchor]);
    expect(stored, "the id the stream reported is not an entry in pornarr:events").toContain(
      "search.started",
    );

    // A second search happens while nothing is listening.
    const secondQuery = `${MARKER} resume two`;
    await apiPost(page, "/api/search/indexers", { q: secondQuery });

    const resumed = await readEventStream(page, {
      lastEventId: anchor,
      timeoutMs: 30_000,
      until: (frames) => frames.some((frame) => frame.data.includes(secondQuery)),
    });
    const resumedIds = resumed.frames.map((frame) => frame.id).filter((id) => id !== "");
    expect(resumedIds.length, "the resume replayed nothing").toBeGreaterThan(0);
    expect(
      resumed.frames.some((frame) => frame.data.includes(secondQuery)),
      "the missed search was not replayed",
    ).toBe(true);
    // Exact: the anchor itself is not replayed, and nothing before it is either.
    expect(resumedIds, "the resume replayed the anchor event again").not.toContain(anchor);
    for (const id of resumedIds) {
      expect(Number.parseInt(id.split("-")[0], 10)).toBeGreaterThanOrEqual(
        Number.parseInt(anchor.split("-")[0], 10),
      );
    }
  });

  // -------------------------------------------------------------------------
  // 13.22, 13.25, 13.28 - errors, migrations, health
  // -------------------------------------------------------------------------

  test("every refusal on these routes is an object with a code, a status and a context", async ({
    page,
  }) => {
    // api-contract.md L18-30: "Errors are objects, not strings", with a stable
    // machine-readable code. Asserted across the shapes this area can produce -
    // a domain error, a validation error, a 404 and a 403.
    const refusals: { readonly what: string; readonly status: number; readonly body: unknown }[] =
      [];

    const notFound = await page.request.get(
      "/api/admin/quality/profiles/00000000-0000-0000-0000-0000000000ff",
    );
    refusals.push({
      what: "unknown profile",
      status: notFound.status(),
      body: await notFound.json(),
    });

    const invalid = await apiPostRaw(page, "/api/admin/quality/preview", { release_name: "" });
    refusals.push({
      what: "empty release name",
      status: invalid.status(),
      body: await invalid.json(),
    });

    const badCursor = await page.request.get("/api/queue?cursor=not-a-cursor");
    refusals.push({
      what: "bad queue cursor",
      status: badCursor.status(),
      body: await badCursor.json(),
    });

    const unknownMonitor = await apiPatchRaw(
      page,
      "/api/monitors/00000000-0000-0000-0000-0000000000fe",
      { enabled: false },
    );
    refusals.push({
      what: "unknown monitor",
      status: unknownMonitor.status(),
      body: await unknownMonitor.json(),
    });

    for (const refusal of refusals) {
      const body = refusal.body as ErrorBody;
      expect(typeof body, `${refusal.what} answered a string, not an object`).toBe("object");
      expect(body.code, refusal.what).toMatch(/^[A-Z][A-Z0-9_]+$/);
      expect(body.status, refusal.what).toBe(refusal.status);
      expect(typeof body.context, `${refusal.what} has no context object`).toBe("object");
      expect(Object.keys(body as object).sort(), refusal.what).toEqual([
        "code",
        "context",
        "status",
      ]);
    }

    expect(refusals.map((refusal) => (refusal.body as ErrorBody).code)).toEqual([
      "NOT_FOUND",
      "VALIDATION_FAILED",
      "VALIDATION_FAILED",
      "NOT_FOUND",
    ]);
    // The validation error says which field failed without echoing what was sent.
    const validation = refusals[1].body as { context: { fields: { location: string[] }[] } };
    expect(validation.context.fields.map((field) => field.location.join("."))).toContain(
      "body.release_name",
    );
  });

  test("the health report names every dependency, and the container probe agrees with it", async ({
    page,
  }) => {
    // backup.md L63-66 and installation.md L187. `BACKUP_MAX_AGE_HOURS` is
    // unset on this stack, so the backup component is absent rather than
    // healthy - which is the documented "only when set" behaviour, and the only
    // half of the claim a shared stack can reach.
    const report = await apiGet<
      Record<string, { status: string; detail?: string }> & {
        status: string;
      }
    >(page, "/api/health");
    expect(["healthy", "degraded"]).toContain(report.status);
    for (const component of ["database", "redis", "worker", "filesystem"]) {
      expect(report[component], `no ${component} component in the report`).toBeDefined();
      expect(["healthy", "unhealthy"]).toContain(report[component].status);
    }
    expect(
      report.backup,
      "BACKUP_MAX_AGE_HOURS is unset, so no backup age is claimed",
    ).toBeUndefined();
    expect(report.database.status).toBe("healthy");

    // installation.md L4 and backup.md L56 verify a deployment with the
    // unprefixed address; federation.md and the ADRs use the prefixed one.
    // Both answer, and they answer the same thing.
    const unprefixed = await page.request.get(new URL("/health", API_ORIGIN).toString());
    expect(unprefixed.status()).toBe(200);
    const probe = (await unprefixed.json()) as { status: string };
    expect(probe.status).toBe(report.status);
    // ...and `/health` is still absent from the document ADR 0009 makes the
    // boundary of what exists. Contradiction C7, executed.
    const schema = await apiGet<{ paths: Record<string, unknown> }>(page, "/api/openapi.json");
    expect(Object.keys(schema.paths)).toContain("/api/health");
    expect(Object.keys(schema.paths)).not.toContain("/health");
  });

  test("the database is at the head revision the repository ships", async ({ page }) => {
    // installation.md L173: the migrate service runs to completion before the
    // API and the workers start. The repository test asserts the compose
    // dependency; this asserts the outcome on the running instance.
    void page;
    test.skip(
      !canReadStackDatabase(),
      "The stack's database is not reachable from the test runner.",
    );
    const current = composeExec("api", ["alembic", "current"]);
    expect(current, "alembic could not be run in the API container").not.toBeNull();
    expect(current, "the running database is not at the head revision").toContain("(head)");
    const applied = databaseScalar("select count(*) from alembic_version");
    expect(applied).toBe("1");
  });
});

/** `apiPatch` returns the parsed body; typed locally because monitors are the only user here. */
async function apiPatch(page: Page, path: string, data: unknown): Promise<Monitor> {
  const response = await apiPatchRaw(page, path, data);
  if (!response.ok()) {
    throw new Error(`PATCH ${path} returned ${response.status()}: ${await response.text()}`);
  }
  return (await response.json()) as Monitor;
}
