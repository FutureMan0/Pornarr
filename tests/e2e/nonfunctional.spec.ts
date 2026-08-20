/**
 * Piece 14 — accessibility, internationalisation, the error contract, the shell.
 *
 * ONE RULE RUNS THROUGH ALL OF IT: an axe pass is the floor, not the proof.
 * `expectNoAccessibilityViolations` already runs on most screens from
 * `navigation.spec.ts`, and this campaign has twice watched a real regression
 * survive it — a `<section tabIndex>` fix whose reversal left the only axe run
 * green, and `<html lang="en">` on a page rendering German, which axe's
 * `html-has-lang` cannot see because the attribute is present and well formed.
 * So every test below names the affordance it is about and asserts that, by
 * name, with the documented line in the message. axe runs beside them, never
 * instead of them.
 *
 * The two locale files and `helpers.ts` are shared with other builders, so
 * nothing here edits either.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import {
  TESTING_RELEASE_TITLE,
  apiGet,
  apiPostRaw,
  canDriveStackRedis,
  expectNoAccessibilityViolations,
  indexerSearch,
  loginAsAdmin,
  seedRequest,
  stackRedis,
  testingDownloadClient,
  testingIndexer,
} from "./helpers";

/**
 * The locale files, read rather than imported.
 *
 * They are the source of truth for what a screen must say, so every assertion
 * below quotes them instead of a phrase typed into this file — a sentence edited
 * in `en.json` and forgotten on a screen has to fail here. Read off disk because
 * the e2e directory is outside every tsconfig project and a JSON import would
 * depend on the transpiler's import-attribute support.
 */
type Locale = {
  readonly nav: Record<string, string>;
  readonly errors: Record<string, string>;
  readonly errorSteps: Record<string, string>;
};

function locale(name: string): Locale {
  const here = dirname(fileURLToPath(import.meta.url));
  return JSON.parse(
    readFileSync(join(here, "..", "..", "apps", "web", "src", "i18n", `${name}.json`), "utf8"),
  ) as Locale;
}

const en = locale("en");
const de = locale("de");

/**
 * Every screen the signed-in administrator can reach, with the name its own
 * `h1` must carry. Table-driven including the settings sub-screens, because a
 * heading rule that only holds on the screens somebody remembered to list is
 * not a rule.
 */
const SCREENS: readonly (readonly [path: string, heading: string])[] = [
  ["/admin", "Dashboard"],
  ["/admin/quarantine", "Quarantine review"],
  ["/admin/moderation", "Ratings & comments"],
  ["/admin/scan", "Scan & import"],
  ["/admin/tags", "Tags"],
  ["/admin/invites", "Invitations"],
  ["/library", "Library"],
  ["/search", "Search"],
  ["/requests", "Requests"],
  ["/downloads", "Downloads"],
  ["/monitors", "Monitors"],
  ["/recommendations", "Recommendations"],
  ["/feed", "Sent to you"],
  ["/continue", "Continue watching"],
  ["/shorts", "Shorts"],
  ["/collections", "Collections"],
  ["/watchlist", "Watchlist"],
  ["/settings", "Settings"],
  ["/settings/root-folders", "Root folders"],
  ["/settings/quality", "Quality profiles"],
  ["/settings/indexers", "Indexers"],
  ["/settings/metadata", "Metadata providers"],
  ["/settings/peers", "Shared libraries"],
];

/** `--primary` in the rose accent, which is what `tokens.css` resolves it to. */
const PRIMARY_RING = "rgb(217, 98, 159)";

/** Everything the browser will hand focus to, in document order. */
const FOCUSABLE =
  "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1']), summary, video[controls]";

/**
 * Wait for the screen's own heading, so nothing below reads a skeleton.
 *
 * The allowance is longer than Playwright's default because the stack is shared:
 * a route's chunk and its first query land behind whatever three other suites
 * are asking of the same API, and five seconds is a measurement of the neighbours
 * rather than of the screen. Nothing else here is loosened — this waits for the
 * screen to exist, and every assertion after it is exact.
 */
async function openScreen(page: Page, path: string, heading: string): Promise<void> {
  await page.goto(path);
  await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible({
    timeout: 20_000,
  });
}

/**
 * A queue with at least one job in it, whoever put it there.
 *
 * Three cases in this file read a live queue row. They used to skip when the
 * queue was empty, which on a run in alphabetical order it always is: this file
 * comes before `queue.spec.ts`, so nothing has grabbed anything yet. A skip is
 * not a proof, so the queue is filled here instead - by the product's own path,
 * a search against the compose fixture indexer followed by a grab.
 *
 * Returns `null` only when `docker-compose.testing.yml` is not running, which
 * is a capability the environment genuinely lacks.
 */
async function queueWithAJob(page: Page): Promise<QueueRow[] | null> {
  const existing = await apiGet<{ items: QueueRow[] }>(page, "/api/queue?limit=20");
  if (existing.items.length > 0) return existing.items;

  const indexer = await testingIndexer(page);
  const client = await testingDownloadClient(page);
  if (indexer === null || client === null) return null;

  const search = await indexerSearch(page, TESTING_RELEASE_TITLE);
  const release = search.items.find((item) => item.indexer_id === indexer.id);
  expect(release, "The fixture indexer answered the fan-out with nothing.").toBeDefined();
  const request = await seedRequest(page, TESTING_RELEASE_TITLE);
  // 201 the first time the client is handed this release, 200 when it already
  // holds it: the compose fixture is shared and grabbed once.
  const grabbed = await apiPostRaw(page, `/api/requests/${request.id}/grab`, {
    release_id: (release as { id: string }).id,
  });
  expect([200, 201], await grabbed.text()).toContain(grabbed.status());
  await expect
    .poll(
      async () => (await apiGet<{ items: QueueRow[] }>(page, "/api/queue?limit=20")).items.length,
      {
        timeout: 20_000,
        intervals: [500],
      },
    )
    .toBeGreaterThan(0);
  return (await apiGet<{ items: QueueRow[] }>(page, "/api/queue?limit=20")).items;
}

