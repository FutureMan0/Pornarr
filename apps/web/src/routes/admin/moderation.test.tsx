/**
 * A6 — moderation, at the seam where a decision reaches the server.
 *
 * The claims worth holding: a filter re-queries rather than sifting a page, an
 * action reaches the endpoint that performs it, and the two privacy properties
 * survive the administrator's view — no author under an anonymous household,
 * and never the name of whoever filed a report.
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

function comment(overrides: Record<string, unknown> = {}) {
  return {
    id: "c-1",
    media_id: "m-1",
    media_title: "Blue Hour, Part 2",
    author: "Mira",
    body: "Wrong performer tagged here I think.",
    state: "open",
    stars: 4,
    likes: 0,
    you_liked: false,
    is_own: false,
    reports: 0,
    created_at: "2026-08-16T18:00:00Z",
    edited_at: null,
    ...overrides,
  };
}

const RATINGS = {
  average: 4.1,
  count: 342,
  breakdown: { "5": 62, "4": 38, "3": 17, "2": 8, "1": 3 },
  top: [{ media_id: "m-2", title: "Static Garden", average: 5, count: 4 }],
};

const SETTINGS = {
  private_libraries: false,
  pooled_search: true,
  anonymous_social: false,
};

function stub(comments: object[] = [comment()]): URL[] {
  const calls: URL[] = [];
  server.use(
    http.get("/api/admin/comments", ({ request }) => {
      calls.push(new URL(request.url));
      return HttpResponse.json(comments);
    }),
    http.get("/api/admin/ratings/overview", () => HttpResponse.json(RATINGS)),
    http.get("/api/admin/settings", () => HttpResponse.json(SETTINGS)),
  );
  return calls;
}

describe("the moderation list", () => {
  test("a filter asks the server, rather than sifting what happened to load", async () => {
    const calls = stub();
    renderApp("/admin/moderation");

    await screen.findByText("Wrong performer tagged here I think.");
    await userEvent.setup().click(screen.getByRole("button", { name: "Reported" }));

    // The reported ones are not necessarily on the first page. Filtering in the
    // browser would quietly moderate only what was already fetched.
    await waitFor(() =>
      expect(calls.some((url) => url.searchParams.get("reported_only") === "true")).toBe(true),
    );
  });

  test("hiding a remark reaches the endpoint that hides it", async () => {
    const hidden: string[] = [];
    stub();
    server.use(
      http.post("/api/admin/comments/:id/hide", ({ params }) => {
        hidden.push(String(params.id));
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderApp("/admin/moderation");

    await userEvent.setup().click(await screen.findByRole("button", { name: "Hide" }));

    await waitFor(() => expect(hidden).toStrictEqual(["c-1"]));
  });

  test("an already-hidden remark offers no second hiding", async () => {
    stub([comment({ state: "hidden" })]);
    renderApp("/admin/moderation");

    const button = await screen.findByRole("button", { name: "Hidden" });

    expect(button.hasAttribute("disabled")).toBe(true);
  });

  test("a report is a count, never a name", async () => {
    stub([comment({ reports: 2 })]);
    renderApp("/admin/moderation");

    // Naming the reporter turns moderation into a dispute between two people.
    // The API does not send it and the screen must not imply it.
    expect(await screen.findByText("2 reports")).toBeTruthy();
    expect(screen.queryByText(/Reported by/)).toBeNull();
  });

  test("an anonymous household is told this queue is the exception", async () => {
    stub();
    server.use(
      http.get("/api/admin/settings", () =>
        HttpResponse.json({ ...SETTINGS, anonymous_social: true }),
      ),
    );
    renderApp("/admin/moderation");

    // The server attributes here deliberately, so a report can be judged. An
    // administrator who believes the household is anonymous must not learn
    // otherwise by reading a name.
    expect(await screen.findByText(/anonymous everywhere else/)).toBeTruthy();
    expect(screen.getByText("Mira")).toBeTruthy();
  });

  test("an attributed household needs no such notice", async () => {
    stub();
    renderApp("/admin/moderation");

    await screen.findByText("Mira");

    expect(screen.queryByText(/anonymous everywhere else/)).toBeNull();
  });

  test("a remark with no author still renders", async () => {
    stub([comment({ author: null })]);
    renderApp("/admin/moderation");

    // Not a state the server produces here today, but a null must not blank
    // the row out.
    expect(await screen.findByText("Someone")).toBeTruthy();
  });

  test("nothing to review says so", async () => {
    stub([]);
    renderApp("/admin/moderation");

    expect(await screen.findByText("Nothing to review.")).toBeTruthy();
  });
});

describe("the house rules", () => {
  test("each switch reports the state the server is in", async () => {
    stub();
    renderApp("/admin/moderation");

    const pooled = await screen.findByRole("switch", { name: /Pooled search/ });
    const priv = screen.getByRole("switch", { name: /Private libraries/ });

    expect(pooled.getAttribute("aria-checked")).toBe("true");
    expect(priv.getAttribute("aria-checked")).toBe("false");
  });

  test("flipping one writes that setting and nothing else", async () => {
    const bodies: unknown[] = [];
    stub();
    server.use(
      http.patch("/api/admin/settings", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ ...SETTINGS, private_libraries: true });
      }),
    );
    renderApp("/admin/moderation");

    await userEvent.setup().click(await screen.findByRole("switch", { name: /Private libraries/ }));

    // One key. Sending the whole settings object back would overwrite anything
    // another administrator changed while this screen was open.
    await waitFor(() => expect(bodies).toStrictEqual([{ private_libraries: true }]));
  });
});

describe("what the household thinks", () => {
  test("shows the distribution behind the average, not just the number", async () => {
    stub();
    renderApp("/admin/moderation");

    expect(await screen.findByText("4.1")).toBeTruthy();
    // "Everyone agrees it is a four" and "half loved it, half did not" have the
    // same average and are not the same library.
    expect(screen.getByText("62")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
  });

  test("an unrated library says so instead of showing zero out of five", async () => {
    stub();
    server.use(
      http.get("/api/admin/ratings/overview", () =>
        HttpResponse.json({ average: null, count: 0, breakdown: {}, top: [] }),
      ),
    );
    renderApp("/admin/moderation");

    expect(await screen.findByText("Nobody has rated anything yet.")).toBeTruthy();
  });
});
