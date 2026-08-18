/**
 * The screen wrapper must be a new element when somebody arrives somewhere, and
 * the same element when a screen is only correcting its own address.
 *
 * This is not a test about animation. It is a test about the shorts feed, which
 * writes the address on every scroll so that a clip can be linked to. Keyed on
 * the pathname, each of those writes would remount the feed: the video would
 * restart and the reader would be thrown back up the list, with the cause looking
 * like a scroll bug rather than like a decision somebody made about motion.
 *
 * `replace` versus `push` is the distinction the router already draws, and it is
 * the one worth pinning down, because the next person to touch this will reach
 * for `location.pathname` — it is the obvious thing, and it is wrong.
 */
import { cleanup, screen, waitFor } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { renderApp, signedIn, useMockApi } from "../test/harness";

useMockApi();
afterEach(cleanup);

beforeEach(() => {
  signedIn();
});

/** The element the screen animation is attached to. */
function wrapper(): Element {
  const found = document.querySelector("main .pa-enter");
  if (found === null) throw new Error("the screen wrapper is missing");
  return found;
}

describe("the screen wrapper", () => {
  test("survives a screen rewriting its own address", async () => {
    const { router } = renderApp("/shorts");
    await waitFor(() => expect(document.querySelector("main .pa-enter")).not.toBeNull());
    const before = wrapper();

    // What the feed does on every scroll.
    await act(async () => {
      await router.navigate("/shorts/s-1", { replace: true });
    });
    await act(async () => {
      await router.navigate("/shorts/s-2", { replace: true });
    });

    // The same node, so the feed's scroll position, its observer and its player
    // are all still the ones it started with.
    expect(wrapper()).toBe(before);
  });

  test("is replaced when somebody goes somewhere", async () => {
    const { router } = renderApp("/library");
    await waitFor(() => expect(screen.queryByRole("heading", { level: 1 })).not.toBeNull());
    const before = wrapper();

    await act(async () => {
      await router.navigate("/watchlist");
    });

    expect(wrapper()).not.toBe(before);
  });

  test("is replaced on the way back, too", async () => {
    const { router } = renderApp("/library");
    await waitFor(() => expect(screen.queryByRole("heading", { level: 1 })).not.toBeNull());

    await act(async () => {
      await router.navigate("/watchlist");
    });
    const onWatchlist = wrapper();

    await act(async () => {
      await router.navigate(-1);
    });

    // A back button is an arrival: the reader is somewhere they were not a moment
    // ago, which is the whole test of whether a screen should animate.
    expect(wrapper()).not.toBe(onWatchlist);
  });
});
