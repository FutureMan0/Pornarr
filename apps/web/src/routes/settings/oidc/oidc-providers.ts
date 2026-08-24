/**
 * The configured OIDC providers, as one query.
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

export type OidcProvider =
  paths["/api/admin/oidc"]["get"]["responses"][200]["content"]["application/json"][number];

export type OidcProviderWrite =
  paths["/api/admin/oidc"]["post"]["requestBody"]["content"]["application/json"];

export const OIDC_ADMIN_PROVIDERS_KEY = ["admin", "oidc-providers"] as const;

export function useOidcAdminProviders(): UseQueryResult<OidcProvider[], ApiRequestError> {
  const session = useSession();

  return useQuery<OidcProvider[], ApiRequestError>({
    queryKey: OIDC_ADMIN_PROVIDERS_KEY,
    enabled: session.data?.role === "admin",
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/oidc");
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
  });
}
