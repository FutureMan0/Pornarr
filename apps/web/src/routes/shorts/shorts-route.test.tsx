import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test, vi } from "vitest";
import { renderApp, server, signedIn, useMockApi } from "../../test/harness";

useMockApi();

afterEach(cleanup);

function short(overrides: Record<string, unknown> = {}) {
  return {
    id: "short-1",
    media_id: "media-1",
    parent_media_id: "media-1",
    marker_id: null,
    media_title: "Compose Test Scene",
    title: "The opening",
    start_seconds: 5,
    end_seconds: 20,
    duration_seconds: 15,
    source: "manual",
    average_stars: null,
    comment_count: 0,
    ...overrides,
  };
}

const SECOND = short({
  id: "short-2",
  media_id: "media-2",
  media_title: "Second Scene",
  title: "The second one",
  start_seconds: 30,
  end_seconds: 45,
  duration_seconds: 15,
});

/** Every clip in these tests direct-plays; the HLS path needs a browser. */
function playable() {
  return http.get("/api/media/:mediaId/playback-info", () =>
    HttpResponse.json({ direct_play: true }),
  );
}

function videoElement(): HTMLVideoElement {
  const element = document.querySelector("video");
  if (element === null) throw new Error("no player on screen");
  return element;
}

test("the feed is reachable from the primary navigation", async () => {
  signedIn();
  server.use(
    http.get("/api/shorts", () => HttpResponse.json([])),
    // The library is where the walk starts, and it asks for its own facets.
    http.get("/api/library/facets", () =>
      HttpResponse.json({ studios: [], performers: [], tags: [] }),
    ),
  );
  const user = userEvent.setup();

  renderApp("/library");

  // `/api/shorts` was served from the first release with nothing linking to it.
  await user.click(await screen.findByRole("link", { name: "Shorts" }));

  await screen.findByRole("heading", { name: "Shorts", level: 1 });
  expect(screen.getByRole("heading", { name: "There are no shorts yet" })).not.toBeNull();
});

test("the clip starts at its offset and stops at its end", async () => {
  signedIn();
  const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  server.use(
    http.get("/api/shorts", () => HttpResponse.json([short()])),
    playable(),
  );

  renderApp("/shorts");

  await screen.findByRole("heading", { name: "The opening", level: 2 });
  const video = await waitFor(() => {
    const element = videoElement();
    // Direct play points the element at the parent title: a short has no file.
    expect(element.getAttribute("src")).toBe("/api/media/media-1/stream");
    return element;
  });

  // A clip is a window into the title, so the element opens at the window's
  // start rather than at the beginning of the file.
  fireEvent.loadedMetadata(video);
  expect(video.currentTime).toBe(5);

  video.currentTime = 20;
  fireEvent.timeUpdate(video);
  expect(pause).toHaveBeenCalled();

  pause.mockRestore();
});

test("an arrow key and the next button both move to the following clip", async () => {
  signedIn();
  server.use(
    http.get("/api/shorts", () => HttpResponse.json([short(), SECOND])),
    playable(),
  );
  const user = userEvent.setup();

  renderApp("/shorts");

  await screen.findByRole("heading", { name: "The opening", level: 2 });
  expect(screen.getByText("1 of 2")).not.toBeNull();

  fireEvent.keyDown(window, { key: "ArrowDown" });

  await screen.findByRole("heading", { name: "The second one", level: 2 });
  await waitFor(() => expect(videoElement().getAttribute("src")).toBe("/api/media/media-2/stream"));
  fireEvent.loadedMetadata(videoElement());
  expect(videoElement().currentTime).toBe(30);

  // The gesture is a shortcut for a control, never the only way through.
  await user.click(screen.getByRole("button", { name: "Previous clip" }));
  await screen.findByRole("heading", { name: "The opening", level: 2 });
  expect(screen.getByText("1 of 2")).not.toBeNull();
});

test("a clip links back to the title it was cut from", async () => {
  signedIn();
  server.use(
    http.get("/api/shorts", () => HttpResponse.json([short()])),
    playable(),
  );

  renderApp("/shorts");

  const link = await screen.findByRole("link", { name: "Open the full title" });
  expect(link.getAttribute("href")).toBe("/library/media-1");
});
