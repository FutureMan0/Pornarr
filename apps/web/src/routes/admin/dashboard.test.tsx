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

const RECENT = {
  id: "m-1",
  title: "Aurora 214",
  studio: "Northwind",
  duration_seconds: 1445,
  quality: "2160p",
  resolution: "3840x2160",
  rating: 4.5,
  rating_count: 12,
  tag_count: 9,
  comment_count: 12,
};

// A software-only baseline: no working method, one machine-level rejection.
// Tests about the panel itself override this rather than the default, so
// every other test on this screen sees a fixed, boring answer.
const CAPABILITIES = {
  methods: [],
  rejections: [
    { acceleration: null, reason: "hardware acceleration is disabled by configuration" },
  ],
  nvidia_gpus: [],
};

const LIMITS = {
  hardware: 0,
  software: 1,
  per_user: 2,
  hardware_in_use: 0,
  software_in_use: 0,
  configured_hardware: null,
  configured_software: null,
  configured_per_user: 2,
  effective_hardware: 0,
  effective_software: 1,
  effective_per_user: 2,
};

function stub(
  overview: object = OVERVIEW,
  recent: object[] = [RECENT],
  capabilities: object = CAPABILITIES,
  limits: object = LIMITS,
): URL[] {
  const libraryCalls: URL[] = [];
  server.use(
    http.get("/api/admin/overview", () => HttpResponse.json(overview)),
    http.get("/api/admin/audit", () => HttpResponse.json([])),
    http.get("/api/library", ({ request }) => {
      libraryCalls.push(new URL(request.url));
      return HttpResponse.json({ items: recent, next_offset: null });
    }),
    http.get("/api/admin/transcode/capabilities", () => HttpResponse.json(capabilities)),
    http.get("/api/admin/transcode/limits", () => HttpResponse.json(limits)),
  );
  return libraryCalls;
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

describe("recently added", () => {
  test("shows the newest titles and links into them", async () => {
    stub();
    renderApp("/admin");

    const link = await screen.findByRole("link", { name: /Aurora 214/ });

    expect(link.getAttribute("href")).toBe("/library/m-1");
  });

  test("asks the library rather than a second ordering of its own", async () => {
    const calls = stub();
    renderApp("/admin");

    await screen.findByRole("link", { name: /Aurora 214/ });

    // The library endpoint already orders by most recently touched. A separate
    // "recent" endpoint would be a second ordering to keep in step with it.
    await waitFor(() => expect(calls[0]?.searchParams.get("limit")).toBe("5"));
  });

  test("an empty library shows no row at all", async () => {
    stub(OVERVIEW, []);
    renderApp("/admin");

    await screen.findByText("Metadata matched");

    expect(screen.queryByText("Recently added")).toBeNull();
  });
});

describe("the transcoding report", () => {
  test("names each acceleration method that works", async () => {
    stub(OVERVIEW, [RECENT], {
      methods: [
        {
          acceleration: "nvenc",
          codecs: [
            { codec: "h264", maximum_tested_resolution: "3840x2160" },
            { codec: "hevc", maximum_tested_resolution: "1920x1080" },
          ],
        },
      ],
      rejections: [],
      nvidia_gpus: ["NVIDIA GeForce RTX 3060"],
    });
    renderApp("/admin");

    expect(await screen.findByText("NVENC — H264, HEVC")).toBeTruthy();
  });

  test("gives a rejection's reason exactly as the endpoint sent it, not reworded", async () => {
    stub(OVERVIEW, [RECENT], {
      methods: [],
      rejections: [{ acceleration: "vaapi", reason: "no supported encoder is listed by ffmpeg" }],
      nvidia_gpus: [],
    });
    renderApp("/admin");

    // The exact sentence `detect_hardware_capabilities` produces
    // (packages/media/pornarr_media/capabilities.py) - a client-side rewrite
    // would be a second place that sentence could drift from what FFmpeg said.
    expect(
      await screen.findByText("VAAPI unavailable — no supported encoder is listed by ffmpeg"),
    ).toBeTruthy();
  });

  test("names the machine-level reason when no method was even attempted", async () => {
    stub(OVERVIEW, [RECENT], {
      methods: [],
      rejections: [
        { acceleration: null, reason: "hardware acceleration is disabled by configuration" },
      ],
      nvidia_gpus: [],
    });
    renderApp("/admin");

    expect(
      await screen.findByText("Not available — hardware acceleration is disabled by configuration"),
    ).toBeTruthy();
  });

  test("shows the current session counts beside the report", async () => {
    stub(OVERVIEW, [RECENT], CAPABILITIES, {
      ...LIMITS,
      hardware_in_use: 1,
      effective_hardware: 2,
      software_in_use: 3,
      effective_software: 8,
    });
    renderApp("/admin");

    const slots = await screen.findByText(/hardware slots in use/);
    expect(slots.textContent).toContain("1 of 2 hardware slots in use");
    expect(slots.textContent).toContain("3 of 8 software slots in use");
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
