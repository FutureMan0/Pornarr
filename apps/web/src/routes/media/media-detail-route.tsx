/**
 * B3 — one title, in two columns.
 *
 * WHAT WAS WRONG WITH THE STACK. Everything on this screen used to be a
 * full-width block, one under the other: player, watchlist, rating, comments,
 * related, tags, file. Reading the resolution of the file meant scrolling past
 * every comment, and the related row — the thing that keeps somebody watching —
 * sat a full screen below the title it relates to. The design puts reference
 * material in a rail beside the player, where you can glance at it, and leaves
 * the main column for the things you act on.
 *
 * THE HEADING IS THE SHELL'S. `usePageTitle` already prints the title and the
 * studio · year subtitle in the header, so repeating them here would be the same
 * words twice, forty pixels apart.
 *
 * THE GENERATED CLIENT, NOT `fetch`. This screen predates the contract and was
 * still declaring its own `Detail` type by hand, which is a copy of the server's
 * response that nothing checks — it had already drifted (no `rating`, no
 * `in_my_library`, no `added_at`). The generated client makes a server change a
 * compile error here.
 */
import { Button, Input } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { JSX } from "react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";

import type { PlayerHandle } from "../../components/player/video-player";
import { VideoPlayer } from "../../components/player/video-player";
import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { usePageTitle } from "../../shell/page-title";
import { CommentsPanel } from "./comments-panel";
import { DetailRail } from "./detail-rail";
import { RatingPanel } from "./rating-panel";
import { RelatedPanel } from "./related-panel";
import { SceneMarkers } from "./scene-markers";
import { WatchlistToggle } from "./watchlist-toggle";

export function MediaDetailRoute(): JSX.Element {
  const { mediaId = "" } = useParams();
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [tag, setTag] = useState("");
  /** Held so a scene marker can move the player that is already on screen. */
  const player = useRef<PlayerHandle | null>(null);

  const detail = useQuery({
    queryKey: ["media", mediaId],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/media/{media_id}", {
        params: { path: { media_id: mediaId } },
      });
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const correct = useMutation({
    mutationFn: async () => {
      const { error, response } = await getApiClient().POST("/api/media/{media_id}/tags", {
        params: { path: { media_id: mediaId } },
        body: { name: tag },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => {
      setTag("");
      void queryClient.invalidateQueries({ queryKey: ["media", mediaId] });
    },
  });

  // The title of this screen is the title of the work, so there is no generic
  // name to show before it arrives — hence the loading string standing in.
  // Studio and year become the subtitle, which is where the design puts them.
  usePageTitle(
    detail.data?.title ?? t("media.loading"),
    detail.data === undefined
      ? undefined
      : [detail.data.studio, detail.data.release_date].filter(Boolean).join(" · ") || undefined,
  );

  if (detail.isPending) return <p className="text-sm text-ink-muted">{t("media.loading")}</p>;
  if (detail.isError || !detail.data)
    return (
      <p role="alert" className="text-sm text-ink">
        {t("errors.generic")}
      </p>
    );

  const media = detail.data;

  return (
    <section className="flex flex-col gap-6" aria-label={media.title}>
      {/* The rail drops under the main column below `xl`. Two columns need the
          player to stay watchable, and 20rem taken out of a 1024px window
          leaves a player nobody wants. */}
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="flex min-w-0 flex-col gap-5">
          {media.playable ? (
            <VideoPlayer mediaId={media.id} title={media.title} handleRef={player} />
          ) : (
            <p className="rounded-lg bg-surface-2 p-4 text-sm text-ink-muted">
              {t("media.unavailable")}
            </p>
          )}

          <div className="flex flex-wrap items-center justify-between gap-3">
            <WatchlistToggle mediaId={media.id} />
            <p className="text-2xs text-ink-faint">
              {t("media.confidence", {
                value:
                  media.confidence === null
                    ? t("media.unknown")
                    : `${Math.round(media.confidence * 100)}%`,
              })}
              {" · "}
              {media.metadata_source}
            </p>
          </div>

          {/* Only rendered once there is something to jump to, and only useful
              while a player exists to be moved. */}
          {media.playable ? (
            <SceneMarkers
              mediaId={media.id}
              onSeek={(seconds) => {
                player.current?.seek(seconds);
              }}
            />
          ) : null}

          <section aria-labelledby="tags-heading" className="card">
            <h2 id="tags-heading" className="text-sm text-ink">
              {t("media.tags")}
            </h2>
            <ul className="flex flex-wrap gap-1.5">
              {media.tags.map((item) => (
                <li
                  key={`${item.name}-${item.source}`}
                  className="rounded-full bg-surface-3 px-2.5 py-1 text-2xs text-ink-muted"
                >
                  {item.name}
                  <span className="text-ink-faint">
                    {" · "}
                    {Math.round(item.confidence * 100)}%
                  </span>
                </li>
              ))}
            </ul>
            <form
              className="flex gap-2 pt-1"
              onSubmit={(event) => {
                event.preventDefault();
                correct.mutate();
              }}
            >
              <Input
                value={tag}
                onChange={(event) => setTag(event.target.value)}
                aria-label={t("media.correctTag")}
                className="flex-1"
              />
              <Button type="submit" disabled={!tag.trim() || correct.isPending}>
                {t("media.addTag")}
              </Button>
            </form>
          </section>

          {/* Rating and comments side by side on a wide screen, stacked below
              it. They are the two halves of "what the household thinks" and
              reading one without the other is half the picture. */}
          <div className="grid gap-5 lg:grid-cols-[minmax(0,18rem)_minmax(0,1fr)]">
            <RatingPanel mediaId={media.id} />
            <CommentsPanel mediaId={media.id} />
          </div>
        </div>

        <DetailRail
          performers={media.performers}
          studio={media.studio}
          releaseDate={media.release_date}
          durationSeconds={media.duration_seconds}
          resolution={media.resolution}
          sizeBytes={media.size}
          addedAt={media.added_at}
          path={media.path}
          inMyLibrary={media.in_my_library}
        />
      </div>

      {/* Full width, below both columns: a row of artwork reads as a row, and
          squeezing it into the main column would cut it to three tiles. */}
      <RelatedPanel mediaId={media.id} />
    </section>
  );
}