/** Only the fields the three queue cases in this file read. */
type QueueRow = {
  readonly id: string;
  readonly status: string;
  readonly title: string | null;
  readonly release_guid: string;
};

const NO_TESTING_STACK =
  "docker-compose.testing.yml is not running, so nothing can be put in the queue to read.";

test.describe("every function is reachable by keyboard", () => {
  // The first case walks all twenty-three screens in one test, which is more
  // navigation than the default per-test budget covers on a shared stack.
  test.describe.configure({ timeout: 180_000 });

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  /**
   * DESIGN.md L297: "Focus order follows visual order."
   *
   * Asserted as the property that makes it true rather than by reading pixels:
   * the browser's sequential navigation order is document order unless a
   * positive `tabindex` overrides it, and a positive `tabindex` is the only
   * mechanism in HTML that can put focus somewhere the eye is not. So the claim
   * is checked two ways at once — nothing anywhere carries a positive tabindex,
   * and pressing Tab really does walk the document's own order.
   */
  test("Tab walks the screen in document order, and nothing jumps the queue", async ({ page }) => {
    for (const [path, heading] of SCREENS) {
      await openScreen(page, path, heading);

      const positive = await page.evaluate(() =>
        [...document.querySelectorAll<HTMLElement>("[tabindex]")]
          .filter((element) => Number(element.getAttribute("tabindex")) > 0)
          .map((element) => `${element.tagName.toLowerCase()}[tabindex=${element.tabIndex}]`),
      );
      expect(
        positive,
        `${path} uses a positive tabindex, which is the one way to make focus order disagree with visual order (DESIGN.md L297).`,
      ).toEqual([]);
    }

    // And the walk itself, once, on the screen with the most controls in it.
    await openScreen(page, "/settings", "Settings");
    const expected = await page.evaluate((selector) => {
      const visible = [...document.querySelectorAll<HTMLElement>(selector)].filter((element) => {
        const box = element.getBoundingClientRect();
        return box.width > 0 && box.height > 0;
      });
      visible.forEach((element, index) => element.setAttribute("data-tab-order", String(index)));
      return visible.length;
    }, FOCUSABLE);
    expect(expected, "The settings screen offered nothing to tab through.").toBeGreaterThan(10);

    await page.evaluate(() => document.body.focus());
    const reached: number[] = [];
    for (let step = 0; step < expected; step += 1) {
      await page.keyboard.press("Tab");
      const order = await page.evaluate(() =>
        document.activeElement?.getAttribute("data-tab-order"),
      );
      if (order === null || order === undefined) break;
      reached.push(Number(order));
    }

    // The first ten steps are enough to prove the correspondence and short
    // enough not to depend on how many library rows happen to be loaded.
    expect(reached.slice(0, 10), "Tab did not walk the settings screen in document order.").toEqual(
      [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    );
  });

  /**
   * DESIGN.md L295-297: "a 2px ring in `--primary` at 3:1 against its
   * surroundings, never removed, never replaced by a colour change alone."
   *
   * Both halves of the codebase, because they used to disagree. Every component
   * in `packages/ui` drew the ring; controls authored in `apps/web` fell through
   * to the user agent's own, which Chromium paints as a white and near-black
   * two-tone outline. Visible — so axe passed it and so did WCAG 2.4.11 — and a
   * second focus indicator on half the product, which is what the design says in
   * as many words it does not have.
   *
   * Computed style AND painted pixels. A computed `outline-color` is what the
   * stylesheet asked for; the screenshot is what the reader gets, and the two
   * came apart here: `outline: 1px auto` computes its colour as `currentColor`
   * and paints something else entirely.
   */
  test("the focus ring is 2px of --primary, in both halves of the codebase", async ({ page }) => {
    await openScreen(page, "/settings", "Settings");

    const controls = [
      // packages/ui: a Menu trigger, which has always drawn the ring.
      page.getByRole("button", { name: "Language" }),
      // apps/web: a sidebar link, an app-authored control with no ring of its own.
      page
        .getByRole("navigation", { name: "Primary" })
        .getByRole("link", { name: "Settings" }),
    ];

    for (const control of controls) {
      const box = await control.boundingBox();
      expect(box, "The control under test is not laid out.").not.toBeNull();
      const clip = {
        x: (box as { x: number }).x - 6,
        y: (box as { y: number }).y - 6,
        width: (box as { width: number }).width + 12,
        height: (box as { height: number }).height + 12,
      };
      const before = await page.screenshot({ clip });

      await control.focus();
      const name = await control.textContent();
      const outline = async (): Promise<{ width: string; style: string; color: string }> =>
        control.evaluate((element) => {
          const computed = getComputedStyle(element);
          return {
            width: computed.outlineWidth,
            style: computed.outlineStyle,
            color: computed.outlineColor,
          };
        });

      // Polled rather than read once: Tailwind's `transition-colors` includes
      // `outline-color`, so on a control carrying it the ring interpolates from
      // whatever it was to `--primary` over `--duration-fast`. What the claim is
      // about is where it lands.
      await expect
        .poll(async () => (await outline()).color, {
          timeout: 2_000,
          message: `${name}'s focus ring never settles on --primary (DESIGN.md L295-297).`,
        })
        .toBe(PRIMARY_RING);
      const style = await outline();

      expect(style.style, `${name} draws no focus outline (DESIGN.md L295-297).`).toBe("solid");
      expect(
        Number.parseFloat(style.width),
        `${name}'s focus ring is not 2px (DESIGN.md L295-297).`,
      ).toBeGreaterThanOrEqual(2);

      // And what was actually painted. The ring's own colour has to be among the
      // pixels that changed, or the computed value describes a rule the browser
      // did not honour.
      const after = await page.screenshot({ clip });
      const painted = await page.evaluate(
        async ([a, b]) => {
          const load = async (data: string): Promise<ImageData> => {
            const image = new Image();
            image.src = `data:image/png;base64,${data}`;
            await image.decode();
            const canvas = document.createElement("canvas");
            canvas.width = image.width;
            canvas.height = image.height;
            const context = canvas.getContext("2d") as CanvasRenderingContext2D;
            context.drawImage(image, 0, 0);
            return context.getImageData(0, 0, image.width, image.height);
          };
          const first = await load(a as string);
          const second = await load(b as string);
          const counts = new Map<string, number>();
          for (let index = 0; index < first.data.length; index += 4) {
            const same =
              first.data[index] === second.data[index] &&
              first.data[index + 1] === second.data[index + 1] &&
              first.data[index + 2] === second.data[index + 2];
            if (same) continue;
            const key = `rgb(${second.data[index]}, ${second.data[index + 1]}, ${second.data[index + 2]})`;
            counts.set(key, (counts.get(key) ?? 0) + 1);
          }
          return [...counts.entries()].sort((one, two) => two[1] - one[1]).slice(0, 4);
        },
        [before.toString("base64"), after.toString("base64")],
      );

      expect(
        painted.map(([colour]) => colour),
        `${name} paints ${JSON.stringify(painted)} on focus, not the design's --primary ring (DESIGN.md L295-297).`,
      ).toContain(PRIMARY_RING);
    }
  });

  /**
   * DESIGN.md L297-298: "Focus is trapped in dialogs and returned to the trigger
   * on close."
   *
   * The navigation sheet, which is a phone-width surface and therefore the one
   * dialog reachable without seeding anything. It is a native `<dialog>` opened
   * with `showModal()`, so the trap is the platform's; what this proves is that
   * the product actually uses that element rather than a hand-rolled overlay,
   * and that the explicit focus restore in `sheet.tsx` does its job.
   */
  test("a dialog holds focus and hands it back to the control that opened it", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openScreen(page, "/library", "Library");

    const trigger = page.getByRole("button", { name: "More" });
    await trigger.focus();
    await expect(trigger).toBeFocused();
    await page.keyboard.press("Enter");

    const sheet = page.getByRole("dialog");
    await expect(sheet).toBeVisible();
    await expect(sheet).toHaveAccessibleName("Everywhere else");

    /**
     * Focus cannot reach a control outside the sheet. Twenty tabs is more than
     * the sheet holds, so this walks past the end and around again.
     *
     * `<body>` is allowed and is not a loophole: it is where Chromium parks
     * focus for one step as a modal dialog's tab cycle wraps, and it is not a
     * control — nothing behind the sheet can be operated from it. The assertion
     * is about the background's controls, and it names any it reaches.
     */
    const trail: string[] = [];
    for (let step = 0; step < 20; step += 1) {
      await page.keyboard.press("Tab");
      trail.push(
        await page.evaluate(() => {
          const active = document.activeElement;
          if (active === null) return "none";
          const dialog = document.querySelector("dialog[open]");
          if (dialog?.contains(active) === true) return "inside";
          if (active === document.body || active === document.documentElement) return "wrap";
          return `escaped to ${active.tagName.toLowerCase()} "${(active.textContent ?? "").trim().slice(0, 30)}"`;
        }),
      );
    }
    expect(
      trail.filter((entry) => entry.startsWith("escaped")),
      "Focus reached a control behind the open sheet (DESIGN.md L297-298).",
    ).toEqual([]);
    expect(
      trail.filter((entry) => entry === "inside").length,
      "The sheet never held focus at all.",
    ).toBeGreaterThan(10);

    await expectNoAccessibilityViolations(page);

    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
    // Back where it came from, not at the top of the document.
    await expect(trigger).toBeFocused();
  });
});

