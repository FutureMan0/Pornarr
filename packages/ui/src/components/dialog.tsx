/**
 * Modal dialog, built on the native `<dialog>` element.
 *
 * DESIGN.md refuses "any reinvention of a standard affordance for flavour", and
 * the platform element is not a flavour question: `showModal()` supplies a real
 * focus trap, Escape-to-close, an inert background and the top layer — four
 * things a hand-rolled div gets subtly wrong and then has to keep getting right
 * forever. This component's entire job is to keep that element in sync with an
 * `open` prop and to put focus back where it came from.
 *
 * DESIGN.md also says modal is never the first answer. Exhaust inline and
 * progressive alternatives before reaching for this.
 */
import { useEffect, useId, useRef } from "react";
import type { JSX, ReactNode } from "react";
import { cx } from "../lib/cx";
import css from "./dialog.module.css";

export type DialogProps = {
  /** Controlled: the element is opened and closed to match this. */
  readonly open: boolean;
  /** Fired once the element has closed, however it closed. */
  readonly onClose: () => void;
  readonly title: string;
  readonly children: ReactNode;
  readonly footer?: ReactNode;
  /** Body becomes a skeleton and carries `aria-busy`. */
  readonly loading?: boolean;
  /** Rendered as a live alert above the body. */
  readonly error?: string;
  /** The close control refuses input, and Escape is cancelled with it. */
  readonly closeDisabled?: boolean;
  readonly className?: string;
};

export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  loading,
  error,
  closeDisabled,
  className,
}: DialogProps): JSX.Element {
  const titleId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  // Declared before the open/close effect so the listeners exist by the time a
  // dialog that mounts already open dispatches anything.
  useEffect(() => {
    const element = dialogRef.current;
    if (element === null) return undefined;

    const handleClose = (): void => {
      // Native `showModal()` restores focus to the previously focused element,
      // but only while that element is still connected — and a React tree that
      // re-renders the trigger during the dialog's life breaks that. Restoring
      // explicitly is idempotent (focusing the already-focused element is a
      // no-op) and is the only version of this we can actually rely on.
      const trigger = returnFocusRef.current;
      returnFocusRef.current = null;
      if (trigger?.isConnected === true) trigger.focus();
      onClose();
    };
    const handleCancel = (event: Event): void => {
      // A dialog whose close control is disabled is mid-commit; Escape must not
      // be a way around that.
      if (closeDisabled === true) event.preventDefault();
    };

    element.addEventListener("close", handleClose);
    element.addEventListener("cancel", handleCancel);
    return () => {
      element.removeEventListener("close", handleClose);
      element.removeEventListener("cancel", handleCancel);
    };
  }, [onClose, closeDisabled]);

  useEffect(() => {
    const element = dialogRef.current;
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
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      className={cx(
        css.dialog,
        loading === true && css.loading,
        error !== undefined && css.error,
        className,
      )}
    >
      <div className={css.header}>
        <h2 id={titleId} className={css.title}>
          {title}
        </h2>
        <button
          type="button"
          className={css.close}
          disabled={closeDisabled}
          // The element closes itself so that every exit — button, Escape,
          // programmatic — runs through the same `close` event.
          onClick={() => dialogRef.current?.close()}
        >
          <span aria-hidden="true">&times;</span>
          <span className={css.srOnly}>Close</span>
        </button>
      </div>

      {error !== undefined ? (
        <p role="alert" className={css.errorMessage}>
          {error}
        </p>
      ) : null}

      <div className={css.body} aria-busy={loading}>
        {loading === true ? (
          // DESIGN.md: loading is a skeleton matching the eventual layout, never
          // a spinner. Deliberately not animated — the duration tokens collapse
          // to 1ms under reduced motion, which turns a pulse into a strobe.
          <div className={css.skeleton} aria-hidden="true">
            <span className={css.skeletonLine} />
            <span className={css.skeletonLine} />
            <span className={css.skeletonLine} />
          </div>
        ) : (
          children
        )}
      </div>

      {footer !== undefined ? <footer className={css.footer}>{footer}</footer> : null}
    </dialog>
  );
}
