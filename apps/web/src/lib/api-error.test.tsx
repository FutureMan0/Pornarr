/**
 * What a person actually reads when the API refuses.
 *
 * `api-error-codes.test.ts` proves the map is complete against the Python. This
 * proves the map reaches a screen: the sentence in the alert, rendered through
 * the real route table and the real error path, for codes that until now came out
 * as "Something went wrong (INDEXER_CONNECTION_FAILED)."
 *
 * The login endpoint is used as the delivery mechanism for codes that are raised
 * elsewhere in the product. What is under test is the code-to-sentence path — one
 * path, shared by every screen — not the endpoint, and driving the wizard's
 * indexer step in a browser needs an unconfigured server, which lives in
 * `tests/e2e/setup.spec.ts`.
 */
import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, test, vi } from "vitest";
import { LOCALE_STORAGE_KEY, setLocale } from "../i18n";
import de from "../i18n/de.json";
import en from "../i18n/en.json";
import { renderApp, server, useMockApi } from "../test/harness";
import { messageForErrorCode, nextStepForErrorCode } from "./api-error";

useMockApi();

afterEach(async () => {
  cleanup();
  window.localStorage.removeItem(LOCALE_STORAGE_KEY);
  await setLocale("en");
});

/** The sentence the product produced for these codes before they were translated. */
function fallbackFor(code: string): string {
  return en.errors.unknown.replace("{{code}}", code);
}

/** Sign in, with the API answering `code` instead, and hand back the alert's text. */
async function alertTextForCode(code: string, status: number): Promise<string> {
  server.use(
    http.post("/api/auth/login", () =>
      HttpResponse.json({ code, status, context: {} }, { status }),
    ),
  );
  renderApp("/");
  const user = userEvent.setup();

  await user.type(await screen.findByLabelText(en.login.username), "ada");
  await user.type(screen.getByLabelText(en.login.password), "whatever");
  await user.click(screen.getByRole("button", { name: en.login.submit }));

  return (await screen.findByRole("alert")).textContent ?? "";
}

describe("a code that used to render the fallback", () => {
  test("states the cause of an indexer that will not answer", async () => {
    const text = await alertTextForCode("INDEXER_CONNECTION_FAILED", 422);

    expect(text).toContain("The indexer did not answer, or answered with something unusable.");
    expect(text).not.toBe(fallbackFor("INDEXER_CONNECTION_FAILED"));
    // And the other half of the contract, on the screen rather than only in the
    // map. PRODUCT.md L59-60 asks for the cause *and* the next step, and asserting
    // the map alone let a screen render the cause on its own and still pass.
    expect(nextStepForErrorCode("INDEXER_CONNECTION_FAILED")).toBe(
      "Check the base URL and API key, and that the indexer is running, then test it again.",
    );
    expect(text).toContain(nextStepForErrorCode("INDEXER_CONNECTION_FAILED"));
  });

  test("states the cause of a download client that will not answer", async () => {
    const text = await alertTextForCode("DOWNLOAD_CLIENT_CONNECTION_FAILED", 422);

    expect(text).toContain("The download client did not answer, or refused these credentials.");
    expect(text).not.toBe(fallbackFor("DOWNLOAD_CLIENT_CONNECTION_FAILED"));
    expect(nextStepForErrorCode("DOWNLOAD_CLIENT_CONNECTION_FAILED")).toBe(
      "Check the host, port and credentials, and that the client is running, then test it again.",
    );
    expect(text).toContain(nextStepForErrorCode("DOWNLOAD_CLIENT_CONNECTION_FAILED"));
  });

  test("states the rule when the API refuses an administrator password", async () => {
    // The server-side half of the twelve-character rule. The refusal has to state
    // the rule, because a caller that is not the wizard never saw it.
    const text = await alertTextForCode("SETUP_PASSWORD_TOO_WEAK", 422);

    expect(text).toContain(
      "That administrator password is shorter than 12 characters, or uses only one kind of character.",
    );
    expect(nextStepForErrorCode("SETUP_PASSWORD_TOO_WEAK")).toBe(
      "Use at least 12 characters from two kinds: lower case, upper case, digits, symbols.",
    );
    expect(text).toContain(nextStepForErrorCode("SETUP_PASSWORD_TOO_WEAK"));
  });

  test("says it in German too, which is the whole reason codes are the contract", async () => {
    await setLocale("de");

    expect(messageForErrorCode("INDEXER_CONNECTION_FAILED")).toBe(
      de.errors.INDEXER_CONNECTION_FAILED,
    );
    expect(messageForErrorCode("INDEXER_CONNECTION_FAILED")).not.toContain("went wrong");
    expect(nextStepForErrorCode("SETUP_PASSWORD_TOO_WEAK")).toBe(
      de.errorSteps.SETUP_PASSWORD_TOO_WEAK,
    );
  });
});

describe("the fallback that is left", () => {
  test("still produces a sentence, so a newer server cannot blank a screen", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});

    const message = messageForErrorCode("SHIPPED_BY_A_LATER_VERSION");

    expect(message).toContain("SHIPPED_BY_A_LATER_VERSION");
    expect(message.length).toBeGreaterThan("SHIPPED_BY_A_LATER_VERSION".length);
    vi.restoreAllMocks();
  });

  test("is no longer silent about it", () => {
    // Reaching it is a defect against PRODUCT.md, and a defect nothing says out
    // loud is how thirty-three codes went untranslated for as long as they did.
    const reported = vi.spyOn(console, "error").mockImplementation(() => {});

    messageForErrorCode("A_CODE_NOBODY_TRANSLATED");

    expect(reported).toHaveBeenCalledTimes(1);
    expect(String(reported.mock.calls[0]?.[0])).toContain("A_CODE_NOBODY_TRANSLATED");
    expect(String(reported.mock.calls[0]?.[0])).toContain("ERROR_CODES");

    // Once per code, not once per render: a broken endpoint must not drown the
    // console it is trying to be noticed in.
    messageForErrorCode("A_CODE_NOBODY_TRANSLATED");
    expect(reported).toHaveBeenCalledTimes(1);
    vi.restoreAllMocks();
  });

  test("is not reached by a code the API is known to emit", () => {
    const reported = vi.spyOn(console, "error").mockImplementation(() => {});

    messageForErrorCode("INDEXER_CONNECTION_FAILED");
    nextStepForErrorCode("INDEXER_CONNECTION_FAILED");

    expect(reported).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });
});
