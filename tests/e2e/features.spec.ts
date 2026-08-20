import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
/**
 * One worked flow per area the rest of the suite only ever visits.
 *
 * `navigation.spec.ts` proves every screen is reachable, contrasts correctly
 * and passes axe. That is not the same claim as "the feature works": a screen
 * that lists collections and offers no way to put a title in one passes
 * navigation and fails a household. Each case here drives one area the way a
 * person uses it - through the browser, against the running stack - and stops
 * at the first thing the interface cannot do.
 */
import { type Page, expect, test } from "@playwright/test";
import {
  type LibraryItem,
  apiDelete,
  apiGet,
  apiPost,
  apiPostRaw,
  enabledRootFolder,
  expectNoAccessibilityViolations,
  hasFfmpeg,
  hostPathFor,
  libraryItemByTitle,
  loginAsAdmin,
  seedLibraryMedia,
} from "./helpers";

const RUN = `f${Date.now().toString(36)}`;
const NO_FIXTURE =
  "The suite cannot put a title in the library: either ffmpeg is missing or the stack's " +
  "data volume is not reachable from here.";

/** The one title every case here works on. */
async function aTitle(page: Page): Promise<LibraryItem | null> {
  return seedLibraryMedia(page);
}

/**
 * A title in the library that no browser can direct-play.
 *
 * High 4:4:4 Predictive with 4:4:4 chroma: `_profile_family` in
 * `pornarr_core/playback.py` refuses to fold that onto "high", so
 * `playback-info` answers `direct_play: false` and asking to watch it opens a
 * transcode session. Written into the configured root folder and adopted by a
 * scan, which is the only way this suite puts a file in the library.
 */
async function aTranscodingTitle(page: Page): Promise<LibraryItem | null> {
  const title = "Features Transcode Only";
  const existing = await libraryItemByTitle(page, title);
  if (existing !== null) return existing;

  const folder = await enabledRootFolder(page);
  if (folder === null || !hasFfmpeg()) return null;
  const root = hostPathFor(folder.path);
  if (root === null) return null;

  test.setTimeout(Math.max(test.info().timeout, 300_000));
  const target = join(root, `${title}.mp4`);
  if (!existsSync(target)) {
    execFileSync(
      "ffmpeg",
      [
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=640x360:rate=15:duration=600",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=600",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "34",
        "-profile:v",
        "high444",
        "-pix_fmt",
        "yuv444p",
        "-c:a",
        "aac",
        "-shortest",
        target,
      ],
      { stdio: "ignore" },
    );
  }

  // A scan asked for twice in one wall-clock minute is collapsed, so adopting
  // a file the stack has never seen means asking again on a slower cadence.
  let lastScan = 0;
  await expect
    .poll(
      async () => {
        if (Date.now() - lastScan > 20_000) {
          lastScan = Date.now();
          const scan = await apiPostRaw(page, `/api/admin/library/root-folders/${folder.id}/scan`);
          expect([202, 409]).toContain(scan.status());
        }
        return (await libraryItemByTitle(page, title)) !== null;
      },
      { timeout: 180_000, intervals: [2_000] },
    )
    .toBe(true);
  return libraryItemByTitle(page, title);
}

