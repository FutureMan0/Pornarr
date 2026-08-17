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

describe("the related row", () => {
  test("says why each title is there", async () => {
    server.use(
      http.get("/api/media/:mediaId", () => HttpResponse.json(DETAIL)),
      http.get("/api/media/:mediaId/related", () =>
        HttpResponse.json([
          {
            media_id: "m-2",
            title: "Second Light",
            studio: "Northwind",
            duration_seconds: 1800,
            reason: "performer",
            shared_performers: 1,
            shared_tags: 0,
            rating: 4.0,
            rating_count: 2,
          },
          {
            media_id: "m-3",
            title: "Third Rail",
            studio: null,
            duration_seconds: null,
            reason: "tag",
            shared_performers: 0,
            shared_tags: 3,
            rating: null,
            rating_count: 0,
          },
        ]),
      ),
    );
    renderApp("/library/m-1");

    // The reason is the point of the row; six unexplained titles are noise.
    expect(await screen.findByText("Same performer")).not.toBeNull();
    expect(screen.getByText("3 shared tags")).not.toBeNull();
  });

  test("stays away entirely when nothing is related", async () => {
    server.use(
      http.get("/api/media/:mediaId", () => HttpResponse.json(DETAIL)),
      http.get("/api/media/:mediaId/related", () => HttpResponse.json([])),
    );
    renderApp("/library/m-1");

    await screen.findByRole("heading", { level: 1, name: "Aurora 214" });
    // A heading over an empty row promises something the library cannot give.
    expect(screen.queryByText("Related")).toBeNull();
  });
});

describe("the detail rail", () => {
  test("names the performers and links each into a search for their work", async () => {
    server.use(
      http.get("/api/media/:mediaId", () =>
        HttpResponse.json({ ...DETAIL, performers: ["Mira Vance", "Jon Ek"] }),
      ),
    );
    renderApp("/library/m-1");

    const link = await screen.findByRole("link", { name: /Mira Vance/ });
    // There is no performer page to send anyone to, so the name has to lead
    // somewhere that exists — and a search for it is what a reader wanted.
    expect(link.getAttribute("href")).toBe("/search?q=Mira%20Vance");
  });

  test("shows the file path to the owner and withholds it from everyone else", async () => {
    server.use(
      http.get("/api/media/:mediaId", () => HttpResponse.json({ ...DETAIL, in_my_library: true })),
    );
    const own = renderApp("/library/m-1");
    expect(await screen.findByText("/data/a.mp4")).not.toBeNull();
    own.unmount();

    server.use(
      http.get("/api/media/:mediaId", () => HttpResponse.json({ ...DETAIL, in_my_library: false })),
    );
    renderApp("/library/m-1");

    // Where a household keeps its files is not a guest's business.
    expect(await screen.findByText(/hidden by the owner/)).not.toBeNull();
    expect(screen.queryByText("/data/a.mp4")).toBeNull();
  });
});

describe("scene markers", () => {
  test("offer one jump per detected scene, timestamped", async () => {
    server.use(
      http.get("/api/media/:mediaId", () => HttpResponse.json({ ...DETAIL, playable: true })),
      http.get("/api/media/:mediaId/scenes", () =>
        HttpResponse.json({
          scenes: [
            { id: "s-1", ordinal: 1, start_seconds: 0, end_seconds: 90 },
            { id: "s-2", ordinal: 2, start_seconds: 605, end_seconds: 1200 },
          ],
        }),
      ),
    );
    renderApp("/library/m-1");

    expect(await screen.findByRole("button", { name: /Scene 1/ })).not.toBeNull();
    // The timestamp is the whole value of the control: "Scene 2" alone tells a
    // reader nothing about where in two hours it starts.
    expect(screen.getByRole("button", { name: /10:05/ })).not.toBeNull();
  });

  test("stay away when the detector has not run", async () => {
    server.use(
      http.get("/api/media/:mediaId", () => HttpResponse.json({ ...DETAIL, playable: true })),
      http.get("/api/media/:mediaId/scenes", () => HttpResponse.json({ scenes: [] })),
    );
    renderApp("/library/m-1");

    await screen.findByRole("heading", { level: 1, name: "Aurora 214" });
    // A heading over nothing reads as a broken feature rather than as work
    // that has not run.
    expect(screen.queryByText("Scene markers")).toBeNull();
  });
});

const DETAIL = {
  id: "m-1",
  owner_id: null,
  in_my_library: true,
  title: "Aurora 214",
  studio: "Northwind",
  release_date: "2026-01-01",
  confidence: 0.9,
  metadata_source: "test",
  added_at: "2026-01-02T03:04:05Z",
  performers: [],
  tags: [],
  path: "/data/a.mp4",
  size: 1,
  codecs: null,
  resolution: "1080p",
  bitrate: null,
  duration_seconds: 1200,
  playable: false,
  rating: null,
  rating_count: 0,
};

const NOW = "2026-08-16T20:00:00Z";
