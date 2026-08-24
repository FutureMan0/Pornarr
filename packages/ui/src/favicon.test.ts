/**
 * The tab icon has to follow the accent, and the colour it follows with has to be
 * the same colour the sidebar shows.
 *
 * The second requirement is the one worth testing: two independent renderings of
 * one brand — a CSS filter on screen, a computed hex in a data URL — agree only
 * as long as they are computed the same way. A palette hand-picked for the icon
 * would pass a "does it change" test and still be visibly wrong.
 */
import { describe, expect, test } from "vitest";

import { applyAccentFavicon, faviconDataUrl, faviconSvg } from "./favicon";
import { applyCssFilter, parseHex } from "./lib/filter-colour";
import { MARK_GRADIENT } from "./lib/mark";

/** The rotation the token declares for the second accent. */
const AMBER_FILTER = "hue-rotate(63deg) saturate(1.08)";

describe("a CSS filter applied to one colour", () => {
  test("no filter is the identity, however it is spelled", () => {
    expect(applyCssFilter("#ff14a4", "none")).toBe("#ff14a4");
    expect(applyCssFilter("#ff14a4", "")).toBe("#ff14a4");
    expect(applyCssFilter("#ff14a4", "   ")).toBe("#ff14a4");
  });

  test("a full turn comes back where it started", () => {
    // The matrix is periodic in 360°, so this is a check on the maths rather
    // than on a value somebody wrote down.
    const [r, g, b] = parseHex(applyCssFilter("#ff14a4", "hue-rotate(360deg)"));
    const [wantR, wantG, wantB] = parseHex("#ff14a4");
    expect(Math.abs(r - wantR)).toBeLessThanOrEqual(1);
    expect(Math.abs(g - wantG)).toBeLessThanOrEqual(1);
    expect(Math.abs(b - wantB)).toBeLessThanOrEqual(1);
  });

  test("turns are degrees too", () => {
    expect(applyCssFilter("#ff14a4", "hue-rotate(0.5turn)")).toBe(
      applyCssFilter("#ff14a4", "hue-rotate(180deg)"),
    );
  });

  test("saturate(1) changes nothing and saturate(0) leaves grey", () => {
    expect(applyCssFilter("#ff14a4", "saturate(1)")).toBe("#ff14a4");

    const grey = applyCssFilter("#ff14a4", "saturate(0)");
    const [r, g, b] = parseHex(grey);
    // Desaturating fully collapses the channels onto the luminance, so all three
    // land on the same value.
    expect(r).toBe(g);
    expect(g).toBe(b);
  });

  test("percentages and multipliers are the same thing", () => {
    expect(applyCssFilter("#ff14a4", "saturate(108%)")).toBe(
      applyCssFilter("#ff14a4", "saturate(1.08)"),
    );
  });

  test("the accent rotation moves the pink somewhere else entirely", () => {
    const rotated = applyCssFilter("#ff14a4", AMBER_FILTER);
    expect(rotated).not.toBe("#ff14a4");

    // Pink is red-dominant with a strong blue; the rotation the token declares
    // takes it toward the warm end, so green overtakes blue.
    const [, g, b] = parseHex(rotated);
    expect(g).toBeGreaterThan(b);
  });

  test("a filter it does not model leaves the colour alone rather than guessing", () => {
    // Better a recognisable brand colour than a confidently wrong one.
    expect(applyCssFilter("#ff14a4", "blur(2px) invert(1)")).toBe("#ff14a4");
  });

  test("nonsense in, something visible out", () => {
    expect(applyCssFilter("not-a-colour", "none")).toBe("not-a-colour");
    expect(applyCssFilter("#zzz", "hue-rotate(10deg)")).toBe("#000000");
  });

  test("short hex is expanded, not misread", () => {
    expect(applyCssFilter("#f0a", "saturate(1)")).toBe("#ff00aa");
  });
});

describe("the generated icon", () => {
  test("carries the mark's own gradient when there is no filter", () => {
    const svg = faviconSvg("none");
    for (const colour of MARK_GRADIENT) expect(svg).toContain(colour);
  });

  test("carries transformed colours under the accent's filter, and none of the originals", () => {
    const svg = faviconSvg(AMBER_FILTER);
    for (const colour of MARK_GRADIENT) {
      expect(svg).not.toContain(colour);
      expect(svg).toContain(applyCssFilter(colour, AMBER_FILTER));
    }
  });

  test("draws the same glyph as the component rather than a second drawing of it", () => {
    // Both read `MARK_PATH`. This is what stops the icon and the sidebar drifting
    // into two different logos.
    expect(faviconSvg("none")).toContain("M 349.515 268.394");
  });

  test("no glow: it is a smear at sixteen pixels", () => {
    expect(faviconSvg("none")).not.toContain("feGaussianBlur");
  });

  test("the data URL escapes the characters that would truncate it", () => {
    const url = faviconDataUrl("none");
    expect(url.startsWith("data:image/svg+xml,")).toBe(true);
    // An unescaped '#' ends the URL at the first gradient reference and the tab
    // shows nothing at all.
    expect(url.includes("#")).toBe(false);
    expect(decodeURIComponent(url.slice("data:image/svg+xml,".length))).toBe(faviconSvg("none"));
  });
});

describe("installing the icon", () => {
  test("adds one link ahead of the static ones and replaces it on the next call", () => {
    document.head.innerHTML = '<link rel="icon" href="/favicon.ico" />';

    applyAccentFavicon();
    const links = document.head.querySelectorAll("link[rel='icon']");
    expect(links).toHaveLength(2);
    // First, because a browser picking between icons takes the first format it
    // understands.
    expect(links[0]?.getAttribute("type")).toBe("image/svg+xml");

    const first = links[0]?.getAttribute("href");
    applyAccentFavicon();
    expect(document.head.querySelectorAll("link[rel='icon']")).toHaveLength(2);
    expect(document.head.querySelector("link[type='image/svg+xml']")?.getAttribute("href")).toBe(
      first,
    );
  });

  test("a document that reports no filter still gets an icon", () => {
    document.head.innerHTML = "";
    // jsdom resolves custom properties to "" unless a stylesheet sets them, which
    // is exactly the shape of the missing-token case.
    applyAccentFavicon();

    const href = document.head.querySelector("link[type='image/svg+xml']")?.getAttribute("href");
    expect(href).toBe(faviconDataUrl(""));
  });

  test("no document is not a crash", () => {
    expect(() => applyAccentFavicon(undefined, undefined)).not.toThrow();
  });
});
