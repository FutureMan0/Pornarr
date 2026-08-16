import type { paths } from "@pornarr/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { getApiClient } from "../../../lib/api";
import { apiFailure } from "../../../lib/api-error";

type Profiles = paths["/api/filters"]["get"]["responses"][200]["content"]["application/json"];
const KEY = ["filters"] as const;
export function FiltersRoute() {
  const { t } = useTranslation(); const cache = useQueryClient(); const [title, setTitle] = useState(""); const [result, setResult] = useState<string>();
  const profiles = useQuery({ queryKey: KEY, queryFn: async (): Promise<Profiles> => { const { data, error, response } = await getApiClient().GET("/api/filters"); if (!data || error) throw apiFailure(error, response); return data; } });
  const dryRun = useMutation({ mutationFn: async () => { const { data, error, response } = await getApiClient().POST("/api/filters/dry-run", { body: { title } }); if (!data || error) throw apiFailure(error, response); return data; }, onSuccess: (data) => setResult(data.rule_id ? `${data.action}: ${data.rule_id}` : data.action) });
  const toggle = useMutation({ mutationFn: async (rule: Profiles["personal_rules"][number]) => { const { error, response } = await getApiClient().PATCH("/api/filters/rules/{rule_id}", { params: { path: { rule_id: rule.id } }, body: { kind: rule.kind, pattern: rule.pattern, action: rule.action, enabled: !rule.enabled } }); if (error) throw apiFailure(error, response); }, onSuccess: () => void cache.invalidateQueries({ queryKey: KEY }) });
  if (profiles.isPending) return <p>{t("filters.loading")}</p>; if (profiles.isError) return <p role="alert">{t("errors.generic")}</p>;
  return <section><h1>{t("filters.title")}</h1><h2>{t("filters.global")}</h2><ul>{profiles.data.global_rules.map((rule) => <li key={rule.id}>{rule.kind}: {rule.pattern} · {rule.action}</li>)}</ul><h2>{t("filters.personal")}</h2><ul>{profiles.data.personal_rules.map((rule) => <li key={rule.id}>{rule.kind}: {rule.pattern} · {rule.action}<button type="button" onClick={() => toggle.mutate(rule)}>{rule.enabled ? t("filters.disable") : t("filters.enable")}</button></li>)}</ul><h2>{t("filters.dryRun")}</h2><form onSubmit={(event) => { event.preventDefault(); dryRun.mutate(); }}><label htmlFor="filter-title">{t("filters.titleField")}</label><input id="filter-title" value={title} onChange={(event) => setTitle(event.target.value)} required /><button type="submit">{t("filters.test")}</button></form>{result ? <p role="status">{result}</p> : null}</section>;
}
