import { expect, test } from "@playwright/test";
import {
  ADMIN_PASSWORD,
  ADMIN_USERNAME,
  expectNoAccessibilityViolations,
  setUpAdministrator,
  waitForWebServer,
} from "./helpers";

test("an administrator can set up, sign in, reach the library and sign out", async ({ page }) => {
  await waitForWebServer(page);
  const setupStatus = await page.request.get("/api/setup/status");
  expect(setupStatus.ok()).toBe(true);
  const { configured } = (await setupStatus.json()) as { configured: boolean };
  if (configured) {
    await page.goto("/login");
  } else {
    await setUpAdministrator(page);
  }

  await expectNoAccessibilityViolations(page);
  await page.getByLabel("Username").fill(ADMIN_USERNAME);
  await page.getByLabel("Password").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  // By level: an empty library also renders a "Your library is empty" heading.
  await expect(page.getByRole("heading", { name: "Library", level: 1 })).toBeVisible();
  await expectNoAccessibilityViolations(page);

  await page.getByRole("button", { name: ADMIN_USERNAME }).click();
  await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});
