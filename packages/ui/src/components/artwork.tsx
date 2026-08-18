/**
 * Placeholder artwork for a title that has no poster.
 *
 * Deliberately generated rather than a grey box. A library is scanned before it
 * is illustrated, and a wall of identical rectangles is unreadable — a stable
 * colour per title is what lets the eye find the one it saw yesterday.
 *
 * Swap this for an `<img>` once real posters exist; the geometry and the badge
 * slots are the same either way.
 */
import type { CSSProperties, JSX, ReactNode } from "react";

import { cx } from "../lib/cx";
import styles from "./artwork.module.css";

/**
 * How far a title may stray from the theme's art hue, in degrees either way.
 *
 * Wide enough to tell a grid apart, narrow enough that the page stays in the
 * accent's family. Beyond about 60° the tiles start arguing with the accent
 * rather than sitting beside it.
 */
const HUE_SPREAD = 50;

export type ArtworkBlur = "hidden" | "small" | "none";

export interface ArtworkProps {
  /**
   * Any stable number from the record — an id hash works. The same seed always
   * produces the same colour, which is the whole point.
   */
  readonly seed: number;
  readonly blur?: ArtworkBlur | undefined;
  /** CSS aspect ratio. The grid uses 16/10; a shorts rail uses 9/16. */
  readonly ratio?: string | undefined;
  /** Shown centred when the artwork is hidden — the "eye closed" affordance. */
  readonly veil?: ReactNode | undefined;
  readonly children?: ReactNode | undefined;
  readonly className?: string | undefined;
}

/** Maps any integer into the band, deterministically and without bias to one end. */
export function hueOffset(seed: number): number {
  const wrapped = ((Math.trunc(seed) % 360) + 360) % 360;
  return Math.round((wrapped / 360) * 2 * HUE_SPREAD - HUE_SPREAD);
}

export function Artwork({
  seed,
  blur = "hidden",
  ratio = "16 / 10",
  veil,
  children,
  className,
}: ArtworkProps): JSX.Element {
  const style = {
    "--art-h": `${hueOffset(seed)}deg`,
    "--art-ratio": ratio,
  } as CSSProperties;

  return (
    <div
      className={cx(
        styles.frame,
        blur === "hidden" && styles.hidden,
        blur === "small" && styles.small,
        className,
      )}
      style={style}
    >
      <div className={styles.wash} aria-hidden="true" />
      <div className={styles.scrim} aria-hidden="true" />
      {blur === "hidden" && veil !== undefined ? (
        <div className={styles.veil} aria-hidden="true">
          {veil}
        </div>
      ) : null}
      {children}
    </div>
  );
}
