import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["{apps,packages}/**/*.{test,spec}.{ts,tsx}"],
    environment: "node",
    // The design system renders real DOM to assert its states; nothing else in
    // the workspace needs a browser environment.
    environmentMatchGlobs: [
      ["packages/ui/**", "jsdom"],
      ["apps/web/**", "jsdom"],
    ],
    // Repairs web storage on Node versions that ship an inert `localStorage`
    // global; see the file for why jsdom's own never arrives there.
    setupFiles: ["./vitest.setup.ts"],
    // Return the authored class name from a CSS Module import instead of a
    // build-time hash, so a test can assert which state a component is in.
    css: { modules: { classNameStrategy: "non-scoped" } },
    passWithNoTests: false,
  },
});
