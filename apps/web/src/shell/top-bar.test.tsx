/**
 * The top bar's three additions: the screen's name, the artwork control, and
 * the persistent connection indicator.
 *
 * The artwork control is the one worth the most care. It is the only thing
 * standing between a shared screen and a wall of uncensored posters, so what is
 * tested is not that a class name changes but that the state survives a reload
 * and that the cautious value is the one you get when anything goes wrong.
 */
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { artIsVisible, resetArtVisibility, setArtVisible } from "../lib/art-visibility";
import { renderApp, setViewportWidth, signedIn, useMockApi } from "../test/harness";

useMockApi();

afterEach(() => {
  cleanup();
  window.localStorage.removeItem(STORAGE_KEY);
  resetArtVisibility();
});

beforeEach(() => {
  signedIn();
  setViewportWidth(1440);
});

const STORAGE_KEY = "pornarr-art-visible";

describe("the screen's name", () => {
  test("is the page's only level-one heading", async () => {
    renderApp("/library");

    const headings = await screen.findAllByRole("heading", { level: 1 });

    // Not "there is a heading" but "there is exactly one": the whole reason the
    // routes gave theirs up is that two would be announced twice.
    expect(headings).toHaveLength(1);
    expect(headings[0]?.textContent).toBe("Library");
  });

  test("changes when the screen does", async () => {
    renderApp("/watchlist");

    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Watchlist"),
    );
  });
});

describe("the artwork control", () => {
  test("starts hidden, which is the safe end", async () => {
    renderApp("/library");

    const toggle = await screen.findByRole("button", { name: "Art hidden" });

    expect(toggle.getAttribute("aria-pressed")).toBe("false");
  });

  test("reveals artwork and says so", async () => {
    renderApp("/library");
    const toggle = await screen.findByRole("button", { name: "Art hidden" });

    await userEvent.setup().click(toggle);

    const revealed = await screen.findByRole("button", { name: "Art shown" });
    expect(revealed.getAttribute("aria-pressed")).toBe("true");
    expect(artIsVisible()).toBe(true);
  });

  test("remembers the choice for the next visit", async () => {
    renderApp("/library");

    await userEvent.setup().click(await screen.findByRole("button", { name: "Art hidden" }));

    // A per-device setting, deliberately not sent to the server: the same
    // person wants art hidden in a shared office and shown at home.
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("true");
  });

  test("a storage that throws leaves artwork hidden rather than failing", () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage is blocked for this origin");
    });

    // Safari in private mode, or any browser with storage blocked. The value
    // must fall back to hidden — the cautious end — and must not propagate.
    expect(() => resetArtVisibility()).not.toThrow();
    expect(artIsVisible()).toBe(false);

    getItem.mockRestore();
  });

  test("a storage that refuses writes still applies the choice to this page", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });

    setArtVisible(true);

    // It will not survive a reload, which is acceptable; refusing to reveal
    // artwork because a write failed would not be.
    expect(artIsVisible()).toBe(true);

    setItem.mockRestore();
  });
});

describe("the connection indicator", () => {
  test("says the connection is live rather than leaving it to be inferred", async () => {
    renderApp("/library");

    // Always present, in every state. An indicator that appears only on failure
    // cannot be trusted to be absent.
    expect(await screen.findByText("Live")).toBeTruthy();
  });
});
