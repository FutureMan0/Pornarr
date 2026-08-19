/**
 * Requests: what somebody asked for, and every way that ask can be moved.
 *
 * A request is created through `POST /api/requests`, the same call the search
 * screen makes, so what the screen renders afterwards is the product's own
 * state and not a fixture pushed into the database. Every mutation here is
 * confirmed by reading the request back rather than by believing the response
 * that performed it - a handler that returns the right body and writes nothing
 * would pass the second and fail the first.
 *
 * The grab and the download-client lifecycle live in `queue.spec.ts`, which
 * needs the compose testing services; this file needs nothing but the API.
 */
import { type Page, expect, test } from "@playwright/test";
import {
  type SeededRequest,
  apiGet,
  apiPatch,
  apiPatchRaw,
  apiPostRaw,
  expectNoAccessibilityViolations,
  loginAsAdmin,
  seedRequest,
} from "./helpers";

type RequestRecord = {
  readonly id: string;
  readonly user_id: string;
  readonly query: string;
  readonly selected_release_guid: string | null;
  readonly status: string;
  readonly priority: number;
  readonly is_automatic: boolean;
  readonly history: { readonly status: string }[];
};

type ErrorBody = { readonly code: string; readonly status: number };

async function requestById(page: Page, id: string): Promise<RequestRecord> {
  const found = (await apiGet<RequestRecord[]>(page, "/api/requests")).find(
    (item) => item.id === id,
  );
  if (found === undefined) throw new Error(`Request ${id} is missing from /api/requests.`);
  return found;
}

