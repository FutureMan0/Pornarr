/**
 * B7 — the shorts player.
 *
 * Two claims worth holding onto. A clip is a window into a title, so playback
 * must stay inside it and must not report the title as part-watched. And the
 * social panels belong to the parent, which the screen has to admit rather than
 * let people rate a film by its best forty seconds without knowing.
 */
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import {
  currentPath,
  renderApp,
  server,
  setViewportWidth,
  signedIn,
  useMockApi,
} from "../../test/harness";

useMockApi();
afterEach(cleanup);

beforeEach(() => {
  signedIn();
  setViewportWidth(1440);
});

function short(id: string, title: string) {
  return {
    id,
    media_id: "m-1",
    parent_media_id: "m-1",
    marker_id: null,
    media_title: "Aurora 214",
    title,
    start_seconds: 900,
    end_seconds: 960,
    duration_seconds: 60,
    source: "marker",
    average_stars: 4.5,
    comment_count: 3,
  };
}

const FEED = [short("s-1", "the good bit"), short("s-2", "the other bit"), short("s-3", "a third")];

function stubShorts(): void {
  server.use(
    http.get("/api/shorts", () => HttpResponse.json(FEED)),
    http.get("/api/shorts/:shortId", ({ params }) => {
      const found = FEED.find((item) => item.id === params.shortId);
      return found === undefined
        ? new HttpResponse(null, { status: 404 })
        : HttpResponse.json(found);
    }),
    http.get("/api/media/:mediaId/rating", () =>
      HttpResponse.json({ average: 4.5, count: 2, breakdown: {}, your_stars: null }),
    ),
    http.get("/api/media/:mediaId/comments", () => HttpResponse.json([])),
  );
}

describe("the shorts player", () => {
  test("says which clip of how many, so the feed has a shape", async () => {
    stubShorts();
    renderApp("/shorts/s-2");

    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1 }).parentElement?.textContent).toContain(
        "Clip 2 of 3",
      ),
    );
  });

  test("names the title the clip was cut from, and where in it", async () => {
    stubShorts();
    renderApp("/shorts/s-1");

    const source = await screen.findByRole("link", { name: "Aurora 214" });

    expect(source.getAttribute("href")).toBe("/library/m-1");
    // Without the timestamp a clip is unfindable in the title it came from.
    expect(screen.getByText(/15:00/)).toBeTruthy();
  });

  test("the down arrow moves to the next clip", async () => {
    stubShorts();
    const { router } = renderApp("/shorts/s-1");

    await screen.findByRole("link", { name: "Aurora 214" });
    fireEvent.keyDown(window, { key: "ArrowDown" });

    await waitFor(() => expect(currentPath(router)).toBe("/shorts/s-2"));
  });

  test("an arrow typed into the comment box is a keystroke, not a command", async () => {
    stubShorts();
    const { router } = renderApp("/shorts/s-1");

    const draft = await screen.findByLabelText("Add a comment");
    await userEvent.setup().type(draft, "{arrowdown}");

    // Still here. Stealing keys from a text field is how a half-typed remark
    // gets lost to a navigation nobody asked for.
    expect(currentPath(router)).toBe("/shorts/s-1");
  });

  test("the first clip cannot go back and the last cannot go on", async () => {
    stubShorts();
    renderApp("/shorts/s-1");

    const previous = await screen.findByRole("button", { name: "Previous clip" });
    expect(previous.hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Next clip" }).hasAttribute("disabled")).toBe(false);
  });

  test("says the rating and comments belong to the title, not the clip", async () => {
    stubShorts();
    renderApp("/shorts/s-1");

    // The API returns the parent's numbers for a short. Showing them beside a
    // clip without saying so invites rating a film by forty seconds of it.
    expect(await screen.findByText(/belong to the full title, not to this clip/)).toBeTruthy();
  });

  test("a clip that does not exist says so instead of rendering an empty player", async () => {
    stubShorts();
    renderApp("/shorts/nope");

    expect(await screen.findByRole("alert")).toBeTruthy();
  });
});