test.describe("collections", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("a shelf is made, named, and found again after a reload", async ({ page }) => {
    const name = `Shelf ${RUN}`;
    await page.goto("/collections");
    await expect(page.getByRole("heading", { name: "Collections", level: 1 })).toBeVisible();

    await page.getByLabel("Name").fill(name);
    await page.getByRole("button", { name: "Create", exact: true }).click();

    const shelf = page.getByRole("link", { name: new RegExp(name) });
    await expect(shelf).toBeVisible();
    await page.reload();
    await expect(page.getByRole("link", { name: new RegExp(name) })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  test("a title is put on a shelf, and the shelf then holds it", async ({ page }) => {
    // PRODUCT GAP. `PUT /api/collections/{collection_id}/items/{media_id}` and
    // its DELETE exist and are covered by the API tests; nothing in
    // `apps/web/src` ever calls either. A collection can be created, named,
    // shared and deleted, and never filled - so the detail screen's
    // "Nothing on this shelf yet." is the only state it can ever be in.
    const media = await aTitle(page);
    test.skip(media === null, NO_FIXTURE);
    if (media === null) return;

    const name = `Filled ${RUN}`;
    await page.goto("/collections");
    await page.getByLabel("Name").fill(name);
    await page.getByRole("button", { name: "Create", exact: true }).click();
    await expect(page.getByRole("link", { name: new RegExp(name) })).toBeVisible();

    // From the title, the way a person adds one: open it, put it on a shelf.
    await page.goto(`/library/${media.id}`);
    const add = page.getByRole("button", { name: /Add to collection|Save to collection/i });
    await expect(
      add,
      "The detail screen offers no way to put this title on a shelf, so a collection can " +
        "never hold anything. See PUT /api/collections/{collection_id}/items/{media_id}.",
    ).toBeVisible();
    await add.click();
    await page.getByRole("button", { name, exact: true }).click();

    await page.goto("/collections");
    await page.getByRole("link", { name: new RegExp(name) }).click();
    await expect(page.getByRole("link", { name: new RegExp(media.title) })).toBeVisible();
  });
});

test.describe("watchlist", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("a title is saved from its own page, survives a reload, and comes off again", async ({
    page,
  }) => {
    const media = await aTitle(page);
    test.skip(media === null, NO_FIXTURE);
    if (media === null) return;

    await page.goto(`/library/${media.id}`);
    const toggle = page.getByRole("button", { name: /Add to watchlist|Remove/ });
    await expect(toggle).toBeVisible();
    if ((await toggle.getAttribute("aria-pressed")) === "true") {
      await toggle.click();
      await expect(toggle).toHaveAttribute("aria-pressed", "false");
    }
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-pressed", "true");

    await page.goto("/watchlist");
    await expect(page.getByRole("heading", { name: "Watchlist", level: 1 })).toBeVisible();
    const saved = page.getByRole("link", { name: new RegExp(media.title) });
    await expect(saved).toBeVisible();
    await expectNoAccessibilityViolations(page);

    // The list is the record, not the button: reload before removing.
    await page.reload();
    await page
      .getByRole("listitem")
      .filter({ hasText: media.title })
      .getByRole("button", { name: "Remove" })
      .click();
    await expect(page.getByRole("link", { name: new RegExp(media.title) })).toHaveCount(0);
  });
});

test.describe("continue watching", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("a position that was reached is offered back, and the row says where it stopped", async ({
    page,
  }) => {
    const media = await aTitle(page);
    test.skip(media === null, NO_FIXTURE);
    if (media === null) return;

    // Reported the way the player reports it, because that is the only writer.
    await apiPost(page, `/api/playback/${media.id}/progress`, {
      position_seconds: 12,
      duration_seconds: 30,
    });

    await page.goto("/continue");
    await expect(page.getByRole("heading", { name: "Continue watching", level: 1 })).toBeVisible();
    const row = page.getByRole("listitem").filter({ hasText: media.title });
    await expect(
      row,
      "A title that was played is not offered back. See GET /api/playback/continue-watching.",
    ).toBeVisible();
    await expectNoAccessibilityViolations(page);

    const progress = await apiGet<{ media_id: string; position_seconds: number }[]>(
      page,
      "/api/playback/continue-watching",
    );
    expect(progress.find((item) => item.media_id === media.id)?.position_seconds).toBe(12);
  });
});

test.describe("tags", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("a tag put on a title shows on it, in the vocabulary, and filters the library", async ({
    page,
  }) => {
    const media = await aTitle(page);
    test.skip(media === null, NO_FIXTURE);
    if (media === null) return;

    const tag = `gauntlet-${RUN}`;
    await page.goto(`/library/${media.id}`);
    await page.getByRole("textbox", { name: "Correct tag" }).fill(tag);
    await page.getByRole("button", { name: "Add corrected tag" }).click();
    await expect(page.getByText(tag, { exact: false })).toBeVisible();

    // The administrator's vocabulary knows it too, with a count.
    await page.goto("/admin/tags");
    await expect(page.getByRole("heading", { name: "Tags", level: 1 })).toBeVisible();
    await page.getByRole("searchbox", { name: "Search tags" }).fill(tag);
    await expect(page.getByRole("cell", { name: tag }).first()).toBeVisible();

    // And it narrows the library.
    const facets = await apiGet<{ tags: { value: string; count: number }[] }>(
      page,
      "/api/library/facets",
    );
    expect(
      facets.tags.find((facet) => facet.value.toLowerCase() === tag),
      "A tag on a title is not in the library's own facets, so nothing can be filtered by it.",
    ).toBeDefined();
  });
});

