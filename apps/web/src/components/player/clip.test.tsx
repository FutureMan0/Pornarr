/**
 * Playing part of a title.
 *
 * jsdom has no media pipeline, so nothing here plays. What it does have is the
 * element and its events, which is exactly where the clip logic lives: seek in
 * on metadata, stop at the end mark, and — the one that would be invisible
 * until a user complained — never report a clip's offset as the film's
 * position.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { server, useMockApi } from "../../test/harness";
import { VideoPlayer } from "./video-player";

useMockApi();
afterEach(cleanup);

/** jsdom leaves `currentTime` writable but never advances it; we do. */
function at(video: HTMLVideoElement, seconds: number): void {
  Object.defineProperty(video, "currentTime", {
    value: seconds,
    writable: true,
    configurable: true,
  });
}

function video(): HTMLVideoElement {
  const element = document.querySelector("video");
  if (element === null) throw new Error("the player rendered no video element");
  return element;
}

beforeEach(() => {
  server.use(
    http.get("/api/media/:mediaId/playback-info", () => HttpResponse.json({ direct_play: true })),
  );
});

describe("a clip", () => {
  test("seeks to its start once the media knows its duration", async () => {
    render(
      <VideoPlayer mediaId="m-1" title="Aurora" clip={{ startSeconds: 900, endSeconds: 960 }} />,
    );

    const element = video();
    at(element, 0);
    fireEvent.loadedMetadata(element);

    // Seeking before metadata is silently dropped and the clip plays from the
    // top of the film instead.
    expect(element.currentTime).toBe(900);
  });

  test("stops at its end and says it has finished", async () => {
    const onEnded = vi.fn();
    render(
      <VideoPlayer
        mediaId="m-1"
        title="Aurora"
        clip={{ startSeconds: 900, endSeconds: 960 }}
        onEnded={onEnded}
      />,
    );

    const element = video();
    const pause = vi.spyOn(element, "pause").mockImplementation(() => {});
    at(element, 961);
    fireEvent.timeUpdate(element);

    expect(pause).toHaveBeenCalled();
    expect(onEnded).toHaveBeenCalled();
  });

  test("does not report the film as part-watched", async () => {
    const posts: string[] = [];
    server.use(
      http.post("/api/playback/:mediaId/progress", ({ request }) => {
        posts.push(request.url);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    render(
      <VideoPlayer mediaId="m-1" title="Aurora" clip={{ startSeconds: 900, endSeconds: 960 }} />,
    );

    const element = video();
    Object.defineProperty(element, "duration", { value: 7200, configurable: true });
    at(element, 930);
    fireEvent.timeUpdate(element);

    // Forty seconds in the shorts feed must not put a two-hour title in
    // "continue watching" at the fifteen-minute mark.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(posts).toStrictEqual([]);
  });
});

describe("a whole title", () => {
  test("still reports progress, which is what the resume row is built on", async () => {
    const posts: string[] = [];
    server.use(
      http.post("/api/playback/:mediaId/progress", ({ request }) => {
        posts.push(new URL(request.url).pathname);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    render(<VideoPlayer mediaId="m-1" title="Aurora" />);

    const element = video();
    Object.defineProperty(element, "duration", { value: 7200, configurable: true });
    at(element, 930);
    fireEvent.timeUpdate(element);

    await waitFor(() => expect(posts).toStrictEqual(["/api/playback/m-1/progress"]));
  });
});
