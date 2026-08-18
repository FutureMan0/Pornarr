/**
 * The numbers beside the navigation entries.
 *
 * The design puts a count on the destinations where something can be waiting.
 * It stays deliberately small: a badge on every entry is a badge on none, and
 * a zero is not news — a destination with nothing waiting shows no number at
 * all rather than a "0" the eye has to read and discard.
 *
 * These reuse the query keys the screens themselves use, so opening Downloads
 * and reading its count never produce two different answers or two requests.
 */
import { useQuery } from "@tanstack/react-query";

import { getApiClient } from "../lib/api";
import type { NavId } from "./sidebar";

/** Long enough that navigating does not re-fetch, short enough to feel live. */
const STALE_MS = 30_000;

export type NavCounts = Partial<Record<NavId, number>>;

export function useNavCounts(): NavCounts {
  const queue = useQuery({
    queryKey: ["queue"],
    queryFn: async () => {
      const { data } = await getApiClient().GET("/api/queue");
      return data ?? [];
    },
    staleTime: STALE_MS,
    // A failing count must never take a screen down with it: the navigation is
    // how you get away from a broken screen.
    retry: false,
  });
  const requests = useQuery({
    queryKey: ["requests"],
    queryFn: async () => {
      const { data } = await getApiClient().GET("/api/requests");
      return data ?? [];
    },
    staleTime: STALE_MS,
    retry: false,
  });

  const counts: NavCounts = {};
  if (Array.isArray(queue.data) && queue.data.length > 0) counts.downloads = queue.data.length;
  // Only what is still open. A finished request is history, not a task.
  const open = Array.isArray(requests.data)
    ? requests.data.filter((request) => !TERMINAL.has(String(request.status))).length
    : 0;
  if (open > 0) counts.requests = open;
  return counts;
}

const TERMINAL = new Set(["completed", "cancelled", "failed"]);
