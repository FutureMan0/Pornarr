import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test } from "vitest";
import { renderApp, server, signedIn, useMockApi } from "../../test/harness";

useMockApi();

afterEach(cleanup);

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
  expect(await screen.findByRole("heading", { name: "Nothing matches these filters" })).toBeTruthy();
});
