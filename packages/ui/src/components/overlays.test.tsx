/**
 * The overlay layer: dialog, menu, tooltip.
 *
 * Three of the four things these components promise — a focus trap, an inert
 * background, a top layer — are the platform's job, delegated on purpose. What
 * is tested here is the part that is ours: that we ask the platform for them
 * (`showModal`, never `show`), that state and ARIA reach every value DESIGN.md
 * requires, and that focus goes back where it came from.
 *
 * Like contrast.test.ts, the stylesheets are read as text rather than mirrored
 * in TypeScript. A state that exists in the component but has no rule in the CSS
 * is not a state, and one source of truth is what keeps that honest.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { JSX } from "react";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";

// This workspace does not run vitest with `globals: true`, so Testing Library's
// automatic cleanup never registers itself and every render piles up in the same
// document. Unmounting explicitly is what keeps the queries unambiguous.
afterEach(cleanup);
import { Dialog } from "./dialog";
import dialogCss from "./dialog.module.css";
import { Menu } from "./menu";
import menuCss from "./menu.module.css";
import { Tooltip } from "./tooltip";

/**
 * jsdom 25.0.1 constructs `HTMLDialogElement` but implements neither
 * `showModal()` nor `close()` (both landed in jsdom 26). Without them every
 * dialog test throws before it asserts anything.
 *
 * This stands in for the missing methods in the test environment only — the
 * component is not adjusted to work around the gap. The stand-in supplies just
 * the mechanics jsdom is missing: toggle `open`, move focus inward the way
 * `showModal()` does, honour Escape via a cancellable `cancel` event, and fire
 * `close`. It deliberately does NOT restore focus on close, because that is the
 * behaviour the dialog implements itself and it has to be genuinely observable
 * rather than supplied by its own test harness.
 */
const showModalSpy = vi.fn();
const showSpy = vi.fn();

beforeAll(() => {
  const proto = HTMLDialogElement.prototype;
  // Delegate to the real implementation wherever there is one, so this stops
  // being a stand-in and becomes only a spy the day jsdom is upgraded.
  const native = {
    showModal: typeof proto.showModal === "function" ? proto.showModal : undefined,
    show: typeof proto.show === "function" ? proto.show : undefined,
    close: typeof proto.close === "function" ? proto.close : undefined,
  };
  const escapeHandlers = new WeakMap<HTMLDialogElement, (event: Event) => void>();

  proto.showModal = function showModal(this: HTMLDialogElement): void {
    showModalSpy(this);
    if (native.showModal !== undefined) {
      native.showModal.call(this);
      return;
    }
    this.open = true;
    const onKeyDown = (event: Event): void => {
      if (!(event instanceof KeyboardEvent) || event.key !== "Escape") return;
      event.preventDefault();
      const notCancelled = this.dispatchEvent(new Event("cancel", { cancelable: true }));
      if (notCancelled) this.close();
    };
    this.addEventListener("keydown", onKeyDown);
    escapeHandlers.set(this, onKeyDown);
    const first = this.querySelector<HTMLElement>("button, [href], input, select, textarea");
    first?.focus();
  };

  proto.show = function show(this: HTMLDialogElement): void {
    showSpy(this);
    if (native.show !== undefined) {
      native.show.call(this);
      return;
    }
    this.open = true;
  };

  proto.close = function close(this: HTMLDialogElement): void {
    if (native.close !== undefined) {
      native.close.call(this);
      return;
    }
    if (!this.open) return;
    this.open = false;
    const handler = escapeHandlers.get(this);
    if (handler !== undefined) {
      this.removeEventListener("keydown", handler);
      escapeHandlers.delete(this);
    }
    this.dispatchEvent(new Event("close"));
  };
});

/** A dialog behind a trigger, so focus has somewhere real to return to. */
function DialogHarness(props: {
  readonly loading?: boolean;
  readonly error?: string;
  readonly closeDisabled?: boolean;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open details
      </button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Release details"
        footer={<button type="button">Grab</button>}
        {...props}
      >
        <p>Body copy</p>
      </Dialog>
    </>
  );
}

const dialogOf = (): HTMLDialogElement => {
  const element = document.querySelector("dialog");
  if (element === null) throw new Error("no dialog rendered");
  return element;
};