test.describe("the feed", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("a title is sent to somebody, and they are the ones who see it", async ({
    page,
    browser,
  }) => {
    // PRODUCT GAP. `POST /api/sends`, `GET /api/sends/sent` and
    // `GET /api/sends/received` exist. `feed-route.tsx` calls exactly one send
    // route - `POST /api/sends/{send_id}/seen` - so the screen can dismiss a
    // send nothing in the application is able to create.
    const media = await aTitle(page);
    test.skip(media === null, NO_FIXTURE);
    if (media === null) return;

    // Somebody to send it to. The household is one account until an invitation
    // is redeemed, and a send with nobody on the other end proves nothing.
    const username = `friend-${RUN}`;
    const password = "Gauntlet-Friend-2026!";
    const invite = await apiPost<{ token: string }>(page, "/api/admin/invites", {
      valid_days: 1,
      note: `features ${username}`,
    });
    const guest = await browser.newContext({ baseURL: page.context()._options?.baseURL });
    try {
      const redeemed = await guest.request.post(`/api/invites/${invite.token}/redeem`, {
        data: { username, password },
      });
      expect(redeemed.status(), await redeemed.text()).toBe(201);

      await page.goto(`/library/${media.id}`);
      const send = page.getByRole("button", { name: "Send to someone" });
      await expect(
        send,
        "Nothing in the interface can send a title to another member, so the feed's " +
          '"Sent to you" shelf can never fill. See POST /api/sends.',
      ).toBeVisible();
      await send.click();
      await page.getByLabel("Who").selectOption({ label: username });
      await page.getByLabel("Note").fill("worth a look");
      await page.getByRole("button", { name: "Send", exact: true }).click();
      await expect(page.getByText("Sent.")).toBeVisible();

      // The recipient is the one who sees it, in their own feed.
      // Signed in through the context's own request, whose cookie jar the
      // page shares: this case is about the send arriving, and driving the
      // sign-in form again here would only re-prove `login.spec.ts`.
      const signedIn = await guest.request.post("/api/auth/login", {
        data: { username, password },
      });
      expect(signedIn.status(), await signedIn.text()).toBe(200);
      const friend = await guest.newPage();
      await friend.goto("/feed");
      await expect(friend.getByRole("heading", { name: "Sent to you" })).toBeVisible();
      await expect(friend.getByText(media.title)).toBeVisible();
      await expect(friend.getByText("worth a look")).toBeVisible();

      // And the sender sees it on their own feed, with whether it has landed.
      await page.goto("/feed");
      const outbox = page.getByRole("region", { name: "Sent by you" });
      await expect(
        outbox,
        "The sender has no record of what they handed on. See GET /api/sends/sent.",
      ).toBeVisible();
      // Scoped to this run's recipient: the shelf accumulates, and every send
      // in it is of the same shared fixture.
      const line = outbox.getByRole("listitem").filter({ hasText: username });
      await expect(line).toBeVisible();
      await expect(line.getByText(media.title)).toBeVisible();
      await expect(line.getByText("Not seen yet")).toBeVisible();

      const sentList = await apiGet<{ media_id: string; recipient: string }[]>(
        page,
        "/api/sends/sent",
      );
      expect(sentList.some((item) => item.media_id === media.id)).toBe(true);
    } finally {
      await guest.close();
    }
  });
});

