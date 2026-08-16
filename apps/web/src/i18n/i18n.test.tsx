/**
 * Translation and formatting, as behaviour rather than as configuration.
 *
 * The locale switch is exercised through the real shell — the menu in the top
 * bar, the same one a user clicks — because "switching locale changes every
 * visible string" is a claim about the rendered document, not about i18next.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, test } from "vitest";
import {
  ERROR_CODES,
  RETRYABLE_ERROR_CODES,
  messageForError,
  messageForErrorCode,
} from "../lib/api-error";
import {
  TEST_USER,
  renderApp,
  server,
  setViewportWidth,
  signedIn,
  useMockApi,
} from "../test/harness";
import de from "./de.json";
import en from "./en.json";
import {
  Numeric,
  formatBitrate,
  formatBytes,
  formatDate,
  formatDuration,
  formatEstimate,
  formatNumber,
  formatRelativeDate,
  formatSpeed,
  localeName,
} from "./format";
import i18n, { LOCALES, LOCALE_STORAGE_KEY, currentLocale, setLocale } from "./index";

useMockApi();

afterEach(async () => {
  cleanup();
  window.localStorage.removeItem(LOCALE_STORAGE_KEY);
  await setLocale("en");
});

describe("detection and override", () => {
  test("falls back to the browser's language, which jsdom reports as English", () => {
    expect(currentLocale()).toBe("en");
  });

  test("the override wins and is persisted for the next load", async () => {
    await setLocale("de");

    expect(currentLocale()).toBe("de");
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("de");
    // Detection is configured to read that key before it reads the browser, so
    // the stored answer is what a cold load would resolve to.
    expect(i18n.options.detection?.order?.[0]).toBe("localStorage");
  });

  test("a regional variant resolves to its base language", async () => {
    await i18n.changeLanguage("de-AT");
    expect(currentLocale()).toBe("de");
  });
});

describe("switching locale", () => {
  test("changes every visible string in the shell", async () => {
    setViewportWidth(1440);
    signedIn();
    renderApp("/library");
    const user = userEvent.setup();

    // English first: navigation, heading and the library loading state.
    expect(await screen.findByRole("navigation", { name: en.nav.primary })).toBeTruthy();
    expect(screen.getByRole("heading", { name: en.nav.library })).toBeTruthy();
    expect(screen.getByText(en.library.loading)).toBeTruthy();
    expect(screen.getByRole("button", { name: en.activity.title })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: en.locale.label }));
    await user.click(screen.getByRole("menuitem", { name: localeName("de") }));

    // German everywhere at once: link label, route heading, body copy, and the
    // accessible name of a landmark that no screen owns.
    await waitFor(() =>
      expect(screen.getByRole("navigation", { name: de.nav.primary })).toBeTruthy(),
    );
    expect(screen.getByRole("heading", { name: de.nav.library })).toBeTruthy();
    expect(await screen.findByText(de.library.empty)).toBeTruthy();
    expect(screen.getByRole("button", { name: de.activity.title })).toBeTruthy();
    expect(screen.queryByText(en.library.empty)).toBeNull();
  });

  test("changes the login screen", async () => {
    renderApp("/");

    expect(await screen.findByRole("button", { name: en.login.submit })).toBeTruthy();

    await setLocale("de");

    await waitFor(() => expect(screen.getByRole("button", { name: de.login.submit })).toBeTruthy());
    expect(screen.getByLabelText(de.login.username)).toBeTruthy();
  });
});

describe("error codes", () => {
  test("every code the API defines has a message and a next step in every locale", () => {
    const resources: Record<(typeof LOCALES)[number], typeof en> = { en, de };

    for (const locale of LOCALES) {
      const messages = resources[locale].errors as unknown as Readonly<Record<string, string>>;
      // DESIGN.md: "an error states the cause and the next step". The cause
      // alone passes no review, so the walk that guards the map guards both —
      // adding a code to ERROR_CODES with no way out of it fails CI.
      const steps = resources[locale].errorSteps as unknown as Readonly<Record<string, string>>;
      for (const code of ERROR_CODES) {
        expect(messages[code], `${locale}.json is missing errors.${code}`).toBeTruthy();
        expect(steps[code], `${locale}.json is missing errorSteps.${code}`).toBeTruthy();
      }
    }
  });

  test("a code is retryable only if it is a code", () => {
    // The retry list is a subset of the map, so a rename cannot leave a button
    // pointing at a code that no longer exists.
    for (const code of RETRYABLE_ERROR_CODES) {
      expect(ERROR_CODES).toContain(code);
    }
  });

  test("the locales carry exactly the same keys", () => {
    const keys = (value: unknown, prefix = ""): string[] => {
      if (typeof value !== "object" || value === null) return [prefix];
      return Object.entries(value).flatMap(([key, child]) =>
        keys(child, prefix === "" ? key : `${prefix}.${key}`),
      );
    };

    expect(keys(de).sort()).toEqual(keys(en).sort());
  });

  test("an unknown code renders the generic sentence with the code in it", () => {
    const message = messageForErrorCode("SOMETHING_NOBODY_SHIPPED");

    expect(message).toContain("SOMETHING_NOBODY_SHIPPED");
    expect(message.length).toBeGreaterThan("SOMETHING_NOBODY_SHIPPED".length);
  });

  test("a value that is not an API error still produces a sentence", () => {
    expect(messageForError(new TypeError("boom"))).toBe(en.errors.generic);
    expect(messageForError(null)).toBe(en.errors.generic);
  });

  test("an unknown code from the server reaches the screen, never blank", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({ code: "TEAPOT_ON_FIRE", status: 418, context: {} }, { status: 418 }),
      ),
    );
    renderApp("/");
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText(en.login.username), "ada");
    await user.type(screen.getByLabelText(en.login.password), "whatever");
    await user.click(screen.getByRole("button", { name: en.login.submit }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("TEAPOT_ON_FIRE");
    expect(alert.textContent?.trim().length).toBeGreaterThan("TEAPOT_ON_FIRE".length);
  });

  test("error messages follow the locale", async () => {
    expect(messageForErrorCode("INVALID_CREDENTIALS")).toBe(en.errors.INVALID_CREDENTIALS);

    await setLocale("de");

    expect(messageForErrorCode("INVALID_CREDENTIALS")).toBe(de.errors.INVALID_CREDENTIALS);
  });
});

describe("formatting", () => {
  test("file sizes use binary units and the locale's decimal mark", () => {
    expect(formatBytes(1_610_612_736, "en")).toBe("1.5 GiB");
    expect(formatBytes(1_610_612_736, "de")).toBe("1,5 GiB");
    expect(formatBytes(512, "en")).toBe("512 B");
  });

  test("speeds and bitrates keep their notation and localise their number", () => {
    expect(formatSpeed(5_242_880, "en")).toBe("5.0 MiB/s");
    expect(formatBitrate(4_500_000, "en")).toBe("4.5 Mbit/s");
    expect(formatBitrate(4_500_000, "de")).toBe("4,5 Mbit/s");
  });

  test("a running time is clock notation, identical in both locales by design", () => {
    expect(formatDuration(1_452, "en")).toBe("24:12");
    expect(formatDuration(1_452, "de")).toBe("24:12");
    expect(formatDuration(5_052, "en")).toBe("1:24:12");
  });

  test("a coarse estimate takes its unit name from the locale", () => {
    const english = formatEstimate(12 * 60, 18 * 60, "en");
    const german = formatEstimate(12 * 60, 18 * 60, "de");

    expect(english.startsWith("~")).toBe(true);
    expect(english).toContain("12");
    expect(english).toContain("18");
    // "min" in English, "Min." in German: the whole point of routing this
    // through Intl rather than concatenating a hardcoded unit.
    expect(german).not.toBe(english);
    expect(formatEstimate(600, 600, "en")).toBe("~10 min");
  });

  test("dates and relative dates differ between locales", () => {
    const day = new Date(2025, 2, 14);

    expect(formatDate(day, "en")).not.toBe(formatDate(day, "de"));
    expect(formatDate(day, "de")).toContain("2025");

    const now = new Date(2025, 2, 17);
    expect(formatRelativeDate(day, "en", now)).not.toBe(formatRelativeDate(day, "de", now));
    expect(formatRelativeDate(day, "en", now)).toContain("days ago");
  });

  test("plain numbers follow the locale's separators", () => {
    expect(formatNumber(1_234.5, "en", 1)).toBe("1,234.5");
    expect(formatNumber(1_234.5, "de", 1)).toBe("1.234,5");
  });

  test("a language names itself", () => {
    expect(localeName("de")).toBe("Deutsch");
    expect(localeName("en")).toBe("English");
  });

  test("Numeric carries the design system's tabular class", () => {
    render(<Numeric className="text-right">{formatBytes(1024, "en")}</Numeric>);

    const node = screen.getByText("1.0 KiB");
    expect(node.className).toContain("tabular");
    expect(node.className).toContain("text-right");
  });
});

describe("live announcements", () => {
  test("the account menu and its items are translated", async () => {
    setViewportWidth(1440);
    signedIn();
    renderApp("/library");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: TEST_USER.username }));
    expect(screen.getByRole("menuitem", { name: en.account.signOut })).toBeTruthy();

    await setLocale("de");

    await waitFor(() =>
      expect(screen.getByRole("menuitem", { name: de.account.signOut })).toBeTruthy(),
    );
  });
});
