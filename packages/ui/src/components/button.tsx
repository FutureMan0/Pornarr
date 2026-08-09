/**
 * Button. A native `<button>` and nothing else — DESIGN.md rules out any
 * reinvention of a standard affordance, so there is no role="button" div here
 * and no custom focus handling.
 *
 * Ships all seven states DESIGN.md requires: default, hover, focus-visible,
 * active, disabled, loading and error. Hover, focus and active are CSS
 * pseudo-classes; loading and error are props, because they are application
 * state rather than pointer state.
 */
import type { ComponentPropsWithoutRef } from "react";
import { cx } from "../lib/cx";
import css from "./button.module.css";

export type ButtonProps = {
  variant?: "primary" | "secondary" | "ghost";
  /** Work is in flight: announces aria-busy and refuses further interaction. */
  loading?: boolean;
  /** The action this button performs is in an invalid state. */
  error?: boolean;
} & ComponentPropsWithoutRef<"button">;

export const Button = ({
  variant = "primary",
  loading = false,
  error = false,
  // Defaulted, because a bare <button> inside a form submits it, and that has
  // never once been what the caller meant.
  type = "button",
  className,
  disabled = false,
  children,
  ...rest
}: ButtonProps) => (
  <button
    type={type}
    disabled={disabled || loading}
    aria-busy={loading || undefined}
    aria-invalid={error || undefined}
    className={cx(
      css.button,
      css[variant],
      disabled && css.disabled,
      loading && css.loading,
      error && css.error,
      className,
    )}
    {...rest}
  >
    {children}
  </button>
);
