/**
 * The shell's structural behaviour.
 *
 * jsdom lays nothing out, so none of this can be measured in pixels. What it
 * can check is that one viewport width produces one structure — which is why
 * `useSidebarLayout` derives a named layout rather than leaving the three forms
 * to CSS: the name is the thing a test, and a reader, can hold onto.
 */
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import en from "../i18n/en.json";
import {
  TEST_USER,
  renderApp,
  server,
  setViewportWidth,
  signedIn,
  useMockApi,
} from "../test/harness";
import { NAV_ITEMS } from "./sidebar";

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
  });

  test("is a rail just below 1280px", async () => {
    setViewportWidth(1279);
    renderApp("/library");

    const nav = await screen.findByRole("navigation", { name: "Primary" });
    expect(nav.dataset.layout).toBe("rail");
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

describe("reaching every screen", () => {
  test("the sidebar links to every destination", async () => {
    setViewportWidth(1440);
    renderApp("/library");

    const nav = await screen.findByRole("navigation", { name: "Primary" });
    for (const item of NAV_ITEMS) {
      const link = within(nav).getByRole("link", { name: en.nav[item.id] });
      expect(link.getAttribute("href")).toBe(item.path);
    }
  });

  test("Search is one of them, not only a field in the bar", async () => {
    setViewportWidth(1440);
    renderApp("/library");

    const nav = await screen.findByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: en.nav.search }).getAttribute("href")).toBe(
      "/search",
    );
  });

  test("the drawer carries the same destinations, Search included", async () => {
    setViewportWidth(390);
    renderApp("/library");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: en.nav.navigation }));

    const nav = screen.getByRole("navigation", { name: "Primary" });
    for (const item of NAV_ITEMS) {
      expect(within(nav).getByRole("link", { name: en.nav[item.id] })).toBeTruthy();
    }
  });

  test("following the Monitors link opens the monitors screen, not a placeholder", async () => {
    setViewportWidth(1440);
    server.use(http.get("/api/monitors", () => HttpResponse.json([])));
    renderApp("/library");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("link", { name: en.nav.monitors }));

    expect(await screen.findByText(en.monitors.empty)).toBeTruthy();
  });

  test("following the Recommendations link opens the recommendations screen", async () => {
    setViewportWidth(1440);
    server.use(http.get("/api/recommendations", () => HttpResponse.json([])));
    renderApp("/library");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("link", { name: en.nav.recommendations }));

    expect(await screen.findByText(en.recommendations.empty)).toBeTruthy();
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
  test("opens the global search with the standard keyboard shortcut", async () => {
    setViewportWidth(1440);
    renderApp("/library");
    const input = await screen.findByLabelText("Search");
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });

    expect(document.activeElement).toBe(input);
  });

  test("Tab reaches the account menu and opens it", async () => {
    setViewportWidth(1440);
    renderApp("/library");
    const user = userEvent.setup();

    const menuTrigger = await screen.findByRole("button", { name: TEST_USER.username });

    // Every nav link is a stop before the menu is, so the budget is derived from
    // the destinations rather than fixed — adding one must not fail this test.
    let reached = false;
    for (let step = 0; step < NAV_ITEMS.length + 6 && !reached; step += 1) {
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

describe("one popover at a time", () => {
  test("opening a menu dismisses the panel the bar already had open", async () => {
    setViewportWidth(1440);
    renderApp("/library");
    const user = userEvent.setup();

    const activity = await screen.findByRole("button", { name: en.activity.title });
    await user.click(activity);
    expect(screen.getByRole("region", { name: en.activity.title })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: en.locale.label }));

    expect(screen.queryByRole("region", { name: en.activity.title })).toBeNull();
    expect(activity.getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByRole("menu", { name: en.locale.label })).toBeTruthy();
  });

  test("and opening the next one dismisses that menu in turn", async () => {
    setViewportWidth(1440);
    renderApp("/library");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: en.locale.label }));
    await user.click(screen.getByRole("button", { name: TEST_USER.username }));

    expect(screen.queryByRole("menu", { name: en.locale.label })).toBeNull();
    expect(screen.getByRole("menu", { name: TEST_USER.username })).toBeTruthy();
  });
});

describe("the bar at phone width", () => {
  /**
   * jsdom lays nothing out, so "the search field is usable" cannot be measured
   * here — the browser walk does that. What this holds onto is the structure
   * that makes it true: the bar names its layout the way the sidebar does, and
   * the control that had to give up its 90px of label kept its name.
   */
  test("names its layout so the two rows cannot drift from the sidebar's form", async () => {
    setViewportWidth(390);
    renderApp("/library");

    const bar = await screen.findByRole("banner");
    expect(bar.dataset.layout).toBe("drawer");
  });

  test("the drawer trigger is an icon that still announces itself", async () => {
    setViewportWidth(390);
    renderApp("/library");

    const trigger = await screen.findByRole("button", { name: en.nav.navigation });
    expect(trigger.textContent).toBe("");
  });

  test("the bar is one row again above the breakpoint", async () => {
    setViewportWidth(1440);
    renderApp("/library");

    const bar = await screen.findByRole("banner");
    expect(bar.dataset.layout).toBe("full");
    expect(screen.queryByRole("button", { name: en.nav.navigation })).toBeNull();
  });
});