test.describe("the account", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("a member can see and change their own profile", async ({ page }) => {
    // PRODUCT GAP. `GET/PATCH /api/account/profile`, `GET /api/account/storage`
    // and `GET/DELETE /api/account/oidc` have no screen at all. A member cannot
    // change their display name, see what they are using, or unlink an
    // identity they linked - and an operator who removes an OIDC provider
    // leaves those identities behind with no way to reach them.
    await page.goto("/settings");
    const link = page.getByRole("navigation", { name: "Settings areas" }).getByRole("link", {
      name: /^(Account|Profile)$/i,
    });
    await expect(
      link,
      "Settings offers no account section. See /api/account/profile, /storage and /oidc.",
    ).toBeVisible();
    await link.click();
    await expect(page.getByRole("heading", { name: "Account", level: 1 })).toBeVisible();

    // The name other members see is the reader's to change, and the change is
    // read back from the server rather than believed from the form.
    const chosen = `Gauntlet ${RUN}`;
    await page.getByLabel("Display name").fill(chosen);
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Saved.")).toBeVisible();
    expect(
      (await apiGet<{ display_name: string | null }>(page, "/api/account/profile")).display_name,
    ).toBe(chosen);

    // What today has cost, and the identities that can reach this account.
    await expect(page.getByRole("heading", { name: "Today", level: 2 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Linked identities", level: 2 })).toBeVisible();
    await expectNoAccessibilityViolations(page);

    // What the instance has worked out can be thrown away, which is the one
    // control a shared household asks for.
    await expect(
      page.getByRole("button", { name: "Forget what you know about me" }),
      "Nothing clears the interest profile. See DELETE /api/recommendations/profile.",
    ).toBeVisible();

    // Put back, because the rest of the suite reads this account's name.
    await page.getByLabel("Display name").fill("");
    await page.getByRole("button", { name: "Save" }).click();
    await expect
      .poll(
        async () =>
          (await apiGet<{ display_name: string | null }>(page, "/api/account/profile"))
            .display_name,
      )
      .toBeNull();
  });
});

test.describe("members", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("an administrator can see who has an account and change what they may do", async ({
    page,
  }) => {
    // PRODUCT GAP. `GET /api/admin/users` and `PATCH /api/admin/users/{user_id}`
    // are the only way to promote, demote or suspend anybody, and no screen
    // calls either. Invites can be issued; the people who redeem them can
    // never be seen again.
    await page.goto("/settings");
    const link = page.getByRole("link", { name: /^(Members|Users|People)$/i });
    await expect(
      link,
      "No screen lists the instance's members. See GET /api/admin/users.",
    ).toBeVisible();
    await link.click();

    await expect(page.getByRole("heading", { name: "Members", level: 1 })).toBeVisible();
    const me = await apiGet<{ username: string }>(page, "/api/auth/me");
    const mine = page.getByRole("listitem").filter({ hasText: me.username });
    await expect(mine).toBeVisible();
    // An administrator cannot lock themselves out from here, and the screen
    // says so rather than offering a button the server would refuse.
    await expect(mine.getByRole("button")).toHaveCount(0);
    await expectNoAccessibilityViolations(page);
  });
});

test.describe("download clients", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("the client the setup wizard added can be tested, edited and removed again", async ({
    page,
  }) => {
    // PRODUCT GAP. Indexers, metadata providers, peers, OIDC providers, root
    // folders and quality profiles all have a settings screen. The download
    // client - the one piece of configuration that decides whether anything is
    // ever fetched - has none: `POST /api/admin/download-clients` is called
    // once by the setup wizard and then the four routes that manage it are
    // unreachable for the rest of the instance's life.
    const clients = await apiGet<{ id: string; name: string }[]>(
      page,
      "/api/admin/download-clients",
    );
    expect(clients.length, "The stack has no download client configured.").toBeGreaterThan(0);

    await page.goto("/settings");
    await expect(
      page.getByRole("link", { name: /Download client/i }),
      "Settings has no download-client section, so the client can never be re-tested, " +
        "re-pointed or replaced. See /api/admin/download-clients.",
    ).toBeVisible();

    await page.getByRole("link", { name: /Download client/i }).click();
    await expect(page.getByRole("heading", { name: "Download clients", level: 1 })).toBeVisible();

    const client = clients[0] as { id: string; name: string };
    const row = page.getByRole("listitem").filter({ hasText: client.name });
    await expect(row).toBeVisible();

    // The test button asks the real client, and what it learns is written down.
    await row.getByRole("button", { name: `Test ${client.name}` }).click();
    await expect(row.getByText("Answering")).toBeVisible();
    // Read once, deliberately. A request's transaction commits before its
    // answer reaches the caller (`database_session` in
    // `apps/api/pornarr_api/auth.py`), so the row this test just caused is
    // there by the time the screen has told us about it. Waiting here instead
    // would hide the day that stops being true.
    expect(
      (
        await apiGet<{ id: string; last_tested_at: string | null }[]>(
          page,
          "/api/admin/download-clients",
        )
      ).find((item) => item.id === client.id)?.last_tested_at,
      "The screen said the client answered and the server recorded no test.",
    ).not.toBeNull();

    // Editing and removing are driven on a client of this case's own. The
    // shared one is what every acquisition test in the suite grabs through,
    // and a case that renames it - or writes its credential - takes the rest
    // of the run down with it.
    const name = `Throwaway ${RUN}`;
    const addForm = page.getByRole("form", { name: "Add a client" });
    await addForm.getByLabel("Name", { exact: true }).fill(name);
    await addForm.getByLabel("Host", { exact: true }).fill("qbittorrent");
    await addForm.getByLabel("Username").fill("admin");
    await addForm.getByLabel("Password").fill("adminadmin");
    await addForm.getByRole("button", { name: "Save" }).click();

    const own = page.getByRole("listitem").filter({ hasText: name });
    await expect(own).toBeVisible();
    const created = (
      await apiGet<{ id: string; name: string }[]>(page, "/api/admin/download-clients")
    ).find((item) => item.name === name);
    expect(created, "The screen shows a client the server does not have.").toBeDefined();

    // Editing keeps the row rather than replacing it, which is the whole
    // reason this is not "delete and add again": a client's health and its
    // history hang off the id. The secret box is left empty, which has to mean
    // "keep the one you have" - the value never comes back, so a form has
    // nothing to resend.
    await own.getByRole("button", { name: `Edit ${name}` }).click();
    await own.locator("form").getByLabel("Port", { exact: true }).fill("8081");
    await own.getByRole("button", { name: "Save" }).click();
    await expect
      .poll(
        async () =>
          (await apiGet<{ id: string; port: number }[]>(page, "/api/admin/download-clients")).find(
            (item) => item.id === (created as { id: string }).id,
          )?.port,
      )
      .toBe(8081);

    await own.getByRole("button", { name: `Remove ${name}` }).click();
    await own.getByRole("button", { name: "Remove the client" }).click();
    await expect(page.getByRole("listitem").filter({ hasText: name })).toHaveCount(0);
    await expectNoAccessibilityViolations(page);
  });
});

