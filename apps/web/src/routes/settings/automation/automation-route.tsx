import type { paths } from "@pornarr/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { getApiClient } from "../../../lib/api";
import { apiFailure } from "../../../lib/api-error";

type Automation = paths["/api/automation"]["get"]["responses"][200]["content"]["application/json"];
const KEY = ["automation"] as const;
export function AutomationRoute() {
  const { t } = useTranslation(); const cache = useQueryClient(); const [confirm, setConfirm] = useState(false);
  const automation = useQuery({ queryKey: KEY, queryFn: async (): Promise<Automation> => { const { data, error, response } = await getApiClient().GET("/api/automation"); if (!data || error) throw apiFailure(error, response); return data; } });
  const save = useMutation({ mutationFn: async (body: Omit<Automation, "usage" | "decisions">) => { const { error, response } = await getApiClient().PUT("/api/automation", { body }); if (error) throw apiFailure(error, response); }, onSuccess: () => void cache.invalidateQueries({ queryKey: KEY }) });
  const kill = useMutation({ mutationFn: async () => { const { error, response } = await getApiClient().POST("/api/automation/kill"); if (error) throw apiFailure(error, response); }, onSuccess: () => void cache.invalidateQueries({ queryKey: KEY }) });
  if (automation.isPending) return <p>{t("automation.loading")}</p>; if (automation.isError) return <p role="alert">{t("errors.generic")}</p>;
  const item = automation.data; const body = { enabled: item.enabled, minimum_score: item.minimum_score, daily_download_limit_gb: item.daily_download_limit_gb, max_concurrent_jobs: item.max_concurrent_jobs, max_downloads_per_day: item.max_downloads_per_day, allowed_qualities: item.allowed_qualities ?? [], blocked_tags: item.blocked_tags ?? [], blocked_performers: item.blocked_performers ?? [] };
  return <section><h1>{t("automation.title")}</h1><p>{t("automation.usage", { used: item.usage.downloaded_bytes + item.usage.reserved_bytes, limit: item.daily_download_limit_gb * 1_000_000_000 })}</p>{item.enabled ? <button type="button" onClick={() => kill.mutate()}>{t("automation.kill")}</button> : confirm ? <button type="button" onClick={() => save.mutate({ ...body, enabled: true })}>{t("automation.confirmEnable")}</button> : <button type="button" onClick={() => setConfirm(true)}>{t("automation.enable")}</button>}<h2>{t("automation.history")}</h2><ul>{item.decisions.map((decision) => <li key={decision.id}>{decision.action}: {String(decision.context.reason ?? decision.target ?? "")}</li>)}</ul></section>;
}
