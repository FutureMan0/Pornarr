import type { ReactNode } from "react";
import { cx } from "../lib/cx";
import css from "./skeleton.module.css";

export interface SkeletonTextProps {
  readonly lines?: number;
  readonly className?: string;
}

/**
 * Stand-in for a block of text. The shape is the point: as many lines as the
 * real content has, so nothing moves when it arrives.
 */
export const SkeletonText = ({ lines = 3, className }: SkeletonTextProps) => {
  const keys = Array.from({ length: Math.max(1, lines) }, (_, index) => `line-${index}`);
  return (
    <div className={cx(css.text, className)} aria-hidden="true">
      {keys.map((key) => (
        <span key={key} className={css.line} />
      ))}
    </div>
  );
};

export interface SkeletonPosterProps {
  readonly className?: string;
}

/**
 * Stand-in for a poster or video still. Holds 16:9 because DESIGN.md pins the
 * library grid's placeholder to the poster aspect — that is what keeps the grid
 * from reflowing as images load.
 */
export const SkeletonPoster = ({ className }: SkeletonPosterProps) => (
  <div className={cx(css.poster, className)} aria-hidden="true" />
);

export interface SkeletonRegionProps {
  readonly label: string;
  readonly children: ReactNode;
}

/**
 * Announces that a region is loading. The skeletons inside are aria-hidden —
 * a screen reader has nothing to gain from a shimmering rectangle — so the
 * container carries the one polite message, and the label says what is loading
 * rather than that something is.
 */
export const SkeletonRegion = ({ label, children }: SkeletonRegionProps) => (
  // <output> carries role="status" implicitly, which is the semantic element for
  // a polite live region — no explicit role needed.
  <output className={css.region} aria-busy="true">
    <span className="visually-hidden">{label}</span>
    {children}
  </output>
);
