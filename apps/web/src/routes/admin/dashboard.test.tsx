/**
 * A1 — the dashboard, and who gets to see it.
 *
 * The numbers are the server's; what is tested here is what the screen does
 * with them. Chiefly: unmeasured storage must not be drawn as an empty disk,
 * and a guest must not be offered a destination that will answer 403.
 */
import { cleanup, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import {
  TEST_USER,
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

const OVERVIEW = {
  titles: 3482,
  titles_added_this_week: 37,
  untagged: 214,
  guests: 4,
  storage: { used_bytes: 6_710_886_400_000, total_bytes: 9_895_604_649_984, volumes: 3 },
  health: {
    total: 3482,
    metadata_matched: 3273,
    artwork_present: 2820,
    tagged: 2542,
    duplicates_flagged: 18,
  },
  last_scan_at: "2026-08-16T20:00:00Z",
};

function stub(overview: object = OVERVIEW): void {
  server.use(
    http.get("/api/admin/overview", () => HttpResponse.json(overview)),
    http.get("/api/admin/audit", () => HttpResponse.json([])),
  );
}

describe("the dashboard", () => {
  test("draws each health figure as a share of the library", async () => {
    stub();
    renderApp("/admin");

    // 3273 of 3482. The server sends counts and the total; rounding happens
    // once, here, rather than in two places that could disagree.
    const matched = await screen.findByText("Metadata matched");
    expect(matched.parentElement?.textContent).toContain("94%");
    expect(screen.getByText("Tagged").parentElement?.textContent).toContain("73%");
  });

  test("unmeasured storage says so instead of drawing an empty disk", async () => {
    stub({
      ...OVERVIEW,
      storage: { used_bytes: null, total_bytes: null, volumes: 2 },
    });
    renderApp("/admin");

    // "0 B of 0 B" reads as a disk with nothing on it, which is the opposite
    // of "nobody has looked".
    expect(await screen.findByText(/not yet measured/)).toBeTruthy();
    expect(screen.queryByText(/0 B/)).toBeNull();
  });

  test("no library folder at all is a different sentence from unmeasured", async () => {
    stub({ ...OVERVIEW, storage: { used_bytes: null, total_bytes: null, volumes: 0 } });
    renderApp("/admin");

    // "0 volumes, not yet measured" describes a measurement that has not
    // happened. Nothing has been set up to measure.
    expect(await screen.findByText("No library folder yet")).toBeTruthy();
  });

  test("an empty library is a state, not a division by zero", async () => {
    stub({
      ...OVERVIEW,
      titles: 0,
      untagged: 0,
      health: {
        total: 0,
        metadata_matched: 0,
        artwork_present: 0,
        tagged: 0,
        duplicates_flagged: 0,
      },
    });
    renderApp("/admin");

    expect(await screen.findByText(/Nothing in the library to measure yet/)).toBeTruthy();
    expect(screen.queryByText("Metadata matched")).toBeNull();
  });

  test("held-back files link to where they are dealt with", async () => {
    stub();
    renderApp("/admin");

    const link = await screen.findByRole("link", { name: "18 files" });

    // A number with nowhere to go is a number nobody acts on.
    expect(link.getAttribute("href")).toBe("/admin/quarantine");
  });

  test("a quiet server says nothing has happened rather than showing a blank panel", async () => {
    stub();
    renderApp("/admin");

    expect(await screen.findByText("Nothing has happened yet.")).toBeTruthy();
  });
});

describe("who is offered the dashboard", () => {
  test("an administrator has it in the navigation", async () => {
    stub();
    renderApp("/library");

    const nav = await screen.findByRole("navigation", { name: "Primary" });

    await waitFor(() => expect(nav.textContent).toContain("Dashboard"));
  });

  test("a guest is not, because it would only answer 403", async () => {
    server.use(http.get("/api/auth/me", () => HttpResponse.json({ ...TEST_USER, role: "user" })));
    stub();
    renderApp("/library");

    const nav = await screen.findByRole("navigation", { name: "Primary" });

    await waitFor(() => expect(nav.textContent).toContain("Library"));
    // Not a security measure — the endpoint checks the role itself — but a
    // destination that cannot work has no business being offered.
    expect(nav.textContent).not.toContain("Dashboard");
  });
});