test.describe("notifications", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("what happened while a member was away is readable, and can be turned off", async ({
    page,
  }) => {
    // PRODUCT GAP. Five routes - the list, one read, read-all, the preferences
    // and one preference - and no screen calls any of them. The server records
    // notifications nobody can read.
    const notifications = await apiGet<{ id: string; read_at: string | null }[]>(
      page,
      "/api/notifications",
    );

    await page.goto("/library");
    const bell = page.getByRole("button", { name: /^Notifications/ });
    await expect(
      bell,
      "Nothing in the shell opens the notifications the server keeps. See /api/notifications.",
    ).toBeVisible();
    await bell.click();

    const unread = notifications.filter((item) => item.read_at === null);
    if (unread.length > 0) {
      await page.getByRole("button", { name: "Mark all read" }).click();
      await expect
        .poll(async () =>
          (await apiGet<{ read_at: string | null }[]>(page, "/api/notifications")).every(
            (item) => item.read_at !== null,
          ),
        )
        .toBe(true);
    }

    // And what gets recorded is the reader's own choice, on their account.
    await page.goto("/settings/account");
    await expect(
      page.getByRole("heading", { name: "What you are told about", level: 2 }),
    ).toBeVisible();
    const preference = page.getByRole("checkbox").first();
    const wasOn = await preference.isChecked();
    await preference.click();
    await expect
      .poll(
        async () =>
          (await apiGet<{ enabled: boolean }[]>(page, "/api/notifications/preferences"))[0]
            ?.enabled,
      )
      .toBe(!wasOn);
    // Put back: the rest of the suite runs against this account.
    await preference.click();
    await expect
      .poll(
        async () =>
          (await apiGet<{ enabled: boolean }[]>(page, "/api/notifications/preferences"))[0]
            ?.enabled,
      )
      .toBe(wasOn);
    await expectNoAccessibilityViolations(page);
  });
});

