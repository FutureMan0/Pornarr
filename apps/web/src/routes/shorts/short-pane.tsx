/**
 * One clip in the feed: the video, what it was cut from, the rail, and — on a
 * wide screen — the comments beside it.
 *
 * THE FRAME IS THE SCREEN. Everything else is drawn on top of it: the caption
 * bottom-left, the rail bottom-right, the sound control top-right. That is the
 * shape of every short-form product and it is what the owner asked for. It costs
 * something, and the cost is paid rather than ignored — text over artwork nobody
 * controls is text whose contrast nobody controls, so both overlays sit on a
 * gradient scrim dark enough to make the ratio deterministic regardless of the
 * frame beneath.
 *
 * This is a reversal. An earlier version put the rail beside the video and the
 * caption under it, on the argument that legibility beats imitation. The
 * argument was sound and the result did not read as short-form at all; the scrim
 * is what lets both be true.
 *
 * COMMENTS TO THE RIGHT, ON A WIDE SCREEN. Which is what TikTok's desktop web
 * does, and what a 1440-pixel window has room for beside a 9/16 frame. Below
 * that width there is no room for a column, so the comment button opens them in
 * a dialog instead — the same panel, a different container.
 *
 * AND ON A PHONE THE FRAME IS THE SCREEN, edge to edge. It used to be a rounded
 * card with padding around it inside a padded page, which is a video player on a
 * page and the opposite of what this is. A phone is not 9/16 either — 390×844 is
 * nearer 9/19.5 — so the picture is cropped to fill rather than fitted, because
 * a fitted clip leaves bands top and bottom and gives the whole illusion away.
 *
 * THE RATING AND THE COUNT BELONG TO THE TITLE. That is what the API returns for
 * a short, and the label says so rather than letting a viewer think they are
 * rating forty seconds.
 */
import { Artwork, Dialog } from "@pornarr/ui";
import type { JSX } from "react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import type { PlayerHandle } from "../../components/player/video-player";
import { VideoPlayer } from "../../components/player/video-player";
import { tileBlur, useArtVisible } from "../../lib/art-visibility";
import { formatDuration, seedFrom } from "../../lib/format";
import { CommentsPanel } from "../media/comments-panel";
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
  /** The comment column has room. Below this the button opens a dialog. */
  readonly wide: boolean;
  /**
   * Fill the screen rather than sit on it.
   *
   * On a phone the frame is the viewport: no padding around it, no rounded
   * corners, and the picture cropped to fill rather than fitted inside a 9/16
   * box. A phone is not 9/16 — 390×844 is nearer 9/19.5 — so honouring the ratio
   * would leave bands above and below and make the feed a video player on a page.
   */
  readonly phone: boolean;
}

