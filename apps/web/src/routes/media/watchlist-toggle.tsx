/**
 * Add or remove this title from the watch-later queue.
 *
 * Reads the list rather than a per-title endpoint, because the list is small,
 * already cached by the watchlist screen, and one query answers the question
 * for every title the session will visit.
 */
import { Button } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { WATCHLIST_QUERY_KEY } from "../watchlist/watchlist-route";

export function WatchlistToggle({ mediaId }: { readonly mediaId: string }): JSX.Element {
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

  const saved = (watchlist.data ?? []).some((item) => item.media_id === mediaId);

  const toggle = useMutation({
    mutationFn: async () => {
      const client = getApiClient();
      const { error, response } = saved
        ? await client.DELETE("/api/watchlist/{media_id}", {
            params: { path: { media_id: mediaId } },
          })
        : await client.POST("/api/watchlist", { body: { media_id: mediaId } });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => void cache.invalidateQueries({ queryKey: WATCHLIST_QUERY_KEY }),
  });

  return (
    <Button
      variant="secondary"
      className="self-start"
      aria-pressed={saved}
      disabled={toggle.isPending || watchlist.isPending}
      onClick={() => toggle.mutate()}
    >
      {saved ? t("watchlist.remove") : t("watchlist.add")}
    </Button>
  );
}
