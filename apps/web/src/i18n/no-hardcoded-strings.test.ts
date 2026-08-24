/**
 * @vitest-environment node
 *
 * This file reads source off disk and needs no DOM. Under the jsdom environment
 * the rest of `apps/web` uses, `import.meta.url` is not a file:// URL and the
 * tree walk below cannot resolve its own directory.
 *
 * The rule "no user-facing string is written into a component" — enforced.
 *
 * There is no ESLint in this repository and Biome ships no equivalent rule, so
 * the check is a test. It parses each file with the TypeScript compiler rather
 * than scanning it with a regular expression: a regex cannot tell a sentence
 * inside JSX from the same characters inside a comment, a type argument or a
 * CSS selector, and a check with false positives is a check that gets deleted.
 *
 * WHAT COUNTS AS A VIOLATION
 *
 *   1. A JSX text node containing two or more consecutive letters. Text between
 *      tags is always rendered, so there is no such thing as a "technical" one.
 *   2. A string literal given to one of the attributes in `USER_FACING_NAMES`,
 *      or to an object property of the same name.
 *
 * WHAT IS ALLOWED, AND WHY
 *
 *   - Anything inside `{...}`. `{t("nav.library")}` is an expression, not text,
 *     and so is every other computed value.
 *   - Every other attribute: `className`, `role`, `type`, `autoComplete`, `id`,
 *     `href`, `name`. Their values are technical vocabulary, not prose, and the
 *     browser rather than the reader is the audience.
 *   - Anything with fewer than two consecutive letters: `/`, `:`, `~`, `×`. A
 *     symbol is notation and reads the same in every language.
 *   - Test files and `src/test/`. Fixtures are allowed to say things in English;
 *     they are never shipped.
 *
 * KNOWN LIMIT: a sentence assigned to an ordinary variable in a `.ts` file is
 * not caught unless the property is named like one of `USER_FACING_NAMES`. The
 * error map is covered separately, by the test that walks `ERROR_CODES`.
 */
import { readFileSync, readdirSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { describe, expect, test } from "vitest";

/**
 * Names whose value a person reads. The first four are the accessible-name and
 * tooltip attributes; `label` is added because `@pornarr/ui` uses it as the
 * accessible name of Menu, MenuItem and SkeletonRegion, which is exactly where
 * an untranslated string would hide from a visual review.
 */
const USER_FACING_NAMES: ReadonlySet<string> = new Set([
  "placeholder",
  "title",
  "aria-label",
  "alt",
  "label",
]);

/** Two consecutive letters. Below that it is punctuation or a unit symbol. */
const PROSE = /\p{L}{2,}/u;

interface HardcodedString {
  readonly file: string;
  readonly line: number;
  readonly text: string;
}

function propertyKey(node: ts.PropertyAssignment): string {
  const name = node.name;
  if (ts.isIdentifier(name) || ts.isStringLiteral(name)) return name.text;
  return "";
}

/** The check itself, as a function so the tests below can plant a violation. */
function findHardcodedStrings(fileName: string, source: string): HardcodedString[] {
  const sourceFile = ts.createSourceFile(
    fileName,
    source,
    ts.ScriptTarget.ESNext,
    /* setParentNodes */ true,
    ts.ScriptKind.TSX,
  );
  const found: HardcodedString[] = [];

  const record = (node: ts.Node, raw: string): void => {
    const text = raw.trim();
    if (!PROSE.test(text)) return;
    const { line } = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
    found.push({ file: fileName, line: line + 1, text });
  };

  const visit = (node: ts.Node): void => {
    if (ts.isJsxText(node)) {
      record(node, node.text);
    } else if (ts.isJsxAttribute(node) && USER_FACING_NAMES.has(node.name.getText(sourceFile))) {
      const value = node.initializer;
      if (value !== undefined && ts.isStringLiteral(value)) record(value, value.text);
    } else if (ts.isPropertyAssignment(node) && USER_FACING_NAMES.has(propertyKey(node))) {
      if (ts.isStringLiteral(node.initializer)) record(node.initializer, node.initializer.text);
    }
    ts.forEachChild(node, visit);
  };

  visit(sourceFile);
  return found;
}

const SRC_ROOT = fileURLToPath(new URL("..", import.meta.url));

function isChecked(relativePath: string): boolean {
  if (/\.(test|spec)\.tsx?$/.test(relativePath)) return false;
  if (relativePath.split(sep)[0] === "test") return false;
  return /\.tsx?$/.test(relativePath);
}

function sourceFiles(directory: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const full = join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...sourceFiles(full));
      continue;
    }
    if (isChecked(relative(SRC_ROOT, full))) files.push(full);
  }
  return files;
}

describe("no hardcoded user-facing strings", () => {
  test("every string in apps/web/src comes from the locale files", () => {
    const violations = sourceFiles(SRC_ROOT).flatMap((file) =>
      findHardcodedStrings(
        relative(SRC_ROOT, file).split(sep).join("/"),
        readFileSync(file, "utf8"),
      ),
    );

    // Reported as a list rather than a count: a failure has to name the file,
    // the line and the words, or nobody can act on it.
    const report = violations.map((v) => `${v.file}:${v.line}  ${JSON.stringify(v.text)}`);
    expect(report, `move these into apps/web/src/i18n/en.json:\n${report.join("\n")}`).toEqual([]);
  });

  test("it scans a meaningful number of files", () => {
    // A walk that silently finds nothing would pass the test above forever.
    expect(sourceFiles(SRC_ROOT).length).toBeGreaterThan(10);
  });
});

describe("the check itself", () => {
  test("catches a planted JSX sentence, attribute and label property", () => {
    const source = [
      "export function Broken() {",
      "  return (",
      "    <div>",
      "      <p>This screen is not built yet.</p>",
      '      <input placeholder="Search releases" />',
      '      <Menu items={[{ id: "a", label: "Sign out" }]} />',
      "    </div>",
      "  );",
      "}",
    ].join("\n");

    const found = findHardcodedStrings("broken.tsx", source);

    expect(found.map((v) => [v.line, v.text])).toEqual([
      [4, "This screen is not built yet."],
      [5, "Search releases"],
      [6, "Sign out"],
    ]);
  });

  test("allows expressions, technical attributes and bare symbols", () => {
    const source = [
      "export function Fine({ t }: { t: (k: string) => string }) {",
      "  return (",
      '    <div className="flex items-center gap-2" role="group" data-layout="rail">',
      '      <span>{t("nav.library")}</span>',
      "      <span>/</span>",
      '      <input type="search" autoComplete="username" placeholder={t("search.placeholder")} />',
      '      <img src="/poster.png" alt="" />',
      "    </div>",
      "  );",
      "}",
    ].join("\n");

    expect(findHardcodedStrings("fine.tsx", source)).toEqual([]);
  });
});
