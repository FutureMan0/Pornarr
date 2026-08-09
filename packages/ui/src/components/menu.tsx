/**
 * Menu button — a trigger plus a list of actions.
 *
 * Unlike the dialog there is no native element to lean on, so the ARIA menu
 * button pattern is implemented here by hand: `aria-haspopup`/`aria-expanded`
 * on the trigger, `role="menu"` over `role="menuitem"` children, roving
 * tabindex, Arrow/Home/End navigation, and Escape returning focus to the
 * trigger.
 *
 * Disabled items stay focusable and carry `aria-disabled` rather than being
 * skipped. The APG recommends this: an item the user cannot reach is an item
 * they cannot discover, and "why is this greyed out" is a question they can
 * only ask about something they found.
 */
import { useEffect, useId, useRef, useState } from "react";
import type { FocusEvent, JSX, KeyboardEvent } from "react";
import { cx } from "../lib/cx";
import css from "./menu.module.css";

export type MenuItem = {
  readonly id: string;
  readonly label: string;
  readonly onSelect: () => void;
  readonly disabled?: boolean;
};

export type MenuProps = {
  /** Trigger text, and the accessible name of the menu it opens. */
  readonly label: string;
  readonly items: readonly MenuItem[];
  /** Items are replaced by a skeleton and the menu carries `aria-busy`. */
  readonly loading?: boolean;
  /** Rendered as a live alert inside the menu. */
  readonly error?: string;
  /** The trigger itself refuses input. */
  readonly disabled?: boolean;
  readonly className?: string;
};

const SKELETON_ROWS = [0, 1, 2] as const;

export function Menu({
  label,
  items,
  loading,
  error,
  disabled,
  className,
}: MenuProps): JSX.Element {
  const menuId = useId();
  const [open, setOpen] = useState(false);
  // null means "the menu itself holds focus" — the loading and error states have
  // no item to land on.
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const navigable = loading !== true && items.length > 0;

  useEffect(() => {
    if (!open) return;
    if (activeIndex === null) {
      menuRef.current?.focus();
      return;
    }
    // noUncheckedIndexedAccess: the slot may be empty on the render that opens
    // the menu, and optional chaining is the whole narrowing this needs.
    itemRefs.current[activeIndex]?.focus();
  }, [open, activeIndex]);

  const openAt = (index: number | null): void => {
    setActiveIndex(navigable ? index : null);
    setOpen(true);
  };

  const closeAndReturnFocus = (): void => {
    setOpen(false);
    setActiveIndex(null);
    triggerRef.current?.focus();
  };

  const onTriggerKeyDown = (event: KeyboardEvent<HTMLButtonElement>): void => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      openAt(0);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      openAt(items.length - 1);
    }
  };

  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    const count = items.length;
    switch (event.key) {
      case "Escape": {
        event.preventDefault();
        closeAndReturnFocus();
        break;
      }
      case "Tab": {
        // Let the browser move focus onward; the menu simply stops existing.
        setOpen(false);
        setActiveIndex(null);
        break;
      }
      case "ArrowDown": {
        event.preventDefault();
        if (!navigable) break;
        setActiveIndex((current) => (current === null ? 0 : (current + 1) % count));
        break;
      }
      case "ArrowUp": {
        event.preventDefault();
        if (!navigable) break;
        setActiveIndex((current) => (current === null ? count - 1 : (current - 1 + count) % count));
        break;
      }
      case "Home": {
        event.preventDefault();
        if (navigable) setActiveIndex(0);
        break;
      }
      case "End": {
        event.preventDefault();
        if (navigable) setActiveIndex(count - 1);
        break;
      }
      default:
        break;
    }
  };

  // Focus leaving the whole control closes it. This is what makes Tab out, and
  // clicking another control, behave the way a menu is expected to.
  const onRootBlur = (event: FocusEvent<HTMLDivElement>): void => {
    const next = event.relatedTarget;
    if (next instanceof Node && event.currentTarget.contains(next)) return;
    setOpen(false);
    setActiveIndex(null);
  };

  const select = (item: MenuItem): void => {
    if (item.disabled === true) return;
    item.onSelect();
    closeAndReturnFocus();
  };

  return (
    // React implements onBlur with focusout, so it bubbles from the trigger and
    // from every item.
    <div className={cx(css.root, className)} onBlur={onRootBlur}>
      <button
        type="button"
        ref={triggerRef}
        className={css.trigger}
        aria-haspopup="menu"
        aria-expanded={open}
        // Only while the menu exists: aria-controls pointing at nothing is worse
        // than no aria-controls.
        {...(open ? { "aria-controls": menuId } : {})}
        disabled={disabled}
        onClick={() => (open ? closeAndReturnFocus() : openAt(0))}
        onKeyDown={onTriggerKeyDown}
      >
        {label}
      </button>

      {open ? (
        <div
          id={menuId}
          ref={menuRef}
          role="menu"
          aria-label={label}
          aria-busy={loading}
          tabIndex={-1}
          className={cx(
            css.menu,
            loading === true && css.loading,
            error !== undefined && css.error,
          )}
          onKeyDown={onMenuKeyDown}
        >
          {error !== undefined ? (
            <p role="alert" className={css.errorMessage}>
              {error}
            </p>
          ) : null}

          {loading === true
            ? SKELETON_ROWS.map((row) => (
                // Skeleton matching the eventual layout, per DESIGN.md. Static:
                // reduced motion collapses the duration tokens to 1ms, which
                // would turn a pulse into a strobe.
                <span key={row} className={css.skeletonItem} aria-hidden="true" />
              ))
            : items.map((item, index) => (
                <button
                  key={item.id}
                  type="button"
                  role="menuitem"
                  ref={(node) => {
                    itemRefs.current[index] = node;
                  }}
                  // Roving tabindex: exactly one item is in the tab order.
                  tabIndex={activeIndex === index ? 0 : -1}
                  aria-disabled={item.disabled}
                  className={cx(css.item, item.disabled === true && css.disabled)}
                  onClick={() => select(item)}
                >
                  {item.label}
                </button>
              ))}
        </div>
      ) : null}
    </div>
  );
}
