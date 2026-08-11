/**
 * The session, as three hooks over one query.
 *
 * Authentication is a cookie the browser holds and JavaScript cannot read, so
 * there is nothing to keep in a store and nothing to put in localStorage. The
 * only honest source of truth is `GET /api/auth/me`, and the query cache is
 * already a cache with invalidation — so the session *is* a query, and login
 * and logout are mutations that write its result.
 *
 * `null` is a value here, not an absence: it means "asked, and not signed in".
 * `undefined` means "not asked yet", which is what the boundary shows a
 * skeleton for.
 */
import type { paths } from "@pornarr/api-client";
import type { UseMutationResult, UseQueryResult } from "@tanstack/react-query";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getApiClient } from "../lib/api";
import { type ApiRequestError, apiFailure } from "../lib/api-error";

export type SessionUser =
  paths["/api/auth/me"]["get"]["responses"][200]["content"]["application/json"];

export type Credentials =
  paths["/api/auth/login"]["post"]["requestBody"]["content"]["application/json"];

export const SESSION_QUERY_KEY = ["auth", "session"] as const;

/**
 * The current user, or null.
 *
 * A 401 is the expected answer for a signed-out visitor, so it resolves rather
 * than rejects — an error state here would put an error screen in front of
 * every first-time load.
 */
export function useSession(): UseQueryResult<SessionUser | null, ApiRequestError> {
  return useQuery<SessionUser | null, ApiRequestError>({
    queryKey: SESSION_QUERY_KEY,
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/auth/me");
      if (response.status === 401) return null;
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/**
 * Sign in. On success the session query is written directly rather than
 * invalidated: the response body is the same shape `/api/auth/me` returns, so
 * refetching it would be a second round trip for an answer already in hand.
 */
export function useLogin(): UseMutationResult<SessionUser, ApiRequestError, Credentials> {
  const queryClient = useQueryClient();

  return useMutation<SessionUser, ApiRequestError, Credentials>({
    mutationFn: async (credentials) => {
      const { data, error, response } = await getApiClient().POST("/api/auth/login", {
        body: credentials,
      });
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
    onSuccess: (user) => {
      queryClient.setQueryData(SESSION_QUERY_KEY, user);
    },
  });
}

export interface LogoutOptions {
  /** Ends every session for this user, not just this browser's. */
  readonly everywhere?: boolean;
}

/**
 * Sign out.
 *
 * The API scopes the readable CSRF cookie to the SPA, so the shared client can
 * echo it on this request. A failure is surfaced and the cache is emptied only
 * on success — showing a login screen while the server-side session is still
 * alive would be a lie about the security state.
 */
export function useLogout(): UseMutationResult<void, ApiRequestError, LogoutOptions> {
  const queryClient = useQueryClient();

  return useMutation<void, ApiRequestError, LogoutOptions>({
    mutationFn: async (options) => {
      const path = options.everywhere === true ? "/api/auth/logout-everywhere" : "/api/auth/logout";
      const { error, response } = await getApiClient().POST(path);
      if (error !== undefined) throw apiFailure(error, response);
    },
    onSuccess: () => {
      // Everything cached belongs to the account that just left — except the
      // session query itself, which is written with the answer we already have.
      // Removing that one instead would leave its observer with no data, and an
      // observer with no data refetches: a `/api/auth/me` still in flight from
      // before the logout is exactly how a signed-out user lands back inside
      // the shell.
      queryClient.removeQueries({
        predicate: (query) => query.queryKey[0] !== SESSION_QUERY_KEY[0],
      });
      queryClient.setQueryData(SESSION_QUERY_KEY, null);
    },
  });
}