test.describe("meaning never rides on colour alone", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  /**
   * DESIGN.md L77-78: "Never signal state by colour alone. Every status carries
   * an icon or a text label beside its colour."
   *
   * Asserted against the API's own value rather than against a list of words
   * this test made up: whatever `/api/queue` says a job's status is, that word
   * has to be legible in the row. A badge that renders a coloured pill and
   * nothing else passes an axe run and fails this.
   */
  test("a queue row says its state in words, not only in colour", async ({ page }) => {
    const items = await queueWithAJob(page);
    test.skip(items === null, NO_TESTING_STACK);
    if (items === null) return;

    // A job the screen is actually showing. The "active" tab is everything
    // still in flight, so it drops `completed` and `removed` rows
    // (`queue-route.tsx:45`), and a shared queue accumulates plenty of both -
    // asking for the first job the API lists was asking for a row that is not
    // on the page.
    const done = new Set(["completed", "removed"]);
    const inFlight = items.find((item) => !done.has(item.status));
    const job = (inFlight ?? items.find((item) => item.status === "completed")) as
      | QueueRow
      | undefined;
    test.skip(
      job === undefined,
      "Every job in the shared queue was removed, and no tab shows a removed job.",
    );
    if (job === undefined) return;

    await openScreen(page, "/downloads", "Downloads");
    if (inFlight === undefined) await page.getByRole("tab", { name: "Completed" }).click();
    const row = page
      .locator("main")
      .getByRole("row")
      .filter({ hasText: job.title ?? job.release_guid })
      .first();
    await expect(row).toBeVisible();

    // The word itself, and it is not carried by a colour: the element holding it
    // has text content of its own.
    const badge = row.getByText(job.status, { exact: true });
    await expect(
      badge,
      `The queue row shows no readable status for ${job.id} (DESIGN.md L77-78).`,
    ).toBeVisible();
    await expect(badge).toHaveText(job.status);
  });

  /**
   * DESIGN.md L296-297 again, from the other side: the current navigation entry
   * is marked by a bar in the accent (`.pa-nav`). A bar is a colour, so the
   * machine-readable half has to be there too, and it is what a screen reader
   * uses. `aria-current="page"` on exactly one entry.
   */
  test("the current navigation entry is marked in the accessibility tree, not only by a bar", async ({
    page,
  }) => {
    for (const [path, heading] of [
      ["/library", "Library"],
      ["/downloads", "Downloads"],
      ["/settings", "Settings"],
    ] as const) {
      await openScreen(page, path, heading);
      const current = page
        .getByRole("navigation", { name: "Primary" })
        .locator('[aria-current="page"]');
      await expect(
        current,
        `${path} marks ${await current.count()} navigation entries as current; exactly one is right (DESIGN.md L249-252).`,
      ).toHaveCount(1);
    }
  });
});

