/** The downloads queue. An idle queue has to say so, not render nothing. */
import { cleanup, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, test } from "vitest";
import en from "../../i18n/en.json";
import { renderApp, server, signedIn, useMockApi } from "../../test/harness";

useMockApi();
afterEach(cleanup);

describe("downloads queue", () => {
  test("says nothing is transferring when the queue is empty", async () => {
    signedIn();
    server.use(http.get("/api/queue", () => HttpResponse.json({ items: [], next_cursor: null })));
    renderApp("/downloads");

    expect(await screen.findByText(en.queue.empty)).toBeTruthy();
  });

  test("lists the jobs instead of the empty sentence when there are any", async () => {
    signedIn();
    server.use(
      http.get("/api/queue", () =>
        HttpResponse.json({
          items: [
            {
              id: "3f1d2c4b-5a6e-4f7a-8b9c-0d1e2f3a4b5c",
              client_name: "Example Client",
              status: "downloading",
              priority: 50,
              download_speed_bytes: null,
              error: null,
              queue_estimate: { low_seconds: null, high_seconds: null, confidence: "low" },
            },
          ],
          next_cursor: null,
        }),
      ),
    );
    renderApp("/downloads");

    expect(await screen.findByText("Example Client")).toBeTruthy();
    expect(screen.queryByText(en.queue.empty)).toBeNull();
  });
});
