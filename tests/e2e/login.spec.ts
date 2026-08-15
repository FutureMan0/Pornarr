import AxeBuilder from "@axe-core/playwright";
import { type Page, expect, test } from "@playwright/test";

const ADMIN_USERNAME = "e2e-admin";
const ADMIN_PASSWORD = "E2E administrator password! 2026";

async function expectNoAccessibilityViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
}

async function waitForWebServer(page: Page): Promise<void> {
  await expect
    .poll(
      async () => {
        try {
          return (await page.request.get("/")).ok();
        } catch {
          return false;
        }
      },
      { timeout: 30_000 },
    )
    .toBe(true);
}

async function setUpAdministrator(page: Page): Promise<void> {
  await page.goto("/setup");

  await page.getByLabel("Username").fill(ADMIN_USERNAME);
  await page.getByLabel("Password").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("textbox", { name: "Library path" }).fill("/data");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Skip for now" }).click();
  await page.getByRole("button", { name: "Complete setup" }).click();
  await page.getByRole("link", { name: "Sign in" }).click();
}

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
  await expect(page.getByRole("heading", { name: "Library" })).toBeVisible();
  await expectNoAccessibilityViolations(page);

  await page.getByRole("button", { name: ADMIN_USERNAME }).click();
  await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});
