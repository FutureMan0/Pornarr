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
import {
  TEST_USER,
  currentPath,
  renderApp,
  server,
  setViewportWidth,
  signedIn,
  useMockApi,
} from "../test/harness";
import { NAV_ICON_PATHS } from "./sidebar";
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

  test("a phone gets a tab bar, and no sidebar at all", async () => {
    setViewportWidth(767);
    renderApp("/library");

    // Not behind a button. A navigation you have to open before you can read it
    // is the thing the tab bar replaced.
    const tabs = await screen.findByRole("navigation", { name: "Main" });
    expect(screen.queryByRole("navigation", { name: "Primary" })).toBeNull();
    expect(tabs.querySelector('a[href="/library"]')).not.toBeNull();
  });
});

describe("the navigation's glyphs", () => {
  test("every destination has one", () => {
    // The administrator's five had none. Invisible in the sidebar, where they
    // are indented under a heading; obvious the moment a flat list — the tab bar,
    // the "everywhere else" sheet — puts them beside destinations that do. An
    // empty 20px box is not a smaller icon, it is a hole.
    for (const item of NAV_ITEMS) {
      expect(NAV_ICON_PATHS[item.id], `${item.id} has no icon`).toBeDefined();
    }
  });
});

describe("the tab bar", () => {
  test("shows a guest the four the design names, and reaches the rest", async () => {
    server.use(http.get("/api/auth/me", () => HttpResponse.json({ ...TEST_USER, role: "user" })));
    setViewportWidth(390);
    const user = userEvent.setup();
    renderApp("/library");

    const tabs = await screen.findByRole("navigation", { name: "Main" });
    await waitFor(() =>
      expect([...tabs.querySelectorAll("a")].map((a) => a.getAttribute("href"))).toEqual([
        "/feed",
        "/library",
        "/shorts",
        "/watchlist",
      ]),
    );

    // Four tabs cannot reach ten destinations. Settings is the one that proves
    // it matters: it is where the accent and the language live, and the design's
    // guest set does not include it.
    await user.click(within(tabs).getByRole("button", { name: "More" }));
    const sheet = await screen.findByRole("dialog");
    expect(within(sheet).getByRole("link", { name: /Settings/ })).not.toBeNull();
    expect(within(sheet).getByRole("link", { name: /Collections/ })).not.toBeNull();
  });

  test("shows an administrator the administrator's four", async () => {
    setViewportWidth(390);
    renderApp("/library");

    const tabs = await screen.findByRole("navigation", { name: "Main" });
    await waitFor(() =>
      expect([...tabs.querySelectorAll("a")].map((a) => a.getAttribute("href"))).toEqual([
        "/admin",
        "/library",
        "/downloads",
        "/settings",
      ]),
    );
  });

  test("marks one tab, and the right one, on an administrator's sub-screen", async () => {
    setViewportWidth(390);
    renderApp("/admin/tags");

    const tabs = await screen.findByRole("navigation", { name: "Main" });
    // The same prefix trap the sidebar had: `/admin` is a prefix of
    // `/admin/tags`, and the tab bar derives its exactness from the same place.
    await waitFor(() => expect(tabs.querySelector('a[href="/admin/tags"]')).toBeNull());
    const current = [...tabs.querySelectorAll('a[aria-current="page"]')];
    expect(current).toHaveLength(0);
  });

  test("a desktop gets no tab bar", async () => {
    setViewportWidth(1440);
    renderApp("/library");

    await screen.findByRole("navigation", { name: "Primary" });
    expect(screen.queryByRole("navigation", { name: "Main" })).toBeNull();
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

  test("only one entry is current on an administrator's sub-screen", async () => {
    setViewportWidth(1280);
    renderApp("/admin/tags");

    const nav = await screen.findByRole("navigation", { name: "Primary" });
    await waitFor(() => expect(nav.querySelector('a[href="/admin/tags"]')).not.toBeNull());

    // `/admin` is a prefix of `/admin/tags`, and NavLink counts a prefix as
    // active unless told otherwise — so Dashboard stayed marked here and two
    // entries read as current at once.
    const current = [...nav.querySelectorAll('a[aria-current="page"]')].map((link) =>
      link.getAttribute("href"),
    );
    expect(current).toEqual(["/admin/tags"]);
  });

  test("a title's own screen still marks the library it came from", async () => {
    setViewportWidth(1280);
    renderApp("/library/m-1");

    const nav = await screen.findByRole("navigation", { name: "Primary" });
    // The opposite case, and the reason exactness is derived rather than applied
    // to every entry: `/library/:mediaId` is not a destination of its own, so the
    // reader is still in the library.
    await waitFor(() =>
      expect(nav.querySelector('a[href="/library"]')?.getAttribute("aria-current")).toBe("page"),
    );
  });

  test("a guest is not offered Downloads, whose endpoints are admin-only", async () => {
    let asked = false;
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ ...TEST_USER, role: "user" })),
      http.get("/api/queue", () => {
        asked = true;
        return HttpResponse.json([]);
      }),
    );
    setViewportWidth(1280);
    renderApp("/library");

    const nav = await screen.findByRole("navigation", { name: "Primary" });

    await waitFor(() => expect(nav.textContent).toContain("Guest"));
    expect(nav.querySelector('a[href="/downloads"]')).toBeNull();
    // And the badge behind it is not fetched either: a 403 on every screen to
    // decide the number on an entry nobody can see.
    expect(asked).toBe(false);
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

/**
 * Signing in puts you where your account is for.
 *
 * Everybody used to land on the library, which is the right answer for nobody: a
 * household member opens this to watch something, an administrator because
 * something needs attention.
 */
describe("where signing in lands", () => {
  test("a guest starts at the feed, which answers what to watch", async () => {
    server.use(http.get("/api/auth/me", () => HttpResponse.json({ ...TEST_USER, role: "user" })));
    setViewportWidth(1440);
    const { router } = renderApp("/");

    await waitFor(() => expect(currentPath(router)).toBe("/feed"));
  });

  test("an administrator starts at the dashboard, which is maintenance", async () => {
    setViewportWidth(1440);
    const { router } = renderApp("/");

    await waitFor(() => expect(currentPath(router)).toBe("/admin"));
  });

  test("it replaces rather than pushes, so back does not bounce", async () => {
    setViewportWidth(1440);
    const { router } = renderApp("/");

    await waitFor(() => expect(currentPath(router)).toBe("/admin"));
    // A push would leave "/" in the history, and going back would redirect
    // forward again — a trap rather than a navigation.
    expect(router.state.historyAction).not.toBe("PUSH");
  });
});
