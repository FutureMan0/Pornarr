/**
 * Accent switching.
 *
 * The delivered token set ships two accents, rose and amber, selected by a
 * `data-theme` attribute on `<html>`. Everything downstream follows from that
 * one attribute: accent ramp, ground stack, text ramp, placeholder artwork hue,
 * blur strength and the logo's hue rotation.
 *
 * This is deliberately not a React hook. The attribute has to be on the element
 * before the first paint, or the page renders in the default accent and then
 * visibly swaps — `applyStoredTheme()` runs from the entry point, ahead of
 * React, and `setTheme()` is what a settings control calls afterwards.
 */

export const THEMES = ["rose", "amber"] as const;

export type Theme = (typeof THEMES)[number];

export const DEFAULT_THEME: Theme = "rose";

const STORAGE_KEY = "pornarr-theme";
const ATTRIBUTE = "data-theme";

export const isTheme = (value: unknown): value is Theme =>
  typeof value === "string" && (THEMES as readonly string[]).includes(value);

/**
 * Read the stored choice.
 *
 * Storage can throw rather than merely be empty — Safari in private mode, and
 * any browser with cookies blocked for the origin. An accent is not worth
 * failing a page load over, so an unreadable store means the default.
 */
export const storedTheme = (storage: Pick<Storage, "getItem"> | undefined): Theme => {
  try {
    const stored = storage?.getItem(STORAGE_KEY);
    return isTheme(stored) ? stored : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
};

/** Write the attribute the token sheet keys off, and remember the choice. */
export const setTheme = (
  theme: Theme,
  element: Pick<Element, "setAttribute"> | undefined,
  storage: Pick<Storage, "setItem"> | undefined,
): void => {
  element?.setAttribute(ATTRIBUTE, theme);
  try {
    storage?.setItem(STORAGE_KEY, theme);
  } catch {
    // A theme that cannot be remembered still applies for this page.
  }
};

/**
 * Apply the stored accent. Call once, from the entry point, before rendering.
 *
 * Returns the accent that was applied so a caller can seed its own state from
 * it rather than reading storage a second time.
 */
export const applyStoredTheme = (
  element: Pick<Element, "setAttribute"> | undefined = globalThis.document?.documentElement,
  storage: Pick<Storage, "getItem" | "setItem"> | undefined = globalThis.localStorage,
): Theme => {
  const theme = storedTheme(storage);
  setTheme(theme, element, storage);
  return theme;
};
