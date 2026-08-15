/** Typed queries and mutations for the two independent search result sections. */
import type { paths } from "@pornarr/api-client";
import type { UseMutationResult, UseQueryResult } from "@tanstack/react-query";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getApiClient } from "../../lib/api";
import { type ApiRequestError, apiFailure } from "../../lib/api-error";

export type LocalSearchResponse =
  paths["/api/search/local"]["get"]["responses"][200]["content"]["application/json"];
export type ExternalSearchResponse =
  paths["/api/search/indexers/{search_id}"]["get"]["responses"][200]["content"]["application/json"];
export type ExternalSearchItem = ExternalSearchResponse["items"][number];
export type SearchFilters = {
  readonly quality?: string | undefined;
  readonly minimumSize?: number | undefined;
  readonly maximumSize?: number | undefined;
  readonly maximumAgeDays?: number | undefined;
  readonly indexerId?: string | undefined;
  readonly protocol?: string | undefined;
  readonly minimumSeeders?: number | undefined;
  readonly sort: "relevance" | "age" | "size" | "quality" | "seeders" | "estimated_time";
};

export const SEARCH_QUERY_KEY = ["search"] as const;

export function useLocalSearch(
  query: string,
  filters: SearchFilters,
): UseQueryResult<LocalSearchResponse, ApiRequestError> {
  return useQuery<LocalSearchResponse, ApiRequestError>({
    queryKey: [...SEARCH_QUERY_KEY, "local", query, filters],
    enabled: query.length > 0,
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/search/local", {
        params: {
          query: {
            q: query,
            quality: filters.quality ?? null,
            minimum_size_bytes: filters.minimumSize ?? null,
            maximum_size_bytes: filters.maximumSize ?? null,
            maximum_age_days: filters.maximumAgeDays ?? null,
            sort: localSort(filters.sort),
          },
        },
      });
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
  });
}

export function useStartIndexerSearch(): UseMutationResult<string, ApiRequestError, string> {
  return useMutation<string, ApiRequestError, string>({
    mutationFn: async (query) => {
      const { data, error, response } = await getApiClient().POST("/api/search/indexers", {
        body: { q: query },
      });
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data.id;
    },
  });
}

export function useIndexerSearch(
  searchId: string | null,
  filters: SearchFilters,
): UseQueryResult<ExternalSearchResponse, ApiRequestError> {
  return useQuery<ExternalSearchResponse, ApiRequestError>({
    queryKey: [...SEARCH_QUERY_KEY, "indexers", searchId, filters],
    enabled: searchId !== null,
    refetchInterval: searchId === null ? false : 1_000,
    queryFn: async () => {
      if (searchId === null) throw new Error("search id is required");
      const { data, error, response } = await getApiClient().GET(
        "/api/search/indexers/{search_id}",
        {
          params: {
            path: { search_id: searchId },
            query: {
              quality: filters.quality ?? null,
              minimum_size_bytes: filters.minimumSize ?? null,
              maximum_size_bytes: filters.maximumSize ?? null,
              maximum_age_days: filters.maximumAgeDays ?? null,
              indexer_id: filters.indexerId ?? null,
              protocol: filters.protocol ?? null,
              minimum_seeders: filters.minimumSeeders ?? null,
              sort: filters.sort,
            },
          },
        },
      );
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
  });
}

export function useGrabRelease(): UseMutationResult<void, ApiRequestError, ExternalSearchItem> {
  const queryClient = useQueryClient();
  return useMutation<void, ApiRequestError, ExternalSearchItem>({
    mutationFn: async (release) => {
      const request = await getApiClient().POST("/api/requests", {
        body: { query: release.title, selected_release_guid: release.guid, priority: 50 },
      });
      if (request.error !== undefined || request.data === undefined) {
        throw apiFailure(request.error, request.response);
      }
      const grab = await getApiClient().POST("/api/requests/{request_id}/grab", {
        params: { path: { request_id: request.data.id } },
        body: { release_id: release.id },
      });
      if (grab.error !== undefined || grab.data === undefined)
        throw apiFailure(grab.error, grab.response);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["requests"] });
    },
  });
}

function localSort(sort: SearchFilters["sort"]): "relevance" | "age" | "size" | "quality" {
  return sort === "seeders" || sort === "estimated_time" ? "relevance" : sort;
}
