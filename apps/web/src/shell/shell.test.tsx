/**
 * The shell's structural behaviour.
 *
 * jsdom lays nothing out, so none of this can be measured in pixels. What it
 * can check is that one viewport width produces one structure — which is why
 * `useSidebarLayout` derives a named layout rather than leaving the three forms
 * to CSS: the name is the thing a test, and a reader, can hold onto.
 */
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { TEST_USER, renderApp, setViewportWidth, signedIn, useMockApi } from "../test/harness";

useMockApi();
afterEach(cleanup);

beforeEach(() => {
  signedIn();
});

describe("responsive structure", () => {
  test("is a full sidebar at 1280px", async () => {
    setViewportWidth(1280);
    renderApp("/library");

    const nav = await screen.findByRole("navigation", { name: "Primary" });
    expect(nav.dataset.layout).toBe("full");
    expect(nav.className).toContain("w-[var(--sidebar-width)]");
  });

  test("is a rail just below 1280px", async () => {
    setViewportWidth(1279);
    renderApp("/library");

    const nav = await screen.findByRole("navigation", { name: "Primary" });
    expect(nav.dataset.layout).toBe("rail");
    expect(nav.className).toContain("w-[var(--sidebar-rail-width)]");
  });

  test("is a rail at 768px", async () => {
    setViewportWidth(768);
    renderApp("/library");

    const nav = await screen.findByRole("navigation", { name: "Primary" });
    expect(nav.dataset.layout).toBe("rail");
  });

  test("is a drawer just below 768px, closed until asked for", async () => {
    setViewportWidth(767);
    renderApp("/library");

    const trigger = await screen.findByRole("button", { name: "Navigation" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("navigation", { name: "Primary" })).toBeNull();

    await userEvent.setup().click(trigger);

    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(nav.dataset.layout).toBe("drawer");
    expect(screen.getByRole("dialog", { name: "Navigation" })).toBeTruthy();
  });
});

describe("drawer focus", () => {
  test("moves focus inside and returns it to the trigger on Escape", async () => {
    setViewportWidth(767);
    renderApp("/library");
    const user = userEvent.setup();

    const trigger = await screen.findByRole("button", { name: "Navigation" });
    await user.click(trigger);

    const close = screen.getByRole("button", { name: "Close" });
    await waitFor(() => expect(document.activeElement).toBe(close));

    await user.keyboard("{Escape}");

    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(screen.queryByRole("dialog", { name: "Navigation" })).toBeNull();
  });

  test("keeps Tab inside the drawer", async () => {
    setViewportWidth(767);
    renderApp("/library");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Navigation" }));
    const drawer = screen.getByRole("dialog", { name: "Navigation" });

    for (let step = 0; step < 8; step += 1) {
      await user.tab();
      expect(drawer.contains(document.activeElement)).toBe(true);
    }
  });
});

describe("keyboard reach", () => {
  test("Tab reaches the account menu and opens it", async () => {
    setViewportWidth(1440);
    renderApp("/library");
    const user = userEvent.setup();

    const menuTrigger = await screen.findByRole("button", { name: TEST_USER.username });

    let reached = false;
    for (let step = 0; step < 10 && !reached; step += 1) {
      await user.tab();
      reached = document.activeElement === menuTrigger;
    }
    expect(reached).toBe(true);

    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Sign out" })).toBeTruthy();
  });
});

describe("status cluster", () => {
  test("opens a panel beside its trigger rather than navigating", async () => {
    setViewportWidth(1440);
    renderApp("/library");
    const user = userEvent.setup();

    const trigger = await screen.findByRole("button", { name: "Activity" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");

    await user.click(trigger);

    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("region", { name: "Activity" })).toBeTruthy();
    // Still on the same screen: a panel, not a page.
    expect(screen.getByRole("heading", { name: "Library" })).toBeTruthy();
  });
});
