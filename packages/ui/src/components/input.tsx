/**
 * Text input. A native `<input>`, styled — not a contenteditable div and not a
 * wrapper that fakes a caret.
 *
 * `error` takes a message as well as a boolean. When it is a message the input
 * is wired to it with aria-describedby, because a red border that only sighted
 * pointer users can see is not an error state.
 */
import type { ComponentPropsWithRef, ReactNode } from "react";
import { useId } from "react";
import { cx } from "../lib/cx";
import css from "./input.module.css";

/**
 * `ComponentPropsWithRef`, so a caller can hold the element.
 *
 * React 19 passes `ref` to a function component as an ordinary prop, so this
 * needs no `forwardRef` — the ref simply travels in `...rest` onto the input. The
 * global search field needs it to take focus on ⌘K.
 */
export type InputProps = {
  /** The value is being fetched or saved: announces aria-busy, blocks editing. */
  loading?: boolean;
  /** `true` marks the field invalid; a string also renders and announces it. */
  error?: boolean | string;
  /**
   * A glyph inside the field, before the text — a magnifier on a search box.
   *
   * A prop rather than something a caller overlays with `absolute`, because the
   * text has to be moved out of its way and the padding lives in this
   * component's stylesheet: a `pl-8` from outside sits in the same specificity
   * band as `.input`'s own padding and loses on source order.
   *
   * Decorative by construction. It is wrapped in `aria-hidden`, because a field
   * that needs a picture to say what it is has a labelling problem the picture
   * will not fix.
   */
  leading?: ReactNode;
  /**
   * The same, after the text — a keyboard hint, a clear button.
   *
   * Unlike `leading` this is NOT hidden and NOT click-through: the things that
   * belong on the right of a field are usually controls, and a clear button
   * behind `pointer-events: none` is a clear button that cannot be pressed. A
   * caller putting decoration here is responsible for marking it `aria-hidden`
   * itself.
   */
  trailing?: ReactNode;
} & ComponentPropsWithRef<"input">;

export const Input = ({
  loading = false,
  error = false,
  leading,
  trailing,
  className,
  disabled = false,
  "aria-describedby": describedBy,
  ...rest
}: InputProps) => {
  const messageId = useId();
  const message = typeof error === "string" && error.length > 0 ? error : undefined;
  const invalid = error === true || message !== undefined;

  return (
    <div className={css.field}>
      <div className={css.control}>
        {leading === undefined ? null : (
          <span className={css.leading} aria-hidden="true">
            {leading}
          </span>
        )}
        <input
          disabled={disabled || loading}
          aria-busy={loading || undefined}
          aria-invalid={invalid || undefined}
          aria-describedby={cx(describedBy, message !== undefined && messageId) || undefined}
          className={cx(
            css.input,
            disabled && css.disabled,
            loading && css.loading,
            invalid && css.error,
            className,
          )}
          {...rest}
        />
        {trailing === undefined ? null : <span className={css.trailing}>{trailing}</span>}
      </div>
      {message !== undefined && (
        <p id={messageId} role="alert" className={css.message}>
          {message}
        </p>
      )}
    </div>
  );
};
