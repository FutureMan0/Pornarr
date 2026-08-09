/**
 * Tooltip — a supplementary description attached to a control the caller owns.
 *
 * There is no native equivalent, so the ARIA pattern is implemented here:
 * `role="tooltip"`, wired to the trigger with `aria-describedby`, shown on
 * hover *and* on keyboard focus, and dismissible with Escape (WCAG 2.2 SC
 * 1.4.13). Hover-only would put the content out of reach of every keyboard
 * user, so both paths run through one piece of state.
 *
 * Never put essential-only information in here. DESIGN.md uses tooltips for the
 * full text of a truncated release name and for the absolute date behind a
 * relative one — in both cases the tooltip elaborates, it does not inform.
 *
 * The seven interaction states belong to the trigger, which the caller supplies
 * and styles; a tooltip is not itself interactive and must not become so.
 */
import { cloneElement, useEffect, useId, useState } from "react";
import type { HTMLAttributes, JSX, ReactElement, ReactNode } from "react";
import { cx } from "../lib/cx";
import css from "./tooltip.module.css";

export type TooltipProps = {
  readonly content: ReactNode;
  /** The trigger. It is cloned so the description can be wired onto it. */
  readonly children: ReactElement<HTMLAttributes<HTMLElement>>;
  readonly className?: string;
};

export function Tooltip({ content, children, className }: TooltipProps): JSX.Element {
  const tooltipId = useId();
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const shown = (hovered || focused) && !dismissed;

  // Escape has to reach the tooltip even when it was opened by a pointer and
  // focus is somewhere else entirely, so the listener is on the document.
  useEffect(() => {
    if (!shown) return undefined;
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") setDismissed(true);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [shown]);

  // Dismissal lasts until the pointer and focus have both left; otherwise the
  // tooltip springs back the moment React re-renders.
  useEffect(() => {
    if (!hovered && !focused) setDismissed(false);
  }, [hovered, focused]);

  const trigger = cloneElement(children, {
    // exactOptionalPropertyTypes: aria-describedby is either present or absent,
    // never explicitly undefined.
    ...(shown ? { "aria-describedby": tooltipId } : {}),
  });

  return (
    // React's onFocus/onBlur are focusin/focusout and bubble, so the wrapper
    // sees the trigger's focus without the trigger having to cooperate.
    <span
      className={cx(css.root, className)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
    >
      {trigger}
      {shown ? (
        <span role="tooltip" id={tooltipId} className={css.tooltip}>
          {content}
        </span>
      ) : null}
    </span>
  );
}
