import { defineConfig } from "@playwright/test";

/** The Compose stack owns startup; these tests only own browser behaviour. */
export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "test-results",
  fullyParallel: false,
  // One stack, one library, one transcode session cap. Two files at once means
  // one test's player is refused a session another test is holding, or has its
  // own session released by another test's cleanup - failures that say nothing
  // about the product.
  workers: 1,
  retries: process.env.CI === "true" ? 2 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
