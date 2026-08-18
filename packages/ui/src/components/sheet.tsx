/**
 * A sheet that comes up from the bottom of the screen.
 *
 * The phone's answer to a dialog. C3 of the delivered design is one — a grab
 * handle, a title, a clear action, and a primary control across the bottom — and
 * the mobile navigation needs the same thing for the destinations that do not
 * fit in four tabs.
 *
 * SAME ELEMENT AS `Dialog`, DELIBERATELY. `showModal()` supplies a focus trap,
 * Escape, an inert background and the top layer; a hand-rolled overlay gets all
 * four subtly wrong and then has to keep getting them right forever. What differs
 * here is the geometry and the fact that the backdrop is a way out — on a phone,
 * tapping the dimmed area behind a sheet is how everybody closes one.
 *
 * A grab handle is drawn but nothing drags. A handle that looks draggable and is
 * not is a small lie; the alternative is a pointer-event dance that fights the
 * scrolling of the sheet's own content, and every exit — backdrop, Escape, the
 * primary button — is already one tap away. If dragging is ever wanted it belongs
 * here, behind the same element.
 */
import { useEffect, useId, useRef } from "react";
import type { JSX, ReactNode } from "react";
import { cx } from "../lib/cx";
import css from "./sheet.module.css";

export type SheetProps = {
  /** Controlled: the element is opened and closed to match this. */
  readonly open: boolean;
  /** Fired once the sheet has closed, however it closed. */
  readonly onClose: () => void;
  readonly title: string;
  /** A secondary action beside the title — "Clear all" in the filter sheet. */
  readonly action?: ReactNode;
  readonly children: ReactNode;
  /** Pinned below the scrolling body, where a thumb can reach it. */
  readonly footer?: ReactNode;
  readonly className?: string;
};

export function Sheet({
  open,
  onClose,
  title,
  action,
  children,
  footer,
  className,
}: SheetProps): JSX.Element {
  const titleId = useId();
  const sheetRef = useRef<HTMLDialogElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const element = sheetRef.current;
    if (element === null) return undefined;

    const handleClose = (): void => {
      // The platform restores focus only while the trigger is still connected,
      // and a React tree that re-renders during the sheet's life breaks that.
      // Restoring explicitly is idempotent.
      const trigger = returnFocusRef.current;
      returnFocusRef.current = null;
      if (trigger?.isConnected === true) trigger.focus();
      onClose();
    };

    /**
     * The backdrop is a way out, which on a phone is how everybody closes a
     * sheet.
     *
     * A native listener rather than an `onClick` prop, for the same reason
     * `close` is one: every behaviour of this element is wired in one place. A
     * click reaches the dialog element itself only when it lands outside the
     * panel — the panel is what stops it — so this is the whole of backdrop
     * dismissal, with no second transparent layer to reason about. The keyboard
     * equivalent is Escape, which the element already fires.
     */
    const handleClick = (event: MouseEvent): void => {
      if (event.target === element) element.close();
    };

    element.addEventListener("close", handleClose);
    element.addEventListener("click", handleClick);
    return () => {
      element.removeEventListener("close", handleClose);
      element.removeEventListener("click", handleClick);
    };
  }, [onClose]);

  useEffect(() => {
    const element = sheetRef.current;
    if (element === null) return;
    if (open) {
      if (element.open) return;
      const active = document.activeElement;
      returnFocusRef.current = active instanceof HTMLElement ? active : null;
      element.showModal();
      return;
    }
    if (element.open) element.close();
  }, [open]);

  return (
    <dialog ref={sheetRef} aria-labelledby={titleId} className={cx(css.sheet, className)}>
      <div className={css.panel}>
        <span className={css.handle} aria-hidden="true" />

        <div className={css.header}>
          <h2 id={titleId} className={css.title}>
            {title}
          </h2>
          {action === undefined ? null : <span className={css.action}>{action}</span>}
        </div>

        <div className={css.body}>{children}</div>

        {footer === undefined ? null : <div className={css.footer}>{footer}</div>}
      </div>
    </dialog>
  );
}
