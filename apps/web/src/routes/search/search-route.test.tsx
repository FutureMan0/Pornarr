/** The search workspace keeps its input state in the address and starts both sources. */
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, test, vi } from "vitest";
import { renderApp, server, signedIn, useMockApi } from "../../test/harness";

useMockApi();
afterEach(cleanup);

const SEARCH_ID = "2e4e6d7f-1225-4ad9-af59-a72a09a6e7f0";
const RELEASE_ID = "3a168277-0444-47ea-bcff-d8c90028ddfd";

function searchHandlers() {
  const local = vi.fn();
  const started = vi.fn();
  const grabbed = vi.fn();
  server.use(
    http.get("/api/search/local", ({ request }) => {
      local(new URL(request.url).searchParams);
      return HttpResponse.json({
        items: [
          {
            id: "835a30aa-f958-4ea9-9cf9-c2adb9ddd1dc",
            title: "Example Scene",
            studio: "Studio",
            release_date: "2026-08-14",
            quality: "1080p",
            resolution: "1080p",
            size: 2_000_000,
            duration_seconds: null,
            performers: [],
            tags: [],
            relevance: 1,
          },
        ],
        next_cursor: null,
      });
    }),
    http.post("/api/search/indexers", async ({ request }) => {
      started(await request.json());
      return HttpResponse.json({ id: SEARCH_ID }, { status: 202 });
    }),
    http.get(`/api/search/indexers/${SEARCH_ID}`, () =>
      HttpResponse.json({
        id: SEARCH_ID,
        query: "Example",
        cancelled: false,
        statuses: { "d4b9b58f-9a2b-4781-a637-71d35f7b887f": "completed" },
        items: [
          {
            id: RELEASE_ID,
            guid: "example-guid",
            title: "Example Scene 1080p",
            indexer_id: "d4b9b58f-9a2b-4781-a637-71d35f7b887f",
            indexer_name: "Example Indexer",
            protocol: "torrent",
            quality: "1080p",
            size: 2_000_000,
            published_at: "2026-08-14T12:00:00Z",
            seeders: 12,
            estimate: { low_seconds: 60, high_seconds: 120, confidence: "high" },
          },
        ],
      }),
    ),
    http.post("/api/requests", async ({ request }) => {
      grabbed({ request: await request.json() });
      return HttpResponse.json({ id: "c1c1c1c1-1111-4111-8111-c1c1c1c1c1c1" }, { status: 201 });
    }),
    http.post("/api/requests/c1c1c1c1-1111-4111-8111-c1c1c1c1c1c1/grab", async ({ request }) => {
      grabbed({ grab: await request.json() });
      return HttpResponse.json(
        {
          request_id: "c1c1c1c1-1111-4111-8111-c1c1c1c1c1c1",
          download_job_id: "c3c3c3c3-3333-4333-8333-c3c3c3c3c3c3",
          status: "queued",
        },
        { status: 201 },
      );
    }),
  );
  return { local, started, grabbed };
}

describe("search workspace", () => {
  test("keeps filters in the URL while local and indexer results arrive independently", async () => {
    signedIn();
    const calls = searchHandlers();
    const user = userEvent.setup();
    renderApp("/search?q=Example&minimum_size=1000000&sort=size");

    expect(await screen.findByRole("heading", { name: "In your library" })).toBeTruthy();
    expect(await screen.findByText("Example Scene")).toBeTruthy();
    expect(await screen.findByText("Example Scene 1080p")).toBeTruthy();
    expect(calls.local.mock.calls[0]?.[0].get("minimum_size_bytes")).toBe("1000000");
    expect(calls.local.mock.calls[0]?.[0].get("sort")).toBe("size");
    expect(calls.started).toHaveBeenCalledWith({ q: "Example" });

    await user.selectOptions(screen.getByLabelText("Quality"), "1080p");

    expect((screen.getByLabelText("Find a title") as HTMLInputElement).value).toBe("Example");
    await waitFor(() => expect(calls.local.mock.calls.at(-1)?.[0].get("quality")).toBe("1080p"));
  });

  test("creates a selected request before grabbing its cached release", async () => {
    signedIn();
    const calls = searchHandlers();
    const user = userEvent.setup();
    renderApp("/search?q=Example");

    await user.click(await screen.findByRole("button", { name: "Grab" }));

    await waitFor(() => expect(calls.grabbed).toHaveBeenCalledTimes(2));
    expect(calls.grabbed.mock.calls).toEqual([
      [
        {
          request: {
            query: "Example Scene 1080p",
            selected_release_guid: "example-guid",
            priority: 50,
          },
        },
      ],
      [{ grab: { release_id: RELEASE_ID } }],
    ]);
  });
});