export function ShortPane({ clip, active, wide, phone }: ShortPaneProps): JSX.Element {
  const { t } = useTranslation();
  const artVisible = useArtVisible();
  const { saved, busy, toggle } = useWatchlist(clip.parent_media_id);
  const [commentsOpen, setCommentsOpen] = useState(false);
  const player = useRef<PlayerHandle | null>(null);

  /**
   * Space pauses, from anywhere on the screen.
   *
   * The hint at the top of the feed has promised this since before the feed had
   * a player of its own, and it was only ever true while the video itself held
   * focus. It matters more now: there is a comment box beside the video, so the
   * handler has to refuse a keystroke that belongs to something being typed
   * into — otherwise writing a comment pauses the clip a word at a time.
   */
  useEffect(() => {
    if (!active) return;
    const onKey = (event: KeyboardEvent): void => {
      if (event.key !== " " && event.key !== "Spacebar") return;
      const target = event.target as HTMLElement | null;
      if (target !== null) {
        const tag = target.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || target.isContentEditable) return;
        // A button under focus already answers Space by activating; stealing it
        // here would fire both.
        if (tag === "BUTTON" || tag === "A") return;
      }
      event.preventDefault();
      player.current?.toggle();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);

  return (
    <article
      className={
        phone
          ? "flex h-full w-full"
          : "flex h-full w-full items-center justify-center gap-4 px-2 py-3"
      }
    >
      <div
        className={
          phone
            ? "relative h-full w-full overflow-hidden bg-[var(--pa-bg-00)]"
            : "relative flex aspect-[9/16] h-full min-h-0 flex-none overflow-hidden rounded-xl bg-[var(--pa-bg-00)]"
        }
      >
        {active ? (
          <VideoPlayer
            mediaId={clip.parent_media_id}
            title={clip.title}
            portrait
            chrome="minimal"
            loop
            handleRef={player}
            clip={{ startSeconds: clip.start_seconds, endSeconds: clip.end_seconds }}
          />
        ) : (
          /* A still, not a second player. On a phone it fills the frame for the
             same reason the video does: the screen is taller than 9/16, and a
             placeholder that honours the ratio leaves bands the video would not. */
          <Artwork
            seed={seedFrom(clip.id)}
            blur={tileBlur(artVisible)}
            ratio={phone ? "auto" : "9 / 16"}
            className="h-full w-full"
          />
        )}

        {/* The scrim, and the caption on it. `pointer-events-none` on the
            gradient so that pausing still works everywhere the text is not, and
            `auto` back on the link, which is the one thing here to click. */}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-col gap-1 bg-gradient-to-t from-[color-mix(in_oklch,var(--pa-bg-00)_92%,transparent)] via-[color-mix(in_oklch,var(--pa-bg-00)_55%,transparent)] to-transparent px-4 pb-4 pt-16">
          <h2 className="line-clamp-2 pr-16 text-sm font-medium text-ink">{clip.title}</h2>
          <p className="pointer-events-auto pr-16 text-2xs text-ink-muted">
            {t("shorts.player.cutFrom")}{" "}
            <Link to={`/library/${clip.parent_media_id}`} className="text-ink underline">
              {clip.media_title}
            </Link>{" "}
            {t("shorts.player.at", { time: formatDuration(clip.start_seconds) })}
            {" · "}
            <span className="tabular-nums">{formatDuration(clip.duration_seconds)}</span>
          </p>
        </div>

        {/* The rail.
            
            One rhythm, three items: a disc, a glyph, a number under it. The first
            version mixed metaphors — a five-star rating widget shrunk to
            thumbnail size, sitting next to icon buttons, all on one grey slab —
            and read as three unrelated controls that happened to be stacked.
            
            Separate discs rather than one container: a slab says "panel", and
            these are buttons. Each disc is its own translucent ground, which is
            also what makes the contrast deterministic over artwork nobody
            controls.
            
            The rating is one star and a number, not five stars. Five glyphs at
            this size are five smudges; the number is the fact, and the full
            sentence is on the label for anyone who needs it read out. */}
        <div className="absolute bottom-4 right-3 flex flex-col items-center gap-3">
          <RailItem
            label={
              clip.average_stars === null
                ? t("library.rating.none")
                : t("library.rating.value", {
                    value: clip.average_stars,
                    count: clip.comment_count,
                  })
            }
            value={clip.average_stars === null ? "—" : clip.average_stars.toFixed(1)}
            icon={<StarIcon />}
            tone={clip.average_stars === null ? "plain" : "accent"}
          />

          {/* A control only where it does something. On a wide screen the
              comments are already beside the video, and on an inactive pane the
              dialog is not mounted — a button that answers neither is a button
              that lies about being one. */}
          <RailItem
            label={t("shorts.comments")}
            value={String(clip.comment_count)}
            icon={<CommentIcon />}
            {...(wide || !active ? {} : { onClick: () => setCommentsOpen(true) })}
          />

          <RailItem
            label={saved ? t("shorts.saved") : t("shorts.save")}
            value={saved ? t("shorts.saved") : t("shorts.save")}
            icon={<BookmarkIcon filled={saved} />}
            tone={saved ? "accent" : "plain"}
            pressed={saved}
            {...(busy || !active ? {} : { onClick: toggle })}
          />
        </div>
      </div>

      {/* Only the pane in view builds its comment column. Ten mounted panels is
          ten requests for comments nobody is reading. */}
      {wide ? (
        <div className="flex h-full min-h-0 w-[22rem] flex-none flex-col rounded-xl bg-surface-2 p-4">
          {active ? <CommentsPanel mediaId={clip.parent_media_id} layout="column" /> : null}
        </div>
      ) : null}

      {/* Only the active pane, and only once opened. A Dialog renders its
          children whether or not it is open, so one per pane meant eight mounted
          comment panels and eight requests for clips nobody was looking at —
          which is the same waste the column above guards against, arrived at from
          the other direction. */}
      {wide || !active ? null : (
        <Dialog
          open={commentsOpen}
          onClose={() => setCommentsOpen(false)}
          title={t("shorts.comments")}
        >
          {commentsOpen ? (
            <CommentsPanel mediaId={clip.parent_media_id} layout="column" headingVisible={false} />
          ) : null}
        </Dialog>
      )}
    </article>
  );
}

/**
 * One item in the rail: a disc, a glyph, a number.
 *
 * A button when it has something to do and a plain span when it does not, so the
 * accessibility tree never offers a control that answers nothing. The label is
 * the accessible name in both cases; the value under the disc is the short form
 * a reader scans.
 */
function RailItem({
  label,
  value,
  icon,
  tone = "plain",
  pressed,
  onClick,
}: {
  readonly label: string;
  readonly value: string;
  readonly icon: JSX.Element;
  readonly tone?: "plain" | "accent";
  readonly pressed?: boolean;
  readonly onClick?: () => void;
}): JSX.Element {
  const disc = [
    "grid size-11 place-items-center rounded-full",
    // Its own ground, so the glyph holds against any frame beneath it.
    "bg-[color-mix(in_oklch,var(--pa-bg-00)_62%,transparent)]",
    "backdrop-blur-sm",
    // `--primary` is what a filled star and a saved bookmark are everywhere
    // else. The rail used a lighter step of the ramp, which made one fact wear
    // two colours depending on where you met it.
    tone === "accent" ? "text-[var(--primary)]" : "text-ink",
    onClick === undefined
      ? ""
      : "transition-[background-color,scale] duration-[var(--duration-fast)] ease-out hover:bg-[color-mix(in_oklch,var(--pa-bg-00)_82%,transparent)] hover:scale-[var(--hover-scale)] active:scale-100",
  ].join(" ");

  const body = (
    <>
      <span className={disc} aria-hidden="true">
        {icon}
      </span>
      <span className="text-2xs font-medium tabular-nums text-ink drop-shadow-[0_1px_2px_rgba(0,0,0,0.8)]">
        {value}
      </span>
    </>
  );

  const stack = "flex w-14 flex-col items-center gap-1.5";

  if (onClick === undefined) {
    return (
      <span className={stack} aria-label={label} role="img">
        {body}
      </span>
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      {...(pressed === undefined ? {} : { "aria-pressed": pressed })}
      className={`${stack} rounded-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--primary)]`}
    >
      {body}
    </button>
  );
}

function StarIcon(): JSX.Element {
  return (
    <svg viewBox="0 0 24 24" className="size-5" fill="currentColor" aria-hidden="true">
      <path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" />
    </svg>
  );
}

function CommentIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 16 16"
      className="size-5"
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
      className="size-5"
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
