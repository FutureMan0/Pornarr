import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeAll, expect, test } from "vitest";
import { renderApp, server, signedIn, useMockApi } from "../../test/harness";

useMockApi();

afterEach(cleanup);

/**
 * jsdom has no ResizeObserver, and both the grid and its virtualizer measure
 * themselves through one. This reports a fixed viewport the moment either
 * starts observing, which is the only part of the API they use.
 */
beforeAll(() => {
  const size = { inlineSize: 1200, blockSize: 800 };
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    writable: true,
    value: class {
      #callback: (entries: unknown[]) => void;

      constructor(callback: (entries: unknown[]) => void) {
        this.#callback = callback;
      }

      observe(target: Element): void {
        this.#callback([
          {
            target,
            borderBoxSize: [size],
            contentRect: { width: size.inlineSize, height: size.blockSize },
          },
        ]);
      }

      unobserve(): void {}
      disconnect(): void {}
    },
  });
});

function item(overrides: Record<string, unknown> = {}) {
  return {
    id: "m-1",
    title: "Local title",
    studio: null,
    release_date: null,
    duration_seconds: null,
    quality: null,
    resolution: null,
    position_seconds: null,
    progress_duration_seconds: null,
    poster_url: "/api/media/m-1/poster",
    sprite_url: null,
    peer_id: null,
    peer_name: null,
    ...overrides,
  };
}

test("an empty library with no root folder offers the screen that fixes it", async () => {
  signedIn();
  server.use(http.get("/api/admin/library/root-folders", () => HttpResponse.json([])));

  renderApp("/library");

  await screen.findByRole("heading", { name: "Your library is empty" });
  const action = await screen.findByRole("link", { name: "Add a root folder" });
  // Naming the fix is what the old copy did; this has to reach it.
  expect(action.getAttribute("href")).toBe("/settings/root-folders");
});

test("once a root folder exists the empty library asks for a request instead", async () => {
  signedIn();
  server.use(
    http.get("/api/admin/library/root-folders", () =>
      HttpResponse.json([
        {
          id: "folder-1",
          path: "/media/library",
          enabled: true,
          free_space_bytes: 1_073_741_824,
          total_space_bytes: 2_147_483_648,
          last_scanned_at: null,
          last_space_checked_at: null,
          low_space_warning_sent: false,
          same_filesystem_as_downloads: true,
          warning: null,
        },
      ]),
    ),
  );

  renderApp("/library");

  const action = await screen.findByRole("link", { name: "Open requests" });
  expect(action.getAttribute("href")).toBe("/requests");
  expect(screen.queryByRole("link", { name: "Add a root folder" })).toBeNull();
});

test("picking a facet filters the library by that value", async () => {
  signedIn();
  const requested: string[] = [];
  server.use(
    http.get("/api/library/facets", () =>
      HttpResponse.json({
        studios: [{ value: "Probe Studio", count: 2 }],
        performers: [],
        tags: [{ value: "Solo", count: 1 }],
      }),
    ),
    http.get("/api/library", ({ request }) => {
      requested.push(new URL(request.url).search);
      return HttpResponse.json({ items: [], next_offset: null });
    }),
  );

  renderApp("/library");
  const user = userEvent.setup();

  // The options are the library's own values, with how many carry them.
  await screen.findByRole("option", { name: "Probe Studio (2)" });
  await user.selectOptions(screen.getByLabelText("Studio"), "Probe Studio");

  await waitFor(() => {
    expect(requested.some((search) => search.includes("studio=Probe+Studio"))).toBe(true);
  });
  expect(
    await screen.findByRole("heading", { name: "Nothing matches these filters" }),
  ).toBeTruthy();
});

test("the source picker browses every library and each card says where it came from", async () => {
  signedIn();
  const requested: string[] = [];
  server.use(
    http.get("/api/library/facets", () =>
      HttpResponse.json({ studios: [], performers: [], tags: [] }),
    ),
    http.get("/api/admin/peers", () =>
      HttpResponse.json([
        {
          id: "peer-1",
          name: "Anna's Pornarr",
          base_url: "https://pornarr.example.net",
          enabled: true,
          health: "healthy",
          health_reason: null,
          last_tested_at: null,
          media_count: 2,
        },
      ]),
    ),
    http.get("/api/library", ({ request }) => {
      const query = new URL(request.url).searchParams;
      requested.push(query.toString());
      if (query.get("source") !== "all")
        return HttpResponse.json({ items: [item()], next_offset: null });
      const cursor = query.get("cursor");
      return HttpResponse.json({
        items:
          cursor === null
            ? [
                item(),
                item({
                  id: "m-2",
                  title: "Borrowed",
                  peer_id: "peer-1",
                  peer_name: "Anna's Pornarr",
                }),
              ]
            : [item({ id: "m-3", title: "The next page" })],
        // Merged pages count from no single zero, so paging is by cursor.
        next_offset: null,
        next_cursor: cursor === null ? "page-2" : null,
      });
    }),
  );

  renderApp("/library");
  const user = userEvent.setup();

  await screen.findByRole("option", { name: "All libraries" });
  // By role, because the sidebar has a "Library" link with the same name.
  await user.selectOptions(screen.getByRole("combobox", { name: "Library" }), "all");

  await waitFor(() => expect(requested.some((query) => query.includes("source=all"))).toBe(true));
  // The peer only becomes a destination once the reader has left their own
  // library; before that the picker never asks an administrator-only endpoint.
  expect(await screen.findByRole("option", { name: "Anna's Pornarr" })).toBeTruthy();
  expect(await screen.findByText("From Anna's Pornarr")).toBeTruthy();
  expect(screen.getByText("From this library")).toBeTruthy();

  const scroller = document.querySelector(".overflow-auto");
  if (scroller === null) throw new Error("the grid did not render");
  fireEvent.scroll(scroller);

  await waitFor(() =>
    expect(requested.some((query) => query.includes("cursor=page-2"))).toBe(true),
  );
});
