import type { paths } from "@pornarr/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { getApiClient } from "../../../lib/api";
import { apiFailure } from "../../../lib/api-error";

type Client = paths["/api/admin/download-clients"]["get"]["responses"][200]["content"]["application/json"][number];
const KEY = ["admin", "download-clients"] as const;
export function DownloadClientsRoute() {
  const { t } = useTranslation(); const cache = useQueryClient();
  const clients = useQuery({ queryKey: KEY, queryFn: async (): Promise<Client[]> => { const { data, error, response } = await getApiClient().GET("/api/admin/download-clients"); if (!data || error) throw apiFailure(error, response); return data; } });
  const test = useMutation({ mutationFn: async (id: string) => { const { error, response } = await getApiClient().POST("/api/admin/download-clients/{client_id}/test", { params: { path: { client_id: id } } }); if (error) throw apiFailure(error, response); }, onSuccess: () => void cache.invalidateQueries({ queryKey: KEY }) });
  const remove = useMutation({ mutationFn: async (id: string) => { const { error, response } = await getApiClient().DELETE("/api/admin/download-clients/{client_id}", { params: { path: { client_id: id } } }); if (error) throw apiFailure(error, response); }, onSuccess: () => void cache.invalidateQueries({ queryKey: KEY }) });
  if (clients.isPending) return <p>{t("downloadClients.loading")}</p>; if (clients.isError) return <p role="alert">{t("errors.generic")}</p>;
  return <section><h1>{t("downloadClients.title")}</h1><p>{t("downloadClients.intro")}</p><ul>{clients.data.map((client) => <li key={client.id}><strong>{client.name}</strong><p>{client.protocol} · {client.health}</p>{client.last_error ? <p role="alert">{client.last_error}</p> : null}<button onClick={() => test.mutate(client.id)}>{t("downloadClients.test")}</button><button onClick={() => remove.mutate(client.id)}>{t("downloadClients.remove")}</button></li>)}</ul></section>;
}
