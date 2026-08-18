import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test, vi } from "vitest";
import { renderApp, server, signedIn, useMockApi } from "../../../test/harness";

useMockApi();

afterEach(cleanup);

function folder(overrides: Record<string, unknown> = {}) {
  return {
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
    ...overrides,
  };
}

test("settings offers its areas and root folders is one of them", async () => {
  signedIn();
  server.use(
    http.get("/api/admin/library/root-folders", () => HttpResponse.json([])),
    http.get("/api/admin/quality/definitions", () => HttpResponse.json([])),
    http.get("/api/admin/quality/profiles", () => HttpResponse.json([])),
    http.get("/api/admin/quality/custom-formats", () => HttpResponse.json([])),
  );
  const user = userEvent.setup();

  renderApp("/settings");

  // A bare redirect to quality profiles used to be the whole of settings, which
  // is what kept root folders unreachable.
  const sections = await screen.findByRole("navigation", { name: "Settings areas" });
  expect(sections).not.toBeNull();

  await user.click(screen.getByRole("link", { name: "Root folders" }));

  await screen.findByRole("heading", { name: "Root folders", level: 1 });

  await user.click(screen.getByRole("link", { name: "Quality profiles" }));

  await screen.findByRole("heading", { name: "Quality profiles", level: 1 });
  // The section list survives the move, so the way back is still on screen.
  expect(screen.getByRole("link", { name: "Root folders" })).not.toBeNull();
});

test("lists what the API reports about a folder and adds another", async () => {
  signedIn();
  const created = vi.fn();
  let folders = [folder({ same_filesystem_as_downloads: false })];
  server.use(
    http.get("/api/admin/library/root-folders", () => HttpResponse.json(folders)),
    http.post("/api/admin/library/root-folders", async ({ request }) => {
      created(await request.json());
      const added = folder({ id: "folder-2", path: "/media/archive" });
      folders = [...folders, added];
      return HttpResponse.json(added, { status: 201 });
    }),
  );
  const user = userEvent.setup();

  renderApp("/settings/root-folders");

  await screen.findByRole("heading", { name: "/media/library", level: 3 });
  expect(screen.getByText("1.0 GiB")).not.toBeNull();
  expect(screen.getByText("2.0 GiB")).not.toBeNull();
  expect(screen.getAllByText("Never")).toHaveLength(2);
  expect(
    screen.getByText(
      "This folder is on a different filesystem from downloads, so imports copy the file instead of hardlinking it.",
    ),
  ).not.toBeNull();

  await user.type(screen.getByLabelText("Folder path"), "/media/archive");
  await user.click(screen.getByRole("button", { name: "Add root folder" }));

  await waitFor(() => expect(created).toHaveBeenCalledOnce());
  expect(created.mock.calls[0]?.[0]).toEqual({ path: "/media/archive", enabled: true });
  await screen.findByRole("heading", { name: "/media/archive", level: 3 });
});

test("removing a folder is confirmed before anything is deleted", async () => {
  signedIn();
  const deleted = vi.fn();
  server.use(
    http.get("/api/admin/library/root-folders", () => HttpResponse.json([folder()])),
    http.delete("/api/admin/library/root-folders/folder-1", () => {
      deleted();
      return new HttpResponse(null, { status: 204 });
    }),
  );
  const user = userEvent.setup();

  renderApp("/settings/root-folders");

  await user.click(await screen.findByRole("button", { name: "Remove /media/library" }));

  // The first press asks rather than acts, and says what removal does and does
  // not do while the path is still on screen.
  expect(deleted).not.toHaveBeenCalled();
  expect(screen.getByText(/Nothing on disk is deleted/)).not.toBeNull();

  await user.click(screen.getByRole("button", { name: "Keep folder" }));
  expect(deleted).not.toHaveBeenCalled();
  expect(screen.getByRole("heading", { name: "/media/library", level: 3 })).not.toBeNull();

  await user.click(screen.getByRole("button", { name: "Remove /media/library" }));
  await user.click(screen.getByRole("button", { name: "Remove folder" }));

  await waitFor(() => expect(deleted).toHaveBeenCalledOnce());
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "/media/library", level: 3 })).toBeNull(),
  );
});

test("a folder that still holds media reports why it cannot be removed", async () => {
  signedIn();
  server.use(
    http.get("/api/admin/library/root-folders", () => HttpResponse.json([folder()])),
    http.delete("/api/admin/library/root-folders/folder-1", () =>
      HttpResponse.json(
        { code: "ROOT_FOLDER_HAS_MEDIA", status: 409, context: {} },
        { status: 409 },
      ),
    ),
  );
  const user = userEvent.setup();

  renderApp("/settings/root-folders");

  await user.click(await screen.findByRole("button", { name: "Remove /media/library" }));
  await user.click(screen.getByRole("button", { name: "Remove folder" }));

  // The cause and the next step, from the code — never a server-supplied string.
  await screen.findByText(
    "That root folder still holds media, so removing it would leave those files unreachable.",
  );
  expect(
    screen.getByText(
      "Move or delete the media in that folder on the server first, then remove it here.",
    ),
  ).not.toBeNull();
});
