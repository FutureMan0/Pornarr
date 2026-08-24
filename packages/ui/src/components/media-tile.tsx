/**
 * One title in a grid.
 *
 * The atom the library, the search results, the collections and the feed all
 * repeat, so everything about it is a prop rather than a variant: the caller
 * supplies already-formatted strings and an accessible rating sentence, and the
 * tile decides nothing about locale or units.
 *
 * Counters are omitted when they are zero rather than shown as "0". A grid of
 * zeroes is a grid the eye has to read and discard.
 *
 * `poster` is how a real image gets in. The generated placeholder is the
 * fallback for a title that has none — the mockups are all placeholder because
 * they have no library behind them, which is not a reason to throw away artwork
 * the scanner already produced.
 */
import type { CSSProperties, JSX, ReactNode } from "react";

import { cx } from "../lib/cx";
import { Artwork, type ArtworkBlur } from "./artwork";
import styles from "./media-tile.module.css";
import { Stars } from "./stars";

export interface MediaTileProps {
  readonly title: string;
  /** Studio, performer, or whatever the screen considers the subtitle. */
  readonly meta?: string | undefined;
  /** Pre-formatted. The tile does not know what a duration looks like here. */
  readonly duration?: string | undefined;
  readonly resolution?: string | undefined;
  /** 0 to 1. Anything above zero draws the resume bar. */
  readonly progress?: number | undefined;
  readonly rating?: number | null | undefined;
  /**
   * The sentence a screen reader hears for the rating.
   *
   * Omit it to leave the rating off the tile altogether — for a row where the
   * rating is not the point and where the source has none to give.
   */
  readonly ratingLabel?: string | undefined;
  readonly tagCount?: number | undefined;
  readonly commentCount?: number | undefined;
  /** Any stable number from the record; drives the placeholder colour. */
  readonly seed: number;
  readonly blur?: ArtworkBlur | undefined;
  /**
   * CSS aspect ratio for the artwork. A clip is 9/16 and a title is 16/10, and
   * the shape is the fastest thing telling a reader which of the two they are
   * looking at — faster than a duration badge and faster than reading a title.
   */
  readonly ratio?: string | undefined;
  /** A real image for this title. Falls back to the generated placeholder. */
  readonly poster?: ReactNode | undefined;
  readonly veil?: ReactNode | undefined;
  /** Wraps the whole tile — usually a link to the detail screen. */
  readonly action?: ((content: ReactNode) => ReactNode) | undefined;
  readonly className?: string | undefined;
}

export function MediaTile({
  title,
  meta,
  duration,
  resolution,
  progress = 0,
  rating = null,
  ratingLabel,
  tagCount = 0,
  commentCount = 0,
  seed,
  blur = "hidden",
  ratio,
  poster,
  veil,
  action,
  className,
}: MediaTileProps): JSX.Element {
  const overlays = (
    <>
      {resolution === undefined ? null : (
        <span className={cx("text-2xs", styles.badge, styles.resolution)}>{resolution}</span>
      )}
      {duration === undefined ? null : (
        <span className={cx("text-2xs", styles.badge, styles.duration)}>{duration}</span>
      )}
      {progress > 0 ? (
        <span className={styles.progress} aria-hidden="true">
          <span
            className={styles.progressValue}
            style={{ width: `${Math.min(100, Math.max(0, progress * 100))}%` }}
          />
        </span>
      ) : null}
    </>
  );

  const content = (
    <>
      {poster === undefined ? (
        <Artwork seed={seed} blur={blur} veil={veil} ratio={ratio} className={styles.art}>
          {overlays}
        </Artwork>
      ) : (
        <div
          className={cx(styles.art, styles.frame, blur === "hidden" && styles.blurred)}
          style={ratio === undefined ? undefined : ({ "--art-ratio": ratio } as CSSProperties)}
        >
          {poster}
          <span className={styles.scrim} aria-hidden="true" />
          {overlays}
        </div>
      )}

      <div className={styles.body}>
        <span className={cx("text-sm", styles.title)}>{title}</span>

        <span className={cx("text-2xs", styles.line)}>
          <span className={styles.studio}>{meta ?? ""}</span>
          {tagCount > 0 ? (
            <span className={cx(styles.counter, styles.tags)}>
              <TagIcon />
              {tagCount}
            </span>
          ) : null}
        </span>

        {/* Omitting `ratingLabel` drops the stars entirely, which is a different
            statement from `rating={null}`: null means "nobody has rated this",
            and no label means "this tile is not about ratings". The continue-
            watching row is the second case — five grey stars and a dash on every
            tile, five times over, for a question nobody asked. */}
        {ratingLabel === undefined && commentCount === 0 ? null : (
          <span className={cx("text-2xs", styles.line)}>
            {ratingLabel === undefined ? null : (
              <>
                <Stars value={rating} label={ratingLabel} />
                <span className="tabular-nums">{rating === null ? "—" : rating.toFixed(1)}</span>
              </>
            )}
            {commentCount > 0 ? (
              <span className={styles.counter}>
                <CommentIcon />
                {commentCount}
              </span>
            ) : null}
          </span>
        )}
      </div>
    </>
  );

  const tile = <article className={cx(styles.tile, className)}>{content}</article>;
  return action === undefined ? tile : <>{action(tile)}</>;
}

function TagIcon(): JSX.Element {
  return (
    <svg
      className={styles.icon}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M2.5 7.3V3a.5.5 0 0 1 .5-.5h4.3a1 1 0 0 1 .7.3l5.2 5.2a1 1 0 0 1 0 1.4l-4.3 4.3a1 1 0 0 1-1.4 0L2.8 8.5a1 1 0 0 1-.3-.7Z" />
      <circle cx="5.5" cy="5.5" r="0.75" fill="currentColor" />
    </svg>
  );
}

function CommentIcon(): JSX.Element {
  return (
    <svg
      className={styles.icon}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M13.5 8.5a5 5 0 0 1-5 5H3.5l1.2-2A5 5 0 1 1 13.5 8.5Z" />
    </svg>
  );
}
