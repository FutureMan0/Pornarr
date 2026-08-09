/**
 * Text input. A native `<input>`, styled — not a contenteditable div and not a
 * wrapper that fakes a caret.
 *
 * `error` takes a message as well as a boolean. When it is a message the input
 * is wired to it with aria-describedby, because a red border that only sighted
 * pointer users can see is not an error state.
 */
import type { ComponentPropsWithoutRef } from "react";
import { useId } from "react";
import { cx } from "../lib/cx";
import css from "./input.module.css";

export type InputProps = {
  /** The value is being fetched or saved: announces aria-busy, blocks editing. */
  loading?: boolean;
  /** `true` marks the field invalid; a string also renders and announces it. */
  error?: boolean | string;
} & ComponentPropsWithoutRef<"input">;

export const Input = ({
  loading = false,
  error = false,
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
      {message !== undefined && (
        <p id={messageId} role="alert" className={css.message}>
          {message}
        </p>
      )}
    </div>
  );
};
