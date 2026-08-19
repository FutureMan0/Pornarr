/**
 * The search half of the nine flows: running a search, the local results it
 * finds in the library, the external results indexers return, and grabbing one
 * of them.
 *
 * Two things this file refuses to do. It never asserts a status code and calls
 * a behaviour proven - a search that answers 202 proves the route exists and
 * nothing else. And it never waits out a timeout with a sleep: every wait here
 * drains the thing it is waiting for to a terminal state and fails loudly when
 * it does not arrive.
 *
 * The failure paths are driven with real indexers rather than mocks. A Torznab
 * adapter pointed at the fake indexer's `/torrent` endpoint receives a real
 * bencoded torrent where XML was expected; one pointed at the API's own
 * `/api/library` receives a real 401. Both are the product's own client code
 * meeting a real HTTP response, which is the only way the classification, the
 * circuit breaker and the screen can all be judged at once.
 */
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import {
  type APIResponse,
  type Browser,
  type Locator,
  type Page,
  expect,
  test,
} from "@playwright/test";
import {
  CONTROL_INDEXER_EMPTY_TERM,
  TESTING_RELEASE_TITLE,
  apiDelete,
  apiGet,
  apiPost,
  apiPostRaw,
  apiPut,
  collectEvents,
  collectedEvents,
  controlIndexer,
  expectNoAccessibilityViolations,
  libraryItems,
  loginAsAdmin,
  queueJobs,
  seedLibraryMedia,
  stopCollectingEvents,
  testingDownloadClient,
  testingIndexer,
} from "./helpers";

/** Verifying a two-hundred-megabyte fixture is the slow part, not the network. */
const ACQUISITION_TIMEOUT_MILLISECONDS = 240_000;

const NO_FIXTURE =
  "The suite cannot write a fixture into a configured root folder: either ffmpeg is missing or the stack's data volume is not reachable from here.";

const NO_INDEXER =
  "The fan-out needs the compose testing indexer: start it with " +
  "docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.testing.yml up -d";

const NO_DOCKER =
  "Proving which parameters reach an indexer needs the fake indexer's own request log, which is only readable through `docker compose logs`.";

/** PRODUCT.md L35-36: the core loop succeeds on first attempt in under thirty seconds. */
const CORE_LOOP_BUDGET_MILLISECONDS = 30_000;

const COMPOSE_PROJECT = process.env.E2E_COMPOSE_PROJECT ?? "pornarr_gauntlet";

type Estimate = {
  readonly low_seconds: number | null;
  readonly high_seconds: number | null;
  readonly confidence: string;
};

type ReleaseMatch = {
  readonly kind: "new" | "present" | "upgrade";
  readonly media_id: string | null;
  readonly score: number | null;
  readonly breakdown: Record<string, number>;
};

type ExternalItem = {
  readonly id: string;
  readonly guid: string;
  readonly title: string;
  readonly indexer_id: string;
  readonly indexer_name: string;
  readonly protocol: string;
  readonly quality: string | null;
  readonly size: number | null;
  readonly published_at: string | null;
  readonly seeders: number | null;
  readonly estimate: Estimate;
  readonly match: ReleaseMatch;
};

type IndexerSearch = {
  readonly id: string;
  readonly query: string;
  readonly statuses: Record<string, string>;
  readonly cancelled: boolean;
  readonly items: ExternalItem[];
};

type LocalItem = {
  readonly id: string;
  readonly title: string;
  readonly studio: string | null;
  readonly quality: string | null;
  readonly resolution: string | null;
  readonly relevance: number;
};

type LocalResponse = { readonly items: LocalItem[]; readonly next_cursor: string | null };

type FacetValue = { readonly value: string; readonly count: number };

type Facets = {
  readonly studio: FacetValue[];
  readonly resolution: FacetValue[];
  readonly duration: FacetValue[];
  readonly rating: FacetValue[];
  readonly tag: FacetValue[];
  readonly matched: number;
  readonly total: number;
  readonly capped: boolean;
};

type ConfiguredIndexer = {
  readonly id: string;
  readonly name: string;
  readonly base_url: string;
  readonly enabled: boolean;
  readonly priority: number;
  readonly search_categories: string[];
  readonly health: string;
  readonly health_reason: string | null;
  readonly last_error: string | null;
  readonly stats: { readonly queries: number; readonly failures: number };
};

/**
 * A term no cached release title can contain.
 *
 * `search_indexers` now composes its targets as a union
 * (`search_targets`, `apps/worker/pornarr_worker/search.py`): an indexer the
 * cache can answer for is served from cache, and every other configured indexer
 * is still queried live. That union is asserted head-on by `a cached release is
 * served from cache without removing the other indexers from the fan-out`. This
 * helper exists for the other direction - every assertion about what an indexer
 * was actually *asked* has to start from a term the cache cannot claim, or it
 * would be measuring a query that never left the process.
 */
function uncachedQuery(): string {
  return `gauntlet ${randomUUID().slice(0, 8)}`;
}

/**
 * Start an indexer search and drain it: every reporting indexer has left
 * `pending`, or the test fails saying so. Radarr's `WaitForCompletion` rather
 * than a fixed sleep - see `IntegrationTestBase.cs:131-138`.
 */
async function drainIndexerSearch(page: Page, query: string, filters = ""): Promise<IndexerSearch> {
  const started = await apiPostRaw(page, "/api/search/indexers", { q: query });
  expect(started.status(), await started.text()).toBe(202);
  const { id } = (await started.json()) as { id: string };
  expect(id).toMatch(/^[0-9a-f-]{36}$/);

  let latest: IndexerSearch | null = null;
  await expect
    .poll(
      async () => {
        latest = await apiGet<IndexerSearch>(
          page,
          `/api/search/indexers/${id}${filters === "" ? "" : `?${filters}`}`,
        );
        const statuses = Object.values(latest.statuses);
        return statuses.length > 0 && statuses.every((status) => status !== "pending");
      },
      {
        timeout: 60_000,
        intervals: [500],
        message: `The indexer search for "${query}" never left "pending". Check worker-indexer.`,
      },
    )
    .toBe(true);
  if (latest === null) throw new Error("unreachable: the poll above returned true");
  return latest;
}

async function configuredIndexers(page: Page): Promise<ConfiguredIndexer[]> {
  return apiGet<ConfiguredIndexer[]>(page, "/api/admin/indexers");
}

async function indexerById(page: Page, id: string): Promise<ConfiguredIndexer> {
  const found = (await configuredIndexers(page)).find((indexer) => indexer.id === id);
  if (found === undefined) throw new Error(`Indexer ${id} is no longer configured.`);
  return found;
}

/**
 * Create an indexer that will fail in a chosen way, and hand it to `use`.
 *
 * The circuit breaker counts failures in Redis against the indexer's id, so a
 * fresh row per test is what keeps one failure test out of the next one's
 * arithmetic. Deleting it again matters as much: an enabled indexer joins every
 * other builder's search on this one shared stack.
 */
async function withIndexer(
  page: Page,
  body: Record<string, unknown>,
  use: (indexer: ConfiguredIndexer) => Promise<void>,
): Promise<void> {
  const name = `E2E ${DISPOSABLE_PREFIX}${randomUUID().slice(0, 8)}`;
  const created = await apiPost<ConfiguredIndexer>(page, "/api/admin/indexers", { ...body, name });
  try {
    await use(created);
  } finally {
    await apiDelete(page, `/api/admin/indexers/${created.id}`);
  }
}

/**
 * Every request line the fake indexer has served, or null when docker is out of
 * reach. Its own access log is the only place that records which query string
 * Pornarr actually sent, which is what "restricts what that indexer is asked
 * for" has to be measured against.
 */
function fakeIndexerRequests(): string[] | null {
  try {
    const output = execFileSync(
      "docker",
      ["compose", "-p", COMPOSE_PROJECT, "logs", "--no-color", "--no-log-prefix", "fake-indexer"],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] },
    );
    return output.split("\n").filter((line) => line.includes('"GET '));
  } catch {
    return null;
  }
}

