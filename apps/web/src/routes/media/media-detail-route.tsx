import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
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
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [tag, setTag] = useState("");
  const detail = useQuery({
    queryKey: ["media", mediaId],
    queryFn: async (): Promise<Detail> => {
      const response = await fetch(`/api/media/${mediaId}`);
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
    <section className="flex flex-col gap-6" aria-labelledby="media-heading">
      <header>
        <h1 id="media-heading" className="text-xl text-ink">
          {media.title}
        </h1>
        <p className="text-sm text-ink-muted">
          {media.studio ?? "—"}
          {media.release_date ? ` · ${media.release_date}` : ""}
        </p>
      </header>
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
        <VideoPlayer mediaId={media.id} title={media.title} />
      ) : (
        <p>{t("media.unavailable")}</p>
      )}
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
        <form
          className="mt-3 flex gap-2"
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
