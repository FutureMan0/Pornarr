/**
 * The control set, held to DESIGN.md: "Every interactive component ships
 * default, hover, focus-visible, active, disabled, loading and error. Half a
 * set is not a component."
 *
 * The seven states are proved in two halves, because they live in two places.
 * Hover, focus-visible and active are pointer and keyboard states with no DOM
 * representation — asserting them means asserting the stylesheet declares them,
 * so those tests read the CSS module off disk the way contrast.test.ts reads
 * tokens.css. Disabled, loading and error are application state, so those are
 * asserted on the rendered element: the class the component selects and the
 * ARIA it announces.
 *
 * The same on-disk read also enforces the token rule. A hardcoded duration
 * silently opts a component out of the prefers-reduced-motion override, and a
 * hardcoded colour opts it out of contrast.test.ts; neither failure is visible
 * by rendering, so both are checked as text.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";
import { Button } from "./button";
import { Checkbox } from "./checkbox";
import { Input } from "./input";
import { Select } from "./select";

// This workspace runs vitest without globals, so React Testing Library cannot
// find an `afterEach` to register its own teardown against. Unmounting by hand
// keeps one test's DOM out of the next test's queries.
afterEach(cleanup);

const MODULES = ["button", "input", "select", "checkbox"] as const;

// Resolved through node:path rather than `new URL(..., import.meta.url)`: Vite
// rewrites that form into a `?url` import, which it refuses to do for a CSS
// module. These files are read as text, not loaded as stylesheets.
const HERE = dirname(fileURLToPath(import.meta.url));

const stylesheet = (name: string): string => readFileSync(join(HERE, `${name}.module.css`), "utf8");

describe.each(MODULES)("%s.module.css declares all seven states", (name) => {
  const css = stylesheet(name);

  test("default", () => expect(css).toMatch(new RegExp(`\\.${name}\\s*\\{`)));
  test("hover", () => expect(css).toContain(":hover"));
  test("focus-visible", () => expect(css).toContain(":focus-visible"));
  test("active", () => expect(css).toContain(":active"));
  test("disabled", () => expect(css).toContain(":disabled"));
  test("loading", () => expect(css).toMatch(/\.loading[\s.:{,]/));
  test("error", () => expect(css).toMatch(/\.error[\s.:{,]/));

  test("focus is a 2px ring in --primary, never removed", () => {
    expect(css).toContain("outline: 2px solid var(--primary)");
    expect(css).toContain("outline-offset: 2px");
    expect(css).not.toContain("outline: none");
  });
});

describe.each(MODULES)("%s.module.css takes every value from tokens.css", (name) => {
  const css = stylesheet(name);

  test("no hardcoded colour", () => {
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(css).not.toMatch(/\b(?:oklch|rgba?|hsla?)\(/);
  });

  test("no hardcoded duration — that would escape the reduced-motion override", () => {
    expect(css).not.toMatch(/\d+m?s\b/);
    expect(css).toContain("var(--duration-");
    expect(css).toContain("var(--ease)");
  });

  test("no literal length beyond the 1px border and the 2px focus ring", () => {
    const literals = [...css.matchAll(/\b\d+(?:\.\d+)?px\b/g)].map(([match]) => match);
    expect(literals.filter((value) => value !== "1px" && value !== "2px")).toEqual([]);
  });

  test("the resting control boundary is --border-control", () => {
    expect(css).toContain("var(--border-control)");
  });
});

describe("Button", () => {
  test("default: enabled, no busy or invalid state announced", () => {
    render(<Button>Scan library</Button>);
    const button = screen.getByRole<HTMLButtonElement>("button", { name: "Scan library" });

    expect(button.classList.contains("button")).toBe(true);
    expect(button.classList.contains("primary")).toBe(true);
    expect(button.disabled).toBe(false);
    expect(button.getAttribute("aria-busy")).toBeNull();
    expect(button.getAttribute("aria-invalid")).toBeNull();
    // A bare <button> in a form submits it; that is never what the caller meant.
    expect(button.type).toBe("button");
  });

  test.each(["primary", "secondary", "ghost"] as const)(
    "variant %s selects its class",
    (variant) => {
      render(<Button variant={variant}>Retry</Button>);
      expect(screen.getByRole("button", { name: "Retry" }).classList.contains(variant)).toBe(true);
    },
  );

  test("disabled: refuses interaction and says so", () => {
    render(<Button disabled>Scan library</Button>);
    const button = screen.getByRole<HTMLButtonElement>("button", { name: "Scan library" });

    expect(button.classList.contains("disabled")).toBe(true);
    expect(button.disabled).toBe(true);
  });

  test("loading: aria-busy, and interaction is blocked while work is in flight", () => {
    render(<Button loading>Scan library</Button>);
    const button = screen.getByRole<HTMLButtonElement>("button", { name: "Scan library" });

    expect(button.classList.contains("loading")).toBe(true);
    expect(button.getAttribute("aria-busy")).toBe("true");
    expect(button.disabled).toBe(true);
  });

  test("error: aria-invalid, and the button stays operable so it can be retried", () => {
    render(<Button error>Scan library</Button>);
    const button = screen.getByRole<HTMLButtonElement>("button", { name: "Scan library" });

    expect(button.classList.contains("error")).toBe(true);
    expect(button.getAttribute("aria-invalid")).toBe("true");
    expect(button.disabled).toBe(false);
  });

  test("native props and a caller class pass through to the element", () => {
    render(
      <Button type="submit" className="wide" name="action" value="scan">
        Scan library
      </Button>,
    );
    const button = screen.getByRole<HTMLButtonElement>("button", { name: "Scan library" });

    expect(button.type).toBe("submit");
    expect(button.classList.contains("wide")).toBe(true);
    expect(button.name).toBe("action");
    expect(button.value).toBe("scan");
  });
});

describe("Input", () => {
  test("default: enabled, no busy or invalid state announced", () => {
    render(<Input aria-label="Root folder" defaultValue="/media" />);
    const input = screen.getByRole<HTMLInputElement>("textbox", { name: "Root folder" });

    expect(input.classList.contains("input")).toBe(true);
    expect(input.disabled).toBe(false);
    expect(input.value).toBe("/media");
    expect(input.getAttribute("aria-busy")).toBeNull();
    expect(input.getAttribute("aria-invalid")).toBeNull();
  });

  test("disabled: refuses interaction and says so", () => {
    render(<Input aria-label="Root folder" disabled />);
    const input = screen.getByRole<HTMLInputElement>("textbox", { name: "Root folder" });

    expect(input.classList.contains("disabled")).toBe(true);
    expect(input.disabled).toBe(true);
  });

  test("loading: aria-busy, editing blocked", () => {
    render(<Input aria-label="Root folder" loading />);
    const input = screen.getByRole<HTMLInputElement>("textbox", { name: "Root folder" });

    expect(input.classList.contains("loading")).toBe(true);
    expect(input.getAttribute("aria-busy")).toBe("true");
    expect(input.disabled).toBe(true);
  });

  test("error as a boolean: invalid, with no message to describe", () => {
    render(<Input aria-label="Root folder" error />);
    const input = screen.getByRole<HTMLInputElement>("textbox", { name: "Root folder" });

    expect(input.classList.contains("error")).toBe(true);
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(input.getAttribute("aria-describedby")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("error as a message: rendered, announced, and wired with aria-describedby", () => {
    render(<Input aria-label="Root folder" error="That path does not exist" />);
    const input = screen.getByRole<HTMLInputElement>("textbox", { name: "Root folder" });
    const message = screen.getByRole("alert");

    expect(input.classList.contains("error")).toBe(true);
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(message.textContent).toBe("That path does not exist");
    expect(message.id).not.toBe("");
    expect(input.getAttribute("aria-describedby")).toBe(message.id);
  });

  test("a caller's aria-describedby survives alongside the message id", () => {
    render(
      <>
        <span id="hint">Absolute path</span>
        <Input aria-label="Root folder" aria-describedby="hint" error="That path does not exist" />
      </>,
    );
    const input = screen.getByRole<HTMLInputElement>("textbox", { name: "Root folder" });
    const message = screen.getByRole("alert");

    expect(input.getAttribute("aria-describedby")).toBe(`hint ${message.id}`);
  });
});

describe("Select", () => {
  const options = (
    <>
      <option value="any">Any</option>
      <option value="2160p">2160p</option>
    </>
  );

  test("default: a native select, enabled, nothing announced", () => {
    render(
      <Select aria-label="Quality" defaultValue="2160p">
        {options}
      </Select>,
    );
    const select = screen.getByRole<HTMLSelectElement>("combobox", { name: "Quality" });

    // Native, not a role="listbox" div: the options are real <option> elements.
    expect(select.tagName).toBe("SELECT");
    expect(select.classList.contains("select")).toBe(true);
    expect(select.value).toBe("2160p");
    expect(screen.getAllByRole("option")).toHaveLength(2);
    expect(select.disabled).toBe(false);
    expect(select.getAttribute("aria-busy")).toBeNull();
    expect(select.getAttribute("aria-invalid")).toBeNull();
  });

  test("disabled: refuses interaction and says so", () => {
    render(
      <Select aria-label="Quality" disabled>
        {options}
      </Select>,
    );
    const select = screen.getByRole<HTMLSelectElement>("combobox", { name: "Quality" });

    expect(select.classList.contains("disabled")).toBe(true);
    expect(select.disabled).toBe(true);
  });

  test("loading: aria-busy while the options are being fetched", () => {
    render(
      <Select aria-label="Quality" loading>
        {options}
      </Select>,
    );
    const select = screen.getByRole<HTMLSelectElement>("combobox", { name: "Quality" });

    expect(select.classList.contains("loading")).toBe(true);
    expect(select.getAttribute("aria-busy")).toBe("true");
    expect(select.disabled).toBe(true);
  });

  test("error as a boolean: invalid, no message", () => {
    render(
      <Select aria-label="Quality" error>
        {options}
      </Select>,
    );
    const select = screen.getByRole<HTMLSelectElement>("combobox", { name: "Quality" });

    expect(select.classList.contains("error")).toBe(true);
    expect(select.getAttribute("aria-invalid")).toBe("true");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("error as a message: rendered, announced, and wired with aria-describedby", () => {
    render(
      <Select aria-label="Quality" error="Pick a quality profile">
        {options}
      </Select>,
    );
    const select = screen.getByRole<HTMLSelectElement>("combobox", { name: "Quality" });
    const message = screen.getByRole("alert");

    expect(select.getAttribute("aria-invalid")).toBe("true");
    expect(message.textContent).toBe("Pick a quality profile");
    expect(select.getAttribute("aria-describedby")).toBe(message.id);
  });
});

describe("Checkbox", () => {
  test("default: a native checkbox, labelled, enabled, nothing announced", () => {
    render(<Checkbox label="Include unmonitored" />);
    const checkbox = screen.getByRole<HTMLInputElement>("checkbox", {
      name: "Include unmonitored",
    });

    // Native, not a hidden input behind a styled span.
    expect(checkbox.tagName).toBe("INPUT");
    expect(checkbox.type).toBe("checkbox");
    expect(checkbox.classList.contains("checkbox")).toBe(true);
    expect(checkbox.checked).toBe(false);
    expect(checkbox.disabled).toBe(false);
    expect(checkbox.getAttribute("aria-busy")).toBeNull();
    expect(checkbox.getAttribute("aria-invalid")).toBeNull();
  });

  test("disabled: refuses interaction and says so", () => {
    render(<Checkbox label="Include unmonitored" disabled />);
    const checkbox = screen.getByRole<HTMLInputElement>("checkbox", {
      name: "Include unmonitored",
    });

    expect(checkbox.classList.contains("disabled")).toBe(true);
    expect(checkbox.disabled).toBe(true);
  });

  test("loading: aria-busy, toggling blocked while the value is saving", () => {
    render(<Checkbox label="Include unmonitored" loading />);
    const checkbox = screen.getByRole<HTMLInputElement>("checkbox", {
      name: "Include unmonitored",
    });

    expect(checkbox.classList.contains("loading")).toBe(true);
    expect(checkbox.getAttribute("aria-busy")).toBe("true");
    expect(checkbox.disabled).toBe(true);
  });

  test("error as a boolean: invalid, no message", () => {
    render(<Checkbox label="Include unmonitored" error />);
    const checkbox = screen.getByRole<HTMLInputElement>("checkbox", {
      name: "Include unmonitored",
    });

    expect(checkbox.classList.contains("error")).toBe(true);
    expect(checkbox.getAttribute("aria-invalid")).toBe("true");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("error as a message: rendered, announced, and wired with aria-describedby", () => {
    render(<Checkbox label="Include unmonitored" error="Choose at least one filter" />);
    const checkbox = screen.getByRole<HTMLInputElement>("checkbox", {
      name: "Include unmonitored",
    });
    const message = screen.getByRole("alert");

    expect(checkbox.getAttribute("aria-invalid")).toBe("true");
    expect(message.textContent).toBe("Choose at least one filter");
    expect(checkbox.getAttribute("aria-describedby")).toBe(message.id);
  });

  test("checked state and native props pass through", () => {
    render(<Checkbox label="Include unmonitored" name="unmonitored" defaultChecked />);
    const checkbox = screen.getByRole<HTMLInputElement>("checkbox", {
      name: "Include unmonitored",
    });

    expect(checkbox.checked).toBe(true);
    expect(checkbox.name).toBe("unmonitored");
  });
});
