/**
 * The signed-in account's own API keys, as one query.
 *
 * Session-only by the account router's own rule (`session_user` in
 * `account.py` refuses a request already authenticated by a key), and every
 * account may have some, so unlike the admin sections beside this one there is
 * no role to gate the query on.
 */
import type { paths } from "@pornarr/api-client";
import type { UseQueryResult } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import { getApiClient } from "../../../lib/api";
import { type ApiRequestError, apiFailure } from "../../../lib/api-error";

export type ApiKey =
  paths["/api/account/api-keys"]["get"]["responses"][200]["content"]["application/json"][number];

export type ApiKeyCreated =
  paths["/api/account/api-keys"]["post"]["responses"][201]["content"]["application/json"];

export const API_KEYS_KEY = ["account", "api-keys"] as const;

export function useApiKeys(): UseQueryResult<ApiKey[], ApiRequestError> {
  return useQuery<ApiKey[], ApiRequestError>({
    queryKey: API_KEYS_KEY,
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/account/api-keys");
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
  });
}