/**
 * One `h1` per screen, and headings that descend without a gap.
 *
 * WCAG 2.2 AA does not require a single `h1`, but PRODUCT.md L96 signs up to
 * "verified rather than assumed" and DESIGN.md's shell renders the screen's name
 * as its only `h1` from the top bar (`shell/page-title.tsx`). A screen that
 * forgets `usePageTitle` renders no `h1` at all — the top bar has nothing to
 * publish — which is invisible to axe and to the eye alike, because the design
 * puts the name in the bar rather than in the page.
 */
type Heading = { readonly level: number; readonly text: string };

/**
 * The screen's headings, once the screen has finished producing them.
 *
 * Not optional plumbing: the shell publishes the `h1` from the top bar the
 * moment a route mounts, while every `h2` under it waits on a query. Reading
 * once behind `openScreen` gives `[h1]` on most screens, and a heading-order
 * check over a list of one is a check that cannot fail — the first draft of this
 * test passed a planted `h1` → `h4` skip on `/settings/quality` for exactly that
 * reason.
 *
 * So: poll until two consecutive reads agree, which is what "the screen has
 * stopped rendering headings" looks like from out here.
 */
async function settledHeadings(page: Page): Promise<Heading[]> {
  const read = async (): Promise<Heading[]> =>
    page.evaluate(() =>
      [...document.querySelectorAll("h1, h2, h3, h4, h5, h6")].map((element) => ({
        level: Number(element.tagName.slice(1)),
        text: (element.textContent ?? "").trim(),
      })),
    );
  let previous = JSON.stringify(await read());
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await page.waitForTimeout(400);
    const current = JSON.stringify(await read());
    if (current === previous) return JSON.parse(current) as Heading[];
    previous = current;
  }
  throw new Error("The screen never stopped adding headings.");
}

test.describe("every screen has one heading that names it, and an order beneath it", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  for (const [path, heading] of SCREENS) {
    test(`${path} declares itself once and descends`, async ({ page }) => {
      await openScreen(page, path, heading);

      const levels = await settledHeadings(page);

      const firsts = levels.filter((entry) => entry.level === 1);
      expect(
        firsts.map((entry) => entry.text),
        `${path} has ${firsts.length} level-one headings; the shell publishes exactly one (apps/web/src/shell/page-title.tsx).`,
      ).toEqual([heading]);

      // No level is skipped on the way down: h1 → h3 is a hole a screen reader's
      // heading list reads as a missing section.
      //
      // Compared against the heading immediately before it, not against the
      // deepest seen so far. "Deepest so far" lets h1 → h2 → h3 → h2 → h4
      // through, and h2 → h4 is the same skip wherever it happens.
      let previous = 0;
      for (const entry of levels) {
        expect(
          entry.level,
          `${path} jumps from h${previous} to h${entry.level} at "${entry.text}".`,
        ).toBeLessThanOrEqual(previous + 1);
        previous = entry.level;
      }
    });
  }
});

test.describe("the interface speaks the reader's language", () => {
  // The last case walks all twenty-three screens in German.
  test.describe.configure({ timeout: 180_000 });

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  /** Switch through the control a reader would use, not through storage. */
  async function switchToGerman(page: Page): Promise<void> {
    await page.getByRole("button", { name: "Language" }).click();
    await page.getByRole("menuitem", { name: "Deutsch" }).click();
    await expect(page.getByRole("navigation", { name: "Hauptnavigation" })).toBeVisible();
  }

  /**
   * ADR 0026: "Interface strings go through i18n from v0.1 with English as
   * source and German as the first translation."
   *
   * Asserted against the locale files themselves rather than against a phrase
   * typed into this test: every navigation entry must read exactly what
   * `de.json` says, and none of them may still read what `en.json` says. A
   * switch that moved half the shell would pass a "some German appeared" check
   * and fails this one by name.
   */
  test("switching language moves every string on the screen, not most of them", async ({
    page,
  }) => {
    const entries = [
      "library",
      "requests",
      "settings",
      "downloads",
      "collections",
      "watchlist",
      "monitors",
      "admin",
      "scan",
      "invites",
    ] as const;

    /**
     * The names the navigation is offering, with the waiting-item count stripped
     * off the end. A destination with something waiting carries the number in
     * its accessible name (`shell/nav-counts.ts`), and whether it has arrived
     * yet depends on a query rather than on the language.
     */
    const labels = async (name: string): Promise<string[]> => {
      const links = page.getByRole("navigation", { name }).getByRole("link");
      await expect(links.first()).toBeVisible();
      return (await links.allInnerTexts()).map((text) =>
        text
          .split("\n")[0]
          ?.replace(/\s*\d+$/, "")
          .trim(),
      ) as string[];
    };

    await openScreen(page, "/library", "Library");
    const english = await labels("Primary");
    for (const key of entries) {
      expect(english, `The English navigation is missing "${en.nav[key]}".`).toContain(en.nav[key]);
    }

    await switchToGerman(page);

    const german = await labels("Hauptnavigation");
    for (const key of entries) {
      expect(
        german,
        `The navigation still does not read "${de.nav[key]}" after switching to German (ADR 0026).`,
      ).toContain(de.nav[key]);
      if (de.nav[key] !== en.nav[key]) {
        expect(german, `"${en.nav[key]}" survived the switch to German (ADR 0026).`).not.toContain(
          en.nav[key],
        );
      }
    }

    // The screen under the shell moved too, not only the chrome around it.
    await expect(page.getByRole("heading", { name: de.nav.library, level: 1 })).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });

  /**
   * WCAG 2.2 SC 3.1.1, Language of Page — which PRODUCT.md L96 signs up to in
   * full, and which axe cannot check: `html-has-lang` and `valid-lang` assert
   * the attribute exists and is well formed, never that it agrees with the words
   * on the screen. `index.html` ships `lang="en"` and nothing moved it, so a
   * German page was announced to a screen reader as English and read with
   * English pronunciation rules throughout.
   */
  test("the document declares the language it is actually rendering", async ({ page }) => {
    await openScreen(page, "/library", "Library");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");

    await switchToGerman(page);

    await expect(
      page.locator("html"),
      "The page renders German and still declares itself English (WCAG 2.2 SC 3.1.1; PRODUCT.md L96).",
    ).toHaveAttribute("lang", "de");

    // And it survives a navigation, because the attribute belongs to the
    // document rather than to the screen that happened to be mounted.
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: de.nav.settings, level: 1 })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "de");
  });

  /**
   * The failure i18n has that nothing else does: a key that reaches the screen
   * instead of a sentence. `t("nav.libary")` is a compile error here thanks to
   * the module augmentation in `i18n/index.ts`, but a key assembled at runtime —
   * `t(\`search.confidence.${level}\`)`, `t(STATUS_KEYS[status])` — is not, and
   * a missing one renders the key itself.
   */
  test("no screen renders a translation key where a sentence belongs", async ({ page }) => {
    await openScreen(page, "/library", "Library");
    await switchToGerman(page);

    /**
     * A whole text node that is a dotted key, not a substring of one.
     *
     * That is how the failure actually looks: i18next replaces the entire
     * string with the key when it cannot resolve it, so the key is the node's
     * whole content. Matching a substring instead would flag "media.mp4" inside
     * a sentence, and the first segment being one of `en.json`'s own namespaces
     * is what separates a key from a filename that happens to have a dot in it.
     */
    const namespaces = Object.keys(en as unknown as Record<string, unknown>);
    for (const [path] of SCREENS) {
      await page.goto(path);
      await expect(page.locator("main")).toBeVisible();
      await page.waitForTimeout(400);
      const keys = await page.evaluate((roots) => {
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        const found: string[] = [];
        for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
          const text = (node.textContent ?? "").trim();
          if (!/^[a-z][a-zA-Z0-9]*(?:\.[a-zA-Z][a-zA-Z0-9_]*){1,4}$/.test(text)) continue;
          if ((roots as string[]).includes(text.split(".")[0] as string)) found.push(text);
        }
        return [...new Set(found)];
      }, namespaces);
      expect(
        keys,
        `${path} rendered a translation key instead of its sentence (ADR 0026).`,
      ).toEqual([]);
    }
  });
});

