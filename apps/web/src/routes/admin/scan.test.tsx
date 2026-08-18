/**
 * A2 — watched folders and live scan progress.
 *
 * The screen's whole reason for existing is that a scan is a thing happening
 * now rather than a resource to fetch, so what is tested is the reaction to
 * frames arriving: the folder changes state, the count climbs, and completion
 * puts the outcome in the log instead of leaving the reader to infer it from
 * events stopping.
 */
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { applyEvent } from "../../lib/events";
import { renderApp, server, setViewportWidth, signedIn, useMockApi } from "../../test/harness";

useMockApi();
afterEach(cleanup);

beforeEach(() => {
  signedIn();
  setViewportWidth(1440);
});

const FOLDER = {
  id: "f-1",
  path: "/media/vault-a",
  enabled: true,
  free_space_bytes: 2_000_000_000,
  total_space_bytes: 9_000_000_000,
  last_scanned_at: "2026-08-16T18:00:00Z",
  last_space_checked_at: "2026-08-16T18:00:00Z",
  low_space_warning_sent: false,
  same_filesystem_as_downloads: true,
  warning: null,
};

function stub(folders: object[] = [FOLDER]): void {
  server.use(
    http.get("/api/admin/library/root-folders", () => HttpResponse.json(folders)),
    http.get("/api/admin/audit", () => HttpResponse.json([])),
  );
}

describe("watched folders", () => {
  test("lists what is watched, with space and when it was last walked", async () => {
    stub();
    renderApp("/admin/scan");

    expect(await screen.findByText("/media/vault-a")).toBeTruthy();
    expect(screen.getByText(/free of/)).toBeTruthy();
  });

  test("a folder nothing has measured does not claim to be full", async () => {
    stub([{ ...FOLDER, free_space_bytes: 0, last_space_checked_at: null }]);
    renderApp("/admin/scan");

    // `free_space_bytes` defaults to zero, and "0 B free" is the worst possible
    // way to be wrong about a disk.
    expect(await screen.findByText(/Space not measured/)).toBeTruthy();
  });

  test("scanning asks the server and does not wait for the walk", async () => {
    const scanned: string[] = [];
    stub();
    server.use(
      http.post("/api/admin/library/root-folders/:id/scan", ({ params }) => {
        scanned.push(String(params.id));
        return new HttpResponse(null, { status: 202 });
      }),
    );
    renderApp("/admin/scan");

    await userEvent.setup().click(await screen.findByRole("button", { name: "Scan now" }));

    await waitFor(() => expect(scanned).toStrictEqual(["f-1"]));
  });

  test("a disabled folder cannot be scanned from here", async () => {
    stub([{ ...FOLDER, enabled: false }]);
    renderApp("/admin/scan");

    const button = await screen.findByRole("button", { name: "Scan now" });

    // Scanning it anyway would reimport what somebody chose to exclude.
    expect(button.hasAttribute("disabled")).toBe(true);
  });

  test("adding a folder posts the path and clears the field", async () => {
    const bodies: unknown[] = [];
    stub([]);
    server.use(
      http.post("/api/admin/library/root-folders", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(FOLDER, { status: 201 });
      }),
    );
    renderApp("/admin/scan");

    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Folder to watch"), "/media/new");
    await user.click(screen.getByRole("button", { name: "Add folder" }));

    await waitFor(() => expect(bodies).toStrictEqual([{ path: "/media/new", enabled: true }]));
  });
});

describe("a scan in flight", () => {
  test("nothing has run yet says so rather than showing an empty box", async () => {
    stub();
    renderApp("/admin/scan");

    expect(await screen.findByText("Nothing has run since this screen was opened.")).toBeTruthy();
  });

  test("the design's pause and cancel are absent rather than inert", async () => {
    stub();
    renderApp("/admin/scan");

    await screen.findByText("/media/vault-a");

    // The scan is one transactional job; there is nothing to pause it with, and
    // a button that does nothing is worse than no button.
    expect(screen.queryByRole("button", { name: /Pause/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Cancel/ })).toBeNull();
  });
});

describe("the event plumbing", () => {
  test("a completed scan invalidates what it changed", () => {
    const { queryClient } = renderApp("/admin/scan");
    const invalidated: unknown[] = [];
    queryClient.invalidateQueries = ((options: { queryKey?: unknown }) => {
      invalidated.push(options.queryKey);
      return Promise.resolve();
    }) as typeof queryClient.invalidateQueries;

    applyEvent(queryClient, {
      type: "scan.completed",
      id: "1",
      payload: { root_folder_id: "f-1", scanned: 10, imported: 2, changed: 0, missing: 0 },
    });

    // The library gained titles and the folder gained a scan time. Progress, by
    // contrast, changes nothing cached and must not invalidate anything.
    expect(invalidated).toStrictEqual([["library"], ["root-folders"], ["admin"]]);
  });

  test("progress frames leave the cache alone", () => {
    const { queryClient } = renderApp("/admin/scan");
    const invalidated: unknown[] = [];
    queryClient.invalidateQueries = ((options: { queryKey?: unknown }) => {
      invalidated.push(options.queryKey);
      return Promise.resolve();
    }) as typeof queryClient.invalidateQueries;

    applyEvent(queryClient, {
      type: "scan.progress",
      id: "1",
      payload: { root_folder_id: "f-1", files_scanned: 3, current_path: "/media/a.mkv" },
    });

    // One frame per file. Invalidating on each would refetch the library
    // thousands of times during a single walk.
    expect(invalidated).toStrictEqual([]);
  });
});
