import type { paths } from "@pornarr/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { getApiClient } from "../../../lib/api";
import { apiFailure } from "../../../lib/api-error";

type Indexer = paths["/api/admin/indexers"]["get"]["responses"][200]["content"]["application/json"][number];
const KEY = ["admin", "indexers"] as const;
export function IndexersRoute() {
  const { t } = useTranslation(); const cache = useQueryClient();
  const indexers = useQuery({ queryKey: KEY, queryFn: async (): Promise<Indexer[]> => { const { data, error, response } = await getApiClient().GET("/api/admin/indexers"); if (!data || error) throw apiFailure(error, response); return data; } });
  const action = useMutation({ mutationFn: async ({ id, action }: { id: string; action: "test" | "reset" | "delete" }) => { const api = getApiClient(); const result = action === "test" ? await api.POST("/api/admin/indexers/{indexer_id}/test", { params: { path: { indexer_id: id } } }) : action === "reset" ? await api.POST("/api/admin/indexers/{indexer_id}/reset", { params: { path: { indexer_id: id } } }) : await api.DELETE("/api/admin/indexers/{indexer_id}", { params: { path: { indexer_id: id } } }); if (result.error) throw apiFailure(result.error, result.response); }, onSuccess: () => void cache.invalidateQueries({ queryKey: KEY }) });
  if (indexers.isPending) return <p>{t("indexers.loading")}</p>; if (indexers.isError) return <p role="alert">{t("errors.generic")}</p>;
  return <section><h1>{t("indexers.title")}</h1><ul>{indexers.data.map((item) => <li key={item.id}><strong>{item.name}</strong><p>{item.protocol} · {item.health} · {t("indexers.priority", { value: item.priority })}</p><p>{t("indexers.stats", { queries: item.stats.queries, failures: item.stats.failures })}</p>{item.last_error ? <p role="alert">{item.last_error}</p> : null}<button onClick={() => action.mutate({ id: item.id, action: "test" })}>{t("indexers.test")}</button><button onClick={() => action.mutate({ id: item.id, action: "reset" })}>{t("indexers.reset")}</button><button onClick={() => action.mutate({ id: item.id, action: "delete" })}>{t("indexers.remove")}</button></li>)}</ul></section>;
}