test.describe("errors state a cause and a next step", () => {
  /**
   * The sign-in screen refuses on purpose here, which costs a slot in the login
   * rate limiter: six attempts per fifteen minutes, counted per account AND per
   * client address (`apps/api/pornarr_api/auth.py:34-35,141-147`). The address
   * bucket is shared with every other suite running from this host, so the tests
   * below give the slots back rather than leaving a trap for the next builder —
   * and for the second consecutive run of this file, which is what found it.
   */
  test.afterAll(() => {
    if (!canDriveStackRedis()) return;
    stackRedis([
      "eval",
      "local keys = redis.call('keys', 'pornarr:auth:login:*') for index = 1, #keys do redis.call('del', keys[index]) end return #keys",
      "0",
    ]);
  });

  /**
   * PRODUCT.md L59-60: "Errors state the cause and the next step, never just
   * that something failed."
   *
   * The sign-in screen, because it is the one error surface reachable without an
   * account and without touching anything the other builders share. The refusal
   * is a real code from the live API, mapped through `api-error.ts` — not a
   * fixture.
   *
   * Which refusal is not fixed, and is not the point. The rate limiter counts
   * per client address, so on a shared host the honest answer to a wrong
   * password is `INVALID_CREDENTIALS` or `LOGIN_RATE_LIMITED` depending on who
   * else has been running. Both are refusals, both have a cause and a step, and
   * the claim is about the mapping — so the code is read off the wire and the
   * screen is held to *that* code's two sentences, out of `en.json` itself. A
   * sentence edited in the locale file and forgotten on the screen fails here.
   */
  test("a refused sign-in says what happened and what to do about it", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Username").fill("nobody-piece-14");
    await page.getByLabel("Password").fill("not-the-password");
    const refusal = page.waitForResponse(
      (response) => response.url().includes("/api/auth/login") && !response.ok(),
    );
    await page.getByRole("button", { name: "Sign in" }).click();
    const body = (await (await refusal).json()) as { code: string };
    const code = body.code;

    expect(
      ["INVALID_CREDENTIALS", "LOGIN_RATE_LIMITED"],
      `The API refused a wrong password with ${code}, which is neither refusal this screen is documented to produce.`,
    ).toContain(code);

    const alert = page.getByRole("alert");
    await expect(alert).toBeVisible();
    await expect(
      alert,
      `The sign-in failure states no cause for ${code} (PRODUCT.md L59-60).`,
    ).toContainText(en.errors[code] as string);
    await expect(
      alert,
      `The sign-in failure states a cause for ${code} and stops, which is exactly what PRODUCT.md L59-60 forbids.`,
    ).toContainText(en.errorSteps[code] as string);

    // Never the code itself, and never the fallback: `errors.unknown` is
    // "Something went wrong ({{code}})." and this campaign started because an
    // operator read one of those.
    await expect(alert).not.toContainText(code);
    await expect(alert).not.toContainText("Something went wrong");
    await expectNoAccessibilityViolations(page);
  });

  /**
   * api-contract.md L28-29: "The frontend maps codes to translated messages; it
   * never renders a server-supplied English string."
   *
   * Proven from the wire: the response body carries a code and a context, and no
   * prose field at all. A `message` or `detail` in the envelope is a string a
   * client would be tempted to render, which is the failure this claim is about.
   */
  test("a refusal arrives as a code and a context, with no prose to render", async ({ page }) => {
    const response = await page.request.post("/api/auth/login", {
      data: { username: "nobody-piece-14", password: "not-the-password" },
    });
    const body = (await response.json()) as Record<string, unknown>;

    expect(Object.keys(body).sort()).toEqual(["code", "context", "status"]);
    expect(body.status).toBe(response.status());
    // Same two refusals, same reason as above.
    expect({ 401: "INVALID_CREDENTIALS", 429: "LOGIN_RATE_LIMITED" }[response.status()]).toBe(
      body.code,
    );

    // Every code the API can produce has a sentence and a step in both locales;
    // the walk that enforces it is `apps/web/src/lib/api-error-codes.test.ts`,
    // and this is the live end of it.
    const code = body.code as string;
    expect(en.errors[code], `${code} has no English cause.`).toBeTruthy();
    expect(en.errorSteps[code], `${code} has no English next step.`).toBeTruthy();
    expect(de.errors[code], `${code} has no German cause.`).toBeTruthy();
    expect(de.errorSteps[code], `${code} has no German next step.`).toBeTruthy();
  });

  /**
   * The same envelope on a route that never reaches application code, which is
   * where a framework's own English prose leaks out. Starlette's default 404 body
   * is `{"detail": "Not Found"}`; `errors.py` replaces it.
   */
  test("a route the API has never heard of answers in the contract's envelope", async ({
    page,
  }) => {
    const response = await page.request.get("/api/piece-14-does-not-exist");
    expect(response.status()).toBe(404);
    expect(await response.json()).toEqual({ code: "NOT_FOUND", status: 404, context: {} });
    expect(await response.text()).not.toContain("Not Found");
  });
});

