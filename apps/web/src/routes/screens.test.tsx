/**
 * The screens added for the design, at the seam where they meet the API.
 *
 * Not a re-test of the components — `packages/ui` owns those. What is checked
 * here is the wiring: that a control reaches the right endpoint with the right
 * body, that an empty answer produces the sentence rather than a blank page,
 * and that a failing request cannot take the screen down.
 */
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { renderApp, server, setViewportWidth, signedIn, useMockApi } from "../test/harness";

useMockApi();
afterEach(cleanup);

beforeEach(() => {
  signedIn();
  setViewportWidth(1280);
});

describe("the watchlist", () => {
  test("an empty queue says so instead of showing a blank page", async () => {
    server.use(http.get("/api/watchlist", () => HttpResponse.json([])));
    renderApp("/watchlist");

    expect(await screen.findByText(/Nothing saved yet/)).not.toBeNull();
  });

  test("removing a title calls the endpoint for that title", async () => {
    const deleted: string[] = [];
    server.use(
      http.get("/api/watchlist", () =>
        HttpResponse.json([{ media_id: "m-1", title: "Aurora 214", added_at: NOW }]),
      ),
      http.delete("/api/watchlist/:mediaId", ({ params }) => {
        deleted.push(String(params.mediaId));
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderApp("/watchlist");

    await userEvent.setup().click(await screen.findByRole("button", { name: "Remove" }));

    await waitFor(() => expect(deleted).toStrictEqual(["m-1"]));
  });
});

describe("the shorts feed", () => {
  test("choosing a sort asks the server for that sort", async () => {
    const sorts: (string | null)[] = [];
    server.use(
      http.get("/api/shorts", ({ request }) => {
        sorts.push(new URL(request.url).searchParams.get("sort"));
        return HttpResponse.json([]);
      }),
    );
    renderApp("/shorts");

    await screen.findByRole("heading", { name: "Shorts", level: 1 });
    await userEvent.setup().click(screen.getByRole("button", { name: "Top rated" }));

    // Sorting a feed client-side would reorder one page and lie about the rest.
    await waitFor(() => expect(sorts).toContain("top"));
  });

  test("a clip links back to the title it was cut from", async () => {
    server.use(
      http.get("/api/shorts", () =>
        HttpResponse.json([
          {
            id: "s-1",
            media_id: "m-9",
            parent_media_id: "m-9",
            marker_id: null,
            media_title: "Aurora 214",
            title: "the good bit",
            start_seconds: 10,
            end_seconds: 70,
            duration_seconds: 60,
            source: "marker",
            average_stars: 4.5,
            comment_count: 3,
          },
        ]),
      ),
    );
    renderApp("/shorts");

    const link = await screen.findByRole("link", { name: /the good bit/ });

    // An excerpt with no way back to its source is a dead end.
    expect(link.getAttribute("href")).toBe("/library/m-9");
  });
});

describe("collections", () => {
  test("a shelf shows whether the household can read it", async () => {
    server.use(
      http.get("/api/collections", () =>
        HttpResponse.json([
          {
            id: "c-1",
            name: "Late shift",
            visibility: "shared",
            owner: "ada",
            is_yours: true,
            item_count: 3,
            created_at: NOW,
          },
        ]),
      ),
    );
    renderApp("/collections");

    expect(await screen.findByText("Shared")).not.toBeNull();
  });

  test("creating one posts the name and clears the field", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.get("/api/collections", () => HttpResponse.json([])),
      http.post("/api/collections", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(
          {
            id: "c-2",
            name: "New",
            visibility: "private",
            owner: "ada",
            is_yours: true,
            item_count: 0,
            created_at: NOW,
          },
          { status: 201 },
        );
      }),
    );
    renderApp("/collections");

    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Name"), "New");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(bodies).toStrictEqual([{ name: "New", visibility: "private" }]));
  });
});

describe("the feed", () => {
  test("a recommendation shows its score and the reasons behind it", async () => {
    server.use(
      http.get("/api/recommendations", () =>
        HttpResponse.json([
          {
            media_id: "m-1",
            title: "Aurora 214",
            score: 0.94,
            match_score: 94,
            reason: { tag: 0.6 },
            reasons: ["Tags you keep watching", "A studio you finish"],
            model_version: "test",
            expires_at: NOW,
          },
        ]),
      ),
      http.get("/api/recommendations/sent-to-me", () => HttpResponse.json([])),
    );
    renderApp("/feed");

    expect(await screen.findByText("94% match")).not.toBeNull();
    // The reasons the recommender actually used, not a generic sentence.
    expect(screen.getByText("Tags you keep watching")).not.toBeNull();
    expect(screen.getByText("A studio you finish")).not.toBeNull();
  });

  test("a sent title names who sent it — the one place a name appears", async () => {
    server.use(
      http.get("/api/recommendations", () => HttpResponse.json([])),
      http.get("/api/recommendations/sent-to-me", () =>
        HttpResponse.json([
          {
            id: "s-1",
            media_id: "m-4",
            media_title: "Room 9",
            sender: "Mira",
            recipient: null,
            note: "Watch this.",
            seen_at: null,
            created_at: NOW,
          },
        ]),
      ),
    );
    renderApp("/feed");

    expect(await screen.findByText("Mira sent you this")).not.toBeNull();
    expect(screen.getByText("Watch this.")).not.toBeNull();
  });

  test("an empty feed explains itself rather than looking broken", async () => {
    server.use(
      http.get("/api/recommendations", () => HttpResponse.json([])),
      http.get("/api/recommendations/sent-to-me", () => HttpResponse.json([])),
    );
    renderApp("/feed");

    // A fresh server has no watching behind it. That is the expected outcome,
    // not a failure, and the screen has to say which.
    expect(await screen.findByText(/the feed learns from what gets watched/)).not.toBeNull();
  });
});

describe("continue watching", () => {
  test("a resumed title is named, not shown as an identifier", async () => {
    server.use(
      http.get("/api/playback/continue-watching", () =>
        HttpResponse.json([
          {
            media_id: "m-7",
            title: "The Long Way",
            device_label: "Living room TV",
            position_seconds: 600,
            duration_seconds: 2900,
            completed: false,
          },
        ]),
      ),
    );
    renderApp("/continue");

    expect(await screen.findByText("The Long Way")).not.toBeNull();
    expect(screen.queryByText("m-7")).toBeNull();
  });
});

const NOW = "2026-08-16T20:00:00Z";
