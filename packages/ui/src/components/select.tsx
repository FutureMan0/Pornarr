/**
 * Select. The native `<select>`. DESIGN.md rules out reinventing a standard
 * affordance, and a div-based listbox is the canonical example: it costs a
 * thousand lines of roving-tabindex code to arrive back at what the platform
 * already does, on a native picker on touch, with type-ahead, for free.
 *
 * Options are passed as `<option>` children.
 */
import type { ComponentPropsWithoutRef } from "react";
import { useId } from "react";
import { cx } from "../lib/cx";
import css from "./select.module.css";

export type SelectProps = {
  /** Options are being fetched: announces aria-busy, blocks selection. */
  loading?: boolean;
  /** `true` marks the field invalid; a string also renders and announces it. */
  error?: boolean | string;
} & ComponentPropsWithoutRef<"select">;

export const Select = ({
  loading = false,
  error = false,
  className,
  disabled = false,
  "aria-describedby": describedBy,
  children,
  ...rest
}: SelectProps) => {
  const messageId = useId();
  const message = typeof error === "string" && error.length > 0 ? error : undefined;
  const invalid = error === true || message !== undefined;

  return (
    <div className={css.field}>
      <select
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        aria-invalid={invalid || undefined}
        aria-describedby={cx(describedBy, message !== undefined && messageId) || undefined}
        className={cx(
          css.select,
          disabled && css.disabled,
          loading && css.loading,
          invalid && css.error,
          className,
        )}
        {...rest}
      >
        {children}
      </select>
      {message !== undefined && (
        <p id={messageId} role="alert" className={css.message}>
          {message}
        </p>
      )}
    </div>
  );
};