/**
 * The entry the screen shows for one indexer, waited for and reported honestly.
 *
 * An indexer that returned no releases is labelled with the first eight
 * characters of its id rather than its name, because the search response carries
 * ids only - see BUILD.md defect 5. A missing list is dumped rather than left as
 * "element not found", which says nothing about which of the region's several
 * branches rendered instead.
 */
async function indexerStatusEntry(page: Page, indexerId: string): Promise<Locator> {
  const external = page.getByRole("region", { name: "From indexers" });
  const entry = external
    .getByRole("list", { name: "Indexer search status" })
    .getByRole("listitem")
    .filter({ hasText: indexerId.slice(0, 8) });
  try {
    await expect.poll(async () => entry.count(), { timeout: 60_000, intervals: [1_000] }).toBe(1);
  } catch {
    throw new Error(
      `The indexer status list never named ${indexerId.slice(0, 8)}. The region reads:\n${await external.innerText()}`,
    );
  }
  return entry;
}

/**
 * How a throwaway indexer is recognised. A test that times out never reaches its
 * own `finally`, and an enabled indexer left behind joins every other builder's
 * search on this shared stack - which is exactly how a stale one pointed at
 * `/torrent` once made the category assertions read another indexer's request.
 */
const DISPOSABLE_PREFIX = "disposable-";

/**
 * The row for one release, found by the title cell's own tooltip.
 *
 * "The row that contains this title" is not specific enough any more. A search
 * reaches every configured indexer now (`search_targets`), and the control
 * indexer answers by echoing the query back inside its release title - so a
 * search for one title can produce a second row that merely quotes it, and a
 * substring filter matches both. The title cell carries `title={item.title}`
 * exactly (`search-route.tsx`), which is the one place the raw release name
 * appears whole and unabbreviated.
 */
function releaseRow(page: Page, scope: Locator, title: string): Locator {
  return scope.getByRole("row").filter({ has: page.locator(`td[title="${title}"]`) });
}

function computed(cell: Locator, property: string): Promise<string> {
  return cell.evaluate(
    (element, name) => window.getComputedStyle(element).getPropertyValue(name),
    property,
  );
}

