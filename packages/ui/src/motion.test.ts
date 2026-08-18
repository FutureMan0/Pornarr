/**
 * @vitest-environment node
 *
 * Reads tokens.css off disk and needs no DOM, like `contrast.test.ts` beside it.
 *
 * The motion guarantee, enforced rather than assumed.
 *
 * DESIGN.md now describes movement the product actually has, which makes the
 * reduced-motion paragraph load-bearing rather than aspirational: whatever is
 * added to the system, a reader who has asked for less movement has to get none.
 * That promise is kept by two things and broken by either of them slipping —
 * every duration collapsing, and every *distance* collapsing with it.
 *
 * The distances are the half people forget. A 1ms transition over ten pixels is
 * not a reduced animation, it is a jump, which is the exact thing the preference
 * asks not to happen.
 */
import { readFileSync } from "node:fs";
import { describe, expect, test } from "vitest";

const css = readFileSync(new URL("./tokens.css", import.meta.url), "utf8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);

/** The declarations inside the reduced-motion override. */
const reduced = ((): ReadonlyMap<string, string> => {
  const block =
    /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? "";
  const found = new Map<string, string>();
  for (const [, name, value] of block.matchAll(/--([\w-]+):\s*([^;]+);/g)) {
    if (name !== undefined && value !== undefined) found.set(name, value.trim());
  }
  return found;
})();

/** Everything declared at the top level, which is where the real values live. */
const declared = ((): ReadonlyMap<string, string> => {
  const withoutMedia = css.replace(/@media[\s\S]*?\n\}/g, "");
  const found = new Map<string, string>();
  for (const [, name, value] of withoutMedia.matchAll(/--([\w-]+):\s*([^;]+);/g)) {
    if (name !== undefined && value !== undefined) found.set(name, value.trim());
  }
  return found;
})();

/** Anything a component could animate over time or distance. */
const MOTION_TOKENS = ["duration-fast", "duration-base", "duration-slow"] as const;
const DISTANCE_TOKENS = ["lift-sm", "lift-md"] as const;

describe("the motion tokens", () => {
  test("every duration is inside the range DESIGN.md states", () => {
    for (const name of MOTION_TOKENS) {
      const value = declared.get(name);
      expect(value, `--${name} should be declared`).toBeDefined();
      const milliseconds = Number.parseFloat(String(value));
      // "150–250ms on transitions. Users are in a task and do not want
      // choreography."
      expect(milliseconds, `--${name} = ${value}`).toBeGreaterThanOrEqual(150);
      expect(milliseconds, `--${name} = ${value}`).toBeLessThanOrEqual(250);
    }
  });

  test("the hover scale is a nudge rather than a jump", () => {
    const value = Number.parseFloat(String(declared.get("hover-scale")));
    expect(value).toBeGreaterThan(1);
    // Past a few percent a grid appears to breathe as the pointer crosses it.
    expect(value).toBeLessThanOrEqual(1.05);
  });

  test("the travel distances stay small enough to read as arriving", () => {
    for (const name of DISTANCE_TOKENS) {
      const pixels = Number.parseFloat(String(declared.get(name)));
      expect(pixels, `--${name}`).toBeGreaterThan(0);
      // Further than this and it is a slide, which is choreography.
      expect(pixels, `--${name}`).toBeLessThanOrEqual(16);
    }
  });
});

describe("reduced motion", () => {
  test("collapses every duration", () => {
    for (const name of MOTION_TOKENS) {
      expect(reduced.get(name), `--${name} must be overridden`).toBe("1ms");
    }
  });

  test("collapses every distance too, which is the half that gets forgotten", () => {
    for (const name of DISTANCE_TOKENS) {
      expect(reduced.get(name), `--${name} must be overridden`).toBe("0px");
    }
    expect(reduced.get("hover-scale")).toBe("1");
  });

  test("nothing that moves has been added without an override", () => {
    // The guard that makes this file worth having. A new `--lift-*`, `--*-scale`
    // or `--duration-*` that is not in the reduced-motion block is movement a
    // reader cannot turn off, and it fails here rather than in somebody's
    // vestibular system.
    const movers = [...declared.keys()].filter(
      (name) =>
        name.startsWith("duration-") ||
        name.startsWith("lift-") ||
        name.endsWith("-scale") ||
        name.startsWith("ease-"),
    );
    // `--ease` and `--ease-*` are curves, not amounts: collapsing a duration to
    // 1ms already makes the curve unobservable.
    const mustCollapse = movers.filter((name) => !name.startsWith("ease"));

    for (const name of mustCollapse) {
      expect(
        reduced.has(name),
        `--${name} moves something and has no prefers-reduced-motion override`,
      ).toBe(true);
    }
  });
});
