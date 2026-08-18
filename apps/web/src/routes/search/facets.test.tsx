/**
 * B2 — the filter sidebar, at the seam where a click becomes a request.
 *
 * The counts themselves are the server's arithmetic and are tested there. What
 * matters here is that a selection reaches the URL (so a filtered search can be
 * bookmarked and shared), that clicking the same option twice clears it, and
 * that the result count says what it means when the server admits it is a
 * floor rather than a total.
 */
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import {
  currentParams,
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

const FACETS = {
  studio: [
    { value: "Northwind", count: 340 },
    { value: "Halcyon", count: 219 },
  ],
  resolution: [{ value: "2160p", count: 612 }],
  duration: [{ value: "20_40", count: 1520 }],
  rating: [
    { value: "4", count: 942 },
    { value: "unrated", count: 1104 },
  ],
  tag: [{ value: "low-light", count: 412 }],
  matched: 86,
  total: 3268,
  capped: false,
};

function stubSearch(facets: object = FACETS): URL[] {
  const facetCalls: URL[] = [];
  server.use(
    http.get("/api/search/local/facets", ({ request }) => {
      facetCalls.push(new URL(request.url));
      return HttpResponse.json(facets);
    }),
    http.get("/api/search/local", () => HttpResponse.json({ items: [], next_cursor: null })),
    http.post("/api/search/indexers", () => HttpResponse.json({ id: "s-1" })),
    http.get("/api/search/indexers/:id", () =>
      HttpResponse.json({ id: "s-1", status: "completed", items: [], indexers: [] }),
    ),
  );
  return facetCalls;
}

describe("the filter sidebar", () => {
  test("shows each option with the count the server computed", async () => {
    stubSearch();
    renderApp("/search?q=night");

    const studio = await screen.findByRole("button", { name: /Northwind/ });

    // The number is the point: a filter list without one makes you click to
    // find out that a choice leads nowhere.
    expect(studio.textContent).toContain("340");
  });

  test("a selection reaches the address, so a filtered search can be shared", async () => {
    stubSearch();
    const { router } = renderApp("/search?q=night");

    await userEvent.setup().click(await screen.findByRole("button", { name: /Northwind/ }));

    await waitFor(() => expect(currentParams(router).get("studio")).toBe("Northwind"));
  });

  test("clicking the chosen option again clears it", async () => {
    stubSearch();
    const { router } = renderApp("/search?q=night&studio=Northwind");
    const user = userEvent.setup();

    // The chip carries the same word, so the option is the one with a count.
    const studio = await screen.findByRole("button", { name: "Northwind 340" });
    expect(studio.getAttribute("aria-pressed")).toBe("true");

    await user.click(studio);

    await waitFor(() => expect(currentParams(router).get("studio")).toBeNull());
  });

  test("the server is asked to recount when a selection changes", async () => {
    const calls = stubSearch();
    renderApp("/search?q=night");

    await screen.findByRole("button", { name: /Northwind/ });
    await userEvent.setup().click(screen.getByRole("button", { name: /Halcyon/ }));

    // Counting in the browser would only ever see the page that was loaded.
    await waitFor(() =>
      expect(calls.some((url) => url.searchParams.get("studio") === "Halcyon")).toBe(true),
    );
  });

  test("an active filter is listed as a chip with its own way off", async () => {
    stubSearch();
    const { router } = renderApp("/search?q=night&studio=Northwind");
    const user = userEvent.setup();

    const chip = await screen.findByRole("button", { name: "Northwind Remove this filter" });
    await user.click(chip);

    await waitFor(() => expect(currentParams(router).get("studio")).toBeNull());
  });

  test("clear all removes every facet but keeps the query", async () => {
    stubSearch();
    const { router } = renderApp("/search?q=night&studio=Northwind&quality=2160p&tag=low-light");

    await userEvent.setup().click(await screen.findByRole("button", { name: "Clear all" }));

    await waitFor(() => {
      const params = currentParams(router);
      expect(params.get("studio")).toBeNull();
      expect(params.get("quality")).toBeNull();
      expect(params.get("tag")).toBeNull();
      // What you typed survives. Clearing filters is not starting over.
      expect(params.get("q")).toBe("night");
    });
  });

  test("the duration bands are worded, not printed as their keys", async () => {
    stubSearch();
    renderApp("/search?q=night");

    expect(await screen.findByRole("button", { name: /20–40 min/ })).toBeTruthy();
  });

  test("a rating floor reads as a floor, and unrated as its own answer", async () => {
    stubSearch();
    renderApp("/search?q=night");

    expect(await screen.findByRole("button", { name: /4 stars and up/ })).toBeTruthy();
    // Nobody rated it is a different statement from rated badly.
    expect(screen.getByRole("button", { name: /Not yet rated/ })).toBeTruthy();
  });
});

describe("the result count", () => {
  test("says how many of how many", async () => {
    stubSearch();
    renderApp("/search?q=night");

    expect(await screen.findByText("86 of 3268")).toBeTruthy();
  });

  test("says the total is a floor when the server capped the walk", async () => {
    stubSearch({ ...FACETS, capped: true });
    renderApp("/search?q=night");

    // A capped count printed as an exact total is the same lie in a quieter
    // voice — the sentence changes, not just the number.
    expect(await screen.findByText("86 of more than 3268")).toBeTruthy();
  });
});

/**
 * The filters on a phone, as C3 draws them: a sheet, not a wall.
 *
 * Eight controls stacked above the first result is a form to fill in rather than
 * a search, and it is what a 390px screen used to show. What is worth pinning
 * down is that moving them behind a button did not take them away: the sheet
 * still writes to the address, so a filtered search is still something you can
 * bookmark and send to somebody.
 */
describe("filters on a phone", () => {
  test("are behind a button, and still reach the address", async () => {
    setViewportWidth(390);
    const user = userEvent.setup();
    const { router } = renderApp("/search?q=night");

    // Not on the screen: the results are.
    expect(screen.queryByLabelText("Minimum seeders")).toBeNull();

    await user.click(await screen.findByRole("button", { name: /^Filters/ }));

    const sheet = await screen.findByRole("dialog");
    const quality = within(sheet).getByLabelText("Quality");
    await user.selectOptions(quality, "1080p");

    await waitFor(() => expect(currentParams(router).get("quality")).toBe("1080p"));
  });

  test("say how many are doing something", async () => {
    setViewportWidth(390);
    renderApp("/search?q=night&quality=1080p&sort=relevance");

    // `sort=relevance` is the default, so it is set without being a filter —
    // counting it would tell every reader they have a filter on.
    expect(await screen.findByRole("button", { name: "Filters (1)" })).not.toBeNull();
  });

  test("clear all empties them and leaves the query alone", async () => {
    setViewportWidth(390);
    const user = userEvent.setup();
    const { router } = renderApp("/search?q=night&quality=1080p&minimum_seeders=5");

    await user.click(await screen.findByRole("button", { name: /^Filters/ }));
    const sheet = await screen.findByRole("dialog");
    await user.click(within(sheet).getByRole("button", { name: "Clear all" }));

    await waitFor(() => expect(currentParams(router).get("quality")).toBeNull());
    expect(currentParams(router).get("minimum_seeders")).toBeNull();
    // The query is not a filter. Clearing the filters must not throw away what
    // the reader was actually looking for.
    expect(currentParams(router).get("q")).toBe("night");
  });

  test("a desktop keeps them on the screen", async () => {
    setViewportWidth(1440);
    renderApp("/search?q=night");

    expect(await screen.findByLabelText("Minimum seeders")).not.toBeNull();
    expect(screen.queryByRole("button", { name: /^Filters/ })).toBeNull();
  });
});
