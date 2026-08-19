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

import { usePageTitle } from "../../shell/page-title";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { tileBlur, useArtVisible } from "../../lib/art-visibility";
import { seedFrom } from "../../lib/format";

export const WATCHLIST_QUERY_KEY = ["watchlist"] as const;

export function WatchlistRoute(): JSX.Element {
  const { t } = useTranslation();
  const artVisible = useArtVisible();
  usePageTitle(t("watchlist.title"));
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
    <section aria-label={t("watchlist.title")} className="flex flex-col gap-6">
      {items.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("watchlist.empty")}</p>
      ) : (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(9.5rem,1fr))] sm:grid-cols-[repeat(auto-fill,minmax(12.5rem,1fr))] gap-4">
          {items.map((item) => (
            <li key={item.media_id} className="flex flex-col gap-2">
              <MediaTile
                blur={tileBlur(artVisible)}
                title={item.title}
                seed={seedFrom(item.media_id)}
                ratingLabel={t("library.rating.none")}
                rating={null}
                action={(content) => (
                  // `article`, the tile's root, does not contribute to an
                  // accessible name from content, so the link needs one of its
                  // own or a screen reader hears nothing but "link".
                  <Link
                    to={`/library/${item.media_id}`}
                    className="block rounded-md"
                    aria-label={item.title}
                  >
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
    // The name of the screen is in the top bar; this region carries it as a
    // label so the section is still identified without a second heading.
    <section aria-label={title}>
      <p className="text-sm text-ink-muted" {...(alert ? { role: "alert" } : {})}>
        {body}
      </p>
    </section>
  );
}