test.describe("motion is a budget, and reduced motion is a supported path", () => {
  /**
   * DESIGN.md L279-289 and PRODUCT.md L99-100. Two halves, and the second is the
   * one that gets forgotten: "The distances matter as much as the durations
   * here. A 1ms transition over ten pixels is not a reduced animation, it is a
   * jump, which is the exact thing the preference asks not to happen."
   */
  test("under reduced motion every duration and every distance collapses", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await loginAsAdmin(page);
    await openScreen(page, "/library", "Library");

    const tokens = await page.evaluate(() => {
      const computed = getComputedStyle(document.documentElement);
      const read = (name: string): string => computed.getPropertyValue(name).trim();
      return {
        fast: read("--duration-fast"),
        base: read("--duration-base"),
        slow: read("--duration-slow"),
        liftSmall: read("--lift-sm"),
        liftMedium: read("--lift-md"),
        hoverScale: read("--hover-scale"),
      };
    });
    expect(tokens, "DESIGN.md L279-289: reduced motion collapses both.").toEqual({
      fast: "1ms",
      base: "1ms",
      slow: "1ms",
      liftSmall: "0px",
      liftMedium: "0px",
      hoverScale: "1",
    });

    // And no component bought its way out with a duration of its own. Every
    // element on the screen, not a sample.
    const rogue = await page.evaluate(() => {
      const seconds = (value: string): number =>
        Math.max(
          0,
          ...value.split(",").map((part) => {
            const trimmed = part.trim();
            return trimmed.endsWith("ms")
              ? Number.parseFloat(trimmed) / 1000
              : Number.parseFloat(trimmed);
          }),
        );
      const offenders: string[] = [];
      for (const element of document.querySelectorAll<HTMLElement>("*")) {
        const computed = getComputedStyle(element);
        const longest = Math.max(
          seconds(computed.transitionDuration),
          seconds(computed.animationDuration),
        );
        if (longest > 0.001) {
          offenders.push(
            `${element.tagName.toLowerCase()}.${element.className.toString().slice(0, 40)} = ${longest}s`,
          );
        }
      }
      return [...new Set(offenders)];
    });
    expect(
      rogue,
      "These name their own duration and therefore cannot be turned off (DESIGN.md L266-268).",
    ).toEqual([]);

    await expectNoAccessibilityViolations(page);
    await page.emulateMedia({ reducedMotion: "no-preference" });
  });

  /**
   * DESIGN.md L314: "Hover effects that change layout or reflow the grid" are
   * banned, and L266-268 says the lift is a scale rather than a translate for
   * exactly this reason: "a card that moves leaves a gap at its old edge, and a
   * card that grows does not."
   *
   * The neighbour's box, before and after. A hover that nudged the grid by one
   * pixel fails, and a transform-only hover — which is what `.pa-raise` is —
   * passes without the neighbour moving at all.
   */
  test("hovering a tile does not move the tile beside it", async ({ page }) => {
    await loginAsAdmin(page);
    await openScreen(page, "/library", "Library");

    const tiles = page.locator("main article");
    // The heading arrives before the grid does; wait for the rows themselves.
    await expect.poll(async () => tiles.count(), { timeout: 20_000 }).toBeGreaterThanOrEqual(3);

    const neighbour = tiles.nth(2);
    await expect(neighbour).toBeVisible();
    const before = await neighbour.boundingBox();

    /**
     * What the hover changed, property by property.
     *
     * The neighbour's box alone is too weak to fail: these tiles sit in a CSS
     * grid whose tracks are sized by the container, so a hovered item can grow a
     * margin or a width and its siblings still will not move. The falsifiable
     * form of "no hover effect changes layout" is which properties the hover
     * touched — and `.pa-raise` and `.tile` are documented to touch exactly two,
     * both of them compositor-only (DESIGN.md L266-268: "a card that moves
     * leaves a gap at its old edge, and a card that grows does not").
     */
    const hovered = tiles.nth(1);
    const snapshot = async (): Promise<Record<string, string>> =>
      hovered.evaluate((element) => {
        const computed = getComputedStyle(element);
        const values: Record<string, string> = {};
        for (const property of Array.from(computed)) {
          values[property] = computed.getPropertyValue(property);
        }
        return values;
      });
    const resting = await snapshot();

    await hovered.hover();
    // Long enough for a 150ms transition to have finished, whatever it animates.
    await page.waitForTimeout(500);
    const raised = await snapshot();

    const changed = Object.keys(raised).filter(
      (property) => raised[property] !== resting[property],
    );
    const layoutProperties = changed.filter((property) =>
      /^(width|height|margin|padding|top|left|right|bottom|inset|font-size|border-.*-width|flex|grid|gap|position|display)/.test(
        property,
      ),
    );
    expect(
      layoutProperties,
      `Hovering a tile changed ${JSON.stringify(layoutProperties)}, which are layout properties (DESIGN.md L314).`,
    ).toEqual([]);
    expect(changed.length, "Hovering a tile changed nothing at all.").toBeGreaterThan(0);

    const after = await neighbour.boundingBox();
    expect(after, "The neighbouring tile left the layout entirely.").not.toBeNull();
    expect(
      { x: Math.round((after as { x: number }).x), y: Math.round((after as { y: number }).y) },
      "Hovering a tile reflowed the grid (DESIGN.md L314).",
    ).toEqual({
      x: Math.round((before as { x: number }).x),
      y: Math.round((before as { y: number }).y),
    });
  });
});

