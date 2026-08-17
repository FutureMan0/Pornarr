/**
 * Whether a title is on the watch-later queue, and the switch that changes it.
 *
 * A hook rather than a component, because two screens need the same state in
 * two shapes: a labelled button on the detail screen, and an icon in the rail
 * beside a short. Duplicating the mutation is how the two drift — one of them
 * ends up invalidating a different key and the other stops updating.
 *
 * Reads the whole list rather than asking per title. The list is small, one
 * query answers the question for every title a session visits, and the
 * watchlist screen has usually cached it already.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { WATCHLIST_QUERY_KEY } from "../watchlist/watchlist-route";

export interface WatchlistState {
  readonly saved: boolean;
  /** True while the list is loading or a change is in flight. */
  readonly busy: boolean;
  readonly toggle: () => void;
}

export function useWatchlist(mediaId: string): WatchlistState {
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

  const change = useMutation({
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

  return {
    saved,
    busy: change.isPending || watchlist.isPending,
    toggle: () => change.mutate(),
  };
}
