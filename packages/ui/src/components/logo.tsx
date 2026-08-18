/**
 * The brand mark, and the lockup that pairs it with the name.
 *
 * INLINE, NOT AN IMAGE. The mark has to take the accent with it — the token set
 * ships `--pa-logo-filter`, which is `none` in rose and a hue rotation in amber
 * — and it has to hold its own on a 56px rail. An `<img>` would do both, but it
 * is also a second request before the first paint of a shell that is otherwise
 * one bundle.
 *
 * THE WORD IS TYPE, NOT ARTWORK. `brand/logo-text.svg` sets "Pornarr" as an SVG
 * `<text>` in Segoe UI, so it renders in a different face on every platform
 * that does not have it — Helvetica on macOS, something else again on Linux.
 * Rendering it as real text in the interface's own family is both steadier and
 * honest about what it is: a wordmark, not a drawn logotype.
 *
 * THE IDS ARE GENERATED. A gradient referenced by `url(#pink)` is global to the
 * document; two marks on one page and the second silently wins. `useId` is what
 * makes the sidebar and the login screen able to coexist.
 */
import type { JSX } from "react";
import { useId } from "react";

import { cx } from "../lib/cx";
import { MARK_GRADIENT, MARK_PATH, MARK_VIEW_BOX } from "../lib/mark";
import styles from "./logo.module.css";

export interface LogoProps {
  /** Rendered size in pixels. The rail uses 22, the sidebar 24. */
  readonly size?: number | undefined;
  readonly className?: string | undefined;
}

/**
 * The mark alone. Decorative by default — wherever it appears the product name
 * is either beside it or in the page title, so a second announcement of it is
 * noise.
 */
export function LogoMark({ size = 24, className }: LogoProps): JSX.Element {
  const id = useId();
  const gradient = `${id}-fill`;
  const glow = `${id}-glow`;

  return (
    <svg
      viewBox={MARK_VIEW_BOX}
      width={size}
      height={size}
      className={cx(styles.mark, className)}
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id={gradient} x1="0" y1="0.7" x2="1" y2="0.3">
          {MARK_GRADIENT.map((colour, index) => (
            <stop key={colour} offset={index / (MARK_GRADIENT.length - 1)} stopColor={colour} />
          ))}
        </linearGradient>
        <filter id={glow} x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur in="SourceGraphic" stdDeviation="14" result="blur" />
          <feFlood floodColor="#ff2da8" floodOpacity="0.35" result="color" />
          <feComposite in="color" in2="blur" operator="in" result="softglow" />
          <feMerge>
            <feMergeNode in="softglow" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      <path fill={`url(#${gradient})`} fillRule="evenodd" filter={`url(#${glow})`} d={MARK_PATH} />
    </svg>
  );
}

export interface WordmarkProps extends LogoProps {
  /** The product's name. Passed in, because this package holds no copy. */
  readonly name: string;
}

/** The mark and the name together, as the sidebar and the login screen use it. */
export function Logo({ name, size = 24, className }: WordmarkProps): JSX.Element {
  return (
    <span className={cx(styles.lockup, className)}>
      <LogoMark size={size} />
      <span className={styles.word}>{name}</span>
    </span>
  );
}
