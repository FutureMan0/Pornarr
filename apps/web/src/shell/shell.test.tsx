/**
 * The shell's structural behaviour.
 *
 * jsdom lays nothing out, so none of this can be measured in pixels. What it
 * can check is that one viewport width produces one structure — which is why
 * `useSidebarLayout` derives a named layout rather than leaving the three forms
 * to CSS: the name is the thing a test, and a reader, can hold onto.
 */
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
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

    // Derived from the nav table rather than a fixed number: every destination
    // added to the sidebar sits between the top of the page and this trigger,
    // so a literal here goes stale the next time the navigation grows. The
    // slack covers the skip link, the search field and the status cluster.
    const budget = NAV_ITEMS.length + 6;

    let reached = false;
    for (let step = 0; step < budget && !reached; step += 1) {
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

describe("who and where you are", () => {
  test("the sidebar says whose server this is and what you are on it", async () => {
    setViewportWidth(1280);
    renderApp("/library");

    const nav = await screen.findByRole("navigation", { name: "Primary" });

    await waitFor(() => expect(nav.textContent).toContain("Admin"));
    expect(nav.textContent).toContain("Pornarr");
    // Not decoration: this is a server people invite friends onto, so the scope
    // of that invitation sits at the top of every screen.
    expect(nav.textContent).toContain("LOCAL");
    expect(nav.textContent).toContain(TEST_USER.username);
  });

  test("a guest is told they are a guest rather than left to infer it", async () => {
    server.use(http.get("/api/auth/me", () => HttpResponse.json({ ...TEST_USER, role: "user" })));
    setViewportWidth(1280);
    renderApp("/library");

    const nav = await screen.findByRole("navigation", { name: "Primary" });

    await waitFor(() => expect(nav.textContent).toContain("Guest"));
    expect(nav.textContent).not.toContain("Admin");
  });

  test("the rail keeps the identity for readers after the pixels are gone", async () => {
    setViewportWidth(1279);
    renderApp("/library");

    const nav = await screen.findByRole("navigation", { name: "Primary" });

    expect(nav.dataset.layout).toBe("rail");
    // Still in the accessibility tree, just not on screen.
    await waitFor(() => expect(nav.textContent).toContain("Admin"));
  });
});

describe("navigation counts", () => {
  test("a destination with something waiting carries the number", async () => {
    server.use(
      http.get("/api/queue", () => HttpResponse.json([{ id: "q-1" }, { id: "q-2" }])),
      http.get("/api/requests", () =>
        HttpResponse.json([
          { id: "r-1", status: "searching" },
          { id: "r-2", status: "completed" },
        ]),
      ),
    );
    setViewportWidth(1280);
    renderApp("/library");

    const nav = await screen.findByRole("navigation", { name: "Primary" });
    const downloads = await screen.findByRole("link", { name: /Downloads/ });

    await waitFor(() => expect(downloads.textContent).toContain("2"));
    // A finished request is history, not a task, so only the open one counts.
    expect(nav.querySelector('a[href="/requests"]')?.textContent).toContain("1");
  });

  test("nothing waiting shows no number rather than a zero", async () => {
    setViewportWidth(1280);
    renderApp("/library");

    const downloads = await screen.findByRole("link", { name: /Downloads/ });

    // The label appears twice — once as the icon's accessible title — so this
    // asks the question it actually means: is there a number?
    await waitFor(() => expect(downloads.textContent).toContain("Downloads"));
    expect(downloads.textContent).not.toMatch(/\d/);
  });

  test("a failing count never takes the navigation down with it", async () => {
    // The navigation is how you get away from a broken screen; it cannot be
    // the thing that breaks.
    server.use(
      http.get("/api/queue", () => new HttpResponse(null, { status: 500 })),
      http.get("/api/requests", () => new HttpResponse(null, { status: 500 })),
    );
    setViewportWidth(1280);
    renderApp("/library");

    const nav = await screen.findByRole("navigation", { name: "Primary" });

    expect(nav.textContent).toContain("Downloads");
    expect(nav.textContent).toContain("Library");
  });
});