test.describe("requests", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("a new request records the ask, its priority and its first status", async ({ page }) => {
    const query = `E2E create ${Date.now()}`;
    const created = (await seedRequest(page, query)) as SeededRequest & RequestRecord;

    // `USER_REQUEST_PRIORITY` in packages/core/pornarr_core/priorities.py. An
    // administrator asking for something is still asking as a user.
    expect(created).toMatchObject({
      query,
      status: "searching",
      priority: 80,
      is_automatic: false,
      selected_release_guid: null,
    });
    expect(created.history.map((entry) => entry.status)).toEqual(["searching"]);

    // Read back rather than trusting the creation response. The status is not
    // re-asserted as "searching": `request_search` runs once a minute and may
    // legitimately have moved it on already, so what is asserted instead is
    // that it entered the lifecycle at "searching" - the first history entry,
    // which never changes - and that wherever it is now is somewhere
    // `_ALLOWED_TRANSITIONS[SEARCHING]` (packages/db/pornarr_db/requests.py:16)
    // permits it to be. The table itself is proven exhaustively in
    // tests/db/test_requests.py.
    const listed = await requestById(page, created.id);
    expect(listed).toMatchObject({ query, priority: 80, user_id: created.user_id });
    expect(listed.history[0].status).toBe("searching");
    expect([
      "searching",
      "results_found",
      "monitoring",
      "not_found",
      "failed",
      "cancelled",
    ]).toContain(listed.status);

    // The status filter is a query, not a sieve over the whole list: asked for
    // the status it actually has, it is there; asked for any other, it is not.
    const matching = await apiGet<RequestRecord[]>(page, `/api/requests?status=${listed.status}`);
    expect(matching.map((item) => item.id)).toContain(created.id);
    expect(matching.every((item) => item.status === listed.status)).toBe(true);
    const available = await apiGet<RequestRecord[]>(page, "/api/requests?status=available");
    expect(available.map((item) => item.id)).not.toContain(created.id);

    await apiPostRaw(page, `/api/requests/${created.id}/cancel`);
  });

  test("a request that cannot be described is refused, with the reason", async ({ page }) => {
    const blank = await apiPostRaw(page, "/api/requests", { query: "   " });
    expect(blank.status()).toBe(422);
    expect(((await blank.json()) as ErrorBody).code).toBe("VALIDATION_FAILED");

    // `RequestCreate.priority` is capped at the user ceiling on the way in;
    // raising a request above it is the separate, audited PATCH.
    const tooHigh = await apiPostRaw(page, "/api/requests", { query: "E2E ceiling", priority: 81 });
    expect(tooHigh.status()).toBe(422);
    expect(((await tooHigh.json()) as ErrorBody).code).toBe("VALIDATION_FAILED");

    const unknown = await apiPostRaw(
      page,
      "/api/requests/00000000-0000-4000-8000-000000000000/pause",
    );
    expect(unknown.status()).toBe(404);
  });

  test("priority is changed on the request and read back from it", async ({ page }) => {
    const created = await seedRequest(page, `E2E priority ${Date.now()}`);

    const raised = await apiPatch<RequestRecord>(page, `/api/requests/${created.id}/priority`, {
      priority: 100,
    });
    expect(raised.priority).toBe(100);
    expect((await requestById(page, created.id)).priority).toBe(100);

    const lowered = await apiPatch<RequestRecord>(page, `/api/requests/${created.id}/priority`, {
      priority: 20,
    });
    expect(lowered.priority).toBe(20);
    expect((await requestById(page, created.id)).priority).toBe(20);

    // `ADMIN_REQUEST_PRIORITY` is the ceiling for everybody, administrator included.
    const beyond = await apiPatchRaw(page, `/api/requests/${created.id}/priority`, {
      priority: 101,
    });
    expect(beyond.status()).toBe(422);
    expect((await requestById(page, created.id)).priority).toBe(20);

    await apiPostRaw(page, `/api/requests/${created.id}/cancel`);
  });

  test("the actions a request has no download for are refused by name", async ({ page }) => {
    const created = await seedRequest(page, `E2E refusals ${Date.now()}`);

    for (const action of ["pause", "resume"]) {
      const response = await apiPostRaw(page, `/api/requests/${created.id}/${action}`);
      expect(response.status(), `${action} on a request with no download`).toBe(409);
      expect(((await response.json()) as ErrorBody).code).toBe("REQUEST_ACTION_INVALID");
    }

    // Retry means "go back to searching", so the answer depends on where the
    // request is - and `request_search` runs once a minute, so a test cannot
    // pin it to "searching". Both branches of the documented table are asserted
    // instead: refused by name from a state with no edge to searching, or
    // accepted and actually landing there. The table is proven exhaustively in
    // tests/db/test_requests.py.
    const retry = await apiPostRaw(page, `/api/requests/${created.id}/retry`);
    if (retry.status() === 409) {
      expect(((await retry.json()) as ErrorBody).code).toBe("REQUEST_ACTION_INVALID");
    } else {
      expect(retry.status(), await retry.text()).toBe(200);
      expect((await requestById(page, created.id)).status).toBe("searching");
    }

    const cancelled = await apiPostRaw(page, `/api/requests/${created.id}/cancel`);
    expect(cancelled.status()).toBe(200);
    expect((await requestById(page, created.id)).status).toBe("cancelled");

    // Cancelled is terminal: nothing moves it, and each refusal says why.
    for (const action of ["cancel", "retry", "pause"]) {
      const response = await apiPostRaw(page, `/api/requests/${created.id}/${action}`);
      expect(response.status(), `${action} on a cancelled request`).toBe(409);
      expect(((await response.json()) as ErrorBody).code).toBe("REQUEST_ACTION_INVALID");
    }
    expect((await requestById(page, created.id)).status).toBe("cancelled");
  });

  test("the requests screen follows a request and cancels it for real", async ({ page }) => {
    const query = `E2E follow status ${Date.now()}`;
    const created = await seedRequest(page, query);
    expect(created.status).toBe("searching");

    await page.goto("/requests");
    await expect(page.getByRole("heading", { name: "Requests", level: 1 })).toBeVisible();

    const entry = page.getByRole("listitem").filter({ hasText: query });
    await expect(entry).toBeVisible();
    await expect(entry.getByRole("heading", { name: query })).toBeVisible();
    // Status and priority are the two things a reader follows a request by, and
    // they share one line - matched whole so the history entries below, which
    // repeat every status word, stay out of it.
    await expect(entry.getByRole("paragraph")).toContainText("Priority 80");
    // Every status the request has passed through is listed, newest search first.
    await expect(entry.getByRole("list", { name: "Request history" })).toBeVisible();

    await expectNoAccessibilityViolations(page);

    await entry.getByRole("button", { name: "Cancel" }).click();

    // Cancelling is terminal: the status changes and the action disappears with
    // it. The wait is longer than the default because the screen refetches the
    // list after the mutation and the stack is shared - seen taking more than
    // five seconds while another suite was driving imports.
    await expect(entry.getByRole("paragraph")).toContainText("Cancelled", { timeout: 20_000 });
    await expect(entry.getByRole("button", { name: "Cancel" })).toHaveCount(0);
    await expect(
      entry
        .getByRole("list", { name: "Request history" })
        .getByRole("listitem")
        .filter({ hasText: "Cancelled" }),
    ).toBeVisible();

    // What the screen says is what the server holds, and the history kept both
    // states rather than overwriting the first.
    const stored = await requestById(page, created.id);
    expect(stored.status).toBe("cancelled");
    const history = stored.history.map((entry_) => entry_.status);
    // The history kept every state rather than overwriting the first: it starts
    // where the request started and ends where the reader put it. Anything in
    // between is the automatic search, which is allowed to have run.
    expect(history[0]).toBe("searching");
    expect(history[history.length - 1]).toBe("cancelled");
    expect(history.length).toBeGreaterThanOrEqual(2);
  });
});
