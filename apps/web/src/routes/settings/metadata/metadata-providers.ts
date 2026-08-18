/**
 * The configured metadata providers, as one query.
 *
 * Administrator-only, so the query is disabled for everyone else rather than
 * fired and left to fail with a 403 each caller would have to interpret.
 */
import type { paths } from "@pornarr/api-client";
import type { UseQueryResult } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import { useSession } from "../../../auth/session";
import { getApiClient } from "../../../lib/api";
import { type ApiRequestError, apiFailure } from "../../../lib/api-error";

export type MetadataProvider =
  paths["/api/admin/metadata-providers"]["get"]["responses"][200]["content"]["application/json"][number];

export type MetadataProviderWrite =
  paths["/api/admin/metadata-providers"]["post"]["requestBody"]["content"]["application/json"];

export const METADATA_PROVIDERS_KEY = ["admin", "metadata-providers"] as const;

export function useMetadataProviders(): UseQueryResult<MetadataProvider[], ApiRequestError> {
  const session = useSession();

  return useQuery<MetadataProvider[], ApiRequestError>({
    queryKey: METADATA_PROVIDERS_KEY,
    enabled: session.data?.role === "admin",
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/metadata-providers");
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
  });
}
