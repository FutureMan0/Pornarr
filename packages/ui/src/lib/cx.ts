/**
 * Joins class names, dropping anything falsy. Conditional classes are how a
 * component selects its state, so `cx(css.base, disabled && css.disabled)`
 * needs to read cleanly.
 */
export const cx = (...parts: readonly (string | false | null | undefined)[]): string =>
  parts.filter((part): part is string => typeof part === "string" && part.length > 0).join(" ");