test.describe("search", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test.afterEach(async ({ page }) => {
    // Belt and braces for the `finally` inside `withIndexer`, which a timed-out
    // test never reaches.
    for (const indexer of await configuredIndexers(page).catch(() => [])) {
      if (indexer.name.startsWith(`E2E ${DISPOSABLE_PREFIX}`)) {
        await apiDelete(page, `/api/admin/indexers/${indexer.id}`).catch(() => undefined);
      }
    }
  });

  test("a typed query searches the library and reports what it found", async ({ page }) => {
    await page.goto("/search");
    await expect(page.getByRole("heading", { name: "Search", level: 1 })).toBeVisible();

    const localResults = page.getByRole("region", { name: "In your library" });
    await expect(
      localResults.getByText("Enter a title to search your local library."),
    ).toBeVisible();

    await page.getByRole("searchbox", { name: "Find a title" }).fill("scene");

    // The query lives in the URL, so the search is shareable and survives a reload.
    await expect(page).toHaveURL(/[?&]q=scene/);
    // Debounced, then either rows or the "nothing matched" line - never the
    // pre-search prompt, which is what proves the search actually ran.
    await expect(
      localResults
        .getByRole("table")
        .or(
          localResults.getByText(
            "No matching library items. Indexer results may still find a release.",
          ),
        ),
    ).toBeVisible();

    await expectNoAccessibilityViolations(page);
  });

  test("local search is fuzzy, not a substring match", async ({ page }) => {
    const media = await seedLibraryMedia(page);
    test.skip(media === null, NO_FIXTURE);
    if (media === null) return;

    const direct = await apiGet<LocalResponse>(
      page,
      `/api/search/local?q=${encodeURIComponent(media.title)}&sort=relevance`,
    );
    expect(direct.items.map((item) => item.title)).toContain(media.title);
    const exact = direct.items.find((item) => item.title === media.title);
    expect(exact?.relevance ?? 0).toBeGreaterThan(0);

    // ADR 0007 L11: "pg_trgm carries local search, fuzzy title matching". The
    // discriminating case is a query that is not a substring of the title, so
    // an `ILIKE '%…%'` implementation would answer nothing.
    const typo = media.title.replace("Fixture", "Fixtrue");
    expect(typo).not.toBe(media.title);
    expect(media.title.toLowerCase()).not.toContain(typo.toLowerCase());
    const fuzzy = await apiGet<LocalResponse>(
      page,
      `/api/search/local?q=${encodeURIComponent(typo)}&sort=relevance`,
    );
    expect(
      fuzzy.items.map((item) => item.title),
      `"${typo}" is not a substring of "${media.title}", so only trigram similarity can find it. ADR 0007 says pg_trgm carries local search.`,
    ).toContain(media.title);

    // The same query against the API the screen calls, so a failure below is the
    // screen's and not the search's.
    await page.goto(`/search?q=${encodeURIComponent(media.title)}`);
    const localResults = page.getByRole("region", { name: "In your library" });
    await expect(
      localResults.getByRole("cell", { name: media.title }),
      "The search screen found nothing the API returns for the same query. It sends every " +
        "unset numeric filter as zero, and a maximum size of zero bytes excludes every file - " +
        "see numberValue in apps/web/src/routes/search/search-route.tsx.",
    ).toBeVisible();

    // DESIGN.md L109-111: "Numbers are tabular everywhere. Sizes, durations,
    // speeds, bitrates, scores, counts, dates." The local table has its own two
    // comparable columns and they were never asserted anywhere; the external
    // table's are asserted in `a release row carries every column…`.
    await expect(localResults.getByRole("columnheader")).toHaveText([
      "Title",
      "Studio",
      "Age",
      "Quality",
      "Size",
    ]);
    const localRow = localResults.getByRole("row").filter({ hasText: media.title }).first();
    for (const [name, index] of [
      ["age", 2],
      ["size", 4],
    ] as const) {
      const cell = localRow.getByRole("cell").nth(index);
      expect(await computed(cell, "text-align"), `the local ${name} is not right-aligned`).toBe(
        "right",
      );
      const figures = cell.locator("span.tabular").first();
      await expect(figures, `the local ${name} does not use tabular figures`).toBeVisible();
      expect(await computed(figures, "font-variant-numeric")).toContain("tabular-nums");
    }
  });

  test("a facet count is the number of rows selecting it yields", async ({ page }) => {
    const media = await seedLibraryMedia(page);
    test.skip(media === null, NO_FIXTURE);
    if (media === null) return;

    // `apps/api/pornarr_api/facets.py` opens by calling a facet count a promise:
    // "select this and you will see 412 titles", and says the one thing it must
    // never do is disagree with the list underneath it. Studio and tag are the
    // dimensions where both sides match exactly, so they are the ones that can
    // hold the promise to the letter; the search is aimed at whichever library
    // title actually carries one.
    let subject: { query: string; facets: Facets } | null = null;
    for (const item of await libraryItems(page)) {
      const facets = await apiGet<Facets>(
        page,
        `/api/search/local/facets?q=${encodeURIComponent(item.title)}`,
      );
      expect(facets.capped, "A test library should never exceed the 5000-row facet cap.").toBe(
        false,
      );
      // With nothing selected, the matched count is the whole reachable set.
      expect(facets.matched).toBe(facets.total);
      if (facets.studio.length > 0 || facets.tag.length > 0) {
        subject = { query: item.title, facets };
        break;
      }
    }
    test.skip(
      subject === null,
      "No library title carries a studio or a tag, so no facet promise can be checked against the list it promises.",
    );
    if (subject === null) return;

    const selections: [string, FacetValue | undefined][] = [
      ["studio", subject.facets.studio[0]],
      ["tag", subject.facets.tag[0]],
    ];
    for (const [dimension, value] of selections) {
      if (value === undefined || value.count > 100) continue;
      const narrowed = await apiGet<LocalResponse>(
        page,
        `/api/search/local?q=${encodeURIComponent(subject.query)}&limit=100&${dimension}=${encodeURIComponent(value.value)}`,
      );
      expect(
        narrowed.items.length,
        `The ${dimension} facet promises ${value.count} rows for "${value.value}" and the list delivers ${narrowed.items.length}.`,
      ).toBe(value.count);
    }
  });

  test("the indexer search is asynchronous, scoped and validated", async ({ page }) => {
    // openapi.json: POST answers 202 with an id, GET reads that id back.
    const started = await apiPostRaw(page, "/api/search/indexers", { q: "scene" });
    expect(started.status()).toBe(202);
    const { id } = (await started.json()) as { id: string };
    const state = await apiGet<IndexerSearch>(page, `/api/search/indexers/${id}`);
    expect(state.id).toBe(id);
    expect(state.query).toBe("scene");
    expect(state.cancelled).toBe(false);

    // A search id nobody started is not somebody else's search to read.
    const stranger = await page.request.get(`/api/search/indexers/${randomUUID()}`);
    expect(stranger.status()).toBe(404);

    // A blank query is not a search for everything.
    const blank = await apiPostRaw(page, "/api/search/indexers", { q: "   " });
    expect(blank.status()).toBe(422);
    const missing = await apiPostRaw(page, "/api/search/indexers", {});
    expect(missing.status()).toBe(422);

    // The local half rejects a size range that can never match rather than
    // quietly returning nothing.
    const impossible = await page.request.get(
      "/api/search/local?q=scene&minimum_size_bytes=200&maximum_size_bytes=100",
    );
    expect(impossible.status()).toBe(422);
    // api-contract.md L28-32: errors leave as a stable machine-readable code.
    expect((await impossible.json()).code).toBe("VALIDATION_FAILED");
  });

  test("a search emits started, result_added and completed over SSE", async ({ page }) => {
    const indexer = await testingIndexer(page);
    test.skip(indexer === null, NO_INDEXER);
    if (indexer === null) return;

    await page.goto("/search");
    const query = uncachedQuery();
    // api-contract.md L45 names three events. They are named SSE events on the
    // shared `/api/events` stream, so the browser is the honest listener.
    // Every builder on this stack signs in as the same administrator and the
    // stream is per user, so the event *names* alone would be satisfied by
    // somebody else's search. Every assertion below is filtered on the
    // `search_id` this test started - which is exactly what
    // `apps/api/pornarr_api/routers/search.py:237` and
    // `apps/worker/pornarr_worker/search.py:100-135` put in the payload.
    await collectEvents(page, ["search.started", "search.result_added", "search.completed"]);
    // The stream has to be subscribed before the search is published; the
    // events carry no replay for a listener that arrives late.
    await page.waitForTimeout(1_000);
    const search = await drainIndexerSearch(page, query);

    type Frame = { readonly type: string; readonly payload: Record<string, unknown> };
    let mine: Frame[] = [];
    await expect
      .poll(
        async () => {
          mine = (await collectedEvents(page))
            .map((event) => ({
              type: event.type,
              payload: JSON.parse(event.data) as Record<string, unknown>,
            }))
            .filter((frame) => frame.payload.search_id === search.id);
          // One `result_added` per reporting indexer, so the shape that matters
          // is the order: started first, completed last, results in between.
          return [mine.at(0)?.type, mine.at(-1)?.type, new Set(mine.map((f) => f.type)).size];
        },
        {
          timeout: 45_000,
          intervals: [500],
          message: `No complete SSE sequence carried search_id ${search.id}. api-contract.md L45 names three events.`,
        },
      )
      .toEqual(["search.started", "search.completed", 3]);
    await stopCollectingEvents(page);

    // The reason each event fired, not merely that it did.
    const started = mine[0];
    const completed = mine[mine.length - 1];
    expect(started.payload.query, "search.started does not carry the query it started.").toBe(
      query,
    );
    expect(
      mine.filter((frame) => frame.type === "search.result_added").length,
      "Fewer progressive frames arrived than indexers reported, so the arrival is not progressive.",
    ).toBe(Object.keys(search.statuses).length);
    const added = mine.find(
      (frame) => frame.type === "search.result_added" && frame.payload.indexer_id === indexer.id,
    );
    expect(
      added,
      "No progressive frame named the fake indexer, so a reader cannot tell whose results arrived.",
    ).toBeTruthy();
    if (added === undefined) return;
    expect(added.payload.status).toBe(search.statuses[indexer.id]);
    expect(Array.isArray(added.payload.results)).toBe(true);
    expect(
      (added.payload.results as unknown[]).length,
      "The fake indexer completed and its progressive frame carried no releases.",
    ).toBeGreaterThan(0);
    expect(completed.payload.statuses).toEqual(search.statuses);
    expect(
      completed.payload.cancelled,
      "A search that finished on its own was published as cancelled.",
    ).toBe(false);
  });

  test("a release row carries every column the design specifies", async ({ page }) => {
    const indexer = await testingIndexer(page);
    test.skip(indexer === null, NO_INDEXER);
    if (indexer === null) return;

    const started = Date.now();
    await page.goto("/search");
    await page.getByRole("searchbox", { name: "Find a title" }).fill("Compose Test Scene");

    const external = page.getByRole("region", { name: "From indexers" });
    const row = releaseRow(page, external, TESTING_RELEASE_TITLE);
    await expect(
      row,
      `The indexer answered but ${TESTING_RELEASE_TITLE} never reached the screen.`,
    ).toBeVisible({ timeout: CORE_LOOP_BUDGET_MILLISECONDS });
    const grab = row.getByRole("button", { name: "Grab" });
    await expect(grab).toBeVisible();
    // PRODUCT.md L35-36: search, recognise the release, request it, inside
    // thirty seconds and without documentation.
    expect(
      Date.now() - started,
      "PRODUCT.md L35-36 makes thirty seconds a design constraint for the core loop.",
    ).toBeLessThan(CORE_LOOP_BUDGET_MILLISECONDS);

    // DESIGN.md L197-212, in order.
    await expect(external.getByRole("columnheader")).toHaveText([
      "Title",
      "Indexer",
      "Quality",
      "Size",
      "Age",
      "Seeders",
      "Score",
      "Time",
      "Action",
    ]);

    const cells = row.getByRole("cell");
    const [title, indexerName, quality, size, age, seeders, score, time] = [
      cells.nth(0),
      cells.nth(1),
      cells.nth(2),
      cells.nth(3),
      cells.nth(4),
      cells.nth(5),
      cells.nth(6),
      cells.nth(7),
    ];

    // Title: mono for the raw release name, truncated with the full name in a tooltip.
    await expect(title).toHaveAttribute("title", TESTING_RELEASE_TITLE);
    expect(await computed(title, "font-family")).toMatch(/mono/i);
    await expect(indexerName).toHaveText(indexer.name);

    // The five comparable columns are right-aligned and tabular, which is what
    // "comparable across rows without arithmetic" reduces to in the DOM.
    for (const [name, cell] of [
      ["size", size],
      ["age", age],
      ["seeders", seeders],
      ["score", score],
      ["time", time],
    ] as const) {
      expect(await computed(cell, "text-align"), `${name} is not right-aligned`).toBe("right");
      const figures = cell.locator("span.tabular").first();
      await expect(figures, `${name} does not use tabular figures`).toBeVisible();
      expect(await computed(figures, "font-variant-numeric")).toContain("tabular-nums");
    }

    // Size reads as a unit, not as a raw byte count nobody can compare.
    await expect(size).toHaveText(/^\d[\d.,]*\s?(B|kB|KB|KiB|MB|MiB|GB|GiB|TB|TiB)$/);
    // Quality is the parsed resolution, not the whole release name.
    await expect(quality).toHaveText("1080p");
    // Seeders on a torrent row: the fake indexer publishes twelve.
    await expect(seeders).toHaveText("12");

    // Age is relative with the absolute date in the title attribute (L208).
    const absolute = await age.getAttribute("title");
    expect(absolute, "The age cell carries no absolute date in its title attribute.").toBeTruthy();
    expect(Number.isNaN(Date.parse(absolute ?? "")), `"${absolute}" is not a readable date`).toBe(
      false,
    );
    await expect(age).not.toHaveText(absolute ?? "");

    // Score carries a hover breakdown of its components (L210).
    const breakdown = await score.getAttribute("title");
    expect(breakdown, "The score cell carries no breakdown in its title attribute.").toBeTruthy();
    expect(breakdown).toContain("Title");
    expect(breakdown).toContain("attributes");
    expect(breakdown).toContain("indexer reliability");

    // PRODUCT.md L96-98: WCAG 2.2 AA throughout, verified rather than assumed,
    // and reachable by keyboard. The nine columns are 68rem wide, so the
    // wrapper scrolls; axe's `scrollable-region-focusable` only fires while the
    // body holds nothing focusable, and by now every row has a Grab button, so
    // an axe run alone would not notice the wrapper losing its name or its tab
    // stop. Both are therefore asserted directly.
    const scrollRegion = external.getByRole("region", { name: "Indexer results" });
    await expect(
      scrollRegion,
      "The scrolling results table is not an accessible region with a name; axe reports " +
        "scrollable-region-focusable on it as soon as no row is focusable yet.",
    ).toBeVisible();
    expect(await computed(scrollRegion, "overflow-x")).toBe("auto");
    await expect(
      scrollRegion,
      "The scrolling results table is not a tab stop, so it cannot be scrolled without a mouse.",
    ).toHaveAttribute("tabindex", "0");
    await scrollRegion.focus();
    expect(
      await page.evaluate(() => document.activeElement?.getAttribute("aria-label")),
      "Focus did not land on the scrolling region.",
    ).toBe("Indexer results");
    await expectNoAccessibilityViolations(page);
  });

  test("a release the library already holds is dimmed and badged, not hidden", async ({ page }) => {
    const indexer = await testingIndexer(page);
    test.skip(indexer === null, NO_INDEXER);
    if (indexer === null) return;
    // Something in the library the fake indexer's release matches well enough to
    // be recognised. Deliberately not a title containing "Compose Test": the
    // acquisition test in this file recognises its own import by that substring.
    const owned = await seedLibraryMedia(page, "Fake Studio - Reference Scene (2026) 1080p");
    test.skip(owned === null, NO_FIXTURE);
    if (owned === null) return;

    await page.goto("/search?q=Compose+Test+Scene");
    const external = page.getByRole("region", { name: "From indexers" });
    const row = releaseRow(page, external, TESTING_RELEASE_TITLE);
    await expect(row).toBeVisible({ timeout: CORE_LOOP_BUDGET_MILLISECONDS });

    // DESIGN.md L214-215, both halves. The badge alone is what the row had.
    const badge = row.getByRole("link", { name: /In library|Upgrade/ });
    await expect(
      badge,
      "A release matching something in the library carries no badge, so the reader cannot tell it is already present.",
    ).toBeVisible();
    expect(await badge.getAttribute("href")).toMatch(/^\/library\/[0-9a-f-]{36}$/);

    const [ink, dimmed] = await page.evaluate(() =>
      ["text-ink", "text-ink-muted"].map((token) => {
        const probe = document.createElement("span");
        probe.className = token;
        document.body.append(probe);
        const colour = window.getComputedStyle(probe).color;
        probe.remove();
        return colour;
      }),
    );
    expect(ink).not.toBe(dimmed);
    expect(
      await computed(row, "color"),
      "A release the library already holds is painted in the same ink as a new one. " +
        "DESIGN.md L214-215 says such a row is dimmed as well as badged.",
    ).toBe(dimmed);
  });

  test("an estimate is a range with a confidence, and an unknown says unknown", async ({
    page,
  }) => {
    const indexer = await testingIndexer(page);
    test.skip(indexer === null, NO_INDEXER);
    if (indexer === null) return;

    // ADR 0031: "Every estimate is returned as a range with a confidence
    // level." The API contract holds for whatever this stack can measure.
    const search = await drainIndexerSearch(page, "Compose Test Scene");
    const item = search.items.find((candidate) => candidate.title === TESTING_RELEASE_TITLE);
    expect(item, "The fake indexer's release did not reach the search state.").toBeTruthy();
    if (item === undefined) return;
    expect(["high", "medium", "low", "unknown"]).toContain(item.estimate.confidence);
    if (item.estimate.confidence === "unknown") {
      expect(item.estimate.low_seconds).toBeNull();
      expect(item.estimate.high_seconds).toBeNull();
    } else {
      expect(item.estimate.low_seconds).not.toBeNull();
      expect(item.estimate.high_seconds).not.toBeNull();
      expect(item.estimate.high_seconds ?? 0).toBeGreaterThanOrEqual(
        item.estimate.low_seconds ?? 0,
      );
    }

    // The screen is the half that has never been checked, and the unknown case
    // is the half of *that* which needs no pin: `performance_measurements` is
    // empty on this stack - the download monitor is its only writer - so
    // `search_estimate` legitimately returns `unknown()` and the row below is
    // rendered from the real API.
    await page.goto("/search?q=Compose+Test+Scene");
    const external = page.getByRole("region", { name: "From indexers" });
    const liveRow = releaseRow(page, external, TESTING_RELEASE_TITLE);
    await expect(liveRow).toBeVisible({ timeout: 30_000 });
    const liveTime = liveRow.getByRole("cell").nth(7);
    if (item.estimate.confidence === "unknown") {
      // DESIGN.md L219-221: "Unknown estimates say 'unknown', never '0' and
      // never a spinner that resolves to nothing." Exactly that word and
      // nothing else: the indicator has three states and unknown is not one of
      // them, so a fourth chip here would make the cell read "Unknown Unknown".
      await expect(liveTime, "An unknown estimate does not say so plainly in the row.").toHaveText(
        "Unknown",
      );
      expect(
        await liveTime.locator("span[aria-hidden='true']").count(),
        "An unknown estimate carries a confidence glyph, which claims a level it does not have.",
      ).toBe(0);
    } else {
      await expect(liveTime).toHaveText(new RegExp(item.estimate.confidence, "i"));
    }

    // A measured estimate cannot be produced from outside - there is no
    // endpoint that records a download-speed sample - so the three-state
    // indicator is driven from a pinned payload. The values are the shape this
    // same endpoint returns once `performance_measurements` holds one.
    let confidence = "medium";
    await page.route("**/api/search/indexers/*", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({
        json: {
          ...search,
          items: search.items.map((candidate) => ({
            ...candidate,
            estimate: { low_seconds: 720, high_seconds: 1_080, confidence },
          })),
        },
      });
    });
    // DESIGN.md L219-221: "Never a bare number. `~12-18 min` with a confidence
    // indicator: three states, distinguished by icon and label, not by colour
    // alone." All three, so "three states" is a matrix rather than one sample.
    const rendered: { glyph: string; label: string; colour: string }[] = [];
    for (const [level, label] of [
      ["high", "High"],
      ["medium", "Medium"],
      ["low", "Low"],
    ] as const) {
      confidence = level;
      await page.goto("/search?q=Compose+Test+Scene");
      const row = releaseRow(page, external, TESTING_RELEASE_TITLE);
      await expect(row).toBeVisible({ timeout: 30_000 });
      const time = row.getByRole("cell").nth(7);
      await expect(time, "The time cell shows a single figure instead of a range.").toHaveText(
        /12.*18/,
      );
      await expect(
        time,
        `A ${level}-confidence range carries no confidence beside it. ADR 0031 and DESIGN.md L219-221 both require the level, and the API already returns it as \`estimate.confidence\`.`,
      ).toHaveText(new RegExp(label, "i"));
      const chip = time.locator("span").filter({ hasText: label }).last();
      const glyph = chip.locator("span[aria-hidden='true']");
      await expect(
        glyph,
        `The ${level} state has no icon, so the three states are told apart by words alone.`,
      ).toBeVisible();
      rendered.push({
        glyph: (await glyph.innerText()).trim(),
        label,
        colour: await computed(chip, "color"),
      });
    }
    await page.unroute("**/api/search/indexers/*");

    expect(
      new Set(rendered.map((state) => state.glyph)).size,
      `The three states share an icon: ${rendered.map((state) => state.glyph).join(" ")}`,
    ).toBe(3);
    expect(
      new Set(rendered.map((state) => state.label)).size,
      "The three states share a label.",
    ).toBe(3);
    // "not by colour alone" read literally: colour carries none of the
    // distinction, so a reader who cannot see it loses nothing.
    expect(
      new Set(rendered.map((state) => state.colour)).size,
      `DESIGN.md L219-221 says the states are distinguished by icon and label, not by colour alone; these render in ${rendered.map((state) => state.colour).join(", ")}.`,
    ).toBe(1);
  });

  test("Newznab is a first-class implementation and a usenet row has no seeders", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    // ADR 0002 L9: both protocols are implemented in Pornarr itself. The
    // Newznab adapter is the same RSS parser behind a usenet indexer row, so
    // pointing one at the compose feed exercises the whole path - the row, the
    // adapter, the parse and the protocol-aware column set.
    await withIndexer(
      page,
      {
        protocol: "usenet",
        implementation: "newznab",
        base_url: process.env.E2E_FAKE_INDEXER_URL ?? "http://fake-indexer:9117/api",
        api_key: "fake",
        priority: 7,
        enabled: true,
      },
      async (usenet) => {
        // `protocol=usenet` because `_external_items` deduplicates identical
        // releases across indexers before returning them: the Torznab copy wins
        // on priority and the Newznab one becomes one of its `alternates`. The
        // filter runs before the deduplication, so it is what isolates this
        // indexer's own row.
        const search = await drainIndexerSearch(page, uncachedQuery(), "protocol=usenet");
        expect(
          search.statuses[usenet.id],
          "A Newznab indexer never completed against a feed the Torznab one parses.",
        ).toBe("completed");
        const item = search.items.find((candidate) => candidate.indexer_id === usenet.id);
        expect(item, "The Newznab adapter returned no release.").toBeTruthy();
        if (item === undefined) return;
        expect(item.protocol).toBe("usenet");
        expect(item.title).toBe(TESTING_RELEASE_TITLE);

        // DESIGN.md L209: "Seeders ... torrent rows only". The feed here carries
        // a seeders attribute a real usenet indexer would not, which is exactly
        // the case that tells a protocol-aware column from a credulous one.
        expect(item.seeders).toBe(12);
        await page.goto(`/search?q=${encodeURIComponent("Compose Test Scene")}&protocol=usenet`);
        const external = page.getByRole("region", { name: "From indexers" });
        const row = external.getByRole("row").filter({ hasText: usenet.name });
        await expect(row).toBeVisible({ timeout: 30_000 });
        await expect(
          row.getByRole("cell").nth(5),
          "A usenet row prints a seeder count. Usenet has no seeders; DESIGN.md L209 puts that column on torrent rows only.",
        ).toHaveText("—");
      },
    );
  });

  test("a per-indexer category selection is what the indexer is asked for", async ({ page }) => {
    const indexer = await testingIndexer(page);
    test.skip(indexer === null, NO_INDEXER);
    if (indexer === null) return;
    const before = fakeIndexerRequests();
    test.skip(before === null, NO_DOCKER);
    if (before === null) return;

    const configured = await indexerById(page, indexer.id);
    const original = configured.search_categories;
    const path = new URL(configured.base_url).pathname;
    try {
      for (const categories of [["6010"], ["1000", "5000"], []]) {
        const written = await apiPut<ConfiguredIndexer>(
          page,
          `/api/admin/indexers/${indexer.id}/search-categories`,
          { search_categories: categories },
        );
        expect(written.search_categories).toEqual(categories);
        // Re-read rather than trusting the mutation's own answer.
        expect((await indexerById(page, indexer.id)).search_categories).toEqual(categories);

        const query = uncachedQuery();
        const search = await drainIndexerSearch(page, query);
        expect(search.statuses[indexer.id]).toBe("completed");

        // The random half of the query is hex, so it survives whatever the
        // HTTP client does to spaces. The path narrows it to this indexer:
        // several indexers on this shared stack point at the same container,
        // and each sends its own `cat`.
        const token = query.split(" ")[1] ?? query;
        const lines = (fakeIndexerRequests() ?? []).slice(before.length);
        const mine = lines.filter((line) => line.includes(token) && line.includes(`GET ${path}?`));
        expect(
          mine.length,
          `The fake indexer logged no ${path} request carrying ${token}. Lines since the start of this test:\n${lines.join("\n")}`,
        ).toBeGreaterThan(0);
        // The negative space: with no selection there must be no `cat` at all,
        // and with one the parameter carries exactly those categories and no
        // others. Jellyfin's CleanStringTests.cs:50 asserts the cases that must
        // not fire; this is the same shape.
        for (const line of mine) {
          const sent = /[?&]cat=([^&\s"]*)/.exec(line)?.[1];
          if (categories.length === 0) {
            expect(
              sent,
              `A cleared selection still restricted the request: ${line}`,
            ).toBeUndefined();
          } else {
            expect(
              decodeURIComponent(sent ?? "").split(","),
              `The request carried cat=${sent}: ${line}`,
            ).toEqual(categories);
          }
        }
      }

      // What the claim does NOT cover, asserted so nobody reads more into it.
      // The fake indexer publishes category 6010 and ignores `cat`; Pornarr
      // hands the restriction to the indexer and does not filter the response,
      // so an indexer that ignores the parameter still gets its releases shown.
      await apiPut(page, `/api/admin/indexers/${indexer.id}/search-categories`, {
        search_categories: ["1000"],
      });
      const unfiltered = await drainIndexerSearch(page, uncachedQuery());
      const item = unfiltered.items.find((candidate) => candidate.title === TESTING_RELEASE_TITLE);
      expect(
        item,
        "Recorded, not endorsed: `search_categories` restricts the request only. Pornarr applies " +
          "no category filter to what comes back, so exclusion is entirely the indexer's to enforce. " +
          "See apps/worker/pornarr_worker/search.py:280-290 and HOLES.md 03.18.",
      ).toBeTruthy();
    } finally {
      await apiPut(page, `/api/admin/indexers/${indexer.id}/search-categories`, {
        search_categories: original,
      });
    }
  });

  test("a malformed indexer response is reported, counted and eventually skipped", async ({
    page,
  }) => {
    // Six drained searches and a page load; the breaker needs every one of them.
    test.setTimeout(180_000);
    await withIndexer(
      page,
      {
        protocol: "torrent",
        implementation: "torznab",
        // A real bencoded torrent where XML was expected. The campaign has
        // already fixed this parser once for unescaped ampersands, so the
        // interesting question is whether the failure is surfaced at all.
        base_url:
          process.env.E2E_FAKE_INDEXER_URL?.replace(/\/api$/, "/torrent") ??
          "http://fake-indexer:9117/torrent",
        api_key: "fake",
        priority: 9,
        enabled: true,
      },
      async (broken) => {
        // Zero the breaker: its counter lives in Redis against this id.
        await apiPost(page, `/api/admin/indexers/${broken.id}/reset`);

        for (let attempt = 1; attempt <= 3; attempt += 1) {
          const search = await drainIndexerSearch(page, uncachedQuery());
          expect(
            search.statuses[broken.id],
            "A Torznab response that is not XML must be reported as malformed, not swallowed.",
          ).toBe("malformed_response");
          const state = await indexerById(page, broken.id);
          expect(state.stats.failures).toBe(attempt);
          // troubleshooting.md L25-26: three consecutive failures, not one.
          expect(
            state.health,
            `The breaker opened after ${attempt} failure(s); the documented threshold is three.`,
          ).toBe(attempt < 3 ? "unknown" : "unhealthy");
        }

        const opened = await indexerById(page, broken.id);
        expect(opened.health_reason).toBe("malformed_response");
        expect(opened.last_error).toBeTruthy();
        expect(opened.last_error ?? "").not.toContain("fake");

        // Skipped while unhealthy: reported as such, and not queried again.
        const skipped = await drainIndexerSearch(page, uncachedQuery());
        expect(skipped.statuses[broken.id]).toBe("unhealthy");
        expect(
          (await indexerById(page, broken.id)).stats.queries,
          "An unhealthy indexer was still queried.",
        ).toBe(opened.stats.queries);

        // The screen has to say the same thing the API does. An indexer that
        // returned nothing is identified in that list by the first eight
        // characters of its id, because `IndexerSearchResponse.statuses` carries
        // ids only and the names are recovered from the results - see BUILD.md
        // defect 5.
        await page.goto(`/search?q=${encodeURIComponent(uncachedQuery())}`);
        const entry = await indexerStatusEntry(page, broken.id);
        await expect(
          entry,
          "A failed indexer is displayed as though it were still being waited for, which hides the failure entirely.",
        ).not.toHaveText(/Waiting/);

        // openapi.json /api/admin/indexers/{id}/reset clears that state.
        const reset = await apiPost<ConfiguredIndexer>(
          page,
          `/api/admin/indexers/${broken.id}/reset`,
        );
        expect(reset.health).toBe("unknown");
        expect(reset.health_reason).toBeNull();
        expect(reset.last_error).toBeNull();
        const rereadAfterReset = await indexerById(page, broken.id);
        expect(rereadAfterReset.health).toBe("unknown");

        // Reset means asked again, not merely relabelled.
        const revived = await drainIndexerSearch(page, uncachedQuery());
        expect(revived.statuses[broken.id]).toBe("malformed_response");
        expect(
          (await indexerById(page, broken.id)).stats.queries,
          "After a reset the indexer was still being skipped rather than queried.",
        ).toBe(opened.stats.queries + 1);
      },
    );
  });

  test("an indexer that matches nothing succeeds, and is not counted as a failure", async ({
    page,
  }) => {
    // troubleshooting.md L26-27: "a wrong category shows as an empty but
    // successful response". The compose control indexer answers this one term
    // with a valid, item-less Torznab feed, which is the same shape a real
    // indexer returns for a category it has nothing in.
    const control = await controlIndexer(page);
    test.skip(
      control === null,
      "The empty-result case needs the compose control indexer, which is started inside the fake-indexer container by `docker exec`.",
    );
    if (control === null) return;
    await apiPost(page, `/api/admin/indexers/${control.id}/reset`);
    const before = await indexerById(page, control.id);

    const search = await drainIndexerSearch(page, CONTROL_INDEXER_EMPTY_TERM);
    expect(
      search.statuses[control.id],
      "An indexer that matched nothing was reported as something other than a completed search.",
    ).toBe("completed");
    expect(
      search.items.filter((item) => item.indexer_id === control.id),
      "The empty feed produced releases.",
    ).toEqual([]);

    const after = await indexerById(page, control.id);
    expect(after.stats.queries).toBe(before.stats.queries + 1);
    // The negative space: nothing found is not a failure, so the breaker must
    // not move and the indexer must stay in service.
    expect(after.stats.failures, "An empty result set was counted as an indexer failure.").toBe(
      before.stats.failures,
    );
    expect(after.health).toBe("healthy");
    expect(after.health_reason).toBeNull();
    expect(after.last_error).toBeNull();

    await page.goto(`/search?q=${encodeURIComponent(CONTROL_INDEXER_EMPTY_TERM)}`);
    await expect(
      await indexerStatusEntry(page, control.id),
      "An indexer that answered honestly with nothing is shown as a failure.",
    ).toHaveText(/Completed/);
  });

  test("a rejected API key opens the breaker at once and is named as authentication", async ({
    page,
  }) => {
    await withIndexer(
      page,
      {
        protocol: "torrent",
        implementation: "torznab",
        // A real 401 from a real server: the API's own library endpoint.
        base_url: process.env.E2E_UNAUTHORIZED_URL ?? "http://api:8000/api/library",
        api_key: "wrong-key",
        priority: 9,
        enabled: true,
      },
      async (rejected) => {
        await apiPost(page, `/api/admin/indexers/${rejected.id}/reset`);

        const search = await drainIndexerSearch(page, uncachedQuery());
        expect(
          search.statuses[rejected.id],
          "troubleshooting.md L26: a wrong API key surfaces as an authentication error.",
        ).toBe("authentication_failed");

        const state = await indexerById(page, rejected.id);
        // The negative space: authentication is not a transient failure, so it
        // must NOT wait for the three-failure threshold the other causes use.
        expect(
          state.health,
          "A rejected key was treated as a transient failure and left the indexer in service.",
        ).toBe("unhealthy");
        expect(state.health_reason).toBe("authentication");
        expect(state.last_error ?? "").not.toContain("wrong-key");

        const skipped = await drainIndexerSearch(page, uncachedQuery());
        expect(skipped.statuses[rejected.id]).toBe("unhealthy");

        await apiPost(page, `/api/admin/indexers/${rejected.id}/reset`);
        expect((await indexerById(page, rejected.id)).health).toBe("unknown");
      },
    );
  });

  test("a repeated query is served from the release cache without asking the indexer", async ({
    page,
  }) => {
    const indexer = await testingIndexer(page);
    test.skip(indexer === null, NO_INDEXER);
    if (indexer === null) return;
    const requestsBefore = fakeIndexerRequests();
    test.skip(requestsBefore === null, NO_DOCKER);
    if (requestsBefore === null) return;

    // Warm the cache through the indexer itself. ADR 0033: the cache is keyed
    // by indexer and GUID, independent of any request.
    const warmed = await drainIndexerSearch(page, TESTING_RELEASE_TITLE);
    expect(["completed", "cached"]).toContain(warmed.statuses[indexer.id]);
    const cachedItem = warmed.items.find((item) => item.title === TESTING_RELEASE_TITLE);
    expect(cachedItem).toBeTruthy();
    if (cachedItem === undefined) return;

    const afterWarming = (fakeIndexerRequests() ?? []).length;
    // A term the cached title contains, so the cache can answer it.
    const cachedSearch = await drainIndexerSearch(page, "Compose Test Scene");
    expect(
      cachedSearch.statuses[indexer.id],
      "ADR 0033 L11: search results can be served from the cache within the TTL.",
    ).toBe("cached");
    expect((fakeIndexerRequests() ?? []).length, "A cache hit still went out to the indexer.").toBe(
      afterWarming,
    );

    // Keyed uniquely by indexer and GUID: repeated searches replace the row
    // rather than accumulating duplicates.
    const guids = cachedSearch.items.map((item) => `${item.indexer_id}:${item.guid}`);
    expect(new Set(guids).size).toBe(guids.length);
    const served = cachedSearch.items.find((item) => item.guid === cachedItem.guid);
    expect(served?.title).toBe(cachedItem.title);
    expect(served?.size).toBe(cachedItem.size);
  });

  test("a cached release is served from cache without removing the other indexers from the fan-out", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    const indexer = await testingIndexer(page);
    test.skip(indexer === null, NO_INDEXER);
    if (indexer === null) return;

    // Warm the cache for the fake indexer alone. The cache is keyed on the
    // release title, not on the query, so a term the cache cannot answer is
    // what forces a real fan-out - and the row it leaves behind is what a later
    // search for a substring of that title will find.
    const warmed = await drainIndexerSearch(page, uncachedQuery());
    expect(
      warmed.statuses[indexer.id],
      "The fake indexer never answered, so nothing was cached to reason about.",
    ).toBe("completed");
    expect(
      warmed.items.some(
        (item) => item.indexer_id === indexer.id && item.title === TESTING_RELEASE_TITLE,
      ),
      "The fake indexer completed without returning its release, so no cache row exists.",
    ).toBe(true);

    await withIndexer(
      page,
      {
        protocol: "torrent",
        implementation: "torznab",
        base_url: process.env.E2E_FAKE_INDEXER_URL ?? "http://fake-indexer:9117/api",
        api_key: "fake",
        priority: 6,
        enabled: true,
      },
      async (second) => {
        // Created after the warming search, so this row owns no cached release
        // at all - `release_cache.indexer_id` cascades from `indexers.id`, and
        // this id has never been searched. Proven configured, enabled and
        // healthy before anything is concluded from its absence.
        const tested = await apiPost<ConfiguredIndexer>(
          page,
          `/api/admin/indexers/${second.id}/test`,
        );
        expect(tested.health, "The second indexer could not reach the feed it shares.").toBe(
          "healthy",
        );
        const ready = await indexerById(page, second.id);
        expect(ready.enabled).toBe(true);
        expect(ready.health).toBe("healthy");

        // README.md L12-13: "One search across the local library and every
        // configured indexer". This is the query the cache can answer.
        //
        // The counter is read immediately before each search and only ever
        // compared as "it moved": `backlog_search` runs on a one-minute cron
        // over the same indexers, so an exact expected total is a coin toss on
        // a shared stack rather than a statement about this search.
        const beforeCached = (await indexerById(page, second.id)).stats.queries;
        const cached = await drainIndexerSearch(page, "Compose Test Scene");
        expect(
          cached.statuses[indexer.id],
          "ADR 0033 L11: within the TTL the cached indexer answers from cache.",
        ).toBe("cached");

        // Both halves of the union, in one search. ADR 0033 sanctions serving a
        // release from the cache; it does not sanction skipping the indexers
        // that have nothing cached, which is what `cached or configured` did
        // (search.py:205) for the whole 24-hour TTL. `search_targets` is the
        // union: the cached indexer costs no request, and the one with no
        // cached row is still asked.
        expect(
          Object.keys(cached.statuses),
          "A configured, enabled, healthy indexer was dropped from a cacheable query - " +
            "the release cache is replacing the fan-out again. See search_targets in search.py.",
        ).toContain(second.id);
        expect(
          cached.statuses[second.id],
          "The second indexer was listed but never actually queried.",
        ).toBe("completed");
        expect(
          (await indexerById(page, second.id)).stats.queries,
          "The second indexer's query counter did not move, so it was never asked.",
        ).toBeGreaterThan(beforeCached);
        // Its results are not asserted here: both indexers serve the same feed,
        // and `_external_items` folds identical GUIDs across indexers into one
        // row whose lower-priority copies become `alternates`.

        // The control, in the same session: a query the cache cannot answer at
        // all reports the same two indexers, both live. That is what makes the
        // assertion above a statement about the cache and not about this
        // indexer answering by accident.
        const beforeFresh = (await indexerById(page, second.id)).stats.queries;
        const fresh = await drainIndexerSearch(page, uncachedQuery());
        expect(Object.keys(fresh.statuses)).toContain(indexer.id);
        expect(Object.keys(fresh.statuses)).toContain(second.id);
        expect(fresh.statuses[second.id]).toBe("completed");
        expect((await indexerById(page, second.id)).stats.queries).toBeGreaterThan(beforeFresh);
      },
    );
  });

  test("one user's search is not another user's to read", async ({ page, browser }) => {
    // PRODUCT.md L44-47: what a user sees is theirs. `indexer_search_status`
    // answers 404 when `state.user_id` is not the caller's
    // (apps/api/pornarr_api/routers/search.py:265-266), and a random UUID
    // cannot tell that check from the one above it - the state simply is not
    // there. Only a real search belonging to a real second account can.
    const started = await apiPostRaw(page, "/api/search/indexers", { q: uncachedQuery() });
    expect(started.status()).toBe(202);
    const { id } = (await started.json()) as { id: string };
    const mine = await apiGet<IndexerSearch>(page, `/api/search/indexers/${id}`);
    expect(mine.id).toBe(id);

    const viewer = await signedInViewer(page, browser);
    test.skip(
      viewer === null,
      "A second account could not be created through the invite flow, so the ownership boundary " +
        "cannot be exercised between two users on this stack.",
    );
    if (viewer === null) return;
    try {
      const stranger = await viewer.get(`/api/search/indexers/${id}`);
      expect(
        stranger.status(),
        "A second signed-in account read a search that belongs to the administrator.",
      ).toBe(404);
      // The reason, not just the verdict: the same account is signed in and can
      // start and read a search of its own, so the 404 above is ownership and
      // not a broken session.
      const own = await viewer.post("/api/search/indexers", { q: uncachedQuery() });
      expect(own.status()).toBe(202);
      const ownId = ((await own.json()) as { id: string }).id;
      expect(ownId).not.toBe(id);
      const readBack = await viewer.get(`/api/search/indexers/${ownId}`);
      expect(readBack.status()).toBe(200);
      expect(((await readBack.json()) as IndexerSearch).id).toBe(ownId);
      // And the boundary is symmetric: the administrator cannot read theirs.
      const backwards = await page.request.get(`/api/search/indexers/${ownId}`);
      expect(
        backwards.status(),
        "The administrator could read a search belonging to another account.",
      ).toBe(404);
    } finally {
      await viewer.close();
    }
  });

  test("indexers are database rows, not environment variables", async ({ page }) => {
    test.setTimeout(90_000);
    // ADR 0002 L9. The first half is provable end to end: a row created through
    // the API participates in the next search and stops participating when it
    // is removed.
    await withIndexer(
      page,
      {
        protocol: "torrent",
        implementation: "torznab",
        base_url: process.env.E2E_FAKE_INDEXER_URL ?? "http://fake-indexer:9117/api",
        api_key: "fake",
        priority: 8,
        enabled: true,
      },
      async (created) => {
        expect((await configuredIndexers(page)).map((item) => item.id)).toContain(created.id);
        const withRow = await drainIndexerSearch(page, uncachedQuery());
        expect(Object.keys(withRow.statuses)).toContain(created.id);
        expect(withRow.statuses[created.id]).toBe("completed");

        await apiDelete(page, `/api/admin/indexers/${created.id}`);
        expect((await configuredIndexers(page)).map((item) => item.id)).not.toContain(created.id);
        const withoutRow = await drainIndexerSearch(page, uncachedQuery());
        expect(Object.keys(withoutRow.statuses)).not.toContain(created.id);
      },
    );

    // The second half is "managed through the administration UI", and the route
    // a screen would call has to exist before the screen can. `PUT` on the row
    // is the whole of it: without it the only edit is delete-and-recreate,
    // which throws away the indexer's health, its statistics and - through
    // `ON DELETE CASCADE` on `release_cache.indexer_id` - its cached releases.
    const schema = (await (await page.request.get("/api/openapi.json")).json()) as {
      paths: Record<string, Record<string, unknown>>;
    };
    expect(
      Object.keys(schema.paths["/api/admin/indexers/{indexer_id}"]).sort(),
      "The indexer row lost its update route, so an indexer cannot be changed after creation.",
    ).toEqual(["delete", "put"]);

    // And an unknown id is a 404 rather than the 405 the route used to answer:
    // the difference between "no such indexer" and "no such operation".
    const csrf = (await page.context().cookies()).find((c) => c.name === "pornarr_csrf")?.value;
    const missing = await page.request.fetch(`/api/admin/indexers/${randomUUID()}`, {
      method: "PUT",
      data: {
        name: "renamed",
        protocol: "torrent",
        implementation: "torznab",
        base_url: "https://indexer.example",
      },
      headers: csrf === undefined ? {} : { "X-CSRF-Token": csrf },
    });
    expect(missing.status(), await missing.text()).toBe(404);
  });

  test("an indexer is added, edited, tested and removed from the administration screen", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    // ADR 0002 L9: "Indexers are rows in the database, managed through the
    // administration UI, not environment variables." The wizard could add the
    // first indexer and nothing could touch it afterwards - there was no screen
    // and no update route. This drives the screen for all of it, and cross-checks
    // every step against the API so a green screen that changed nothing fails.
    const name = `E2E ${DISPOSABLE_PREFIX}${randomUUID().slice(0, 8)}`;
    const baseUrl = process.env.E2E_FAKE_INDEXER_URL ?? "http://fake-indexer:9117/api";

    await page.goto("/settings/indexers");
    await expect(page.getByRole("heading", { name: "Indexers", level: 1 })).toBeVisible();
    // Reached by navigation, not only by address: an unreachable screen is the
    // reason root folders could exist in the API and never appear in the client.
    await expect(
      page.getByRole("navigation", { name: "Settings areas" }).getByRole("link", {
        name: "Indexers",
      }),
    ).toHaveAttribute("href", "/settings/indexers");

    const addForm = page.getByRole("form", { name: "Add an indexer" });
    await addForm.getByLabel("Name").fill(name);
    await addForm.getByLabel("Base URL").fill(baseUrl);
    await addForm.getByLabel("API key").fill("fake");
    await addForm.getByLabel("Priority").fill("7");
    await addForm.getByLabel("Search categories").fill("6000, 6010");
    await addForm.getByRole("button", { name: "Add indexer" }).click();

    const row = page.getByRole("listitem").filter({ hasText: name });
    await expect(row.getByRole("heading", { name, level: 3 })).toBeVisible();
    await expect(row).toContainText(baseUrl);
    await expect(row).toContainText("6000, 6010");
    await expect(row).toContainText("Not tested yet");

    // The screen is not the assertion: the row it wrote is.
    const created = (await configuredIndexers(page)).find((item) => item.name === name);
    expect(created, "The add form reported success without creating a row.").toBeTruthy();
    if (created === undefined) return;
    try {
      expect(created.base_url).toBe(baseUrl);
      expect(created.search_categories).toEqual(["6000", "6010"]);
      expect(created.enabled).toBe(true);

      await row.getByRole("button", { name: `Test ${name}` }).click();
      await expect(row).toContainText("Answering");
      expect(
        (await indexerById(page, created.id)).health,
        "The screen said the indexer answers, and the stored row disagrees.",
      ).toBe("healthy");

      // The edit, which is the half ADR 0002 promised and the product did not
      // have. The key box is left empty on purpose: it is write-only, so an
      // edit screen has nothing to put back in it.
      await row.getByRole("button", { name: `Edit ${name}` }).click();
      const editForm = page.getByRole("form", { name: `Edit ${name}` });
      await editForm.getByLabel("Priority").fill("3");
      await editForm.getByLabel("Search categories").fill("6000");
      await editForm.getByLabel("Search this indexer").uncheck();
      await expect(editForm.getByLabel("API key")).toHaveValue("");
      await editForm.getByRole("button", { name: "Save changes" }).click();

      await expect(row).toContainText("Disabled");
      const edited = await indexerById(page, created.id);
      expect(
        edited.id,
        "The screen replaced the indexer instead of editing it, which discards its cached releases.",
      ).toBe(created.id);
      expect(edited.priority).toBe(3);
      expect(edited.enabled).toBe(false);
      expect(edited.search_categories).toEqual(["6000"]);
      expect(edited.health, "Editing the row reset what the connection test had established.").toBe(
        "healthy",
      );

      // The empty key box kept the stored key rather than clearing it, and the
      // proof is the indexer still authenticating with it.
      const retested = await apiPost<ConfiguredIndexer>(
        page,
        `/api/admin/indexers/${created.id}/test`,
      );
      expect(
        retested.health,
        "Saving with an empty key box wiped the stored key: the indexer no longer authenticates.",
      ).toBe("healthy");

      await row.getByRole("button", { name: `Remove ${name}` }).click();
      // The first press asks rather than acts, and says what removal costs.
      await expect(row).toContainText("every release Pornarr had cached from it is discarded");
      expect((await configuredIndexers(page)).map((item) => item.id)).toContain(created.id);

      await row.getByRole("button", { name: "Remove indexer" }).click();
      await expect(page.getByRole("heading", { name, level: 3 })).toHaveCount(0);
      expect((await configuredIndexers(page)).map((item) => item.id)).not.toContain(created.id);
    } finally {
      await apiDelete(page, `/api/admin/indexers/${created.id}`).catch(() => undefined);
    }
  });

  test("a release from an indexer is grabbed, downloaded and imported", async ({ page }) => {
    // The whole acquisition chain in one test, because every link in it only
    // means something with the others: an indexer that answers, a client that
    // accepts the release, a completion the product notices, and a file that
    // ends up in the library.
    test.setTimeout(ACQUISITION_TIMEOUT_MILLISECONDS + 60_000);
    const [indexer, client] = await Promise.all([
      testingIndexer(page),
      testingDownloadClient(page),
    ]);
    test.skip(
      indexer === null || client === null,
      "Grabbing needs an indexer and a download client: start them with " +
        "docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.testing.yml up -d",
    );

    await page.goto(`/search?q=${encodeURIComponent("Compose Test Scene")}`);
    const externalResults = page.getByRole("region", { name: "From indexers" });
    const row = releaseRow(page, externalResults, TESTING_RELEASE_TITLE);
    await expect(
      row,
      `The indexer answered but ${TESTING_RELEASE_TITLE} never reached the screen.`,
    ).toBeVisible({ timeout: 30_000 });

    await row.getByRole("button", { name: "Grab" }).click();

    // The client verifies the data it was handed and reports the download as
    // finished; a torrent client that keeps seeding says "seeding", not
    // "completed", and both mean the file is on disk.
    await expect
      .poll(
        async () =>
          (await queueJobs(page)).some((job) =>
            ["completed", "seeding", "importing"].includes(job.status),
          ),
        {
          timeout: ACQUISITION_TIMEOUT_MILLISECONDS,
          intervals: [2_000],
          message: "The grabbed release never finished in the download client.",
        },
      )
      .toBe(true);

    await expect
      .poll(
        async () => (await libraryItems(page)).some((item) => item.title.includes("Compose Test")),
        {
          timeout: ACQUISITION_TIMEOUT_MILLISECONDS,
          intervals: [2_000],
          message:
            "The download finished and nothing reached the library. See docs/pipelines/import.md.",
        },
      )
      .toBe(true);
  });
});

