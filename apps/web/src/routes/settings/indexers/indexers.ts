/**
 * The configured indexers, as one query.
 *
 * ADR 0002 L9: "Indexers are rows in the database, managed through the
 * administration UI, not environment variables." The setup wizard could add the
 * first one and nothing could change it afterwards, so this is the other half.
 *
 * The endpoint is administrator-only, so the query is disabled for everyone
 * else rather than fired and left to fail with a 403 each caller would have to
 * interpret. `undefined` therefore means "not asked", never "none configured".
 */
import type { paths } from "@pornarr/api-client";
import type { UseQueryResult } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import { useSession } from "../../../auth/session";
import { getApiClient } from "../../../lib/api";
import { type ApiRequestError, apiFailure } from "../../../lib/api-error";

export type Indexer =
  paths["/api/admin/indexers"]["get"]["responses"][200]["content"]["application/json"][number];

export type IndexerWrite =
  paths["/api/admin/indexers"]["post"]["requestBody"]["content"]["application/json"];

export type IndexerUpdate =
  paths["/api/admin/indexers/{indexer_id}"]["put"]["requestBody"]["content"]["application/json"];

export type IndexerImplementation = "torznab" | "newznab";

/**
 * Which protocol an implementation speaks, exactly as `routers/setup.py:54`
 * fills it in. The wizard refuses to ask an operator for something the chosen
 * adapter already implies, and asking here instead would make the two screens
 * disagree about the same row.
 */
export const INDEXER_PROTOCOLS: Record<IndexerImplementation, string> = {
  torznab: "torrent",
  newznab: "usenet",
};

export const INDEXERS_KEY = ["admin", "indexers"] as const;

export function useIndexers(): UseQueryResult<Indexer[], ApiRequestError> {
  const session = useSession();

  return useQuery<Indexer[], ApiRequestError>({
    queryKey: INDEXERS_KEY,
    enabled: session.data?.role === "admin",
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/indexers");
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
  });
}
