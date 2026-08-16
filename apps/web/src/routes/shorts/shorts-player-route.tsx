/**
 * B7 — one short, playing, with the queue beside it.
 *
 * A clip is a pair of timestamps into a title, not a file of its own. So the
 * player streams the parent and stops at the end mark, and everything social on
 * this screen — the rating, the comments — belongs to that parent. This screen
 * says so out loud rather than implying each clip is rated separately: the
 * numbers the API returns for a short are its title's, and pretending otherwise
 * would have people rating a film by its best forty seconds without knowing.
 *
 * THE QUEUE IS THE FEED. "Up next" is the same ordering the shorts grid uses,
 * with the current clip removed. Generating a separate queue would mean two
 * answers to "what comes after this", and they would drift.
 *
 * SPACE PAUSES, THE ARROWS MOVE. The design puts those in the subtitle because
 * a keyboard user should not have to discover them. The handler ignores keys
 * typed into the comment box — a space in a sentence is a space, not a command.
 */
import { Artwork, Stars } from "@pornarr/ui";
import { useQuery } from "@tanstack/react-query";
import type { JSX } from "react";
import { useCallback, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";

import { VideoPlayer } from "../../components/player/video-player";
import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { tileBlur, useArtVisible } from "../../lib/art-visibility";
import { formatDuration, seedFrom } from "../../lib/format";
import { usePageTitle } from "../../shell/page-title";
import { CommentsPanel } from "../media/comments-panel";
import { RatingPanel } from "../media/rating-panel";

export function ShortsPlayerRoute(): JSX.Element {
  const { t } = useTranslation();
  const { shortId = "" } = useParams();
  const navigate = useNavigate();
  const artVisible = useArtVisible();

  const short = useQuery({
    queryKey: ["short", shortId],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/shorts/{short_id}", {
        params: { path: { short_id: shortId } },
      });
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const feed = useQuery({
    queryKey: ["shorts", "trending"],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/shorts", {
        params: { query: { sort: "trending", limit: 60 } },
      });
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const queue = feed.data ?? [];
  const position = queue.findIndex((item) => item.id === shortId);
  const previous = position > 0 ? queue[position - 1] : undefined;
  const next = position >= 0 && position + 1 < queue.length ? queue[position + 1] : undefined;

  usePageTitle(
    t("shorts.title"),
    position < 0
      ? undefined
      : t("shorts.player.position", { index: position + 1, total: queue.length }),
  );

  const goTo = useCallback(
    (id: string | undefined) => {
      if (id !== undefined) navigate(`/shorts/${id}`);
    },
    [navigate],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      // Never steal a keystroke from something being typed into.
      const target = event.target as HTMLElement | null;
      if (target !== null && (target.tagName === "TEXTAREA" || target.tagName === "INPUT")) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        goTo(next?.id);
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        goTo(previous?.id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goTo, next?.id, previous?.id]);

  if (short.isPending) return <p className="text-sm text-ink-muted">{t("shorts.loading")}</p>;
  if (short.isError || short.data === undefined)
    return (
      <p role="alert" className="text-sm text-ink">
        {t("errors.generic")}
      </p>
    );

  const clip = short.data;

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <section aria-label={clip.title} className="flex flex-col gap-3">
        <p className="text-2xs text-ink-muted">{t("shorts.player.keys")}</p>

        <div className="flex gap-3">
          <div className="mx-auto w-full max-w-[32rem]">
            <VideoPlayer
              mediaId={clip.parent_media_id}
              title={clip.title}
              portrait
              clip={{ startSeconds: clip.start_seconds, endSeconds: clip.end_seconds }}
              onEnded={() => goTo(next?.id)}
            />
          </div>

          {/* The rail from the design. Only the two that navigate are here:
              rating, commenting and saving all live in the panel beside the
              player, and a second control for each would be two places to keep
              in step. */}
          <div className="flex flex-col justify-center gap-2">
            <button
              type="button"
              disabled={previous === undefined}
              onClick={() => goTo(previous?.id)}
              className="rounded-md border border-border-control px-2 py-2 text-ink-muted disabled:opacity-40 enabled:hover:bg-surface-3 enabled:hover:text-ink"
            >
              <span aria-hidden="true">↑</span>
              <span className="visually-hidden">{t("shorts.player.previous")}</span>
            </button>
            <button
              type="button"
              disabled={next === undefined}
              onClick={() => goTo(next?.id)}
              className="rounded-md border border-border-control px-2 py-2 text-ink-muted disabled:opacity-40 enabled:hover:bg-surface-3 enabled:hover:text-ink"
            >
              <span aria-hidden="true">↓</span>
              <span className="visually-hidden">{t("shorts.player.next")}</span>
            </button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
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
          <span className="text-sm tabular-nums text-ink">
            {clip.average_stars === null ? "—" : clip.average_stars.toFixed(1)}
          </span>
          <span className="text-2xs tabular-nums text-ink-muted">
            {formatDuration(clip.duration_seconds)}
          </span>
        </div>

        <h2 className="text-lg text-ink">{clip.title}</h2>
        <p className="text-xs text-ink-muted">
          {/* Where the clip came from and where in it. Without the timestamp a
              clip is unfindable in the title it was cut from. */}
          {t("shorts.player.cutFrom")}{" "}
          <Link to={`/library/${clip.parent_media_id}`} className="text-ink underline">
            {clip.media_title}
          </Link>{" "}
          {t("shorts.player.at", { time: formatDuration(clip.start_seconds) })}
        </p>
      </section>

      <aside className="flex flex-col gap-6">
        {/* Both are the title's, not the clip's — see the note at the top. */}
        <p className="text-2xs text-ink-faint">{t("shorts.player.aboutTitle")}</p>
        <RatingPanel mediaId={clip.parent_media_id} />
        <CommentsPanel mediaId={clip.parent_media_id} />

        <section aria-labelledby="up-next-heading" className="flex flex-col gap-2">
          <h2 id="up-next-heading" className="text-2xs uppercase tracking-[0.08em] text-ink-muted">
            {t("shorts.player.upNext")}
          </h2>
          {queue.length <= 1 ? (
            <p className="text-sm text-ink-muted">{t("shorts.player.queueEmpty")}</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {queue
                .filter((item) => item.id !== shortId)
                .slice(0, 8)
                .map((item) => (
                  <li key={item.id}>
                    <Link
                      to={`/shorts/${item.id}`}
                      className="flex items-center gap-3 rounded-md p-2 hover:bg-surface-2"
                    >
                      <Artwork
                        seed={seedFrom(item.id)}
                        blur={tileBlur(artVisible)}
                        className="w-16 flex-none"
                        ratio="9 / 16"
                      />
                      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                        <span className="truncate text-sm text-ink">{item.title}</span>
                        <span className="truncate text-2xs text-ink-muted">
                          {t("shorts.from", { title: item.media_title })} ·{" "}
                          {formatDuration(item.duration_seconds)}
                        </span>
                      </span>
                    </Link>
                  </li>
                ))}
            </ul>
          )}
        </section>
      </aside>
    </div>
  );
}