/**
 * A second signed-in account, so the ownership boundary on a search is
 * exercised between two users rather than between a user and a UUID nobody
 * owns.
 *
 * Created the way the product creates one - an administrator issues an invite,
 * the invitee redeems it - and reused across runs, so a repeated run adds a
 * session rather than another account. Returns null when the invite flow is not
 * available, and the caller says so in its skip.
 */
const VIEWER_USERNAME = "piece03-viewer";
const VIEWER_PASSWORD = "Piece03-Viewer-2026!";

type ViewerClient = {
  readonly get: (path: string) => Promise<APIResponse>;
  readonly post: (path: string, data: unknown) => Promise<APIResponse>;
  readonly close: () => Promise<void>;
};

async function signedInViewer(page: Page, browser: Browser): Promise<ViewerClient | null> {
  const context = await browser.newContext({ baseURL: test.info().project.use.baseURL });
  const credentials = { username: VIEWER_USERNAME, password: VIEWER_PASSWORD };

  let session = await context.request.post("/api/auth/login", { data: credentials });
  if (!session.ok()) {
    const invite = await apiPostRaw(page, "/api/admin/invites", {
      valid_days: 1,
      note: "piece 03 search ownership boundary",
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
    post: async (path, data) => context.request.post(path, { headers: await csrf(), data }),
    close: () => context.close(),
  };
}