test.describe("shorts", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("the grid opens a clip and the clip can be saved", async ({ page }) => {
    // Made rather than waited for. Shorts appear nightly from what happened to
    // be watched, and on a fresh instance - which is where a run that means
    // anything starts - there are none. A mark and one generate is the same
    // path the case below drives, and it is the only way this one has a clip
    // to open.
    let shorts = await apiGet<{ id: string; media_id: string }[]>(page, "/api/shorts?limit=5");
    if (shorts.length === 0) {
      const media = await aTitle(page);
      test.skip(media === null, NO_FIXTURE);
      if (media === null) return;
      await apiPost(page, `/api/media/${media.id}/markers`, { start_seconds: 2, end_seconds: 9 });
      await apiPost(page, `/api/shorts/media/${media.id}/generate`, { seconds: 15, maximum: 1 });
      await expect
        .poll(
          async () =>
            (await apiGet<{ id: string; media_id: string }[]>(page, "/api/shorts?limit=5")).length,
          { timeout: 30_000, intervals: [1_000] },
        )
        .toBeGreaterThan(0);
      shorts = await apiGet<{ id: string; media_id: string }[]>(page, "/api/shorts?limit=5");
    }

    await page.goto("/shorts/browse");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expectNoAccessibilityViolations(page);

    // The tile's own link, named after the clip: the "back to feed" link sits
    // above the grid and would be the first link on the page.
    const clip = shorts[0] as { id: string; media_id: string };
    await page.locator("main").getByRole("listitem").first().getByRole("link").click();
    await expect(page).toHaveURL(/\/shorts\/[0-9a-f-]+$/);

    // Saving the clip saves the title it was cut from - a clip is a view of a
    // title, not a thing of its own to keep.
    const before = await apiGet<{ media_id: string }[]>(page, "/api/watchlist");
    const saved = before.some((item) => item.media_id === clip.media_id);
    const toggle = page.getByRole("button", { name: /watchlist|save/i }).first();
    await expect(toggle).toBeVisible();
    await toggle.click();
    await expect
      .poll(async () =>
        (await apiGet<{ media_id: string }[]>(page, "/api/watchlist")).some(
          (item) => item.media_id === clip.media_id,
        ),
      )
      .toBe(!saved);
  });

  test("a clip can be made from a title on demand", async ({ page }) => {
    // PRODUCT GAP. `POST /api/shorts/media/{media_id}/generate` and the two
    // administrator routes beside it have no caller. Shorts only ever appear
    // by themselves, overnight, from what happened to be watched.
    const media = await aTitle(page);
    test.skip(media === null, NO_FIXTURE);
    if (media === null) return;

    // A scene to cut from. The endpoint generates clips from this title's
    // marks, so a title nothing has analysed correctly offers nothing - the
    // mark is the setup, not the claim. `POST /api/media/{id}/markers` is the
    // same route the scene detector writes through.
    await apiPost(page, `/api/media/${media.id}/markers`, {
      start_seconds: 2,
      end_seconds: 9,
    });

    await page.goto(`/library/${media.id}`);
    const make = page.getByRole("button", { name: "Make clips" });
    await expect(
      make,
      "Nothing asks for a clip of this title. See POST /api/shorts/media/{media_id}/generate. " +
        "The control lives beside the scene marks, so a title nothing has analysed has none.",
    ).toBeVisible();

    // Whatever this title already has goes first: generating is idempotent per
    // mark, so a title that already carries a clip would answer the click with
    // the clip it already had and prove nothing.
    const existing = await apiGet<{ id: string; media_id: string }[]>(
      page,
      "/api/shorts?limit=100",
    );
    for (const short of existing.filter((item) => item.media_id === media.id)) {
      const removed = await apiDelete(page, `/api/admin/shorts/${short.id}`);
      expect(removed.status(), await removed.text()).toBe(204);
    }
    await expect
      .poll(async () =>
        (await apiGet<{ media_id: string }[]>(page, "/api/shorts?limit=100")).some(
          (short) => short.media_id === media.id,
        ),
      )
      .toBe(false);

    await make.click();
    await expect(page.getByText("Clips made.")).toBeVisible();
    await expect
      .poll(async () =>
        (await apiGet<{ media_id: string }[]>(page, "/api/shorts?limit=100")).some(
          (short) => short.media_id === media.id,
        ),
      )
      .toBe(true);
  });
});

