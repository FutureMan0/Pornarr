/**
 * @vitest-environment node
 *
 * Every error code the backend can emit has a translated sentence — enforced.
 *
 * This file reads Python off disk and needs no DOM; under the jsdom environment
 * the rest of `apps/web` uses, `import.meta.url` is not a file:// URL and the
 * tree walk below cannot resolve its own directory.
 *
 * WHY IT EXISTS
 *
 * `api-error.ts` said "every code the API is documented to produce, in one list"
 * and listed 17 of 50. The other 33, plus the three codes written as literals,
 * fell through to `errors.unknown` — which is "Something went wrong ({{code}})."
 * against PRODUCT.md's "Errors state the cause and the next step, never just that
 * something failed." An operator setting the product up for the first time read
 * "Something went wrong (INDEXER_CONNECTION_FAILED)." and was told neither what
 * had happened nor what to do about it.
 *
 * A list kept in step by hand had already drifted by 33 entries, so it is kept in
 * step by this instead: the source of truth is the Python, and adding a code
 * there without a sentence here fails CI with the code's name in the message.
 *
 * WHAT IT READS
 *
 *   1. `code = "SOMETHING"` or `code: str = "SOMETHING"` on a class body line —
 *      every `PornarrError` subclass across `apps/` and `packages/`. Anchored to
 *      the start of the line so that `trigger.error_code = "duplicate"` and its
 *      kind are not mistaken for one. The annotated spelling is the one the base
 *      class uses and the scan used to walk past it in silence.
 *   2. `error_body("SOMETHING"` — codes emitted without an exception class.
 *   3. `"code": "SOMETHING"` — the same, written straight into a JSON body.
 *   4. The status-to-code map in `apps/api/pornarr_api/errors.py`, for the
 *      conditions Starlette raises before our own code sees the request.
 */
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";
import de from "../i18n/de.json";
import en from "../i18n/en.json";
import { ERROR_CODES, isKnownErrorCode } from "./api-error";

const REPOSITORY_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..");
const PYTHON_ROOTS = ["apps", "packages"];
const STATUS_CODE_MAP = join(REPOSITORY_ROOT, "apps", "api", "pornarr_api", "errors.py");

/**
 * A floor under the scan itself.
 *
 * A regex that matched nothing would make every assertion below vacuously true,
 * which is the failure mode a completeness check cannot afford. Fifty is what the
 * repository held when this was written; the number only ever goes up.
 */
const KNOWN_BACKEND_CODE_COUNT = 50;

function pythonFiles(root: string): string[] {
  return readdirSync(root, { recursive: true, encoding: "utf8" })
    .filter((entry) => entry.endsWith(".py") && !entry.includes("node_modules"))
    .map((entry) => join(root, entry));
}

function matches(source: string, pattern: RegExp): string[] {
  return [...source.matchAll(pattern)].map((match) => match[1] as string);
}

