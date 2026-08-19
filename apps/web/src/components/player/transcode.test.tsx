/**
 * Choosing how a transcoded stream is played.
 *
 * The playlist is HLS, and there are two ways to play one: the element's own
 * support, or hls.js over Media Source Extensions. Which one is asked first is
 * not a detail — Chromium answers `canPlayType("application/vnd.apple.mpegurl")`
 * with "maybe" and then has no demuxer for it, so asking the element first
 * ended every transcoded playback in `DEMUXER_ERROR_COULD_NOT_PARSE` with the
 * working path never tried.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import en from "../../i18n/en.json";
import { server, useMockApi } from "../../test/harness";

const attachMedia = vi.fn();
const loadSource = vi.fn();
const supported = vi.fn(() => true);

vi.mock("hls.js", () => {
  class FakeHls {
    static isSupported = (): boolean => supported();
    loadSource = loadSource;
    attachMedia = attachMedia;
    destroy = vi.fn();
  }
  return { default: FakeHls };
});

const { VideoPlayer } = await import("./video-player");

useMockApi();
afterEach(cleanup);

beforeEach(() => {
  attachMedia.mockClear();
  loadSource.mockClear();
  supported.mockReturnValue(true);
  // What Chromium answers. jsdom's stub answers "", which is the one browser
  // this bug could not have been reproduced in.
  vi.spyOn(HTMLMediaElement.prototype, "canPlayType").mockReturnValue("maybe");
  server.use(
    http.get("/api/media/:mediaId/playback-info", () =>
      HttpResponse.json({ direct_play: false, reasons: ["video_codec_unknown"] }),
    ),
    http.post("/api/transcode/media/:mediaId/sessions", () =>
      HttpResponse.json({
        session_id: "s-1",
        playlist_url: "/api/transcode/sessions/s-1/hls/master.m3u8",
      }),
    ),
    // Unmounting releases the session it started; unmocked, that request is an
    // unhandled rejection rather than a test failure, which is worse.
    http.delete(
      "/api/transcode/sessions/:sessionId",
      () => new HttpResponse(null, { status: 204 }),
    ),
  );
});

describe("a transcoded stream", () => {
  test("is played through hls.js even when the element claims it can play HLS", async () => {
    render(<VideoPlayer mediaId="m-1" title="Aurora" />);

    await waitFor(() => expect(attachMedia).toHaveBeenCalledTimes(1));
    expect(loadSource).toHaveBeenCalledWith("/api/transcode/sessions/s-1/hls/master.m3u8");
    // The playlist must never reach the element directly here: Chromium
    // answers the src with an unsupported-source error and stops.
    expect(document.querySelector("video")?.getAttribute("src")).toBe(null);
  });

  test("falls back to the element where Media Source Extensions are missing", async () => {
    supported.mockReturnValue(false);

    render(<VideoPlayer mediaId="m-1" title="Aurora" />);

    await waitFor(() =>
      expect(document.querySelector("video")?.getAttribute("src")).toBe(
        "/api/transcode/sessions/s-1/hls/master.m3u8",
      ),
    );
    expect(attachMedia).not.toHaveBeenCalled();
  });
});

describe("a refused transcode session", () => {
  test("names the limit rather than the one string every failure used to render", async () => {
    // PRODUCT DEFECT D11: the API answers 429 TRANSCODE_LIMIT_REACHED, and the
    // player used to collapse every failure - this one included - into
    // `player.unavailable`, a sentence that never mentioned a limit at all.
    server.use(
      http.post("/api/transcode/media/:mediaId/sessions", () =>
        HttpResponse.json(
          { code: "TRANSCODE_LIMIT_REACHED", status: 429, context: { limit: "software" } },
          { status: 429 },
        ),
      ),
    );

    render(<VideoPlayer mediaId="m-1" title="Aurora" />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(en.errors.TRANSCODE_LIMIT_REACHED);
    expect(alert.textContent).toContain(en.errorSteps.TRANSCODE_LIMIT_REACHED);
  });
});
