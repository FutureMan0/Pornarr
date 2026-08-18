/**
 * A5 — the settings screen.
 *
 * One property matters more than the rest: a control must write only the
 * setting it governs. The GET returns the whole object, so sending it back is
 * the obvious implementation and the one that silently undoes whatever another
 * administrator changed while this screen sat open.
 */
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { renderApp, server, setViewportWidth, signedIn, useMockApi } from "../../test/harness";

useMockApi();
afterEach(cleanup);

beforeEach(() => {
  signedIn();
  setViewportWidth(1440);
});

const SETTINGS = {
  private_libraries: false,
  pooled_search: true,
  anonymous_social: true,
  recommendation_use_ratings: true,
  recommendation_hide_finished: false,
  recommendation_include_shorts: true,
  recommendation_include_friend_picks: false,
  default_auto_downloads_enabled: false,
  metrics_enabled: true,
  playback_completion_threshold_percent: 90,
  transcode_max_per_user: 2,
  transcode_max_hw_sessions: null,
  transcode_max_sw_sessions: 4,
  default_daily_download_limit_gb: 50,
  default_max_auto_jobs: 3,
  default_max_auto_downloads_per_day: 10,
  request_search_max_age_days: 30,
  min_free_disk_percent: 10,
  quarantine_retention_days: 30,
  audit_retention_days: 365,
  user_event_retention_days: null,
};

function stub(): unknown[] {
  const bodies: unknown[] = [];
  server.use(
    http.get("/api/admin/settings", () => HttpResponse.json(SETTINGS)),
    http.patch("/api/admin/settings", async ({ request }) => {
      bodies.push(await request.json());
      return HttpResponse.json(SETTINGS);
    }),
  );
  return bodies;
}

describe("switches", () => {
  test("show the state the server is in", async () => {
    stub();
    renderApp("/settings");

    const pooled = await screen.findByRole("switch", { name: /Pooled search/ });

    expect(pooled.getAttribute("aria-checked")).toBe("true");
    expect(
      screen.getByRole("switch", { name: /Private libraries/ }).getAttribute("aria-checked"),
    ).toBe("false");
  });

  test("write one key and nothing else", async () => {
    const bodies = stub();
    renderApp("/settings");

    await userEvent.setup().click(await screen.findByRole("switch", { name: /Private libraries/ }));

    // Sending the whole object back would undo another administrator's change.
    await waitFor(() => expect(bodies).toStrictEqual([{ private_libraries: true }]));
  });
});

describe("numbers", () => {
  test("are written when the field is left, not on every keystroke", async () => {
    const bodies = stub();
    renderApp("/settings");

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Playback" }));

    const field = await screen.findByLabelText("Counted as watched at");
    await user.clear(field);
    await user.type(field, "85");

    // Nothing yet: "8" then "85" would both be settings the server briefly held.
    expect(bodies).toStrictEqual([]);

    await user.tab();

    await waitFor(() =>
      expect(bodies).toStrictEqual([{ playback_completion_threshold_percent: 85 }]),
    );
  });

  test("a value out of range is clamped rather than rejected by the server", async () => {
    const bodies = stub();
    renderApp("/settings");

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Playback" }));
    const field = await screen.findByLabelText("Counted as watched at");
    await user.clear(field);
    await user.type(field, "500");
    await user.tab();

    // The API caps this at 100. Sending 500 to be told off is a round trip
    // spent learning something the browser already knew.
    await waitFor(() =>
      expect(bodies).toStrictEqual([{ playback_completion_threshold_percent: 100 }]),
    );
  });

  test("clearing a nullable field means no limit, not zero", async () => {
    const bodies = stub();
    renderApp("/settings");

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Playback" }));
    const field = await screen.findByLabelText("Software transcodes");
    await user.clear(field);
    await user.tab();

    // Zero would mean "no software transcoding at all", which is the opposite
    // of "as many as it takes".
    await waitFor(() => expect(bodies).toStrictEqual([{ transcode_max_sw_sessions: null }]));
  });

  test("leaving a field untouched writes nothing", async () => {
    const bodies = stub();
    renderApp("/settings");

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Playback" }));
    const field = await screen.findByLabelText("Counted as watched at");
    await user.click(field);
    await user.tab();

    expect(bodies).toStrictEqual([]);
  });
});

describe("what is not here", () => {
  test("no list of people, because the server has no user API", async () => {
    stub();
    renderApp("/settings");

    await screen.findByRole("switch", { name: /Pooled search/ });

    // A screen that lists one account and calls it a household is a mock-up.
    expect(screen.queryByText(/People on this server/)).toBeNull();
  });

  test("the sections that do exist are reachable", async () => {
    stub();
    renderApp("/settings");

    const nav = await screen.findByRole("navigation", { name: "Settings sections" });

    for (const section of ["Household", "Playback", "Recommendations", "Downloads"]) {
      expect(nav.textContent).toContain(section);
    }
    // The screens of their own are reached from the tab bar over the areas,
    // not from this list, so there is exactly one link to each.
    expect(screen.getByRole("link", { name: "Quality profiles" }).getAttribute("href")).toBe(
      "/settings/quality",
    );
    expect(screen.getByRole("link", { name: "Scan & import" }).getAttribute("href")).toBe(
      "/admin/scan",
    );
  });
});