test.describe("monitors", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("a monitor is made, searches its backlog on request, and is removed again", async ({
    page,
  }) => {
    // PRODUCT GAP. ADR 0030 added monitors with RSS sync, and the screen shows
    // them, enables them, disables them and asks each for a backlog search.
    // `POST /api/monitors` has no caller anywhere in `apps/web/src`, so the
    // list this screen manages is one nothing in the application can add to.
    const query = `monitor ${RUN}`;
    await page.goto("/monitors");
    await expect(page.getByRole("heading", { name: "Monitors", level: 1 })).toBeVisible();

    const add = page.getByRole("button", { name: /Add monitor|New monitor|Create/i });
    await expect(
      add,
      "Nothing on the monitors screen creates a monitor. See POST /api/monitors.",
    ).toBeVisible();
    await add.click();
    await page.getByRole("textbox", { name: /Query|Search term/i }).fill(query);
    await page
      .getByRole("button", { name: /Save|Create|Add/i })
      .last()
      .click();

    const row = page.getByRole("listitem").filter({ hasText: query });
    await expect(row).toBeVisible();

    const monitors = await apiGet<{ id: string; query: string | null }[]>(page, "/api/monitors");
    const monitor = monitors.find((item) => item.query === query);
    expect(monitor, "The monitor the screen shows is not in the API's own list.").toBeDefined();
    await row.getByRole("button", { name: "Search now" }).click();

    await apiDelete(page, `/api/monitors/${(monitor as { id: string }).id}`);
    await page.reload();
    await expect(page.getByRole("listitem").filter({ hasText: query })).toHaveCount(0);
    await expectNoAccessibilityViolations(page);
  });
});

test.describe("the administration dashboard", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("shows who is transcoding right now, and can end it", async ({ page }) => {
    // PRODUCT GAP, closed. `GET /api/admin/transcode/sessions`, its DELETE,
    // `/failures` and `/performance` had no reader at all: "who is holding the
    // slots", "why did that stream stop" and "is this machine keeping up" were
    // answerable only from the worker's log.
    // A title the browser cannot open, made here rather than borrowed from
    // `transcode.spec.ts`: that file runs after this one, so on a fresh
    // instance there is nothing to start a session on and the case would only
    // ever skip.
    const media = await aTranscodingTitle(page);
    test.skip(
      media === null,
      "The suite cannot write a fixture into a configured root folder: either ffmpeg is " +
        "missing or the stack's data volume is not reachable from here.",
    );
    if (media === null) return;

    const started = await apiPost<{ session_id: string }>(
      page,
      `/api/transcode/media/${media.id}/sessions`,
    );
    try {
      await page.goto("/admin");
      // Scoped to the sessions panel. The dashboard also carries a library
      // rail, and the same title is a tile there - an unscoped listitem
      // matches both.
      const row = page
        .getByRole("region", { name: "Transcoding now" })
        .getByRole("listitem")
        .filter({ hasText: media.title });
      await expect(
        row,
        "A running transcode is not on any administration screen. " +
          "See GET /api/admin/transcode/sessions.",
      ).toBeVisible();

      await row.getByRole("button", { name: `End the transcode of ${media.title}` }).click();
      await expect
        .poll(async () =>
          (await apiGet<{ id: string }[]>(page, "/api/admin/transcode/sessions")).some(
            (session) => session.id === started.session_id,
          ),
        )
        .toBe(false);
      await expectNoAccessibilityViolations(page);
    } finally {
      await apiDelete(page, `/api/transcode/sessions/${started.session_id}`);
    }
  });
});
