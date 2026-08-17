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
      viewBox="228 197 628 628"
      width={size}
      height={size}
      className={cx(styles.mark, className)}
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id={gradient} x1="0" y1="0.7" x2="1" y2="0.3">
          <stop offset="0" stopColor="#ee0083" />
          <stop offset="0.5" stopColor="#ff14a4" />
          <stop offset="1" stopColor="#ff4fc2" />
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
      <path
        fill={`url(#${gradient})`}
        fillRule="evenodd"
        filter={`url(#${glow})`}
        d="M 349.515 268.394 L 346.991 270.918 347.626 511.709 C 348.062 677.001, 348.589 752.913, 349.307 753.816 C 350.987 755.927, 355.670 758.002, 358.700 757.978 C 370.488 757.886, 383.931 742.493, 406.103 703.699 C 431.697 658.920, 451.652 639.075, 482.921 627.305 C 496.361 622.246, 499.251 621.855, 529.500 620.993 C 559.812 620.129, 572.059 618.836, 591.805 614.415 C 676.907 595.358, 732.625 535.708, 736.884 459.094 C 742.361 360.563, 673.152 284.653, 563 268.374 C 552.937 266.887, 539.039 266.625, 451.769 266.273 L 352.039 265.870 349.515 268.394 M 459 440.032 C 459 508.869, 458.790 520.566, 457.502 523.650 C 456.678 525.622, 452.253 531.570, 447.669 536.868 C 410.985 579.264, 389.184 615.722, 375.050 658.313 C 370.785 671.163, 371.597 671.485, 376.885 659.040 C 398.646 607.827, 436.350 566.075, 476.167 549.100 C 495.209 540.981, 505.705 538.900, 537.164 537.004 C 568.857 535.093, 588.102 529.010, 607.172 514.873 C 659.959 475.740, 652.453 395.529, 593.567 369.487 C 573.900 360.789, 566.873 360.013, 507.750 360.006 L 459 360 459 440.032 Z"
      />
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
