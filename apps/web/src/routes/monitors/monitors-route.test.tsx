/**
 * The monitors screen. Reachable from the navigation, and answering with a
 * sentence in every state rather than an empty page.
 */
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, test, vi } from "vitest";
import en from "../../i18n/en.json";
import { renderApp, server, signedIn, useMockApi } from "../../test/harness";

useMockApi();
afterEach(cleanup);

const MONITOR_ID = "9d1c6d1a-8f1e-4a1a-9a0b-2f1c0f5f7a11";

function monitor(enabled: boolean) {
  return {
    id: MONITOR_ID,
    kind: "query",
    performer_id: null,
    studio_id: null,
    query: "Example Scene",
    quality_profile_id: "8ab3a3d0-4b47-4e42-9b6a-7f3d3d4a5b6c",
    enabled,
    minimum_score: 0,
    last_match_at: null,
  };
}

describe("monitors screen", () => {
  test("names what is loading before the monitors arrive", async () => {
    signedIn();
    server.use(http.get("/api/monitors", () => HttpResponse.json([])));
    renderApp("/monitors");

    expect(await screen.findByText(en.monitors.loading)).toBeTruthy();
  });

  test("explains what a monitor is when there are none", async () => {
    signedIn();
    server.use(http.get("/api/monitors", () => HttpResponse.json([])));
    renderApp("/monitors");

    expect(await screen.findByRole("heading", { level: 1, name: en.monitors.title })).toBeTruthy();
    expect(await screen.findByText(en.monitors.empty)).toBeTruthy();
  });

  test("states the failure instead of an empty list when the load fails", async () => {
    signedIn();
    server.use(
      http.get("/api/monitors", () =>
        HttpResponse.json({ code: "BAD_REQUEST", status: 400, context: {} }, { status: 400 }),
      ),
    );
    renderApp("/monitors");

    expect((await screen.findByRole("alert")).textContent).toBe(en.errors.generic);
    expect(screen.queryByText(en.monitors.empty)).toBeNull();
  });

  test("turns a monitor off and starts its backlog search", async () => {
    signedIn();
    const patched = vi.fn();
    const searched = vi.fn();
    server.use(
      http.get("/api/monitors", () => HttpResponse.json([monitor(true)])),
      http.patch(`/api/monitors/${MONITOR_ID}`, async ({ request }) => {
        patched(await request.json());
        return HttpResponse.json(monitor(false));
      }),
      http.post(`/api/monitors/${MONITOR_ID}/backlog-search`, () => {
        searched();
        return new HttpResponse(null, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderApp("/monitors");

    expect(await screen.findByRole("heading", { level: 2, name: "Example Scene" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: en.monitors.disable }));
    await waitFor(() => expect(patched).toHaveBeenCalledWith({ enabled: false }));

    await user.click(await screen.findByRole("button", { name: en.monitors.search }));
    await waitFor(() => expect(searched).toHaveBeenCalled());
  });
});
