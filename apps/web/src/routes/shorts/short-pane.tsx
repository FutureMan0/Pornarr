/**
 * One clip in the feed: the video, what it was cut from, and the rail.
 *
 * THE RAIL IS BESIDE, NOT OVER. Every short-form product stacks its actions on
 * top of the frame, which works when the frame is the whole screen and the
 * actions are three icons. This one carries a rating, a comment count and a
 * save, and those numbers have to be legible against artwork nobody controls —
 * so they sit next to the video rather than on it. At a phone width the rail
 * moves under the frame instead.
 *
 * AND SO IS THE CAPTION. It used to be drawn over the bottom of the frame under
 * a gradient, which is where the browser draws its own control bar — the title
 * landed on top of the scrub bar and the timestamp, and clicking either was a
 * coin toss. It now sits under the video inside the same box, where nothing else
 * is competing for the space and the link to the parent title is plainly a link.
 *
 * ONLY THE ACTIVE PANE MOUNTS A PLAYER. `active` is the feed's answer to which
 * clip is on screen; the rest render their poster frame and nothing else. Ten
 * `<video>` elements decoding at once is how a feed becomes a fan.
 *
 * THE RATING AND THE COUNT BELONG TO THE TITLE. That is what the API returns
 * for a short, and the label says so rather than letting a viewer think they
 * are rating forty seconds.
 */
import { Artwork, Stars } from "@pornarr/ui";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { VideoPlayer } from "../../components/player/video-player";
import { tileBlur, useArtVisible } from "../../lib/art-visibility";
import { formatDuration, seedFrom } from "../../lib/format";
import { useWatchlist } from "../media/use-watchlist";

type Clip = {
  readonly id: string;
  readonly parent_media_id: string;
  readonly media_title: string;
  readonly title: string;
  readonly start_seconds: number;
  readonly end_seconds: number;
  readonly duration_seconds: number;
  readonly average_stars: number | null;
  readonly comment_count: number;
};

export interface ShortPaneProps {
  readonly clip: Clip;
  readonly active: boolean;
}

export function ShortPane({ clip, active }: ShortPaneProps): JSX.Element {
  const { t } = useTranslation();
  const artVisible = useArtVisible();
  const { saved, busy, toggle } = useWatchlist(clip.parent_media_id);

  return (
    <article className="flex h-full w-full max-w-[52rem] flex-col items-center gap-4 px-2 py-3 sm:flex-row sm:items-end sm:justify-center">
      {/* The frame takes the height that is left over and derives its width from
          the ratio, so the caption below it never pushes the video off the pane. */}
      {/* One box at 9/16, with the video and the caption stacked inside it.
          The ratio plus a definite height is what gives the box its width — a
          caption that sits *outside* the box contributes its own text width
          instead, the box overflows it, and the rail lands on top of the video. */}
      <div className="flex aspect-[9/16] min-h-0 flex-col overflow-hidden rounded-lg bg-[var(--pa-bg-00)] sm:h-full">
        <div className="relative min-h-0 flex-1">
          {active ? (
            <VideoPlayer
              mediaId={clip.parent_media_id}
              title={clip.title}
              portrait
              clip={{ startSeconds: clip.start_seconds, endSeconds: clip.end_seconds }}
            />
          ) : (
            /* A still, not a second player. */
            <Artwork
              seed={seedFrom(clip.id)}
              blur={tileBlur(artVisible)}
              ratio="9 / 16"
              className="h-full"
            />
          )}
        </div>

        <div className="flex flex-none flex-col gap-0.5 px-3 py-2">
          <h2 className="truncate text-sm font-medium text-ink">{clip.title}</h2>
          <p className="truncate text-2xs text-ink-muted">
            {t("shorts.player.cutFrom")}{" "}
            <Link to={`/library/${clip.parent_media_id}`} className="text-ink underline">
              {clip.media_title}
            </Link>{" "}
            {t("shorts.player.at", { time: formatDuration(clip.start_seconds) })}
          </p>
        </div>
      </div>

      <div className="flex flex-none items-center gap-4 sm:w-14 sm:flex-col sm:justify-end sm:gap-3 sm:pb-3">
        <span className="flex flex-col items-center gap-0.5">
          <Stars
            value={clip.average_stars}
            label={
              clip.average_stars === null
                ? t("library.rating.none")
                : t("library.rating.value", {
                    value: clip.average_stars,
                    count: clip.comment_count,
                  })
            }
          />
          <span className="text-2xs tabular-nums text-ink-muted">
            {clip.average_stars === null ? "—" : clip.average_stars.toFixed(1)}
          </span>
        </span>

        <Link
          to={`/library/${clip.parent_media_id}`}
          className="flex flex-col items-center gap-0.5 rounded-full px-2 py-1 text-2xs text-ink-muted transition-colors hover:bg-surface-3 hover:text-ink"
        >
          <CommentIcon />
          <span className="tabular-nums">{clip.comment_count}</span>
        </Link>

        <button
          type="button"
          aria-pressed={saved}
          disabled={busy || !active}
          onClick={toggle}
          className={
            saved
              ? "flex flex-col items-center gap-0.5 rounded-full px-2 py-1 text-2xs text-[var(--pa-accent-300)]"
              : "flex flex-col items-center gap-0.5 rounded-full px-2 py-1 text-2xs text-ink-muted transition-colors hover:bg-surface-3 hover:text-ink"
          }
        >
          <BookmarkIcon filled={saved} />
          <span>{saved ? t("shorts.saved") : t("shorts.save")}</span>
        </button>

        <span className="text-2xs tabular-nums text-ink-faint">
          {formatDuration(clip.duration_seconds)}
        </span>
      </div>
    </article>
  );
}

function CommentIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 16 16"
      className="size-4"
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

function BookmarkIcon({ filled }: { readonly filled: boolean }): JSX.Element {
  return (
    <svg
      viewBox="0 0 16 16"
      className="size-4"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M4 2.5h8a.5.5 0 0 1 .5.5v10.4a.3.3 0 0 1-.47.25L8 11.2l-4.03 2.45a.3.3 0 0 1-.47-.25V3a.5.5 0 0 1 .5-.5Z" />
    </svg>
  );
}
