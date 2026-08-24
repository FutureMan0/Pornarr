/**
 * Streaming routes must not hold a database connection while they stream.
 *
 * `database_session` in `apps/api/pornarr_api/auth.py` is a FastAPI yield
 * dependency, and FastAPI keeps those open until the response *body* has
 * finished. A `StreamingResponse` body does not finish while the client stays
 * connected, so a streaming route that depends on it pins one pooled
 * connection -- inside an open transaction -- for the whole life of the stream.
 * The pool is 5 plus 10 overflow, so once sixteen streams are open at the same
 * time there is nothing left for any other request: measured against a build
 * with the defect, twenty concurrent streams put fifteen sessions into
 * `idle in transaction` and `/api/library` stopped answering entirely.
 *
 * `/api/events`, `/api/media/{id}/stream` and the peer proxy take their session
 * from `streaming_session` now, which hands the connection back before the
 * response is returned.
 *
 * Each stream gets its own `APIRequestContext`, and that is not incidental: a
 * single context queues requests behind its own connection limit, so streams
 * opened through one of them are not concurrent no matter how many are asked
 * for, and the pool is never pressed. Separate contexts are what make the
 * simultaneity real. Verified by mutation -- with the fix reverted, this test
 * fails on `/api/library`.
 */
import { expect, test } from "@playwright/test";
import { loginAsAdmin } from "./helpers";

/**
 * Past the 5 + 10 the pool allows, with a margin. Every stream open at once
 * held exactly one session on the defective build.
 */
const CONCURRENT_STREAMS = 20;

/** Long enough to outlast the assertions below, so the streams are open while they run. */
const STREAM_TIMEOUT_MILLISECONDS = 40_000;

/** Long enough that every stream is certainly established before anything is asserted. */
const HOLD_MILLISECONDS = 10_000;

/** The routes an operator would notice first, and one that reads the database itself. */
const ROUTES = ["/api/library?limit=1", "/api/queue/summary", "/api/health"] as const;

test.describe("streaming routes and the connection pool", () => {
  test("the application still answers with more event streams open than the pool has connections", async ({
    page,
    context,
    playwright,
  }, testInfo) => {
    // Twenty held streams plus their teardown outlast the default per-test
    // budget, and a test that dies on the clock proves nothing either way.
    test.setTimeout(180_000);
    await loginAsAdmin(page);

    // A control first: a failure later proves nothing about streams if the API
    // was already failing before any were opened.
    for (const path of ROUTES) {
      const before = await page.request.get(path);
      expect(
        before.status(),
        `${path} was already failing before any stream was opened, so this test could prove nothing.`,
      ).toBe(200);
    }

    // The signed-in session, handed to each independent context so every stream
    // authenticates as this administrator rather than being rejected at the door.
    const storageState = await context.storageState();
    const baseURL = testInfo.project.use.baseURL;

    const holders = await Promise.all(
      Array.from({ length: CONCURRENT_STREAMS }, () =>
        playwright.request.newContext({ baseURL, storageState }),
      ),
    );

    // Not awaited: each request stays open holding its response, which is the
    // whole point. They are settled after the assertions.
    const streams = holders.map((holder) =>
      holder.get("/api/events", { timeout: STREAM_TIMEOUT_MILLISECONDS }).catch(() => undefined),
    );

    try {
      await page.waitForTimeout(HOLD_MILLISECONDS);

      // The subject is the rest of the application, not the streams: the defect
      // was that opening them took everything else down. An exhausted pool
      // makes a route hang rather than answer, so a timeout is reported as the
      // finding it is instead of as a bare `TimeoutError` from the client.
      for (const path of ROUTES) {
        const status = await page.request
          .get(path, { timeout: 20_000 })
          .then((response) => String(response.status()))
          .catch((error: Error) => `no answer at all (${error.message.split("\n")[0]})`);
        expect(
          status,
          `${path} returned ${status} with ${CONCURRENT_STREAMS} event streams open. That is the connection pool exhausted by sessions the streams are holding -- see \`streaming_session\` in apps/api/pornarr_api/auth.py.`,
        ).toBe("200");
      }

      // The health report reads the database rather than merely needing a
      // connection to answer, so it separates a pool that is exhausted from one
      // that is merely busy.
      const health = await page.request.get("/api/health", { timeout: 20_000 });
      expect(health.status()).toBe(200);
      const report = (await health.json()) as { status: string; database: { status: string } };
      expect(
        report.database.status,
        "The database component went unhealthy while the streams were open.",
      ).toBe("healthy");
      expect(report.status).toBe("healthy");
    } finally {
      await Promise.allSettled(streams);
      await Promise.allSettled(holders.map((holder) => holder.dispose()));
    }
  });
});
