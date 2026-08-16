import AxeBuilder from "@axe-core/playwright";
import { type Page, expect } from "@playwright/test";

export const ADMIN_USERNAME = "e2e-admin";
export const ADMIN_PASSWORD = "E2E administrator password! 2026";

export async function expectNoAccessibilityViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
}

export async function waitForWebServer(page: Page): Promise<void> {
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

export async function setUpAdministrator(page: Page): Promise<void> {
  await page.goto("/setup");

  await expect(page.getByRole("heading", { name: "Set up Pornarr" })).toBeVisible();
  await page.getByLabel("Username").fill(ADMIN_USERNAME);
  await page.getByLabel("Password").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Continue" }).click();

  await expect(page.getByRole("heading", { name: "Choose the library path" })).toBeVisible();
  await page.getByRole("textbox", { name: "Library path" }).fill("/data");
  await page.getByRole("button", { name: "Continue" }).click();

  const filtersHeading = page.getByRole("heading", { name: "Review content filters" });
  const copyImports = page.getByRole("button", { name: "Continue with copy imports" });
  await expect(filtersHeading.or(copyImports)).toBeVisible();
  if (await copyImports.isVisible()) {
    await copyImports.click();
    await expect(filtersHeading).toBeVisible();
  }
  await page.getByRole("button", { name: "Continue" }).click();

  await expect(page.getByRole("heading", { name: "Metadata providers" })).toBeVisible();
  await page.getByRole("button", { name: "Skip for now" }).click();

  await expect(page.getByRole("heading", { name: "Review setup" })).toBeVisible();
  await page.getByRole("button", { name: "Complete setup" }).click();

  await expect(page.getByRole("heading", { name: "Setup complete" })).toBeVisible();
  await page.getByRole("link", { name: "Sign in" }).click();
}

export async function loginAsAdmin(page: Page): Promise<void> {
  await waitForWebServer(page);
  const setupStatus = await page.request.get("/api/setup/status");
  expect(setupStatus.ok()).toBe(true);
  const { configured } = (await setupStatus.json()) as { configured: boolean };
  if (!configured) {
    await setUpAdministrator(page);
  } else {
    await page.goto("/login");
  }

  if (page.url().includes("/login")) {
    await page.getByLabel("Username").fill(ADMIN_USERNAME);
    await page.getByLabel("Password").fill(ADMIN_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("heading", { name: "Library" })).toBeVisible();
  }
}
