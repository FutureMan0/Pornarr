/**
 * Checkbox. A native `<input type="checkbox">` inside its own `<label>`, so the
 * label text is part of the hit area without an htmlFor/id dance.
 *
 * There is no hidden input behind a styled span here. `accent-color` tints the
 * real control, which keeps indeterminate state, forced-colors mode and the
 * platform's own checkmark rendering working.
 */
import type { ComponentPropsWithoutRef } from "react";
import { useId } from "react";
import { cx } from "../lib/cx";
import css from "./checkbox.module.css";

export type CheckboxProps = {
  /** Required: an unlabelled checkbox is an unanswerable question. */
  label: string;
  /** The value is being saved: announces aria-busy, blocks toggling. */
  loading?: boolean;
  /** `true` marks the field invalid; a string also renders and announces it. */
  error?: boolean | string;
} & Omit<ComponentPropsWithoutRef<"input">, "type">;

export const Checkbox = ({
  label,
  loading = false,
  error = false,
  className,
  disabled = false,
  "aria-describedby": describedBy,
  ...rest
}: CheckboxProps) => {
  const messageId = useId();
  const message = typeof error === "string" && error.length > 0 ? error : undefined;
  const invalid = error === true || message !== undefined;

  return (
    <div className={css.field}>
      <label className={css.control}>
        <input
          type="checkbox"
          disabled={disabled || loading}
          aria-busy={loading || undefined}
          aria-invalid={invalid || undefined}
          aria-describedby={cx(describedBy, message !== undefined && messageId) || undefined}
          className={cx(
            css.checkbox,
            disabled && css.disabled,
            loading && css.loading,
            invalid && css.error,
            className,
          )}
          {...rest}
        />
        <span className={css.label}>{label}</span>
      </label>
      {message !== undefined && (
        <p id={messageId} role="alert" className={css.message}>
          {message}
        </p>
      )}
    </div>
  );
};
