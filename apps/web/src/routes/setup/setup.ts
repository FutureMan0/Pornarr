/** First-run setup queries and mutations, kept outside the route for testable wiring. */
import type { paths } from "@pornarr/api-client";
import type { UseMutationResult, UseQueryResult } from "@tanstack/react-query";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getApiClient } from "../../lib/api";
import { type ApiRequestError, apiFailure } from "../../lib/api-error";

export type SetupStatus =
  paths["/api/setup/status"]["get"]["responses"][200]["content"]["application/json"];
export type SetupWrite =
  paths["/api/setup/complete"]["post"]["requestBody"]["content"]["application/json"];
export type SetupComplete =
  paths["/api/setup/complete"]["post"]["responses"][201]["content"]["application/json"];
export type SetupPathValidation =
  paths["/api/setup/validate-library-path"]["post"]["responses"][200]["content"]["application/json"];

export const SETUP_STATUS_QUERY_KEY = ["setup", "status"] as const;

export function useSetupStatus(): UseQueryResult<SetupStatus, ApiRequestError> {
  return useQuery<SetupStatus, ApiRequestError>({
    queryKey: SETUP_STATUS_QUERY_KEY,
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/setup/status");
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useLibraryPathValidation(): UseMutationResult<
  SetupPathValidation,
  ApiRequestError,
  Pick<SetupWrite, "library_path">
> {
  return useMutation<SetupPathValidation, ApiRequestError, Pick<SetupWrite, "library_path">>({
    mutationFn: async (body) => {
      const { data, error, response } = await getApiClient().POST(
        "/api/setup/validate-library-path",
        {
          body,
        },
      );
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
  });
}

export function useCompleteSetup(): UseMutationResult<SetupComplete, ApiRequestError, SetupWrite> {
  const queryClient = useQueryClient();
  return useMutation<SetupComplete, ApiRequestError, SetupWrite>({
    mutationFn: async (body) => {
      const { data, error, response } = await getApiClient().POST("/api/setup/complete", { body });
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
    onSuccess: () => {
      queryClient.setQueryData<SetupStatus>(SETUP_STATUS_QUERY_KEY, { configured: true });
    },
  });
}