describe("Dialog", () => {
  test("opens through showModal, never show, so the trap is the platform's", async () => {
    const user = userEvent.setup();
    showModalSpy.mockClear();
    showSpy.mockClear();
    render(<DialogHarness />);

    expect(dialogOf().open).toBe(false);
    await user.click(screen.getByRole("button", { name: "Open details" }));

    expect(dialogOf().open).toBe(true);
    expect(showModalSpy).toHaveBeenCalledTimes(1);
    // `show()` opens a non-modal dialog: no focus trap, no inert background, no
    // top layer. Calling it here would silently drop every guarantee.
    expect(showSpy).not.toHaveBeenCalled();
  });

  test("moves focus into the dialog on open and back to the trigger on close", async () => {
    const user = userEvent.setup();
    render(<DialogHarness />);
    const trigger = screen.getByRole("button", { name: "Open details" });

    await user.click(trigger);
    expect(dialogOf().contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBe(trigger);

    await user.click(screen.getByRole("button", { name: "Close" }));

    expect(dialogOf().open).toBe(false);
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  test("Escape closes it and hands focus back", async () => {
    const user = userEvent.setup();
    render(<DialogHarness />);
    const trigger = screen.getByRole("button", { name: "Open details" });

    await user.click(trigger);
    expect(dialogOf().open).toBe(true);

    await user.keyboard("{Escape}");

    expect(dialogOf().open).toBe(false);
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  test("a disabled close control cancels Escape too", async () => {
    const user = userEvent.setup();
    render(<DialogHarness closeDisabled />);

    await user.click(screen.getByRole("button", { name: "Open details" }));
    const close = screen.getByRole("button", { name: "Close" });
    expect((close as HTMLButtonElement).disabled).toBe(true);

    await user.keyboard("{Escape}");
    // Mid-commit means mid-commit; Escape is not a way around it.
    expect(dialogOf().open).toBe(true);
  });

  test("is labelled by its own title", async () => {
    const user = userEvent.setup();
    render(<DialogHarness />);
    await user.click(screen.getByRole("button", { name: "Open details" }));

    const labelledBy = dialogOf().getAttribute("aria-labelledby");
    expect(labelledBy).not.toBeNull();
    expect(document.getElementById(labelledBy ?? "")?.textContent).toBe("Release details");
  });

  test("loading is a skeleton carrying aria-busy, not the content", async () => {
    const user = userEvent.setup();
    render(<DialogHarness loading />);
    await user.click(screen.getByRole("button", { name: "Open details" }));

    expect(dialogOf().className).toContain(dialogCss.loading);
    expect(dialogOf().querySelector("[aria-busy='true']")).not.toBeNull();
    expect(screen.queryByText("Body copy")).toBeNull();
  });

  test("error is announced, not merely coloured", async () => {
    const user = userEvent.setup();
    render(<DialogHarness error="Indexer unreachable" />);
    await user.click(screen.getByRole("button", { name: "Open details" }));

    expect(dialogOf().className).toContain(dialogCss.error);
    expect(screen.getByRole("alert").textContent).toBe("Indexer unreachable");
  });
});

const MENU_ITEMS = [
  { id: "grab", label: "Grab release", onSelect: vi.fn() },
  { id: "block", label: "Block release", onSelect: vi.fn() },
  { id: "purge", label: "Purge", onSelect: vi.fn(), disabled: true },
];

describe("Menu", () => {
  test("the trigger declares what it opens", async () => {
    const user = userEvent.setup();
    render(<Menu label="Actions" items={MENU_ITEMS} />);
    const trigger = screen.getByRole("button", { name: "Actions" });

    expect(trigger.getAttribute("aria-haspopup")).toBe("menu");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(trigger.getAttribute("aria-controls")).toBeNull();

    await user.click(trigger);

    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    const menu = screen.getByRole("menu");
    expect(trigger.getAttribute("aria-controls")).toBe(menu.id);
    expect(screen.getAllByRole("menuitem")).toHaveLength(3);
  });

  test("opening focuses the first item and roving tabindex keeps one in the tab order", async () => {
    const user = userEvent.setup();
    render(<Menu label="Actions" items={MENU_ITEMS} />);
    await user.click(screen.getByRole("button", { name: "Actions" }));

    const items = screen.getAllByRole("menuitem");
    expect(document.activeElement).toBe(items[0]);
    expect(items.map((item) => item.getAttribute("tabindex"))).toEqual(["0", "-1", "-1"]);
  });

  test("ArrowDown, ArrowUp, Home and End move focus, wrapping at the ends", async () => {
    const user = userEvent.setup();
    render(<Menu label="Actions" items={MENU_ITEMS} />);
    await user.click(screen.getByRole("button", { name: "Actions" }));
    const items = screen.getAllByRole("menuitem");

    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(items[1]);

    await user.keyboard("{End}");
    expect(document.activeElement).toBe(items[2]);

    // Wraps rather than dead-ending, so a menu is never a place to get stuck.
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(items[0]);

    await user.keyboard("{ArrowUp}");
    expect(document.activeElement).toBe(items[2]);

    await user.keyboard("{Home}");
    expect(document.activeElement).toBe(items[0]);
    expect(items[0]?.getAttribute("tabindex")).toBe("0");
    expect(items[2]?.getAttribute("tabindex")).toBe("-1");
  });

  test("Escape closes it and hands focus back to the trigger", async () => {
    const user = userEvent.setup();
    render(<Menu label="Actions" items={MENU_ITEMS} />);
    const trigger = screen.getByRole("button", { name: "Actions" });
    await user.click(trigger);

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("menu")).toBeNull();
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(trigger);
  });

  test("ArrowDown on the trigger opens at the first item, ArrowUp at the last", async () => {
    const user = userEvent.setup();
    render(<Menu label="Actions" items={MENU_ITEMS} />);
    const trigger = screen.getByRole("button", { name: "Actions" });

    trigger.focus();
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(screen.getAllByRole("menuitem")[0]);

    await user.keyboard("{Escape}");
    await user.keyboard("{ArrowUp}");
    expect(document.activeElement).toBe(screen.getAllByRole("menuitem")[2]);
  });

  test("selecting runs the action, closes and returns focus", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<Menu label="Actions" items={[{ id: "grab", label: "Grab release", onSelect }]} />);
    const trigger = screen.getByRole("button", { name: "Actions" });

    await user.click(trigger);
    await user.click(screen.getByRole("menuitem", { name: "Grab release" }));

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  test("a disabled item stays reachable, carries aria-disabled and does nothing", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <Menu
        label="Actions"
        items={[
          { id: "grab", label: "Grab release", onSelect: vi.fn() },
          { id: "purge", label: "Purge", onSelect, disabled: true },
        ]}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Actions" }));
    const purge = screen.getByRole("menuitem", { name: "Purge" });

    expect(purge.getAttribute("aria-disabled")).toBe("true");
    expect(purge.className).toContain(menuCss.disabled);

    // Focusable on purpose: an item nobody can reach is an item nobody can find
    // out is unavailable.
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(purge);

    await user.click(purge);
    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.getByRole("menu")).not.toBeNull();
  });

  test("a disabled trigger does not open", async () => {
    const user = userEvent.setup();
    render(<Menu label="Actions" items={MENU_ITEMS} disabled />);
    const trigger = screen.getByRole("button", { name: "Actions" });

    expect((trigger as HTMLButtonElement).disabled).toBe(true);
    await user.click(trigger);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  test("loading shows a skeleton under aria-busy instead of items", async () => {
    const user = userEvent.setup();
    render(<Menu label="Actions" items={MENU_ITEMS} loading />);
    await user.click(screen.getByRole("button", { name: "Actions" }));

    const menu = screen.getByRole("menu");
    expect(menu.getAttribute("aria-busy")).toBe("true");
    expect(menu.className).toContain(menuCss.loading);
    expect(screen.queryAllByRole("menuitem")).toHaveLength(0);
    // Focus still lands somewhere inside the control, never nowhere.
    expect(document.activeElement).toBe(menu);
  });

  test("error is announced inside the menu", async () => {
    const user = userEvent.setup();
    render(<Menu label="Actions" items={MENU_ITEMS} error="Could not reach the indexer" />);
    await user.click(screen.getByRole("button", { name: "Actions" }));

    const menu = screen.getByRole("menu");
    expect(menu.className).toContain(menuCss.error);
    expect(screen.getByRole("alert").textContent).toBe("Could not reach the indexer");
  });

  test("focus leaving the control closes it", async () => {
    const user = userEvent.setup();
    render(
      <>
        <Menu label="Actions" items={MENU_ITEMS} />
        <button type="button">Elsewhere</button>
      </>,
    );
    await user.click(screen.getByRole("button", { name: "Actions" }));
    expect(screen.getByRole("menu")).not.toBeNull();

    await user.click(screen.getByRole("button", { name: "Elsewhere" }));

    await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
  });
});

describe("Tooltip", () => {
  test("appears on hover and describes its trigger", async () => {
    const user = userEvent.setup();
    render(
      <Tooltip content="Pornarr.S01E01.2160p.HEVC-GROUP">
        <button type="button">Release name</button>
      </Tooltip>,
    );
    const trigger = screen.getByRole("button", { name: "Release name" });

    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(trigger.getAttribute("aria-describedby")).toBeNull();

    await user.hover(trigger);

    const tip = screen.getByRole("tooltip");
    expect(tip.textContent).toBe("Pornarr.S01E01.2160p.HEVC-GROUP");
    expect(trigger.getAttribute("aria-describedby")).toBe(tip.id);

    await user.unhover(trigger);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  test("appears on keyboard focus, not hover alone", async () => {
    const user = userEvent.setup();
    render(
      <>
        <button type="button">Before</button>
        <Tooltip content="Absolute date: 2026-03-14">
          <button type="button">3 days ago</button>
        </Tooltip>
      </>,
    );

    screen.getByRole("button", { name: "Before" }).focus();
    expect(screen.queryByRole("tooltip")).toBeNull();

    // Tab, not hover: a pointer is not available to every user and this is the
    // path that proves it.
    await user.tab();

    expect(document.activeElement).toBe(screen.getByRole("button", { name: "3 days ago" }));
    expect(screen.getByRole("tooltip").textContent).toBe("Absolute date: 2026-03-14");

    await user.tab();
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  test("Escape dismisses it while the trigger keeps focus", async () => {
    const user = userEvent.setup();
    render(
      <Tooltip content="Absolute date: 2026-03-14">
        <button type="button">3 days ago</button>
      </Tooltip>,
    );
    const trigger = screen.getByRole("button", { name: "3 days ago" });

    await user.tab();
    expect(document.activeElement).toBe(trigger);
    expect(screen.getByRole("tooltip")).not.toBeNull();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("tooltip")).toBeNull();
    // Dismissing the description must not also take focus off the control it
    // describes.
    expect(document.activeElement).toBe(trigger);
    expect(trigger.getAttribute("aria-describedby")).toBeNull();
  });

  test("dismissal resets once hover and focus have both left", async () => {
    const user = userEvent.setup();
    render(
      <>
        <button type="button">Elsewhere</button>
        <Tooltip content="Full name">
          <button type="button">Truncated</button>
        </Tooltip>
      </>,
    );
    const trigger = screen.getByRole("button", { name: "Truncated" });

    await user.hover(trigger);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).toBeNull();

    await user.unhover(trigger);
    await user.hover(trigger);

    expect(screen.getByRole("tooltip")).not.toBeNull();
  });
});

/**
 * The stylesheets, read as text. A component that claims a state it has no rule
 * for does not have that state, and a literal value in here is a value that
 * escapes the token set — including the reduced-motion override, which works
 * only because nothing names its own duration.
 */
// Read through a plain path, not `new URL(..., import.meta.url)`: Vite rewrites
// that into an asset import, and an asset import of a CSS Module is an error.
const here = dirname(fileURLToPath(import.meta.url));

const stylesheet = (name: string): string =>
  readFileSync(join(here, `${name}.module.css`), "utf8")
    // Prose in a comment can mention 8px or 150ms; the rules cannot.
    .replace(/\/\*[\s\S]*?\*\//g, "");

const OVERLAYS = ["dialog", "menu", "tooltip"] as const;

describe("the stylesheets use tokens and nothing else", () => {
  for (const name of OVERLAYS) {
    const css = stylesheet(name);

    test(`${name}: no literal colour`, () => {
      expect(css.match(/#[0-9a-fA-F]{3,8}\b/g)).toBeNull();
      expect(css.match(/\b(?:rgba?|hsla?|oklch|oklab)\(/g)).toBeNull();
    });

    test(`${name}: no literal duration`, () => {
      expect(css.match(/\b\d+(?:\.\d+)?m?s\b/g)).toBeNull();
    });

    test(`${name}: no literal z-index`, () => {
      expect(css.match(/z-index:\s*\d/g)).toBeNull();
    });

    test(`${name}: the only literal lengths are the 1px border and 2px ring`, () => {
      const lengths = css.match(/\b\d+(?:\.\d+)?(?:px|rem|em)\b/g) ?? [];
      expect(lengths.filter((length) => length !== "1px" && length !== "2px")).toEqual([]);
    });

    test(`${name}: every transition is a duration token`, () => {
      const transitions = css.match(/transition:[^;]+;/g) ?? [];
      for (const declaration of transitions) {
        expect(declaration).toContain("var(--duration-");
      }
    });

    test(`${name}: the floating surface takes the shadow token`, () => {
      expect(css).toContain("var(--shadow-floating)");
    });
  }
});

describe("every interactive surface ships all seven states", () => {
  // Rule from DESIGN.md: "Every interactive component ships default, hover,
  // focus-visible, active, disabled, loading and error. Half a set is not a
  // component." Tooltip is exempt by construction — it is not interactive, and
  // the trigger it decorates is supplied and styled by the caller.
  for (const name of ["dialog", "menu"] as const) {
    const css = stylesheet(name);

    for (const state of [":hover", ":focus-visible", ":active", ":disabled"] as const) {
      test(`${name}: ${state}`, () => {
        expect(css).toContain(state);
      });
    }

    test(`${name}: loading and error have rules, not just props`, () => {
      expect(css).toMatch(/\.loading\b/);
      expect(css).toMatch(/\.error\b/);
    });

    test(`${name}: the focus ring is 2px of --primary, offset, never removed`, () => {
      expect(css).toContain("outline: 2px solid var(--primary)");
      expect(css).toContain("outline-offset: 2px");
      expect(css.match(/outline:\s*none/g)).toBeNull();
    });
  }
});
