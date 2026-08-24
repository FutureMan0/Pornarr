/**
 * @vitest-environment node
 *
 * The delivered palette, held to the same floor as the existing one.
 *
 * `contrast.test.ts` guards the tokens the components use today. This guards
 * the ones they are being migrated onto, before anything is built on them —
 * finding out that metadata is unreadable on a list row is cheap now and
 * expensive after forty screens exist.
 *
 * It also records where the palette's own limits are. Two of the four ink steps
 * do not clear body-text contrast on the deepest ground, which is a real
 * constraint on how the ramp may be used rather than a defect to fix: the
 * design uses those steps as secondary text on cards, not as body text on
 * tracks. Writing the boundary down here is what stops it being rediscovered.
 */
import { readFileSync } from "node:fs";

import { parse, wcagContrast } from "culori";
import { describe, expect, test } from "vitest";

/** Ground stack, shallowest to deepest. A child is never darker than its parent. */
const GROUNDS = ["pa-bg-00", "pa-bg-0", "pa-bg-1", "pa-bg-2", "pa-bg-3", "pa-bg-4"] as const;
/** Everything except the deepest ground, where the lower ink steps run out. */
const SHALLOW_GROUNDS = GROUNDS.slice(0, -1);
const DEEPEST = "pa-bg-4";
const THEMES = ["rose", "amber"] as const;

type Theme = (typeof THEMES)[number];

const css = readFileSync(new URL("./tokens.css", import.meta.url), "utf8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);

/**
 * Rose is declared on `:root` together with its own attribute selector, amber
 * on that selector alone. Matched by selector rather than by position, so
 * reordering the file cannot silently point this at the wrong theme, and by
 * either quote style, because the formatter has an opinion about which.
 */
const palette = (theme: Theme): ReadonlyMap<string, string> => {
  const selector = new RegExp(`\\[data-theme=['"]${theme}['"]\\][^{]*\\{([\\s\\S]*?)\\n\\}`);
  const block = selector.exec(css)?.[1];
  if (block === undefined) throw new Error(`no [data-theme='${theme}'] block in tokens.css`);
  const found = new Map<string, string>();
  for (const [, name, value] of block.matchAll(/--([\w-]+):\s*([^;]+);/g)) {
    if (name !== undefined && value !== undefined) found.set(name, value.trim());
  }
  return found;
};

const PALETTES: ReadonlyMap<Theme, ReadonlyMap<string, string>> = new Map(
  THEMES.map((theme) => [theme, palette(theme)] as const),
);

const ratio = (theme: Theme, fg: string, bg: string): number => {
  const tokens = PALETTES.get(theme);
  if (tokens === undefined) throw new Error(`no palette for ${theme}`);
  const a = tokens.get(fg);
  const b = tokens.get(bg);
  if (a === undefined) throw new Error(`--${fg} is not defined for ${theme}`);
  if (b === undefined) throw new Error(`--${bg} is not defined for ${theme}`);
  const parsedA = parse(a);
  const parsedB = parse(b);
  if (parsedA === undefined) throw new Error(`--${fg} is not a parseable colour: ${a}`);
  if (parsedB === undefined) throw new Error(`--${bg} is not a parseable colour: ${b}`);
  return wcagContrast(parsedA, parsedB);
};

const atLeast = (theme: Theme, fg: string, bg: string, min: number): void => {
  const measured = ratio(theme, fg, bg);
  expect(
    measured,
    `${theme}: --${fg} on --${bg} is ${measured.toFixed(2)}:1, needs ${min}:1`,
  ).toBeGreaterThanOrEqual(min);
};

describe("both themes define the whole ramp", () => {
  for (const theme of THEMES) {
    test(theme, () => {
      for (const name of [
        ...GROUNDS,
        "pa-text",
        "pa-text-dim",
        "pa-text-muted",
        "pa-text-faint",
        "pa-accent-300",
        "pa-accent-500",
      ]) {
        expect(PALETTES.get(theme)?.has(name), `--${name} missing from ${theme}`).toBe(true);
      }
    });
  }
});

describe("the ground stack lifts, step by step, all the way up", () => {
  // "A child is never darker than its parent": video sits deepest, then page,
  // frame, card, row, track. Nesting a card in a frame must not invert the
  // depth cue, so each step has to be strictly lighter than the one before —
  // which shows up as contrast against the lightest ink falling monotonically.
  for (const theme of THEMES) {
    test(theme, () => {
      const againstInk = GROUNDS.map((ground) => ratio(theme, ground, "pa-text"));
      expect(againstInk).toStrictEqual([...againstInk].sort((a, b) => b - a));
      expect(new Set(againstInk).size, "two grounds are the same shade").toBe(GROUNDS.length);
    });
  }
});

describe("primary text reaches 4.5:1 on every ground", () => {
  for (const theme of THEMES) {
    for (const ground of GROUNDS) {
      for (const ink of ["pa-text", "pa-text-dim"] as const) {
        test(`${theme}: --${ink} on --${ground}`, () => atLeast(theme, ink, ground, 4.5));
      }
    }
  }
});

describe("secondary text reaches 4.5:1 everywhere but the deepest ground", () => {
  for (const theme of THEMES) {
    for (const ground of SHALLOW_GROUNDS) {
      for (const ink of ["pa-text-muted", "pa-text-faint"] as const) {
        test(`${theme}: --${ink} on --${ground}`, () => atLeast(theme, ink, ground, 4.5));
      }
    }
  }
});

describe("the deepest ground is the ramp's limit, and it is a documented one", () => {
  // Measured 3.70–4.09:1 depending on theme and step. Above the 3:1 floor for
  // large text and non-text, below the 4.5:1 body-text requirement. On
  // --pa-bg-4, secondary copy has to move up to --pa-text-dim.
  for (const theme of THEMES) {
    for (const ink of ["pa-text-muted", "pa-text-faint"] as const) {
      test(`${theme}: --${ink} on --${DEEPEST} is large-text only`, () => {
        atLeast(theme, ink, DEEPEST, 3);
        expect(
          ratio(theme, ink, DEEPEST),
          `--${ink} now clears body text on --${DEEPEST}; relax this test and the rule with it`,
        ).toBeLessThan(4.5);
      });
    }
    test(`${theme}: --pa-text-dim is the way out`, () =>
      atLeast(theme, "pa-text-dim", DEEPEST, 4.5));
  }
});

describe("the accent works as a line on every ground", () => {
  // "Accent as a line, not a flood": --pa-accent-500 is for icons, borders,
  // bars and glows, which WCAG 1.4.11 holds to 3:1.
  for (const theme of THEMES) {
    for (const ground of GROUNDS) {
      test(`${theme}: --pa-accent-500 on --${ground}`, () =>
        atLeast(theme, "pa-accent-500", ground, 3));
    }
  }
});

describe("accent text uses the 300 step, which clears body contrast", () => {
  // The delivered README states this rule; this is the arithmetic behind it.
  // --pa-accent-500 measures 3.30:1 on rose's deepest ground, so it cannot
  // carry small text there — --pa-accent-300 reaches 7.56:1 on the same ground.
  for (const theme of THEMES) {
    for (const ground of GROUNDS) {
      test(`${theme}: --pa-accent-300 on --${ground}`, () =>
        atLeast(theme, "pa-accent-300", ground, 4.5));
    }
  }
});
