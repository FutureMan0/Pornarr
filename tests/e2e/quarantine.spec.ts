/**
 * Approving a quarantined item.
 *
 * Quarantine items are produced by the import pipeline when a file trips a
 * filter or falls below the confidence threshold. There is no way to create one
 * without an import, so the approval flow is only real once something has been
 * held for review. `library.spec.ts` owns the assertion that an import happens
 * at all; this file states plainly when it did not.
 */
import { expect, test } from "@playwright/test";
import { expectNoAccessibilityViolations, firstQuarantineItem, loginAsAdmin } from "./helpers";

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
    const item = await firstQuarantineItem(page);
    test.skip(
      item === null,
      "Nothing is in quarantine. Items only come from the import pipeline, and no import ever reaches a quarantine decision on this instance - see the failing import test in library.spec.ts.",
    );
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
  });
});
