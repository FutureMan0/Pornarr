/**
 * The search field, at the seam where it meets the API and the keyboard.
 *
 * What is worth pinning down here is the behaviour that was missing rather than
 * the markup: that typing produces an answer without leaving the screen, that the
 * keyboard can reach it, and that the two ways out — a suggestion and the full
 * results page — both work and are distinguishable.
 */
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import {
  currentParams,
  currentPath,
  renderApp,
  server,
  signedIn,
  useMockApi,
} from "../test/harness";

useMockApi();
afterEach(cleanup);

beforeEach(() => {
  signedIn();
});

function suggestion(id: string, title: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    title,
    studio: "Nightfall",
    release_date: "2026-01-01",
    duration_seconds: 1_800,
    quality: "1080p",
    resolution: "1920x1080",
    rating: 4,
    rating_count: 2,
    size: 1_000,
    performers: [],
    tags: [],
    relevance: 1,
    in_my_library: false,
    ...extra,
  };
}

function serveSuggestions(...items: ReturnType<typeof suggestion>[]) {
  server.use(http.get("/api/search/local", () => HttpResponse.json({ items, next_cursor: null })));
}

describe("the global search field", () => {
  test("advertises the shortcut it has always had", async () => {
    renderApp("/library");
    const field = await screen.findByRole("combobox", { name: "Search" });

    // The property, for a screen reader.
    expect(field.getAttribute("aria-keyshortcuts")).toContain("K");
    // And the glyph, for everyone else. It was implemented and invisible.
    expect(screen.getByText(/⌘K|Ctrl K/)).not.toBeNull();
  });

  test("typing answers from the library without leaving the screen", async () => {
    serveSuggestions(suggestion("m-1", "Aurora 214"), suggestion("m-2", "Low Tide"));
    const user = userEvent.setup();
    const { router } = renderApp("/library");

    await user.type(await screen.findByRole("combobox", { name: "Search" }), "aur");

    expect(await screen.findByText("Aurora 214")).not.toBeNull();
    expect(screen.getByText("Low Tide")).not.toBeNull();
    // Still where we started: a suggestion is not a navigation.
    expect(currentPath(router)).toBe("/library");
  });

  test("one letter is not a query", async () => {
    let asked = false;
    server.use(
      http.get("/api/search/local", () => {
        asked = true;
        return HttpResponse.json({ items: [], next_cursor: null });
      }),
    );
    const user = userEvent.setup();
    renderApp("/library");

    await user.type(await screen.findByRole("combobox", { name: "Search" }), "a");
    // A single letter matches the whole library and answers nothing, so it is
    // not worth a request.
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
    expect(asked).toBe(false);
  });

  test("the arrow keys walk the suggestions and Enter opens the one selected", async () => {
    serveSuggestions(suggestion("m-1", "Aurora 214"), suggestion("m-2", "Low Tide"));
    const user = userEvent.setup();
    const { router } = renderApp("/library");
    const field = await screen.findByRole("combobox", { name: "Search" });

    await user.type(field, "tide");
    await screen.findByText("Low Tide");

    await user.keyboard("{ArrowDown}{ArrowDown}");
    // Focus never leaves the field — that is what lets typing continue — so the
    // active option is named rather than focused.
    await waitFor(() => expect(field.getAttribute("aria-activedescendant")).not.toBeNull());

    await user.keyboard("{Enter}");
    expect(currentPath(router)).toBe("/library/m-2");
  });

  test("arrowing off the end returns to what you typed rather than wrapping", async () => {
    serveSuggestions(suggestion("m-1", "Aurora 214"));
    const user = userEvent.setup();
    renderApp("/library");
    const field = await screen.findByRole("combobox", { name: "Search" });

    await user.type(field, "aur");
    await screen.findByText("Aurora 214");

    await user.keyboard("{ArrowDown}");
    expect(field.getAttribute("aria-activedescendant")).not.toBeNull();
    await user.keyboard("{ArrowDown}");
    // Back to the field: wrapping makes "never mind, use what I typed"
    // unreachable.
    expect(field.getAttribute("aria-activedescendant")).toBeNull();
  });

  test("Enter with nothing selected goes to the full results", async () => {
    serveSuggestions(suggestion("m-1", "Aurora 214"));
    const user = userEvent.setup();
    const { router } = renderApp("/library");

    await user.type(await screen.findByRole("combobox", { name: "Search" }), "aurora{Enter}");

    expect(currentPath(router)).toBe("/search");
    expect(currentParams(router).get("q")).toBe("aurora");
  });

  test("the last row reaches the indexers, which the suggestions never do", async () => {
    serveSuggestions(suggestion("m-1", "Aurora 214"));
    const user = userEvent.setup();
    const { router } = renderApp("/library");

    await user.type(await screen.findByRole("combobox", { name: "Search" }), "aurora");
    await user.click(await screen.findByRole("button", { name: /See all results/ }));

    expect(currentPath(router)).toBe("/search");
    expect(currentParams(router).get("q")).toBe("aurora");
  });

  test("nothing found says so, and says it differently from still looking", async () => {
    serveSuggestions();
    const user = userEvent.setup();
    renderApp("/library");

    await user.type(await screen.findByRole("combobox", { name: "Search" }), "zzzz");

    expect(await screen.findByText(/Nothing in the library matches/)).not.toBeNull();
  });

  test("the field can be emptied, and Escape is not a trapdoor", async () => {
    serveSuggestions(suggestion("m-1", "Aurora 214"));
    const user = userEvent.setup();
    renderApp("/library");
    const field = await screen.findByRole("combobox", { name: "Search" });

    await user.type(field, "aurora");
    await screen.findByText("Aurora 214");

    // First Escape closes the list and keeps the query: losing a query you are
    // still reading results for is worse than one extra keystroke.
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByText("Aurora 214")).toBeNull());
    expect((field as HTMLInputElement).value).toBe("aurora");

    await user.keyboard("{Escape}");
    expect((field as HTMLInputElement).value).toBe("");
  });

  test("the clear button empties the field and hands focus back", async () => {
    const user = userEvent.setup();
    renderApp("/library");
    const field = await screen.findByRole("combobox", { name: "Search" });

    await user.type(field, "aurora");
    await user.click(await screen.findByRole("button", { name: "Clear the search" }));

    expect((field as HTMLInputElement).value).toBe("");
    expect(document.activeElement).toBe(field);
  });

  test("a failing suggestion request does not take the shell down", async () => {
    server.use(
      http.get("/api/search/local", () => HttpResponse.json({ detail: "nope" }, { status: 500 })),
    );
    const user = userEvent.setup();
    const { router } = renderApp("/library");

    await user.type(await screen.findByRole("combobox", { name: "Search" }), "aurora");

    // The bar is how you get away from a broken screen; it must not become one.
    await waitFor(() => expect(screen.queryByText(/Nothing in the library/)).not.toBeNull());
    expect(currentPath(router)).toBe("/library");
    // And the way to the full results is still there.
    expect(screen.getByRole("button", { name: /See all results/ })).not.toBeNull();
  });
});
