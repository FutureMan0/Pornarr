/**
 * Approving a quarantined item.
 *
 * Quarantine items are produced by the import pipeline when a file trips a
 * filter or falls below the confidence threshold. There is no way to create one
 * without an import, so the approval flow is only real once something has been
 * held for review. `library.spec.ts` owns the assertion that an import happens
 * at all; this file states plainly when it did not.
 */
import { mkdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import { type Page, expect, test } from "@playwright/test";
import {
  apiGet,
  apiPut,
  canPlaceCompletedDownloads,
  downloadsRoot,
  expectNoAccessibilityViolations,
  firstQuarantineItem,
  loginAsAdmin,
  writeCompletedDownload,
} from "./helpers";

/** The operator's own filter profile, as `/api/admin/filters/profile` shapes it. */
type FilterProfile = {
  readonly rules: {
    readonly id?: string;
    readonly kind: string;
    readonly pattern: string;
    readonly action: string;
    readonly enabled: boolean;
  }[];
};

const RUN = `q${Date.now().toString(36)}`;

function staging(): string {
  return join(downloadsRoot(), "gauntlet-quarantine", RUN);
}

/**
 * Put one item in quarantine, the only way there is one: an import that trips
 * a filter.
 *
 * Nothing can create a quarantine row directly, and waiting for another spec
 * to leave one behind is waiting for a run order that is not promised. The
 * operator's own route turns a term rule on, a download whose title matches is
 * staged, and the pipeline holds it - which is the product doing exactly what
 * the review screen exists for.
 *
 * Returns the profile as it was found, for the caller to put back.
 */
async function holdSomethingForReview(page: Page): Promise<FilterProfile> {
  const term = `gauntlet-hold-${RUN}`;
  const before = await apiGet<FilterProfile>(page, "/api/admin/filters/profile");
  await apiPut<FilterProfile>(page, "/api/admin/filters/profile", {
    rules: before.rules.map((rule) =>
      rule.kind === "term"
        ? { kind: rule.kind, pattern: term, action: "quarantine", enabled: true }
        : { kind: rule.kind, pattern: rule.pattern, action: rule.action, enabled: rule.enabled },
    ),
  });

  mkdirSync(staging(), { recursive: true });
  writeCompletedDownload(staging(), `Gauntlet Studio - ${term} (2026-05-05) 1080p`, {
    seconds: 12,
    seed: 11,
  });
  await expect
    .poll(async () => (await firstQuarantineItem(page)) !== null, {
      timeout: 180_000,
      intervals: [2_000],
      message:
        "A download that matches an enabled quarantine rule was never held for review. " +
        "See docs/pipelines/import.md step 8.",
    })
    .toBe(true);
  return before;
}

test.describe("quarantine review", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("the review screen reports what is waiting", async ({ page }) => {
    await page.goto("/admin/quarantine");
    await expect(page.getByRole("heading", { name: "Quarantine review", level: 1 })).toBeVisible();
    const item = await firstQuarantineItem(page);
    if (item === null) {
      await expect(page.getByRole("heading", { name: "Nothing needs review" })).toBeVisible();
    } else {
      await expect(page.getByRole("list", { name: "Files for this reason" })).toBeVisible();
    }
    await expectNoAccessibilityViolations(page);
  });

  test("a held item can be approved into the library", async ({ page }) => {
    test.skip(
      !canPlaceCompletedDownloads(),
      "The suite cannot reach the stack's download volume, or ffmpeg is missing, so nothing " +
        "can be held for review.",
    );
    test.setTimeout(300_000);

    const profile = await holdSomethingForReview(page);
    try {
      await approveTheHeldItem(page);
    } finally {
      await apiPut<FilterProfile>(page, "/api/admin/filters/profile", {
        rules: profile.rules.map((rule) => ({
          kind: rule.kind,
          pattern: rule.pattern,
          action: rule.action,
          enabled: rule.enabled,
        })),
      });
      rmSync(join(downloadsRoot(), "gauntlet-quarantine"), { recursive: true, force: true });
    }
  });
});

async function approveTheHeldItem(page: Page): Promise<void> {
  const item = await firstQuarantineItem(page);
  expect(item, "Nothing is in quarantine after one was held.").not.toBeNull();
  if (item === null) return;

  await page.goto("/admin/quarantine");
  await expect(page.getByRole("list", { name: "Files for this reason" })).toBeVisible();

  await page.getByRole("button", { name: "Approve", exact: true }).click();

  // Approval is a decision, not a dismissal: the item leaves the review queue
  // for good, which the API confirms independently of what the screen shows.
  // Approving is real work - the file is moved out of quarantine and a media
  // record is created - so the queue empties a moment after the click.
  await expect
    .poll(
      async () => {
        const remaining = await firstQuarantineItem(page);
        return remaining === null || remaining.id !== item.id;
      },
      { timeout: 30_000, intervals: [1_000] },
    )
    .toBe(true);
  await expectNoAccessibilityViolations(page);
}
