/**
 * Accent switching, including the failure modes that are easy to skip.
 *
 * Storage that throws is the interesting one: Safari in private mode and any
 * origin with cookies blocked raise on both read and write, and a design token
 * is not worth failing a page load over.
 */
import { describe, expect, test, vi } from "vitest";

import { DEFAULT_THEME, applyStoredTheme, isTheme, setTheme, storedTheme } from "./theme";

const memoryStorage = (initial: Record<string, string> = {}) => {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, value),
    read: () => Object.fromEntries(values),
  };
};

const throwingStorage = {
  getItem: () => {
    throw new DOMException("The operation is insecure.", "SecurityError");
  },
  setItem: () => {
    throw new DOMException("The operation is insecure.", "SecurityError");
  },
};

describe("recognising an accent", () => {
  test("only the two shipped names count", () => {
    expect(isTheme("rose")).toBe(true);
    expect(isTheme("amber")).toBe(true);
    expect(isTheme("blurple")).toBe(false);
    expect(isTheme(undefined)).toBe(false);
    expect(isTheme(null)).toBe(false);
  });
});

describe("reading the stored accent", () => {
  test("a stored choice is honoured", () => {
    expect(storedTheme(memoryStorage({ "pornarr-theme": "amber" }))).toBe("amber");
  });

  test("an empty store means the default", () => {
    expect(storedTheme(memoryStorage())).toBe(DEFAULT_THEME);
  });

  test("a value left over from an accent that no longer exists means the default", () => {
    expect(storedTheme(memoryStorage({ "pornarr-theme": "blurple" }))).toBe(DEFAULT_THEME);
  });

  test("storage that throws means the default, not an exception", () => {
    expect(storedTheme(throwingStorage)).toBe(DEFAULT_THEME);
  });

  test("no storage at all means the default", () => {
    expect(storedTheme(undefined)).toBe(DEFAULT_THEME);
  });
});

describe("applying an accent", () => {
  test("the attribute the token sheet keys off is written", () => {
    const element = { setAttribute: vi.fn() };
    const storage = memoryStorage();

    setTheme("amber", element, storage);

    expect(element.setAttribute).toHaveBeenCalledWith("data-theme", "amber");
    expect(storage.read()).toStrictEqual({ "pornarr-theme": "amber" });
  });

  test("an accent that cannot be remembered still applies to this page", () => {
    const element = { setAttribute: vi.fn() };

    expect(() => setTheme("amber", element, throwingStorage)).not.toThrow();
    expect(element.setAttribute).toHaveBeenCalledWith("data-theme", "amber");
  });
});

describe("applying the stored accent at startup", () => {
  test("it writes the attribute and reports what it applied", () => {
    const element = { setAttribute: vi.fn() };

    const applied = applyStoredTheme(element, memoryStorage({ "pornarr-theme": "amber" }));

    expect(applied).toBe("amber");
    expect(element.setAttribute).toHaveBeenCalledWith("data-theme", "amber");
  });

  test("a first visit is pinned to the default rather than left unset", () => {
    // Leaving the attribute off would work — the token sheet defaults on :root —
    // but then a later switch back to rose has nothing to switch *from*.
    const element = { setAttribute: vi.fn() };

    const applied = applyStoredTheme(element, memoryStorage());

    expect(applied).toBe(DEFAULT_THEME);
    expect(element.setAttribute).toHaveBeenCalledWith("data-theme", DEFAULT_THEME);
  });

  test("no document and no storage is survivable", () => {
    expect(applyStoredTheme(undefined, undefined)).toBe(DEFAULT_THEME);
  });
});