test.describe("an estimate is never a bare number", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  /**
   * ADR 0031's consequence, PRODUCT.md L57-59 and DESIGN.md L219-221, on the
   * second surface that shows an estimate.
   *
   * Piece 03 fixed and asserted this for the search row. The queue was never
   * looked at: `/api/queue` has always returned `queue_estimate.confidence` and
   * `queue-route.tsx` rendered `format.estimate(low, high)` in both its layouts,
   * dropping the confidence on the floor — the same defect, one screen over.
   *
   * Asserted against what the API says today rather than against a pinned
   * payload: whatever `queue_estimate` holds, the row has to say it. On a queue
   * whose jobs are all past their waiting statuses that means the honest word
   * "Unknown" and no confidence chip beside it, which is DESIGN.md L221 read
   * literally. The three measured states are driven at unit level, in
   * `apps/web/src/routes/queue/queue.test.tsx`, because a queue job in a waiting
   * status is not something this spec may create on a stack it shares.
   */
  test("the queue's estimate says exactly what the API said, confidence included", async ({
    page,
  }) => {
    type Job = {
      readonly id: string;
      readonly title: string | null;
      readonly release_guid: string;
      readonly queue_estimate: {
        readonly low_seconds: number | null;
        readonly high_seconds: number | null;
        readonly confidence: "high" | "medium" | "low" | "unknown";
      };
    };
    const seeded = await queueWithAJob(page);
    test.skip(seeded === null, NO_TESTING_STACK);
    const queue = await apiGet<{ items: Job[] }>(page, "/api/queue?limit=20");
    expect(queue.items.length, "The queue was seeded and is still empty.").toBeGreaterThan(0);

    await openScreen(page, "/downloads", "Downloads");

    const labels = { high: "High", medium: "Medium", low: "Low" } as const;
    for (const job of queue.items) {
      const row = page
        .locator("main")
        .getByRole("row")
        .filter({ hasText: job.title ?? job.release_guid })
        .first();
      if ((await row.count()) === 0) continue;

      const cell = row.getByRole("cell").last();
      if (job.queue_estimate.low_seconds === null) {
        // DESIGN.md L221: "Unknown estimates say 'unknown', never '0' and never
        // a spinner that resolves to nothing."
        await expect(cell).toHaveText(/Unknown/);
        await expect(cell).not.toHaveText(/High|Medium|Low/);
      } else {
        await expect(cell, `${job.id} shows no range (ADR 0031).`).toHaveText(/~/);
        await expect(
          cell,
          `${job.id} shows a range with no confidence beside it, which is the bare figure ADR 0031 refuses.`,
        ).toContainText(labels[job.queue_estimate.confidence as keyof typeof labels]);
      }
    }
  });

  /**
   * The same claim, on the branch the live stack cannot currently reach.
   *
   * `queue_response` computes a measured estimate only for a job in a waiting
   * status (`routers/queue.py:86-92`), and the shared stack's one download
   * finished long ago and sits at `seeding`. Creating a waiting job means
   * grabbing a release that never completes and leaving it in a queue three
   * other builders read, which is not a trade this piece is entitled to make.
   *
   * So this one case is driven from a response this test writes, and it is
   * written down here because that is a weaker proof than the rest of the file:
   * the shape is not invented, it is what `queued_estimate` returns — a
   * ±20% range at `medium` — which `tests/e2e/queue.spec.ts:1098` asserts the
   * server really produces for every waiting job. What is proven here is only
   * the rendering. The three measured states are also driven at unit level in
   * `apps/web/src/routes/queue/queue.test.tsx`.
   */
  test("a measured estimate reaches the screen with its confidence beside it", async ({ page }) => {
    type Body = {
      items: {
        status: string;
        queue_estimate: {
          low_seconds: number | null;
          high_seconds: number | null;
          confidence: string;
        };
      }[];
      next_cursor: string | null;
    };
    // A row to re-answer, put there by this test rather than inherited. The
    // queue only ever held one because another file had grabbed something
    // first, so on a run where this file goes first — which is the alphabetical
    // order — there was nothing on the screen and the case failed for a reason
    // that is not its own.
    let live = await apiGet<Body>(page, "/api/queue?limit=20");
    if (live.items.length === 0) {
      const indexer = await testingIndexer(page);
      const client = await testingDownloadClient(page);
      test.skip(
        indexer === null || client === null,
        "The queue is empty and docker-compose.testing.yml is not running, so no download can be put in it.",
      );
      const search = await indexerSearch(page, TESTING_RELEASE_TITLE);
      const release = search.items.find(
        (item) => item.indexer_id === (indexer as { id: string }).id,
      );
      expect(release, "The fixture indexer answered the fan-out with nothing.").toBeDefined();
      const request = await seedRequest(page, TESTING_RELEASE_TITLE);
      // 201 the first time the client is handed this release, 200 when it
      // already holds it: the compose fixture is shared and grabbed once.
      const grabbed = await apiPostRaw(page, `/api/requests/${request.id}/grab`, {
        release_id: (release as { id: string }).id,
      });
      expect([200, 201], await grabbed.text()).toContain(grabbed.status());
      await expect
        .poll(async () => (await apiGet<Body>(page, "/api/queue?limit=20")).items.length, {
          timeout: 20_000,
          intervals: [500],
        })
        .toBeGreaterThan(0);
      live = await apiGet<Body>(page, "/api/queue?limit=20");
    }
    expect(live.items.length).toBeGreaterThan(0);

    const isQueueList = (url: URL): boolean => url.pathname === "/api/queue";
    let intercepted = 0;
    await page.route(isQueueList, async (route) => {
      const response = await route.fetch();
      const body = (await response.json()) as Body;
      for (const job of body.items) {
        // Exactly what `queued_estimate` returns for a job that is waiting: a
        // ±20% band around 300 seconds, at medium confidence.
        job.status = "downloading";
        job.queue_estimate = { low_seconds: 240, high_seconds: 360, confidence: "medium" };
      }
      intercepted += 1;
      await route.fulfill({
        status: response.status(),
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
    });

    await openScreen(page, "/downloads", "Downloads");
    await expect.poll(() => intercepted, { timeout: 15_000 }).toBeGreaterThan(0);
    const row = page.locator("main").getByRole("row").nth(1);
    await expect(row).toBeVisible();
    const cell = row.getByRole("cell").last();

    await expect(cell, "The queue row shows no range (ADR 0031).").toHaveText(/~/);
    await expect(
      cell,
      "The queue row shows a range with no confidence beside it, which is the bare figure ADR 0031 refuses.",
    ).toContainText("Medium");
    await page.unroute(isQueueList);
  });

  /**
   * DESIGN.md L109-111 and PRODUCT.md L56-57: numbers are tabular and
   * right-aligned in tables, "everywhere: sizes, durations, speeds, bitrates,
   * scores, counts, dates". The queue's three numeric columns, by computed
   * style rather than by class name — a class that stopped resolving would still
   * be in the markup.
   */
  test("the queue's figures are tabular so the columns compare", async ({ page }) => {
    const seeded = await queueWithAJob(page);
    test.skip(seeded === null, NO_TESTING_STACK);
    if (seeded === null) return;
    const queue = { items: seeded };

    // A row that is really loading, put there by intercepting the list the way
    // the estimate case above does.
    //
    // The progress column is measured only when there is a figure in it. A job
    // whose client reports no size renders `queue.progressUnknown` - a word,
    // deliberately, because DESIGN.md L221 refuses a "0 %" that measures
    // nothing, and `nonfunctional.spec.ts:368` asserts that same row says its
    // state in words. Asking a wordy cell for tabular figures asks it to break
    // the other claim, so the row is given a size and a remainder first.
    const isQueueList = (url: URL): boolean => url.pathname === "/api/queue";
    let intercepted = 0;
    await page.route(isQueueList, async (route) => {
      const response = await route.fetch();
      const body = (await response.json()) as {
        items: {
          status: string;
          size_bytes: number | null;
          remaining_bytes: number | null;
          download_speed_bytes: number | null;
          queue_estimate: {
            low_seconds: number | null;
            high_seconds: number | null;
            confidence: string;
          };
        }[];
      };
      for (const job of body.items) {
        job.status = "downloading";
        job.size_bytes = 4_000_000_000;
        job.remaining_bytes = 1_000_000_000;
        job.download_speed_bytes = 12_500_000;
        job.queue_estimate = { low_seconds: 240, high_seconds: 360, confidence: "medium" };
      }
      intercepted += 1;
      await route.fulfill({
        status: response.status(),
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
    });

    await openScreen(page, "/downloads", "Downloads");
    await expect.poll(() => intercepted, { timeout: 15_000 }).toBeGreaterThan(0);
    const first = queue.items[0] as { title: string | null; release_guid: string };
    const row = page
      .locator("main")
      .getByRole("row")
      .filter({ hasText: first.title ?? first.release_guid })
      .first();
    await expect(row).toBeVisible();

    // Progress, speed and ETA. Item and stage are words, not figures.
    //
    // The cell or the element inside it that actually holds the figure: the
    // progress column wraps its percentage in a span beside the bar, and putting
    // the class on the cell would set it on the bar as well. What the claim is
    // about is the digits.
    for (const index of [1, 3, 4]) {
      const numeric = await row
        .getByRole("cell")
        .nth(index)
        .evaluate((element) =>
          [element, ...element.querySelectorAll("*")].map(
            (node) => getComputedStyle(node).fontVariantNumeric,
          ),
        );
      expect(
        numeric,
        `Nothing in queue column ${index} is set in tabular figures (DESIGN.md L109-111).`,
      ).toContain("tabular-nums");
    }
    await page.unroute(isQueueList);
  });
});

/**
 * Radarr's `IndexHtmlFixture` and `GenericApiFixture`, adapted.
 *
 * Upstream asserts `/` is non-blank and uncached, and that six `Accept` values
 * all get JSON while three get a 406. Pornarr is a Vite SPA behind one origin
 * (ADR 0008), so the equivalent questions are: does the shell come back at all,
 * does a client-side route come back as the shell rather than as an API 404, and
 * is the API surface fenced off from another origin.
 */
test.describe("the shell", () => {
  test("the document root serves the application, not an error", async ({ page }) => {
    const response = await page.request.get("/");
    expect(response.ok()).toBe(true);
    const body = await response.text();
    expect(body).toContain('<div id="root">');
    expect(body.length, "The shell came back blank.").toBeGreaterThan(200);
  });

  test("a client route the server has never heard of still serves the shell", async ({ page }) => {
    // ADR 0008: a single-page application owns its own routing, so an address
    // only the router knows about must not be answered with a 404 body.
    const response = await page.request.get("/library/deep/route/piece-14");
    expect(response.ok()).toBe(true);
    expect(await response.text()).toContain('<div id="root">');
  });

  test("the API is not opened to another origin", async ({ page }) => {
    // The session is a cookie, so an `Access-Control-Allow-Origin` here would be
    // a cross-site read of a household's library. Radarr's CorsFixture asserts
    // its own policy; Pornarr's policy is that there is none.
    const response = await page.request.fetch("/api/library", {
      method: "GET",
      headers: { Origin: "http://another-origin.invalid" },
    });
    expect(response.headers()["access-control-allow-origin"]).toBeUndefined();
    expect(response.headers()["access-control-allow-credentials"]).toBeUndefined();
  });
});
