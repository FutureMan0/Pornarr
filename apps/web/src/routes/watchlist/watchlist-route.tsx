/**
 * The watch-later queue.
 *
 * Deliberately plain: a private list of titles with one action each. The design
 * gives it a tab of its own precisely because it is not a collection — nothing
 * to name, nothing to share, nothing to arrange.
 */
import { Button, MediaTile } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { seedFrom } from "../../lib/format";

export const WATCHLIST_QUERY_KEY = ["watchlist"] as const;

export function WatchlistRoute(): JSX.Element {
  const { t } = useTranslation();
  const cache = useQueryClient();

  const watchlist = useQuery({
    queryKey: WATCHLIST_QUERY_KEY,
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/watchlist");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const remove = useMutation({
    mutationFn: async (mediaId: string) => {
      const { error, response } = await getApiClient().DELETE("/api/watchlist/{media_id}", {
        params: { path: { media_id: mediaId } },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => void cache.invalidateQueries({ queryKey: WATCHLIST_QUERY_KEY }),
  });

  if (watchlist.isPending)
    return <Screen title={t("watchlist.title")} body={t("watchlist.loading")} />;
  if (watchlist.isError)
    return <Screen title={t("watchlist.title")} body={t("errors.generic")} alert />;

  const items = watchlist.data;
  return (
    <section aria-labelledby="watchlist-heading" className="flex flex-col gap-6">
      <h1 id="watchlist-heading" className="text-xl text-ink">
        {t("watchlist.title")}
      </h1>
      {items.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("watchlist.empty")}</p>
      ) : (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(12.5rem,1fr))] gap-4">
          {items.map((item) => (
            <li key={item.media_id} className="flex flex-col gap-2">
              <MediaTile
                title={item.title}
                seed={seedFrom(item.media_id)}
                ratingLabel={t("library.rating.none")}
                rating={null}
                action={(content) => (
                  <Link to={`/library/${item.media_id}`} className="block rounded-md">
                    {content}
                  </Link>
                )}
              />
              <Button
                variant="ghost"
                onClick={() => remove.mutate(item.media_id)}
                disabled={remove.isPending}
              >
                {t("watchlist.remove")}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Screen({
  title,
  body,
  alert = false,
}: {
  readonly title: string;
  readonly body: string;
  readonly alert?: boolean;
}): JSX.Element {
  return (
    <section aria-labelledby="watchlist-heading">
      <h1 id="watchlist-heading" className="text-xl text-ink">
        {title}
      </h1>
      <p className="text-sm text-ink-muted" {...(alert ? { role: "alert" } : {})}>
        {body}
      </p>
    </section>
  );
}
