/**
 * Shorts, as a feed you land in rather than a grid you browse.
 *
 * The claim being tested is a product one: `/shorts` starts something. A grid
 * in front of it hands the viewer a decision they cannot make from a still, so
 * the grid moved to `/shorts/browse` and the feed took the address.
 *
 * The rest is about not being expensive or trapping anyone: one player at a
 * time, and an address that follows the feed without accumulating a history
 * entry per clip scrolled past.
 */
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import {
  type ObserverHarness,
  currentPath,
  renderApp,
  server,
  setViewportWidth,
  signedIn,
  stubIntersectionObserver,
  useMockApi,
} from "../../test/harness";

useMockApi();

let observer: ObserverHarness;

beforeEach(() => {
  signedIn();
  setViewportWidth(1440);
  observer = stubIntersectionObserver();
});

afterEach(() => {
  observer.restore();
  cleanup();
});

function clip(id: string, title: string) {
  return {
    id,
    media_id: `m-${id}`,
    parent_media_id: `m-${id}`,
    marker_id: null,
    media_title: `Title for ${id}`,
    title,
    start_seconds: 900,
    end_seconds: 960,
    duration_seconds: 60,
    source: "marker",
    average_stars: 4.5,
    comment_count: 3,
  };
}

const FEED = [clip("s-1", "the good bit"), clip("s-2", "the other bit"), clip("s-3", "a third")];

function stub(clips = FEED): void {
  server.use(
    http.get("/api/shorts", () => HttpResponse.json(clips)),
    http.get("/api/watchlist", () => HttpResponse.json([])),
    http.get("/api/media/:mediaId/playback-info", () => HttpResponse.json({ direct_play: true })),
  );
}

describe("landing in the feed", () => {
  test("/shorts plays the first clip rather than showing a wall of stills", async () => {
    stub();
    renderApp("/shorts");

    await screen.findByRole("heading", { name: "the good bit", level: 2 });

    // A player, present and mounted, not a thumbnail waiting to be chosen.
    expect(document.querySelectorAll("video")).toHaveLength(1);
  });

  test("only the clip on screen has a player", async () => {
    stub();
    renderApp("/shorts");

    await screen.findByRole("heading", { name: "the good bit", level: 2 });

    // Three panes, one video. Ten decoding at once is how a feed becomes a fan.
    expect(screen.getAllByRole("article")).toHaveLength(3);
    expect(document.querySelectorAll("video")).toHaveLength(1);
  });

  test("an address naming a clip opens on that clip", async () => {
    stub();
    renderApp("/shorts/s-3");

    // Scrolled to the third pane, so a link from a title lands where it points.
    await waitFor(() => expect(observer.scrolledTo).toContain(2));
  });

  test("the down arrow moves one clip", async () => {
    stub();
    renderApp("/shorts");

    await screen.findByRole("heading", { name: "the good bit", level: 2 });
    fireEvent.keyDown(window, { key: "ArrowDown" });

    await waitFor(() => expect(observer.scrolledTo).toContain(1));
  });

  test("the address follows the feed without stacking history", async () => {
    stub();
    const { router } = renderApp("/shorts");

    await screen.findByRole("heading", { name: "the good bit", level: 2 });
    const before = router.state.historyAction;
    // The observer fires outside React's knowledge, exactly as the browser's
    // does; `act` is what makes the resulting state change flush here.
    act(() => observer.notify(1));

    await waitFor(() => expect(currentPath(router)).toBe("/shorts/s-2"));
    // A feed that pushes per clip turns Back into a re-run of everything
    // scrolled past.
    expect(router.state.historyAction).toBe(before);
  });

  test("scrolling is not undone by the address the feed itself wrote", async () => {
    stub();
    renderApp("/shorts");

    await screen.findByRole("heading", { name: "the good bit", level: 2 });
    act(() => observer.notify(1));
    // Long enough for the URL to be written and every effect to settle.
    await new Promise((resolve) => setTimeout(resolve, 50));

    // The feed writes the address as it scrolls. An anchor that watches the
    // address is an anchor watching its own output, and it drags the reader
    // back to the first clip on the first scroll.
    const playing = document.querySelector("video")?.closest("[data-index]");
    expect(playing?.getAttribute("data-index")).toBe("1");
  });

  test("a clip says what it was cut from, and where", async () => {
    stub();
    renderApp("/shorts");

    const source = await screen.findByRole("link", { name: "Title for s-1" });

    expect(source.getAttribute("href")).toBe("/library/m-s-1");
    // Every pane carries its own timestamp, so the assertion has to name which.
    expect(source.parentElement?.textContent).toContain("15:00");
  });

  test("an empty feed explains where clips come from", async () => {
    stub([]);
    renderApp("/shorts");

    // A fresh server has nothing watched behind it, which is a state rather
    // than a fault.
    expect(await screen.findByText(/cut from watch hotspots/)).toBeTruthy();
  });
});

describe("the browse view", () => {
  test("is reachable from the feed and shaped for clips", async () => {
    stub();
    renderApp("/shorts/browse");

    await screen.findByText("the good bit");

    // 9/16, because a clip shaped like a film gets opened by somebody
    // expecting a film.
    const frames = [...document.querySelectorAll("[style*='--art-ratio']")];
    expect(frames.length).toBeGreaterThan(0);
    for (const frame of frames) {
      expect(frame.getAttribute("style")).toContain("9 / 16");
    }
  });

  test("a tile leads into the feed at that clip", async () => {
    stub();
    renderApp("/shorts/browse");

    const link = await screen.findByRole("link", { name: /the good bit/ });

    expect(link.getAttribute("href")).toBe("/shorts/s-1");
  });

  test("sorting re-queries rather than reordering the page", async () => {
    const sorts: (string | null)[] = [];
    server.use(
      http.get("/api/shorts", ({ request }) => {
        sorts.push(new URL(request.url).searchParams.get("sort"));
        return HttpResponse.json(FEED);
      }),
    );
    renderApp("/shorts/browse");

    await screen.findByText("the good bit");
    await userEvent.setup().click(screen.getByRole("button", { name: "Top rated" }));

    await waitFor(() => expect(sorts).toContain("top"));
  });
});
