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

// The generated client is not regenerated as part of this change (a separate
// pass owns openapi.json), so the pieces the backend added after generation
// are typed by hand here instead of read out of `paths`.
export type SetupIndexerImplementation = "torznab" | "newznab";
export type SetupDownloadClientImplementation = "qbittorrent" | "sabnzbd";
export type SetupMetadataProviderImplementation = "stashdb" | "tpdb";

export interface SetupIndexerWrite {
  implementation: SetupIndexerImplementation;
  base_url: string;
  api_key: string;
}

export interface SetupDownloadClientWrite {
  implementation: SetupDownloadClientImplementation;
  host: string;
  port: number;
  credentials: string;
  category?: string | null;
}

export interface SetupMetadataProviderWrite {
  implementation: SetupMetadataProviderImplementation;
  api_key: string;
  endpoint?: string | null;
}

export interface SetupIndexerTestResult {
  categories: { id: string; name: string }[];
}

export type SetupCompleteWrite = SetupWrite & {
  indexer?: SetupIndexerWrite;
  download_client?: SetupDownloadClientWrite;
  metadata_provider?: SetupMetadataProviderWrite;
};

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

/** The contract error body, when the response carries one. */
async function failureBody(response: Response): Promise<unknown> {
  try {
    return await response.clone().json();
  } catch {
    return null;
  }
}

export function useTestIndexerConnection(): UseMutationResult<
  SetupIndexerTestResult,
  ApiRequestError,
  SetupIndexerWrite
> {
  return useMutation<SetupIndexerTestResult, ApiRequestError, SetupIndexerWrite>({
    mutationFn: async (body) => {
      const response = await fetch("/api/setup/test-indexer", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw apiFailure(await failureBody(response), response);
      return (await response.json()) as SetupIndexerTestResult;
    },
  });
}

export function useTestDownloadClientConnection(): UseMutationResult<
  void,
  ApiRequestError,
  SetupDownloadClientWrite
> {
  return useMutation<void, ApiRequestError, SetupDownloadClientWrite>({
    mutationFn: async (body) => {
      const response = await fetch("/api/setup/test-download-client", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw apiFailure(await failureBody(response), response);
    },
  });
}

export function useCompleteSetup(): UseMutationResult<
  SetupComplete,
  ApiRequestError,
  SetupCompleteWrite
> {
  const queryClient = useQueryClient();
  return useMutation<SetupComplete, ApiRequestError, SetupCompleteWrite>({
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
