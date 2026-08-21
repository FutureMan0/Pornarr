/**
 * Following a request's status - the flow between grabbing something and it
 * turning up in the library.
 *
 * The request is created through `POST /api/requests`, the same call the search
 * screen makes, so what the screen renders afterwards is the product's own
 * state and not a fixture pushed into the database.
 */
import { expect, test } from "@playwright/test";
import { expectNoAccessibilityViolations, loginAsAdmin, seedRequest } from "./helpers";

test.describe("request status", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("a request is followed on the requests screen and can be cancelled", async ({ page }) => {
    const query = `E2E follow status ${Date.now()}`;
    const created = await seedRequest(page, query);
    expect(created.status).toBe("searching");

    await page.goto("/requests");
    await expect(page.getByRole("heading", { name: "Requests", level: 1 })).toBeVisible();

    const entry = page.getByRole("listitem").filter({ hasText: query });
    await expect(entry).toBeVisible();
    await expect(entry.getByRole("heading", { name: query })).toBeVisible();
    // Status and priority are the two things a reader follows a request by, and
    // they share one line - matched whole so the history entries below, which
    // repeat every status word, stay out of it.
    await expect(entry.getByRole("paragraph")).toContainText("Priority 80");
    // Every status the request has passed through is listed, newest search first.
    await expect(entry.getByRole("list", { name: "Request history" })).toBeVisible();

    await expectNoAccessibilityViolations(page);

    await entry.getByRole("button", { name: "Cancel" }).click();

    // Cancelling is terminal: the status changes and the action disappears with it.
    await expect(entry.getByRole("paragraph")).toContainText("Cancelled");
    await expect(entry.getByRole("button", { name: "Cancel" })).toHaveCount(0);
    await expect(
      entry
        .getByRole("list", { name: "Request history" })
        .getByRole("listitem")
        .filter({ hasText: "Cancelled" }),
    ).toBeVisible();
  });

  test("the downloads screen reports what the queue is doing", async ({ page }) => {
    await page.goto("/downloads");
    // Scoped to the screen's own region: the application shell renders the
    // primary navigation as a list, so an unscoped listitem is the sidebar.
    const downloads = page.getByRole("region", { name: "Downloads" });
    // The shell's banner owns the screen title, not the region below it, so the
    // heading is asserted on the page and only the list is scoped.
    await expect(page.getByRole("heading", { name: "Downloads", level: 1 })).toBeVisible();
    await expect(
      downloads.getByRole("listitem").first().or(
        // `queue.empty.active`: the screen reports an empty state per tab, and
        // the Active tab is the one it opens on.
        downloads.getByText("Nothing is downloading."),
      ),
    ).toBeVisible();
    await expectNoAccessibilityViolations(page);
  });
});
