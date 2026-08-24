/**
 * A4 — the tag screen.
 *
 * Merge is destructive and cannot be undone, so the thing worth pinning is that
 * the screen never chooses the survivor on the administrator's behalf.
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

const TAGS = [
  { id: "t-1", name: "low-light", media_count: 412, last_used_at: "2026-08-14T10:00:00Z" },
  { id: "t-2", name: "lowlight", media_count: 3, last_used_at: "2026-01-02T10:00:00Z" },
  { id: "t-3", name: "orphan", media_count: 0, last_used_at: null },
];

interface Calls {
  readonly merges: { into: string; body: unknown }[];
  readonly renames: { id: string; body: unknown }[];
  readonly deletes: string[];
  readonly lists: URL[];
}

function stub(tags: object[] = TAGS): Calls {
  const calls: Calls = { merges: [], renames: [], deletes: [], lists: [] };
  server.use(
    http.get("/api/admin/tags", ({ request }) => {
      calls.lists.push(new URL(request.url));
      return HttpResponse.json(tags);
    }),
    http.post("/api/admin/tags/:id/merge", async ({ params, request }) => {
      calls.merges.push({ into: String(params.id), body: await request.json() });
      return HttpResponse.json(TAGS[0]);
    }),
    http.patch("/api/admin/tags/:id", async ({ params, request }) => {
      calls.renames.push({ id: String(params.id), body: await request.json() });
      return HttpResponse.json(TAGS[0]);
    }),
    http.delete("/api/admin/tags/:id", ({ params }) => {
      calls.deletes.push(String(params.id));
      return new HttpResponse(null, { status: 204 });
    }),
  );
  return calls;
}

describe("the tag list", () => {
  test("shows how many titles carry each tag, including none", async () => {
    stub();
    renderApp("/admin/tags");

    await screen.findByText("low-light");

    // A tag on nothing is the first thing worth removing; hiding the zero is
    // how a tag list becomes unmaintainable.
    expect(screen.getByText("0")).toBeTruthy();
    expect(screen.getByText("never")).toBeTruthy();
  });

  test("searching asks the server", async () => {
    const calls = stub();
    renderApp("/admin/tags");

    await userEvent.setup().type(await screen.findByLabelText("Search tags"), "low");

    await waitFor(() =>
      expect(calls.lists.some((url) => url.searchParams.get("q") === "low")).toBe(true),
    );
  });
});

describe("merging", () => {
  test("asks which tag survives instead of picking one", async () => {
    const calls = stub();
    renderApp("/admin/tags");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("checkbox", { name: "Select low-light" }));
    await user.click(screen.getByRole("checkbox", { name: "Select lowlight" }));

    // Not one "Merge" button: a merge cannot be undone, and choosing the
    // survivor by list order is a decision made by an implementation detail.
    expect(screen.getByText("Merge into")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "low-light" }));

    await waitFor(() =>
      expect(calls.merges).toStrictEqual([{ into: "t-1", body: { source_ids: ["t-2"] } }]),
    );
  });

  test("is not offered for a single tag", async () => {
    stub();
    renderApp("/admin/tags");

    await userEvent
      .setup()
      .click(await screen.findByRole("checkbox", { name: "Select low-light" }));

    expect(screen.queryByText("Merge into")).toBeNull();
  });
});

describe("renaming and deleting", () => {
  test("renaming sends the new name for that tag", async () => {
    const calls = stub();
    renderApp("/admin/tags");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("checkbox", { name: "Select lowlight" }));
    await user.click(screen.getByRole("button", { name: "Rename" }));
    const field = screen.getByLabelText("New name");
    await user.clear(field);
    await user.type(field, "low light");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(calls.renames).toStrictEqual([{ id: "t-2", body: { name: "low light" } }]),
    );
  });

  test("deleting is offered only for one tag at a time", async () => {
    const calls = stub();
    renderApp("/admin/tags");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("checkbox", { name: "Select orphan" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(calls.deletes).toStrictEqual(["t-3"]));

    // With two selected there is no single thing to delete, and a bulk delete
    // of tags is not something to offer casually.
    await user.click(await screen.findByRole("checkbox", { name: "Select low-light" }));
    await user.click(screen.getByRole("checkbox", { name: "Select lowlight" }));
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });
});

describe("what is not here", () => {
  test("no group column and no guest-visible eye", async () => {
    stub();
    renderApp("/admin/tags");

    await screen.findByText("low-light");

    // Neither exists in the schema. A control that governs nothing is worse
    // than a missing one on a screen about who can see what.
    expect(screen.queryByText("Group")).toBeNull();
    expect(screen.queryByText(/Guest visible/i)).toBeNull();
  });
});
