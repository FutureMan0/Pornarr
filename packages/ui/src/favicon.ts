/**
 * The tab icon, drawn to match the accent.
 *
 * The static `favicon.ico` is baked in the delivered pink, so switching to the
 * second accent changed the whole interface and the mark in the sidebar and left
 * the browser tab pink — the one part of the product a reader sees while looking
 * at something else entirely.
 *
 * WHY GENERATED RATHER THAN TWO FILES. A favicon is an isolated document: it
 * never sees the host page's custom properties, so it cannot follow a token the
 * way the inline mark does. Two exported PNGs would work and would be two files
 * to re-export by hand whenever the accent or the mark moved. This reads the same
 * `--pa-logo-filter` the screen reads and runs it over the same gradient stops,
 * so the icon cannot disagree with the sidebar.
 *
 * NO GLOW. The component's mark carries a soft outer glow, which at 16 pixels is
 * a smear that costs contrast and buys nothing. The icon is the glyph and the
 * gradient, which is what survives at that size.
 *
 * THE STATIC FILE STAYS. It is what a browser shows before any script has run,
 * and what one that refuses SVG icons shows instead. The generated link is added
 * ahead of it rather than replacing it, so there is always something to fall back
 * to.
 */
import { applyCssFilter } from "./lib/filter-colour";
import { MARK_GRADIENT, MARK_PATH, MARK_VIEW_BOX } from "./lib/mark";

/** Marks the link this module owns, so repeated calls replace rather than pile up. */
const LINK_ID = "pornarr-accent-icon";

const LOGO_FILTER = "--pa-logo-filter";

/**
 * The icon as an SVG document.
 *
 * Exported for its own sake: a string is testable, and asserting on the colours
 * in it is how the accent's effect on the icon is pinned down without a browser.
 */
export function faviconSvg(filter: string): string {
  const stops = MARK_GRADIENT.map((colour) => applyCssFilter(colour, filter));
  const offsets = stops.map((colour, index) => {
    const offset = index / (stops.length - 1);
    return `<stop offset="${offset}" stop-color="${colour}"/>`;
  });
  // No newlines: this ends up in a data URL, and every byte of whitespace is a
  // byte of URL.
  return [
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${MARK_VIEW_BOX}">`,
    `<defs><linearGradient id="g" x1="0" y1="0.7" x2="1" y2="0.3">${offsets.join("")}`,
    "</linearGradient></defs>",
    `<path fill="url(#g)" fill-rule="evenodd" d="${MARK_PATH}"/>`,
    "</svg>",
  ].join("");
}

/**
 * `encodeURIComponent` rather than base64.
 *
 * A percent-encoded SVG stays legible in devtools and is shorter than base64 for
 * this content. `#` is the byte that must not survive: unencoded it truncates the
 * URL at the first gradient reference.
 */
export function faviconDataUrl(filter: string): string {
  return `data:image/svg+xml,${encodeURIComponent(faviconSvg(filter))}`;
}

/**
 * Point the tab at an icon in the current accent.
 *
 * Reads the filter off the document, so it must run after the theme attribute is
 * on `<html>` — `applyStoredTheme` sets that, and this is called straight after.
 * A document that reports no filter still gets an icon: the unfiltered pink,
 * which is the correct answer for the default accent.
 */
export function applyAccentFavicon(
  documentRef: Document | undefined = globalThis.document,
  windowRef: (Window & typeof globalThis) | undefined = globalThis.window,
): void {
  if (documentRef === undefined) return;

  const filter =
    windowRef?.getComputedStyle(documentRef.documentElement).getPropertyValue(LOGO_FILTER) ?? "";

  const existing = documentRef.getElementById(LINK_ID);
  const link = existing instanceof HTMLLinkElement ? existing : documentRef.createElement("link");
  link.id = LINK_ID;
  link.rel = "icon";
  link.type = "image/svg+xml";
  link.href = faviconDataUrl(filter);

  if (existing === null) {
    // Before the static links, because a browser choosing between icons prefers
    // the first it understands.
    documentRef.head.prepend(link);
  }
}
