/** The recommendations screen, in each of the states it can be in. */
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, test, vi } from "vitest";
import en from "../../i18n/en.json";
import { renderApp, server, signedIn, useMockApi } from "../../test/harness";

useMockApi();
afterEach(cleanup);

const MEDIA_ID = "b4a1f0c2-6d3e-4f2a-8c11-1d2e3f4a5b6c";

const RECOMMENDATION = {
  media_id: MEDIA_ID,
  title: "Example Scene",
  score: 0.9,
  match_score: 90,
  reason: { matched_tags: ["outdoors"], matched_performers: ["Ada"] },
  reasons: [],
  model_version: "1",
  expires_at: "2026-09-01T00:00:00Z",
};

describe("recommendations screen", () => {
  test("names what is loading before the recommendations arrive", async () => {
    signedIn();
    server.use(http.get("/api/recommendations", () => HttpResponse.json([])));
    renderApp("/recommendations");

    expect(await screen.findByText(en.recommendations.loading)).toBeTruthy();
  });

  test("says what produces a recommendation when there are none", async () => {
    signedIn();
    server.use(http.get("/api/recommendations", () => HttpResponse.json([])));
    renderApp("/recommendations");

    expect(
      await screen.findByRole("heading", { level: 1, name: en.recommendations.title }),
    ).toBeTruthy();
    expect(await screen.findByText(en.recommendations.empty)).toBeTruthy();
  });

  test("states the failure instead of an empty list when the load fails", async () => {
    signedIn();
    server.use(
      http.get("/api/recommendations", () =>
        HttpResponse.json({ code: "BAD_REQUEST", status: 400, context: {} }, { status: 400 }),
      ),
    );
    renderApp("/recommendations");

    expect((await screen.findByRole("alert")).textContent).toBe(en.errors.generic);
    expect(screen.queryByText(en.recommendations.empty)).toBeNull();
  });

  test("gives the reason beside each title and records disinterest", async () => {
    signedIn();
    const feedback = vi.fn();
    server.use(
      http.get("/api/recommendations", () => HttpResponse.json([RECOMMENDATION])),
      http.post(`/api/recommendations/${MEDIA_ID}/feedback`, async ({ request }) => {
        feedback(await request.json());
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderApp("/recommendations");

    expect(await screen.findByRole("heading", { level: 2, name: "Example Scene" })).toBeTruthy();
    expect(screen.getByText(/outdoors, Ada/)).toBeTruthy();

    await user.click(screen.getByRole("button", { name: en.recommendations.notInterested }));

    await waitFor(() =>
      expect(feedback).toHaveBeenCalledWith({ event_type: "not_interested", subject_id: null }),
    );
  });
});
