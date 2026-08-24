/**
 * A3 — the queue screen.
 *
 * The two things a queue must not do: report figures that describe only the
 * page that happened to load, and draw a progress bar for a job whose size
 * nobody has measured.
 */
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { renderApp, server, setViewportWidth, signedIn, useMockApi } from "../../test/harness";

useMockApi();
afterEach(cleanup);

beforeEach(() => {
  signedIn();
  setViewportWidth(1440);
});

function job(overrides: Record<string, unknown> = {}) {
  return {
    id: "j-1",
    request_id: "r-1",
    title: "The Long Way",
    client_name: "vault-b",
    protocol: "usenet",
    release_guid: "guid-1",
    status: "downloading",
    priority: 50,
    remaining_bytes: 280,
    size_bytes: 1_000,
    download_speed_bytes: 18_400_000,
    error: null,
    estimated_seconds: 252,
    created_at: "2026-08-16T20:00:00Z",
    queue_estimate: { low_seconds: 240, high_seconds: 300, confidence: "high" },
    ...overrides,
  };
}

const SUMMARY = { active: 4, queued: 11, failed: 2, completed: 7, speed_bytes: 18_400_000 };

function stub(items: object[] = [job()]): URL[] {
  const calls: URL[] = [];
  server.use(
    http.get("/api/queue/summary", () => HttpResponse.json(SUMMARY)),
    http.get("/api/queue", ({ request }) => {
      calls.push(new URL(request.url));
      return HttpResponse.json({ items, next_cursor: null });
    }),
  );
  return calls;
}

describe("the queue", () => {
  test("the cards come from the whole queue, not the loaded rows", async () => {
    // One row on screen, eleven queued behind it. Deriving the cards from the
    // page would say "1" and change as somebody scrolls.
    stub([job()]);
    renderApp("/downloads");

    await screen.findByText("The Long Way");

    expect(screen.getByText("11")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
  });

  test("a tab asks the server for that state", async () => {
    const calls = stub();
    renderApp("/downloads");

    await screen.findByText("The Long Way");
    await userEvent.setup().click(screen.getByRole("button", { name: "Failed" }));

    // The failures are not necessarily on the first page.
    await waitFor(() =>
      expect(calls.some((url) => url.searchParams.get("status") === "failed")).toBe(true),
    );
  });

  test("progress is drawn from what is left against the whole", async () => {
    stub([job({ remaining_bytes: 280, size_bytes: 1_000 })]);
    renderApp("/downloads");

    // The figure is the accessible one; the bar beside it is decoration and is
    // hidden from assistive technology so the percentage is announced once.
    expect(await screen.findByText("72%")).toBeTruthy();
  });

  test("an unmeasured job says so instead of showing a bar at zero", async () => {
    stub([job({ remaining_bytes: null, size_bytes: null })]);
    renderApp("/downloads");

    // A bar at zero reads as a stalled download. Nobody has measured it.
    expect(await screen.findByText("Size not reported")).toBeTruthy();
    expect(screen.queryByText(/%$/)).toBeNull();
  });

  test("a job with no request shows its identifier rather than a made-up name", async () => {
    stub([job({ title: null, release_guid: "abc-123" })]);
    renderApp("/downloads");

    expect(await screen.findByText("abc-123")).toBeTruthy();
  });

  test("a failure is announced, not buried in a status chip", async () => {
    stub([job({ status: "failed", error: "no metadata match" })]);
    renderApp("/downloads");

    const alert = await screen.findByRole("alert");

    expect(alert.textContent).toBe("no metadata match");
  });

  test("an empty state says which emptiness it is", async () => {
    stub([]);
    renderApp("/downloads");

    expect(await screen.findByText("Nothing is downloading.")).toBeTruthy();
  });

  test("completed jobs do not linger in the active tab", async () => {
    // The API has no "active" status; it is the absence of a terminal one, and
    // an unfiltered list still contains the finished ones.
    stub([job(), job({ id: "j-2", title: "Done", status: "completed" })]);
    renderApp("/downloads");

    await screen.findByText("The Long Way");

    expect(screen.queryByText("Done")).toBeNull();
  });

  test("a failure stays in the active tab, where somebody will see it", async () => {
    stub([job(), job({ id: "j-2", title: "Broken", status: "failed", error: "no match" })]);
    renderApp("/downloads");

    // A failed download is not finished business. One you have to switch tabs
    // to discover is one nobody discovers.
    expect(await screen.findByText("Broken")).toBeTruthy();
  });

  /**
   * ADR 0031's consequence, on the second screen that shows an estimate.
   *
   * "Every estimate is returned as a range with a confidence level. A single
   * exact figure would claim precision the system does not have, and the
   * interface is built to display the range." PRODUCT.md L57-59 and
   * DESIGN.md L219-221 say the same thing, and DESIGN.md L219-221 says how:
   * three states, told apart by icon and label rather than by colour.
   *
   * The search row was fixed for this in piece 03. The queue was not, and it is
   * the screen where an estimate is read most often — `/api/queue` has always
   * returned `queue_estimate.confidence`, and `queue-route.tsx` dropped it on
   * the floor twice, in the phone list and in the table.
   */
  test("a measured estimate carries its confidence, not just its range", async () => {
    stub([job({ queue_estimate: { low_seconds: 240, high_seconds: 300, confidence: "high" } })]);
    renderApp("/downloads");

    const row = (await screen.findByText("The Long Way")).closest("tr") as HTMLElement;

    // The range, and then the half that says how much to trust it. Asserted on
    // the row rather than the document so a confidence rendered somewhere else
    // on the screen cannot pass for this one.
    expect(row.textContent).toContain("~4\u20135 min");
    expect(row.textContent).toContain("High");
  });

  test("an unknown estimate says so once, without a confidence beside it", async () => {
    // DESIGN.md L221: "Unknown estimates say 'unknown', never '0'". The
    // indicator has exactly three states, so an unknown range gets the word and
    // nothing else — "Unknown Unknown" would be the fix reading worse than the
    // defect.
    stub([
      job({ queue_estimate: { low_seconds: null, high_seconds: null, confidence: "unknown" } }),
    ]);
    renderApp("/downloads");

    const row = (await screen.findByText("The Long Way")).closest("tr") as HTMLElement;

    expect(row.textContent).toContain("Unknown");
    expect(row.textContent?.match(/Unknown/g)?.length).toBe(1);
  });
});
