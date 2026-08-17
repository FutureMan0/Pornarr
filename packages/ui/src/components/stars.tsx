/**
 * A rating, as five stars.
 *
 * The stars are decoration and are marked as such. What a screen reader gets is
 * the sentence — "4.5 out of 5, from 12 ratings" — because five glyphs read out
 * one at a time is noise, and "star star star half-star" is not a rating.
 *
 * Halves are drawn with a clipped overlay rather than a third glyph: it keeps
 * the geometry identical at every step and makes 4.5 look like exactly half of
 * the gap between 4 and 5.
 */
import type { CSSProperties, JSX } from "react";

import { cx } from "../lib/cx";
import styles from "./stars.module.css";

export const MAXIMUM_STARS = 5;

export interface StarsProps {
  /** 0 to 5. Null means nobody has rated it, which is not the same as zero. */
  readonly value: number | null;
  /** Included in the accessible name when present. */
  readonly count?: number;
  /** The sentence a reader hears. The caller owns it because it is translated. */
  readonly label: string;
  readonly className?: string;
}

export function Stars({ value, count, label, className }: StarsProps): JSX.Element {
  const clamped = value === null ? 0 : Math.min(MAXIMUM_STARS, Math.max(0, value));
  const style = { "--stars-fill": `${(clamped / MAXIMUM_STARS) * 100}%` } as CSSProperties;

  return (
    <span className={cx(styles.stars, className)} role="img" aria-label={label} style={style}>
      {/* Two identical rows, the filled one clipped to the score. One row of
          glyphs cannot express a half without a second glyph set. */}
      <span className={styles.track} aria-hidden="true">
        {GLYPHS}
      </span>
      <span className={styles.fill} aria-hidden="true">
        {GLYPHS}
      </span>
      {count === undefined ? null : <span className="visually-hidden">{count}</span>}
    </span>
  );
}

const Star = (): JSX.Element => (
  <svg
    viewBox="0 0 16 16"
    className={styles.star}
    fill="currentColor"
    aria-hidden="true"
    focusable="false"
  >
    <path d="M8 1.6l1.9 4 4.4.6-3.2 3 .8 4.3L8 11.5 4.1 13.5l.8-4.3-3.2-3 4.4-.6z" />
  </svg>
);

/**
 * Keyed by the star's position rather than by its index in the array. The two
 * happen to coincide here — the row is built once and never reordered — but the
 * position is the thing that identifies a star, and saying so costs nothing.
 */
const POSITIONS = Array.from({ length: MAXIMUM_STARS }, (_, index) => index + 1);

const GLYPHS = POSITIONS.map((position) => <Star key={position} />);
