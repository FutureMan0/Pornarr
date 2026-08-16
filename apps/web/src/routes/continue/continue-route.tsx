/**
 * Everything you started and did not finish.
 *
 * The endpoint already excludes completed titles, so this screen does no
 * filtering of its own — a title disappearing from here is the server's
 * decision about what "finished" means, not the client's.
 */
import { MediaTile } from "@pornarr/ui";
import { useQuery } from "@tanstack/react-query";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { formatDuration, progressOf, seedFrom } from "../../lib/format";

export function ContinueRoute(): JSX.Element {
  const { t } = useTranslation();
  const resuming = useQuery({
    queryKey: ["continue-watching"],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/playback/continue-watching");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  return (
    <section aria-labelledby="continue-heading" className="flex flex-col gap-6">
      <h1 id="continue-heading" className="text-xl text-ink">
        {t("continueWatching.title")}
      </h1>

      {resuming.isPending ? (
        <p className="text-sm text-ink-muted">{t("continueWatching.loading")}</p>
      ) : null}
      {resuming.isError ? (
        <p role="alert" className="text-sm text-ink">
          {t("errors.generic")}
        </p>
      ) : null}

      {resuming.data !== undefined ? (
        resuming.data.length === 0 ? (
          <p className="text-sm text-ink-muted">{t("continueWatching.empty")}</p>
        ) : (
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(12.5rem,1fr))] gap-4">
            {resuming.data.map((item) => (
              <li key={item.media_id}>
                <MediaTile
                  title={item.title ?? item.media_id}
                  meta={item.device_label ?? undefined}
                  duration={formatDuration(item.duration_seconds)}
                  progress={progressOf(item.position_seconds, item.duration_seconds)}
                  rating={null}
                  ratingLabel={t("library.rating.none")}
                  seed={seedFrom(item.media_id)}
                  poster={<img src={`/api/media/${item.media_id}/poster`} alt="" loading="lazy" />}
                  action={(content) => (
                    <Link to={`/library/${item.media_id}`} className="block rounded-md">
                      {content}
                    </Link>
                  )}
                />
              </li>
            ))}
          </ul>
        )
      ) : null}
    </section>
  );
}
