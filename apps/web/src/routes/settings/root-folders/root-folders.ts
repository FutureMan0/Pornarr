/**
 * The configured root folders, as one query.
 *
 * Two screens need this: the settings screen that edits the list, and the empty
 * library that has to decide whether the reader's next step is "add a root
 * folder" or "request something". Sharing the query rather than the endpoint is
 * what keeps those two answers from disagreeing.
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

export type RootFolder =
  paths["/api/admin/library/root-folders"]["get"]["responses"][200]["content"]["application/json"][number];

export type RootFolderWrite =
  paths["/api/admin/library/root-folders"]["post"]["requestBody"]["content"]["application/json"];

export const ROOT_FOLDERS_KEY = ["admin", "library", "root-folders"] as const;

export function useRootFolders(): UseQueryResult<RootFolder[], ApiRequestError> {
  const session = useSession();

  return useQuery<RootFolder[], ApiRequestError>({
    queryKey: ROOT_FOLDERS_KEY,
    enabled: session.data?.role === "admin",
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/library/root-folders");
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
  });
}
