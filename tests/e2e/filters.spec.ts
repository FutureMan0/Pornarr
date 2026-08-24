/**
 * The content filter, against the instance the migration actually built.
 *
 * ADR 0017 leaves every filtering decision to the operator, and ADR 0018 puts
 * those decisions in one global profile whose six rules ship off and empty.
 * Migration 0004 seeds that profile inside `upgrade()`, so what is asserted
 * here is the seeded row and the enum types Postgres holds it in — neither of
 * which the SQLite-backed API tests can reach.
 *
 * The stack is shared, so this restores the profile before it finishes and the
 * rule it turns on matches a string nothing in any library contains.
 */
import { expect, test } from "@playwright/test";
import { apiGet, apiPut, loginAsAdmin } from "./helpers";

const KINDS = [
  "term",
  "tag",
  "performer",
  "minimum_confidence",
  "unknown_performer_age",
  "unknown_file_type",
] as const;

/** Matches nothing on a shared stack that other pieces are importing into. */
const PROBE_PATTERN = "zzz-gauntlet-filter-probe";

type Rule = {
  readonly id: string;
  readonly kind: string;
  readonly pattern: string;
  readonly action: string;
  readonly enabled: boolean;
};
type Profile = { readonly id: string; readonly scope: string; readonly rules: Rule[] };

function allOff(): { rules: Omit<Rule, "id">[] } {
  return {
    rules: KINDS.map((kind) => ({ kind, pattern: "", action: "reject", enabled: false })),
  };
}

test.describe("content filter profiles", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test("the seeded global profile is readable and every rule ships off", async ({ page }) => {
    const profile = await apiGet<Profile>(page, "/api/admin/filters/profile");

    expect(profile.scope, "The profile the migration seeds is the global one.").toBe("global");
    expect(profile.rules.map((rule) => rule.kind)).toEqual([...KINDS]);
    expect(
      profile.rules.filter((rule) => rule.enabled),
      "ADR 0017: an instance nobody configured filters nothing.",
    ).toEqual([]);
    expect(profile.rules.every((rule) => rule.pattern === "")).toBe(true);
  });

  test("a rule the administrator turns on is stored and read back", async ({ page }) => {
    try {
      const written = await apiPut<Profile>(page, "/api/admin/filters/profile", {
        rules: KINDS.map((kind) =>
          kind === "term"
            ? { kind, pattern: PROBE_PATTERN, action: "quarantine", enabled: true }
            : { kind, pattern: "", action: "reject", enabled: false },
        ),
      });
      const term = written.rules.find((rule) => rule.kind === "term");
      expect(term?.action, "The action is the operator's, not the seeded default.").toBe(
        "quarantine",
      );

      // Read back through a second request: the decision has to survive the
      // response that reported it, which is the whole of what was missing.
      const stored = await apiGet<Profile>(page, "/api/admin/filters/profile");
      expect(stored.rules.find((rule) => rule.kind === "term")).toMatchObject({
        pattern: PROBE_PATTERN,
        action: "quarantine",
        enabled: true,
      });
      expect(stored.rules.filter((rule) => rule.enabled).map((rule) => rule.kind)).toEqual([
        "term",
      ]);
    } finally {
      await apiPut<Profile>(page, "/api/admin/filters/profile", allOff());
    }

    const restored = await apiGet<Profile>(page, "/api/admin/filters/profile");
    expect(restored.rules.filter((rule) => rule.enabled)).toEqual([]);
  });

  test("an enabled rule with nothing to match on is refused with its reason", async ({ page }) => {
    const refused = await page.request.put("/api/admin/filters/profile", {
      data: {
        rules: KINDS.map((kind) => ({
          kind,
          pattern: "",
          action: "reject",
          enabled: kind === "tag",
        })),
      },
      headers: {
        "X-CSRF-Token":
          (await page.context().cookies()).find((cookie) => cookie.name === "pornarr_csrf")
            ?.value ?? "",
      },
    });

    expect(refused.status()).toBe(422);
    expect(await refused.json()).toEqual({
      code: "FILTER_CONFIGURATION_INVALID",
      status: 422,
      context: { reason: "pattern_required", kind: "tag" },
    });
    const stored = await apiGet<Profile>(page, "/api/admin/filters/profile");
    expect(stored.rules.filter((rule) => rule.enabled)).toEqual([]);
  });
});