/** Every code the API can put in a response body, read out of the Python. */
function backendErrorCodes(): ReadonlySet<string> {
  const found = new Set<string>();
  for (const root of PYTHON_ROOTS) {
    for (const file of pythonFiles(join(REPOSITORY_ROOT, root))) {
      const source = readFileSync(file, "utf8");
      // The annotation is optional because `PornarrError` itself writes
      // `code: str = "INTERNAL_ERROR"`, so that is the form a subclass copied
      // from the base class carries — and without it the scan walked straight
      // past such a subclass and reported nothing at all, which is the one
      // failure a completeness check cannot have. Planted both spellings in a
      // router to check: the plain one was named, the annotated one was not.
      for (const code of matches(source, /^\s*code(?:\s*:\s*str)? = "([A-Z][A-Z0-9_]*)"\s*$/gm)) {
        found.add(code);
      }
      for (const code of matches(source, /error_body\(\s*"([A-Z][A-Z0-9_]*)"/g)) found.add(code);
      for (const code of matches(source, /"code":\s*"([A-Z][A-Z0-9_]*)"/g)) found.add(code);
    }
  }
  const statusMap = readFileSync(STATUS_CODE_MAP, "utf8");
  for (const code of matches(statusMap, /^\s{4}\d{3}: "([A-Z][A-Z0-9_]*)",\s*$/gm)) found.add(code);
  return found;
}

const messages = {
  en: en.errors as unknown as Readonly<Record<string, string>>,
  de: de.errors as unknown as Readonly<Record<string, string>>,
};
const steps = {
  en: en.errorSteps as unknown as Readonly<Record<string, string>>,
  de: de.errorSteps as unknown as Readonly<Record<string, string>>,
};

describe("the codes the backend can actually emit", () => {
  const codes = [...backendErrorCodes()].sort();

  test("the scan finds the codes it is supposed to be checking", () => {
    // Anchored on two the campaign caught rendering the fallback in a browser,
    // so a regex that quietly stopped matching cannot pass this file.
    expect(codes).toContain("INDEXER_CONNECTION_FAILED");
    expect(codes).toContain("DOWNLOAD_CLIENT_CONNECTION_FAILED");
    expect(codes).toContain("SETUP_REQUIRED");
    expect(codes).toContain("WEB_ASSETS_MISSING");
    expect(codes).toContain("RANGE_NOT_SATISFIABLE");
    expect(codes.length).toBeGreaterThanOrEqual(KNOWN_BACKEND_CODE_COUNT);
    // And nothing that is not a code: `trigger.error_code = "duplicate"` is an
    // import trigger's own field and has no business in an HTTP error map.
    expect(codes).not.toContain("duplicate");
  });

  test("every one of them is a code this build knows", () => {
    const missing = codes.filter((code) => !isKnownErrorCode(code));

    expect(
      missing,
      `ERROR_CODES is missing ${missing.length} backend code(s); each renders "${messages.en.unknown}"`,
    ).toEqual([]);
  });

  test("every one of them states a cause and a next step, in both locales", () => {
    for (const locale of ["en", "de"] as const) {
      for (const code of codes) {
        const cause = messages[locale][code];
        const step = steps[locale][code];
        expect(cause, `${locale}.json is missing errors.${code}`).toBeTruthy();
        expect(step, `${locale}.json is missing errorSteps.${code}`).toBeTruthy();
        // Not the fallback wearing a different key, and not a fragment: both
        // halves are sentences somebody can read and act on.
        expect(cause, `errors.${code} in ${locale}.json is the fallback`).not.toContain("{{code}}");
        expect(
          cause?.length,
          `errors.${code} in ${locale}.json is too short to be a cause`,
        ).toBeGreaterThan(20);
        expect(
          step?.length,
          `errorSteps.${code} in ${locale}.json is too short to be a step`,
        ).toBeGreaterThan(20);
        expect(cause?.endsWith("."), `errors.${code} in ${locale}.json is not a sentence`).toBe(
          true,
        );
        expect(step?.endsWith("."), `errorSteps.${code} in ${locale}.json is not a sentence`).toBe(
          true,
        );
      }
    }
  });

  /**
   * The scan runs one way only, and that is the hole in it.
   *
   * Everything above starts from the Python and asks whether the frontend has a
   * sentence. Nothing starts from `docs/api-contract.md` and asks whether the
   * code it names exists — and ADR 0009 makes the contract the boundary, so a
   * code written down there is a promise a client may be generated against.
   *
   * Four of the nine codes the contract offers as its representative vocabulary
   * are in no source file anywhere, `HARDLINK_CROSS_DEVICE` among them — which
   * is the code in the document's own worked example of what an error looks
   * like. Three more were already recorded by earlier pieces:
   * `DUPLICATE_IN_LIBRARY` and `METADATA_CONFIDENCE_LOW` by piece 05 (the
   * product spells them `duplicate` and `low_confidence`, as import-trigger and
   * quarantine reasons rather than HTTP codes), and `DOWNLOAD_CLIENT_UNREACHABLE`
   * by piece 04 (the product raises `DOWNLOAD_CLIENT_CONNECTION_FAILED`).
   *
   * Executed as an expected failure rather than skipped, so the list is real and
   * the day somebody closes the gap this file turns red and says so. Fixing it
   * means either implementing four codes or editing the contract, and which one
   * is a product decision this test is not entitled to make.
   */
  test.fails("every code the contract names as representative exists", () => {
    const contract = readFileSync(join(REPOSITORY_ROOT, "docs", "api-contract.md"), "utf8");
    const section = contract.slice(contract.indexOf("Representative codes"));
    const named = [...new Set(matches(section, /`([A-Z][A-Z0-9_]{4,})`/g))].sort();
    // The scan of the document has to find something, or the assertion below is
    // vacuous in the same way the backend scan would be.
    expect(named.length).toBeGreaterThanOrEqual(9);

    const missing = named.filter((code) => !codes.includes(code));

    expect(
      missing,
      `docs/api-contract.md names ${missing.length} code(s) that exist nowhere in the product: ${missing.join(", ")}`,
    ).toEqual([]);
  });

  test("no two codes share a sentence, which is how a copy-paste hides", () => {
    // Two codes with the same cause means one of them was filled in without being
    // read. Only the cause is checked: two codes may honestly share a next step —
    // an expired release and one that has fallen out of the cache are both fixed
    // by searching again — but if they also share a cause, one of them is wrong.
    const shared = new Map<string, string[]>();
    for (const code of ERROR_CODES) {
      const cause = messages.en[code];
      if (cause === undefined) continue;
      shared.set(cause, [...(shared.get(cause) ?? []), code]);
    }
    const duplicates = [...shared.values()].filter((group) => group.length > 1);

    expect(
      duplicates,
      `these codes share one English sentence: ${JSON.stringify(duplicates)}`,
    ).toEqual([]);
  });
});
