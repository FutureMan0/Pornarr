/**
 * The configured download clients, as one query.
 *
 * The setup wizard creates the first client and, until this section existed,
 * nothing could ever touch it again: the four routes that manage a client had
 * no caller in the application at all. An operator who moved qBittorrent to
 * another port, rotated its password or replaced it with SABnzbd had no way to
 * say so, and the only remaining move was to reinstall.
 *
 * Administrator-only, so the query is disabled for everyone else rather than
 * fired and left to fail with a 403 each caller would have to interpret.
 * `undefined` therefore means "not asked", never "none configured".
 */
import type { paths } from "@pornarr/api-client";
import type { UseQueryResult } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import { useSession } from "../../../auth/session";
import { getApiClient } from "../../../lib/api";
import { type ApiRequestError, apiFailure } from "../../../lib/api-error";

export type DownloadClient =
  paths["/api/admin/download-clients"]["get"]["responses"][200]["content"]["application/json"][number];

export type DownloadClientWrite =
  paths["/api/admin/download-clients"]["post"]["requestBody"]["content"]["application/json"];

/**
 * The edit body, which differs from the create body in one field: the
 * credential is optional, and omitting it keeps the one the server holds. A
 * write-only secret never comes back, so an edit form has nothing to resend.
 */
export type DownloadClientUpdate =
  paths["/api/admin/download-clients/{client_id}"]["put"]["requestBody"]["content"]["application/json"];

export type DownloadClientImplementation = "qbittorrent" | "sabnzbd";

/**
 * Which protocol an implementation speaks, exactly as `routers/setup.py:57`
 * fills it in. The wizard refuses to ask an operator for something the chosen
 * adapter already implies, and asking here instead would make the two screens
 * disagree about the same row.
 */
export const CLIENT_PROTOCOLS: Record<DownloadClientImplementation, string> = {
  qbittorrent: "torrent",
  sabnzbd: "usenet",
};

export const DOWNLOAD_CLIENTS_KEY = ["admin", "download-clients"] as const;

export function useDownloadClients(): UseQueryResult<DownloadClient[], ApiRequestError> {
  const session = useSession();

  return useQuery<DownloadClient[], ApiRequestError>({
    queryKey: DOWNLOAD_CLIENTS_KEY,
    enabled: session.data?.role === "admin",
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/download-clients");
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
  });
}
