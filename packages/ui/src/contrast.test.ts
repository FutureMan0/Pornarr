/**
 * @vitest-environment node
 *
 * This file reads tokens.css off disk and needs no DOM. Under the jsdom
 * environment the rest of this package uses, `import.meta.url` is not a file://
 * URL and readFileSync cannot resolve it.
 *
 * The accessibility guarantee, enforced rather than assumed.
 *
 * DESIGN.md states the contrast rules in prose; this reads the shipped token
 * values out of tokens.css and checks every pair the components actually use.
 * Parsing the stylesheet rather than a TypeScript mirror is deliberate: one
 * source of truth means a token cannot be edited without moving this test.
 */
import { readFileSync } from "node:fs";
import { parse, wcagContrast } from "culori";
import { describe, expect, test } from "vitest";

const SURFACES = ["bg", "surface", "surface-2", "surface-3"] as const;
const SEMANTIC = ["success", "warning", "danger", "info"] as const;

const declarations = (block: string): ReadonlyMap<string, string> => {
  const found = new Map<string, string>();
  for (const [, name, value] of block.matchAll(/--([\w-]+):\s*([^;]+);/g)) {
    if (name !== undefined && value !== undefined) found.set(name, value.trim());
  }
  return found;
};

const tokens = ((): ReadonlyMap<string, string> => {
  const raw = readFileSync(new URL("./tokens.css", import.meta.url), "utf8");
  // Comments first: prose that names a token and a colon ("--primary-ink: white
  // on a rose...") otherwise swallows the declaration that follows it.
  const css = raw.replace(/\/\*[\s\S]*?\*\//g, "");
  // Only the first :root block. The reduced-motion override redefines
  // durations, and picking those up would silently shadow the real values.
  const root = /:root\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? "";
  // The surfaces, inks and brand colours are now aliases onto the delivered
  // ramp, which is declared further down under the default accent's selector.
  // Resolving one level of indirection is what keeps this test measuring the
  // colour that actually reaches the screen rather than the string "var(...)".
  const defaultAccent =
    /:root,\s*\[data-theme=['"]rose['"]\]\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? "";
  const named = declarations(root);
  const ramp = declarations(defaultAccent);

  const resolved = new Map<string, string>();
  for (const [name, value] of named) {
    const alias = /^var\(\s*--([\w-]+)\s*\)$/.exec(value)?.[1];
    if (alias === undefined) {
      resolved.set(name, value);
      continue;
    }
    const target = ramp.get(alias) ?? named.get(alias);
    if (target === undefined) throw new Error(`--${name} aliases --${alias}, which is not defined`);
    if (target.startsWith("var(")) {
      throw new Error(`--${name} aliases --${alias}, which is itself an alias`);
    }
    resolved.set(name, target);
  }
  for (const [name, value] of ramp) if (!resolved.has(name)) resolved.set(name, value);
  return resolved;
})();

const ratio = (fg: string, bg: string): number => {
  const a = tokens.get(fg);
  const b = tokens.get(bg);
  if (a === undefined) throw new Error(`--${fg} is not defined in tokens.css`);
  if (b === undefined) throw new Error(`--${bg} is not defined in tokens.css`);
  const parsedA = parse(a);
  const parsedB = parse(b);
  if (parsedA === undefined) throw new Error(`--${fg} is not a parseable colour: ${a}`);
  if (parsedB === undefined) throw new Error(`--${bg} is not a parseable colour: ${b}`);
  return wcagContrast(parsedA, parsedB);
};

/** Reports the measured value on failure, so a regression names its own number. */
const atLeast = (fg: string, bg: string, min: number): void => {
  const measured = ratio(fg, bg);
  expect(
    measured,
    `--${fg} on --${bg} is ${measured.toFixed(2)}:1, needs ${min}:1`,
  ).toBeGreaterThanOrEqual(min);
};

describe("tokens.css parses", () => {
  test("the surfaces really are the delivered ones, not the neutrals they replaced", () => {
    // Without this, reverting the aliases leaves every ratio below passing on
    // the old palette and the migration silently undoes itself.
    expect(tokens.get("bg")).toBe(tokens.get("pa-bg-0"));
    expect(tokens.get("surface")).toBe(tokens.get("pa-bg-1"));
    expect(tokens.get("ink")).toBe(tokens.get("pa-text"));
    expect(tokens.get("primary")).toBe(tokens.get("pa-accent-500"));
    expect(tokens.get("bg")).toMatch(/^#/);
  });

  test("the colour tokens the components depend on are all present", () => {
    expect(tokens.size).toBeGreaterThan(0);
    for (const name of [...SURFACES, ...SEMANTIC, "ink", "primary", "border-control"]) {
      expect(tokens.has(name), `--${name} missing`).toBe(true);
    }
  });
});

describe("body text reaches 4.5:1", () => {
  // DESIGN.md holds placeholder text to the body requirement as well: the muted
  // grey placeholder is the most common failure and is not acceptable here.
  for (const surface of SURFACES) {
    for (const ink of ["ink", "ink-muted"] as const) {
      test(`--${ink} on --${surface}`, () => atLeast(ink, surface, 4.5));
    }
  }
});

describe("text on the primary colour reaches 4.5:1", () => {
  // This is why primary buttons carry dark labels: white on a rose of this
  // lightness does not get there.
  test("--primary-ink on --primary", () => atLeast("primary-ink", "primary", 4.5));
  test("--primary-ink on --primary-hover", () => atLeast("primary-ink", "primary-hover", 4.5));
});

describe("the focus ring reaches 3:1 against every surface it can land on", () => {
  for (const surface of SURFACES) {
    test(`--primary on --${surface}`, () => atLeast("primary", surface, 3));
  }
});

describe("badge and banner text reaches 4.5:1 on its tinted background", () => {
  // Text on a -weak background is --ink, never the matching hue. --danger on
  // --danger-weak measures 3.72:1, and the hue is not what carries the meaning
  // anyway: DESIGN.md requires an icon or a label beside every status colour.
  for (const role of SEMANTIC) {
    test(`--ink on --${role}-weak`, () => atLeast("ink", `${role}-weak`, 4.5));
  }
  test("--ink on --primary-weak", () => atLeast("ink", "primary-weak", 4.5));
});

describe("secondary text on a tinted background reaches 4.5:1 too", () => {
  // The gap this closes: --ink-muted on --primary-weak measured 3.76:1 after
  // the palette migration and nothing here noticed, because the suite paired
  // --ink-muted only with the four flat surfaces and --primary-weak only with
  // --ink. Axe caught it in the browser instead.
  //
  // A -weak surface exists to carry a label *and* its secondary line, so both
  // inks have to hold on it, not just the brightest one.
  for (const role of SEMANTIC) {
    test(`--ink-muted on --${role}-weak`, () => atLeast("ink-muted", `${role}-weak`, 4.5));
  }
  test("--ink-muted on --primary-weak", () => atLeast("ink-muted", "primary-weak", 4.5));
});

describe("a selected control's label holds on the accent wash", () => {
  // The gap this closes: the sidebar's active item used an inline
  // `color-mix(... 14%, transparent)` rather than a token, so no test here
  // could see it, and the label landed at 4.45:1 — axe found it in CI.
  //
  // Every selected state in the application now sits on --primary-weak, which
  // means this one pairing covers the sidebar, the filter chips, the queue
  // tabs, the facet chips and the settings sections at once.
  test("--pa-accent-300 on --primary-weak", () => atLeast("pa-accent-300", "primary-weak", 4.5));
});

describe("a control boundary reaches 3:1 (WCAG 1.4.11)", () => {
  // --border and --border-strong sit at 1.22:1 and 1.71:1 on --surface. They
  // separate and decorate; they cannot be the only thing identifying a control.
  for (const surface of SURFACES) {
    test(`--border-control on --${surface}`, () => atLeast("border-control", surface, 3));
  }
});

describe("a semantic colour used as an icon reaches 3:1", () => {
  for (const role of SEMANTIC) {
    test(`--${role} on --surface`, () => atLeast(role, "surface", 3));
  }
});
