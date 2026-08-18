import { cx } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams, useSearchParams } from "react-router-dom";
import { VideoPlayer } from "../../components/player/video-player";
import { ErrorScreen } from "../../errors/error-screen";
import { NotFoundRoute } from "../../errors/route-errors";
import {
  ApiRequestError,
  apiFailure,
  isRetryableError,
  messageForError,
  nextStepForError,
} from "../../lib/api-error";
import { usePageTitle } from "../../shell/page-title";
import { CommentsPanel } from "./comments-panel";
import { RatingPanel } from "./rating-panel";
import { RelatedPanel } from "./related-panel";
import { WatchlistToggle } from "./watchlist-toggle";

type Tag = { name: string; confidence: number; source: string };
type Detail = {
  id: string;
  title: string;
  studio: string | null;
  release_date: string | null;
  confidence: number | null;
  metadata_source: string;
  performers: string[];
  tags: Tag[];
  path: string;
  size: number;
  codecs: Record<string, unknown> | null;
  resolution: string | null;
  bitrate: number | null;
  duration_seconds: number | null;
  playable: boolean;
};

/** The contract error body, when the response carries one. */
async function failureBody(response: Response): Promise<unknown> {
  try {
    return await response.clone().json();
  } catch {
    return null;
  }
}

export function MediaDetailRoute() {
  const { mediaId = "" } = useParams();
  // A title in a shared library lives on another instance, and this browser has
  // no key for it: everything about it is fetched through this instance's proxy.
  const peerId = useSearchParams()[0].get("peer") ?? undefined;
  const base = peerId === undefined ? "/api" : `/api/peers/${peerId}/proxy`;
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [tag, setTag] = useState("");
  const detail = useQuery({
    queryKey: ["media", mediaId, peerId ?? null],
    queryFn: async (): Promise<Detail> => {
      const response = await fetch(`${base}/media/${mediaId}`);
      // The status is the whole difference between a title that does not exist
      // and a server that is having a bad day, and the reader needs to be told
      // which one they are looking at.
      if (!response.ok) throw apiFailure(await failureBody(response), response);
      return response.json() as Promise<Detail>;
    },
  });
  const correct = useMutation({
    mutationFn: async () => {
      const response = await fetch(`/api/media/${mediaId}/tags`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: tag }),
      });
      if (!response.ok) throw new Error();
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

  if (detail.isPending) return <p>{t("media.loading")}</p>;
  if (detail.isError || !detail.data) {
    if (detail.error instanceof ApiRequestError && detail.error.status === 404) {
      return <NotFoundRoute />;
    }
    return (
      <ErrorScreen
        title={messageForError(detail.error)}
        nextStep={nextStepForError(detail.error)}
        onRetry={isRetryableError(detail.error) ? () => void detail.refetch() : undefined}
      />
    );
  }
  const media = detail.data;
  return (
    <section className="flex flex-col gap-6" aria-label={media.title}>
      <p className="text-sm text-ink-muted">
        {t("media.confidence", {
          value:
            media.confidence === null
              ? t("media.unknown")
              : `${Math.round(media.confidence * 100)}%`,
        })}{" "}
        · {media.metadata_source}
      </p>
      {media.playable ? (
        <VideoPlayer mediaId={media.id} title={media.title} peerId={peerId} />
      ) : (
        <p>{t("media.unavailable")}</p>
      )}

      {/* Watchlist, ratings, comments and "related" are all this instance's
          own records, keyed by an id that only means something here. A title
          borrowed from a peer has none of them, and asking for them under a
          foreign id would answer about whatever happens to share the number. */}
      {peerId === undefined ? (
        <>
          <WatchlistToggle mediaId={media.id} />

          {/* Rating and comments side by side on a wide screen, stacked below
              it. They are the two halves of "what the household thinks" and
              reading one without the other is half the picture. */}
          <div className="grid gap-8 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
            <RatingPanel mediaId={media.id} />
            <CommentsPanel mediaId={media.id} />
          </div>

          <RelatedPanel mediaId={media.id} />
        </>
      ) : null}
      <section>
        <h2 className="text-lg text-ink">{t("media.tags")}</h2>
        <ul className="flex flex-wrap gap-2">
          {media.tags.map((item) => (
            <li
              key={`${item.name}-${item.source}`}
              className="border border-border px-2 py-1 text-sm text-ink"
            >
              {item.name} · {Math.round(item.confidence * 100)}% · {item.source}
            </li>
          ))}
        </ul>
        {/* Correcting a tag writes to the instance that owns the title, and a
            peer is read-only from here, so the form is only offered at home. */}
        <form
          className={cx("mt-3 flex gap-2", peerId === undefined ? undefined : "hidden")}
          hidden={peerId !== undefined}
          onSubmit={(event) => {
            event.preventDefault();
            correct.mutate();
          }}
        >
          <input
            value={tag}
            onChange={(event) => setTag(event.target.value)}
            aria-label={t("media.correctTag")}
            className="input"
          />
          <button className="button" type="submit" disabled={!tag.trim() || correct.isPending}>
            {t("media.addTag")}
          </button>
        </form>
      </section>
      <section>
        <h2 className="text-lg text-ink">{t("media.file")}</h2>
        <dl className="grid gap-2 text-sm">
          <div>
            <dt className="text-ink-muted">{t("media.path")}</dt>
            <dd className="font-mono text-ink">{media.path}</dd>
          </div>
          <div>
            <dt className="text-ink-muted">{t("media.technical")}</dt>
            <dd className="text-ink">
              {media.resolution ?? "—"} · {media.bitrate ?? "—"} · {media.size}
            </dd>
          </div>
        </dl>
      </section>
    </section>
  );
}
